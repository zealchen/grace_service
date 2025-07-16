import os
import re
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
from pydub import AudioSegment
from llm import invoke_model
import uuid


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

dynamodb_client = boto3.resource("dynamodb")
ses_client = boto3.client("ses")

USERS_TABLE = dynamodb_client.Table(os.environ["USERS_TABLE_NAME"])
FEELINGS_TABLE = dynamodb_client.Table(os.environ["FEELINGS_TABLE_NAME"])
SEND_EMAIL = os.environ["SEND_EMAIL"]


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


def signup(event):
    body = json.loads(event['body'])
    email = body['email']
    
    verification_token = str(uuid.uuid4())
    
    USERS_TABLE.put_item(
        Item={
            'email': email,
            'verified': False,
            'verification_token': verification_token
        }
    )
    
    # Construct the verification link using the API Gateway URL from the event
    # This is passed in from the EventBridge rule
    api_gateway_url = event.get('api_gateway_url', f"https://{event['requestContext']['domainName']}/{event['requestContext']['stage']}")
    verification_link = f"{api_gateway_url}/verify?email={urllib.parse.quote(email)}&token={verification_token}"
    
    ses_client.send_email(
        Source=SEND_EMAIL,
        Destination={'ToAddresses': [email]},
        Message={
            'Subject': {'Data': "Verify your email for AI Prayer Companion"},
            'Body': {
                'Html': {
                    'Data': f"""
                        <p>Thank you for signing up! Please click the link below to verify your email address:</p>
                        <a href="{verification_link}">Verify Email</a>
                    """
                }
            }
        }
    )
    
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
        },
        'body': json.dumps({'message': 'Verification email sent.'})
    }


def verify(event):
    email = event['queryStringParameters']['email']
    token = event['queryStringParameters']['token']
    
    response = USERS_TABLE.get_item(Key={'email': email})
    user = response.get('Item')
    
    if user and user.get('verification_token') == token:
        USERS_TABLE.update_item(
            Key={'email': email},
            UpdateExpression="set verified = :v",
            ExpressionAttributeValues={':v': True}
        )
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'text/html'},
            'body': "<h1>Email verified successfully!</h1><p>You will now receive daily prayer check-ins.</p>"
        }
    
    return {
        'statusCode': 400,
        'headers': {'Content-Type': 'text/html'},
        'body': "<h1>Invalid verification link.</h1>"
    }


def journal(event):
    body = json.loads(event['body'])
    email = body['email']
    feeling = body['feeling']
    
    FEELINGS_TABLE.put_item(
        Item={
            'email': email,
            'timestamp': datetime.utcnow().isoformat(),
            'feeling': feeling
        }
    )
    
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
        },
        'body': json.dumps({'message': 'Journal entry saved.'})
    }


def check_in(event):
    web_bucket_url = event.get('web_bucket_url')
    if not web_bucket_url:
        LOGGER.error("web_bucket_url not found in event")
        return {"statusCode": 500, "body": "web_bucket_url not configured"}

    response = USERS_TABLE.scan(FilterExpression="verified = :v", ExpressionAttributeValues={':v': True})
    users = response.get('Items', [])
    
    for user in users:
        email = user['email']
        journal_link = f"{web_bucket_url}/journal.html?email={urllib.parse.quote(email)}"
        
        ses_client.send_email(
            Source=SEND_EMAIL,
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': "How are you feeling today?"},
                'Body': {
                    'Html': {
                        'Data': f"""
                            <h1>How are you feeling today?</h1>
                            <p>Click the link below to share your thoughts and feelings for today's prayer:</p>
                            <a href="{journal_link}">Share Your Feelings</a>
                        """
                    }
                }
            }
        )
        
    return {"statusCode": 200, "body": "Check-in emails sent."}


def prayer_generation_dispatch(event):
    queue_url = os.environ["PRAYER_REQUEST_QUEUE_URL"]
    sqs_client = boto3.client("sqs")
    
    response = USERS_TABLE.scan(FilterExpression="verified = :v", ExpressionAttributeValues={':v': True})
    users = response.get('Items', [])

    for user in users:
        email = user['email']
        message_body = json.dumps({"recipient_email": email})
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=message_body
        )
        LOGGER.info(f"Dispatched prayer request for {email}")

    return {"statusCode": 200, "body": "Prayer requests dispatched."}


def prayer_generation_process(event):
    for record in event['Records']:
        message = json.loads(record['body'])
        recipient_email = message['recipient_email']
        
        LOGGER.info(f"Processing prayer for {recipient_email}")

        prayers_bucket_name = os.environ["PRAYERS_BUCKET_NAME"]
        lookback_days = int(os.environ["LOOKBACK_DAYS"])
        openai_api_key = os.environ["OPENAI_API_KEY"]
        
        openai_client = openai.OpenAI(api_key=openai_api_key)
        
        start_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        
        response = FEELINGS_TABLE.query(
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
        b. It starts with a quota from Bible(Gospel) and a Bible story that could represent my latest feeling or experience.
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
        
        s3_client = boto3.client("s3")
        file_name = f"prayer-{datetime.utcnow().isoformat()}.mp3"
        s3_key = f"prayers/{recipient_email}/{file_name}"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, file_name)
            with openai_client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts",
                voice="onyx",
                input=prayer_text,
                instructions=instruction,
            ) as response:
                response.stream_to_file(audio_path)
                
            audio_path = merge_prayer_with_pg(audio_path)
            s3_client.upload_file(audio_path, prayers_bucket_name, s3_key, ExtraArgs={
                "ContentType": "audio/mp3",
                "ContentDisposition": "inline"
            })

        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": prayers_bucket_name, "Key": s3_key},
            ExpiresIn=3600*24,
        )

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
            Source=SEND_EMAIL,
            Destination={"ToAddresses": [recipient_email]},
            Message={
                "Subject": {"Data": "Your Daily Prayer Reflection"},
                "Body": {"Html": {"Data": body_html}},
            }
        )
        LOGGER.info(f"Prayer generated and sent to {recipient_email}")

    return {"statusCode": 200, "body": f"Processed {len(event['Records'])} prayer requests."}


def merge_prayer_with_pg(prayer_path):
    prayer = AudioSegment.from_file(prayer_path)
    bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bg.mp3')
    background = AudioSegment.from_file(bg_path)
    if len(background) < len(prayer):
        background = background * (len(prayer) // len(background) + 1)

    background = background[:len(prayer)]
    combined = prayer.overlay(background)
    
    prefix, ext = os.path.splitext(prayer_path)
    final_path = f'{prefix}_bg1{ext}'
    combined.export(final_path, format="mp3")
    return final_path


def handler(event, context):
    LOGGER.info(f"Received event: {json.dumps(event)}")

    # API Gateway routing
    if 'httpMethod' in event:
        path = event['path']
        method = event['httpMethod']
        
        if path == '/signup' and method == 'POST':
            return signup(event)
        elif path == '/verify' and method == 'GET':
            return verify(event)
        elif path == '/journal' and method == 'POST':
            return journal(event)
        elif method == 'OPTIONS':
            return {
                'statusCode': 200,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
                },
                'body': ''
            }

    # SQS routing
    if "Records" in event and event["Records"][0]["eventSource"] == "aws:sqs":
        return prayer_generation_process(event)

    # EventBridge routing
    action = event.get("action")
    if action == "check-in":
        return check_in(event)
    elif action == "prayer-generation-dispatch":
        return prayer_generation_dispatch(event)

    LOGGER.error(f"Unknown event: {event}")
    return {"statusCode": 400, "body": "Invalid action or event source."}
