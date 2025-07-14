import os
import boto3
import json
import tempfile
import logging
import openai
import requests
from bs4 import BeautifulSoup
from datetime import date
from pathlib import Path
from datetime import datetime, timedelta
import urllib.parse
from elevenlabs.client import ElevenLabs
from llm import invoke_model
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def get_today_gospel():
    today = date.today().isoformat()
    url = f"https://bible.usccb.org/daily-bible-reading?date={today}"
    
    response = requests.get(url)
    if response.status_code != 200:
        return f"Failed to fetch data: {response.status_code}"

    soup = BeautifulSoup(response.text, 'html.parser')

    # Find all the reading blocks
    readings = soup.find_all('div', class_='b-verse')
    
    for reading in readings:
        # Find the title of the reading
        title_tag = reading.find('h3', class_='name')
        
        if title_tag and 'Gospel' in title_tag.text:
            gospel_title = title_tag.text.strip()
            
            # Find the content of the reading
            content_body = reading.find('div', class_='content-body')
            if content_body:
                gospel_text = content_body.get_text(separator="\n").strip()
                return f"{gospel_title}\n\n{gospel_text}"

    return ""


def check_in(event):
    api_gateway_url = os.environ.get('API_GATEWAY_URL', 'https://khtpxfrk5d.execute-api.us-east-1.amazonaws.com/prod/') 
    recipient_emails = os.environ["RECIPIENT_EMAIL"].split('|')
    send_email = os.environ["SEND_EMAIL"]

    ses_client = boto3.client("ses")

    subject = "How are you feeling today?"
    body_text = (
        "Click the link to let me know how you are feeling today. "
        "This will help me customize your evening prayer."
    )

    for email in recipient_emails:
        body_html = f"""
            <html>
                <head></head>
                <body>
                    <h1>How are you feeling today?</h1>
                    <p>{body_text}</p>
                    <form action="{api_gateway_url}feelings" method="POST">
                        <input type="hidden" name="email" value="{email}">
                        <textarea name="feeling" rows="4" cols="50"></textarea>
                        <br>
                        <input type="submit" value="Submit">
                    </form>
                </body>
            </html>
        """
        ses_client.send_email(
            Source=send_email,
            Destination={
                "ToAddresses": [email]
            },
            Message={
                "Subject": {
                    "Data": subject
                },
                "Body": {
                    "Text": {
                        "Data": body_text
                    },
                    "Html": {
                        "Data": body_html
                    }
                }
            }
        )

    return {"statusCode": 200, "body": "Email sent successfully!"}

def data_capture(event):
    feelings_table_name = os.environ["FEELINGS_TABLE_NAME"]
    dynamodb_client = boto3.resource("dynamodb")
    table = dynamodb_client.Table(feelings_table_name)

    # The body of the request is a URL-encoded string
    body = event.get("body", "")
    decoded_body = urllib.parse.unquote_plus(body)
    
    # The feeling is in the format "feeling=..."
    form_data = {field.split("=")[0]: field.split("=")[1] for field in decoded_body.split("&")}
    feeling = form_data.get("feeling")
    email = form_data.get("email")

    if not email or not feeling:
        return {
            "statusCode": 400,
            "body": "Missing email or feeling in form data."
        }

    table.put_item(
        Item={
            "email": email,
            "timestamp": datetime.utcnow().isoformat(),
            "feeling": feeling,
        }
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": "<html><body><h1>Thank you for sharing!</h1></body></html>",
    }

def prayer_generation_dispatch(event):
    """
    Dispatches prayer generation requests to SQS for each recipient.
    """
    recipient_emails = os.environ["RECIPIENT_EMAIL"].split('|')
    queue_url = os.environ["PRAYER_REQUEST_QUEUE_URL"]
    sqs_client = boto3.client("sqs")

    for email in recipient_emails:
        message_body = json.dumps({"recipient_email": email})
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=message_body
        )
        LOGGER.info(f"Dispatched prayer request for {email}")

    return {"statusCode": 200, "body": "Prayer requests dispatched."}


