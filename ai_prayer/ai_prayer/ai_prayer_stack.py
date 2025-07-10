import os
from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_apigateway as apigateway,
    aws_events as events,
    aws_events_targets as targets,
    aws_ses as ses,
    aws_sqs as sqs,
    aws_lambda_event_sources as lambda_event_sources,
    RemovalPolicy,
    Duration,
)
from aws_cdk.aws_lambda import Architecture
from aws_cdk.aws_ecr_assets import Platform
from constructs import Construct
from aws_cdk import CfnOutput



class AiPrayerStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, app_config, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # SQS Queue for prayer requests
        prayer_request_queue = sqs.Queue(
            self, "PrayerRequestQueue",
            visibility_timeout=Duration.minutes(5),
            retention_period=Duration.days(4),
        )

        # DynamoDB Table to store user's feelings
        feelings_table = dynamodb.Table(
            self, "FeelingsTable",
            partition_key=dynamodb.Attribute(
                name="email",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING
            ),
            removal_policy=RemovalPolicy.DESTROY,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST
        )

        # S3 Bucket to store prayer audio files
        prayers_bucket = s3.Bucket(
            self, "PrayersBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # SES Email Identity
        ses.EmailIdentity(
            self, "EmailIdentity",
            identity=ses.Identity.email(app_config['send_email'])
        )

        # IAM Role for Lambda Functions
        lambda_role = iam.Role(
            self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )

        # Grant permissions to the Lambda role
        feelings_table.grant_read_write_data(lambda_role)
        prayers_bucket.grant_read_write(lambda_role)
        prayer_request_queue.grant_consume_messages(lambda_role)

        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"]
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["*"]
        ))

        unified_lambda = _lambda.DockerImageFunction(
            self, "PrayerLambda",
            code=_lambda.DockerImageCode.from_image_asset(
                directory=os.path.join(os.path.dirname(__file__), "..", "lambda"),
                platform=Platform.LINUX_AMD64
            ),
            timeout=Duration.minutes(3),
            memory_size=1024,
            role=lambda_role,
            architecture=Architecture.X86_64,
            environment={
                "FEELINGS_TABLE_NAME": feelings_table.table_name,
                "PRAYERS_BUCKET_NAME": prayers_bucket.bucket_name,
                "RECIPIENT_EMAIL": '|'.join(app_config['receive_emails']),
                "LOOKBACK_DAYS": "365",
                "ELEVENLABS_API_KEY": app_config['elevenlab_api_key'],
                "OPENAI_API_KEY": app_config['openai_api_key'],
                "BEDROCK_MODEL_ID": "arn:aws:bedrock:us-east-1:905418107398:inference-profile/us.deepseek.r1-v1:0", 
                "PRAYER_REQUEST_QUEUE_URL": prayer_request_queue.queue_url,
                "SEND_EMAIL": app_config['send_email'],
            },
        )
        
        prayer_request_queue.grant_send_messages(unified_lambda)
        unified_lambda.add_event_source(
            lambda_event_sources.SqsEventSource(prayer_request_queue)
        )


        # API Gateway
        api = apigateway.LambdaRestApi(
            self, "ApiGateway",
            handler=unified_lambda,
            proxy=False
        )

        feelings = api.root.add_resource("feelings")
        feelings.add_method(
            "POST",
            apigateway.LambdaIntegration(
                unified_lambda,
                request_templates={
                    "application/x-www-form-urlencoded": '{ "action": "data-capture", "body": $input.body }'
                }
            )
        )

        # Update environment with the API Gateway URL
        CfnOutput(self, "ApiEndpoint", value=api.url)

        # EventBridge Rules
        # 4:00 PM Check-in
        events.Rule(
            self, "CheckInRule",
            schedule=events.Schedule.cron(minute="0", hour="16"),
            targets=[
                targets.LambdaFunction(
                    unified_lambda,
                    event=events.RuleTargetInput.from_object({
                        "action": "check-in",
                    })
                )
            ]
        )

        # 7:00 AM Prayer
        events.Rule(
            self, "MorningPrayerRule",
            schedule=events.Schedule.cron(minute="0", hour="7"),
            targets=[
                targets.LambdaFunction(
                    unified_lambda,
                    event=events.RuleTargetInput.from_object({"action": "prayer-generation-dispatch"})
                )
            ]
        )

        # 10:00 PM Prayer
        events.Rule(
            self, "EveningPrayerRule",
            schedule=events.Schedule.cron(minute="0", hour="22"),
            targets=[
                targets.LambdaFunction(
                    unified_lambda,
                    event=events.RuleTargetInput.from_object({"action": "prayer-generation-dispatch"})
                )
            ]
        )