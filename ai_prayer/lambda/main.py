import os
import boto3
import json
import tempfile
from datetime import datetime, timedelta
import urllib.parse
from elevenlabs.client import ElevenLabs

def check_in(event):
    api_gateway_url = os.environ.get('API_GATEWAY_URL', 'https://khtpxfrk5d.execute-api.us-east-1.amazonaws.com/prod/') 
    recipient_emails = os.environ["RECIPIENT_EMAIL"].split('|')

    ses_client = boto3.client("ses")

    subject = "How are you feeling today?"
    body_text = (
        "Click the link to let me know how you are feeling today. "
        "This will help me customize your evening prayer."
    )
    body_html = f"""
        <html>
            <head></head>
            <body>
                <h1>How are you feeling today?</h1>
                <p>{body_text}</p>
                <form action="{api_gateway_url}feelings" method="POST">
                    <textarea name="feeling" rows="4" cols="50"></textarea>
                    <br>
                    <input type="submit" value="Submit">
                </form>
            </body>
        </html>
    """

    ses_client.send_email(
        Source="neochen428@gmail.com",
        Destination={
            "ToAddresses": recipient_emails
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
    feeling = decoded_body.split("=")[1]

    table.put_item(
        Item={
            "email": "neochen428@gmail.com",
            "timestamp": datetime.utcnow().isoformat(),
            "feeling": feeling,
        }
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": "<html><body><h1>Thank you for sharing!</h1></body></html>",
    }

def prayer_generation(event):
    feelings_table_name = os.environ["FEELINGS_TABLE_NAME"]
    prayers_bucket_name = os.environ["PRAYERS_BUCKET_NAME"]
    recipient_emails = os.environ["RECIPIENT_EMAIL"].split('|')
    lookback_days = int(os.environ["LOOKBACK_DAYS"])
    elevenlabs_api_key = os.environ["ELEVENLABS_API_KEY"]
    bedrock_model_id = os.environ["BEDROCK_MODEL_ID"]

    elevenlabs_client = ElevenLabs(api_key=elevenlabs_api_key)
    
    # 1. Read user's feelings from DynamoDB
    dynamodb_client = boto3.resource("dynamodb")
    table = dynamodb_client.Table(feelings_table_name)
    
    start_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    
    response = table.query(
        KeyConditionExpression="email = :email AND #ts > :start_date",
        ExpressionAttributeNames={
"#ts": "timestamp"},
        ExpressionAttributeValues={
            ":email": recipient_emails,
            ":start_date": start_date,
        },
    )
    
    feelings = [item["feeling"] for item in response.get("Items", [])]
    
    # 2. Generate a prayer using Bedrock
    bedrock_runtime_client = boto3.client("bedrock-runtime")
    
    prompt = f"""
    Based on the following recent feelings and activities from the past year: {', '.join(feelings)}.

    Please generate a deep, reflective, and personal prayer. The prayer should be structured to last for approximately 3 minutes when spoken aloud at a calm, meditative pace. This means the prayer should be around 450 to 500 words long.

    The prayer should have a clear structure:
    1.  **Opening:** Start with a salutation and a moment of gratitude, acknowledging the user's recent state of mind.
    2.  **Reflection:** Elaborate on the feelings and activities provided. Explore potential themes, challenges, or blessings hidden within these experiences. If there are struggles, offer words of comfort and strength. If there are joys, express gratitude and celebration.
    3.  **Petition/Aspiration:** Make a request for guidance, peace, wisdom, or strength for the day or night ahead, directly related to the user's life.
    4.  **Closing:** End with a concluding thought, a blessing, or a traditional closing.

    The tone should be comforting, empathetic, and uplifting.
    """
    
    body = json.dumps({
        "prompt": f"\n\nHuman:{prompt}\n\nAssistant:",
        "max_tokens_to_sample": 800,
    })

    response = bedrock_runtime_client.invoke_model(
        body=body, modelId=bedrock_model_id
    )
    
    prayer_text = json.loads(response.get("body").read()).get("completion")

    # 3. Convert the prayer to audio using ElevenLabs
    audio = elevenlabs_client.text_to_speech.convert(
        text=prayer_text,
        voice_id="A9evEp8yGjv4c3WsIKuY",
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )
    audio_bytes = b"".join(audio)
    
    # # 4. Store the audio in S3
    s3_client = boto3.client("s3")
    file_name = f"prayer-{datetime.utcnow().isoformat()}.mp3"
    s3_key = f"prayers/{file_name}"
    
    with tempfile.TemporaryDirectory() as temp_dir:
        audio_path = os.path.join(temp_dir, file_name)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        s3_client.upload_file(audio_path, prayers_bucket_name, s3_key, ExtraArgs={
            "ContentType": "audio/mp3",
            "ContentDisposition": "inline"  # 👈 关键：允许内嵌播放
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
        Source="neochen428@gmail.com",
        Destination={
            "ToAddresses": recipient_emails
        },
        Message={
            "Subject": {"Data": subject},
            "Body": {
                "Text": {"Data": body_text},
                "Html": {"Data": body_html},
            },
        }
    )

    return {"statusCode": 200, "body": "Prayer generated and sent successfully!"}

def handler(event, context):
    
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
    elif action == "prayer-generation":
        return prayer_generation(event)
    else:
        return {"statusCode": 400, "body": f"Invalid action: {action}"}