def prayer_generation_process(event):
    """
    Generates and sends a prayer for each recipient from an SQS message batch.
    """
    for record in event['Records']:
        message = json.loads(record['body'])
        recipient_email = message['recipient_email']
        
        LOGGER.info(f"Processing prayer for {recipient_email}")

        feelings_table_name = os.environ["FEELINGS_TABLE_NAME"]
        prayers_bucket_name = os.environ["PRAYERS_BUCKET_NAME"]
        lookback_days = int(os.environ["LOOKBACK_DAYS"])
        elevenlabs_api_key = os.environ["ELEVENLABS_API_KEY"]
        openai_api_key = os.environ["OPENAI_API_KEY"]
        bedrock_model_id = os.environ["BEDROCK_MODEL_ID"]
        send_email = os.environ["SEND_EMAIL"]

        # elevenlabs_client = ElevenLabs(api_key=elevenlabs_api_key)
        openai_client = openai.OpenAI(api_key=openai_api_key)
        
        # 1. Read user's feelings from DynamoDB
        dynamodb_client = boto3.resource("dynamodb")
        table = dynamodb_client.Table(feelings_table_name)
        
        start_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        
        response = table.query(
            KeyConditionExpression="email = :email AND #ts > :start_date",
            ExpressionAttributeNames={"#ts": "timestamp"},
            ExpressionAttributeValues={
                ":email": recipient_email,
                ":start_date": start_date,
            },
        )
        
        feelings = [item["feeling"] for item in response.get("Items", [])]
        if not feelings:
            LOGGER.info(f"no feelings found for {recipient_email}, skipping")
            continue
        
        # 2. Generate a prayer using Bedrock
        bedrock_runtime_client = boto3.client("bedrock-runtime")
        
        recent_activities = feelings[:-1]
        last_day_feeling = feelings[-1]
        
        gospel = get_today_gospel()
        
        prompt = f"""
    Please using five words to summarize my personal characteristics based on the following words: 
    {"\n".join(feelings)}
    
    Output rule:
    1. Output in JSON format with the characteristics as the key, and explanation as the value
    """
        LOGGER.info(f'prompt: {prompt}')
        response = openai_client.responses.create(
            model="gpt-4.1",
            input=prompt
        )
        characteristics = response.output[0].content[0].text
        # characteristics = invoke_model(bedrock_runtime_client, bedrock_model_id, prompt, max_tokens=800)
        
        prompt = f"""
    You are a HOLY prayer creator. Based on my personality:
    {characteristics}
    
    First, look at the today's Gospel:
    {gospel}

    Next, look at my latest feelings:
    {last_day_feeling}
    
    Finally come up with the God words and prayer.
    The final output rule:
    1. First paragraph.
        a. It begin with the sentence: "Let's first look at God's word:"
        b. It starts with a quota from Bible(Gospel) that could represent my latest feeling or experience.
        c. Then it give me some words from God to heal my heart regarding the latest suffering.
    2. Second paragraph
        a. It begin with the sentence: "Now, Let's pray together."
        b. It then gives a prayer of 8 to 10 sentences that begin with a thanking to God's word.
    
    3. Just output those words, do not give explanations."""
        LOGGER.info(f'prompt: {prompt}')
        # prayer_text = invoke_model(bedrock_runtime_client, bedrock_model_id, prompt, max_tokens=800)
        response = openai_client.responses.create(
            model="gpt-4.1",
            input=prompt
        )
        prayer_text = response.output[0].content[0].text

        # 3. Convert the prayer to audio using ElevenLabs
        # audio = elevenlabs_client.text_to_speech.convert(
        #     text=prayer_text,
        #     voice_id="A9evEp8yGjv4c3WsIKuY",
        #     model_id="eleven_multilingual_v2",
        #     output_format="mp3_44100_128",
        # )
        # audio_bytes = b"".join(audio)
        instruction = (
            "Speak as if you are God speaking directly to a beloved child—"
            "with deep authority, infinite compassion, and peaceful pace, "
            "and a voice that is both awe-inspiring and calming."
        )
        
        # 4. Store the audio in S3
        s3_client = boto3.client("s3")
        file_name = f"prayer-{datetime.utcnow().isoformat()}.mp3"
        s3_key = f"prayers/{recipient_email}/{file_name}"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, file_name)
            with openai_client.audio.speech.with_streaming_response.create(
                model="tts-1-hd",
                voice="onyx",
                input=prayer_text,
                instructions=instruction,
            ) as response:
                response.stream_to_file(audio_path)
            s3_client.upload_file(audio_path, prayers_bucket_name, s3_key, ExtraArgs={
                "ContentType": "audio/mp3",
                "ContentDisposition": "inline"
            })

        # Generate a pre-signed URL
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": prayers_bucket_name, "Key": s3_key},
            ExpiresIn=3600*24,
        )

        # 5. Send an email with a link to the audio
        ses_client = boto3.client("ses")
        
        subject = "Your Daily Prayer Reflection"
        body_text = f"""Dear Friend,

Your prayer for today is ready.

Listen to your reflection and prayer here:
{presigned_url}

May peace be with you."""

        body_html = f"""
    <html>
    <body>
        <h1>Your Daily Prayer</h1>
        <p>Dear Friend,</p>
        <p>Your personal prayer reflection is ready. You may listen to it here:</p>
        <p><a href="{presigned_url}">Click to Play</a></p>
        <hr>
        <p style="font-size: 0.8em; color: #666;">You are receiving this email because you opted in for daily prayer updates.</p>
    </body>
    </html>
    """

        ses_client.send_email(
            Source=send_email,
            Destination={
                "ToAddresses": [recipient_email]
            },
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Text": {"Data": body_text},
                    "Html": {"Data": body_html},
                },
            }
        )
        LOGGER.info(f"Prayer generated and sent to {recipient_email}")

    return {"statusCode": 200, "body": f"Processed {len(event['Records'])} prayer requests."}


def handler(event, context):
    
    # Check if the invocation is from SQS
    if "Records" in event and event["Records"][0]["eventSource"] == "aws:sqs":
        return prayer_generation_process(event)

    # If the event is from API Gateway, the body will be a string
    # that needs to be parsed.
    if "body" in event and isinstance(event["body"], str):
        try:
            body = json.loads(event["body"])
            action = body.get("action")
        except json.JSONDecodeError:
            # If the body is not a JSON string, it might be a URL-encoded string
            decoded_body = urllib.parse.unquote_plus(event["body"])
            if "feeling=" in decoded_body:
                action = "data-capture"
            else:
                action = None
    else:
        action = event.get("action")

    if action == "check-in":
        return check_in(event)
    elif action == "data-capture":
        return data_capture(event)
    elif action == "prayer-generation-dispatch":
        return prayer_generation_dispatch(event)
    else:
        return {"statusCode": 400, "body": f"Invalid action: {action}"}
