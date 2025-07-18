import os
import boto3
from datetime import datetime, timedelta

# Environment Variables
USERS_TABLE_NAME = os.environ['USERS_TABLE_NAME']
ADMIN_EMAIL = os.environ['ADMIN_EMAIL']
SEND_EMAIL = os.environ['SEND_EMAIL']

# AWS Clients
dynamodb = boto3.resource('dynamodb')
ses = boto3.client('ses')

def handler(event, context):
    users_table = dynamodb.Table(USERS_TABLE_NAME)
    
    # Scan for users who are not verified
    response = users_table.scan(
        FilterExpression='verified = :v',
        ExpressionAttributeValues={':v': False}
    )
    
    unverified_users = response.get('Items', [])
    
    # Filter for users who signed up more than 24 hours ago
    reportable_users = []
    for user in unverified_users:
        subscribed_at_str = user.get('subscribed_at')
        if subscribed_at_str:
            subscribed_at = datetime.fromisoformat(subscribed_at_str)
            if datetime.utcnow() - subscribed_at > timedelta(hours=24):
                reportable_users.append(user['email'])

    if not reportable_users:
        print("No unverified users older than 24 hours found.")
        return {
            'statusCode': 200,
            'body': 'No reportable users.'
        }

    # Send email report to admin
    subject = "Daily Report: Unverified Users on AI Prayer Companion"
    body_html = f"""
    <h3>Unverified User Report</h3>
    <p>The following users signed up more than 24 hours ago but have not verified their email address:</p>
    <ul>
        {''.join([f'<li>{email}</li>' for email in reportable_users])}
    </ul>
    <p>Total: {len(reportable_users)}</p>
    """
    
    ses.send_email(
        Source=SEND_EMAIL,
        Destination={'ToAddresses': [ADMIN_EMAIL]},
        Message={
            'Subject': {'Data': subject},
            'Body': {'Html': {'Data': body_html}}
        }
    )
    
    print(f"Sent report for {len(reportable_users)} unverified users to {ADMIN_EMAIL}.")
    
    return {
        'statusCode': 200,
        'body': f'Report sent for {len(reportable_users)} users.'
    }
