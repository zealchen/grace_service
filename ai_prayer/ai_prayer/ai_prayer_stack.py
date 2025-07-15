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
    aws_ses_actions as ses_actions,
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


        # EventBridge Rules with Central Time adjustments
        # Note: EventBridge uses UTC, so we adjust the hours accordingly

        # 4:00 PM Central Time = 21:00 UTC (during CDT) or 22:00 UTC (during CST)
        # Using 21:00 UTC for Daylight Saving Time (March - November)
        events.Rule(
            self, "CheckInRule",
            schedule=events.Schedule.expression("cron(0 21 * * ? *)"),  # 4:00 PM CDT = 21:00 UTC
            targets=[
                targets.LambdaFunction(
                    unified_lambda,
                    event=events.RuleTargetInput.from_object({
                        "action": "check-in",
                    })
                )
            ]
        )

        # 7:00 AM Central Time = 12:00 UTC (during CDT) or 13:00 UTC (during CST)
        # Using 12:00 UTC for Daylight Saving Time
        events.Rule(
            self, "MorningPrayerRule",
            schedule=events.Schedule.expression("cron(0 12 * * ? *)"),  # 7:00 AM CDT = 12:00 UTC
            targets=[
                targets.LambdaFunction(
                    unified_lambda,
                    event=events.RuleTargetInput.from_object({"action": "prayer-generation-dispatch"})
                )
            ]
        )

        # 10:00 PM Central Time = 03:00 UTC next day (during CDT) or 04:00 UTC next day (during CST)
        # Using 03:00 UTC for Daylight Saving Time
        events.Rule(
            self, "EveningPrayerRule",
            schedule=events.Schedule.expression("cron(0 3 * * ? *)"),  # 10:00 PM CDT = 03:00 UTC next day
            targets=[
                targets.LambdaFunction(
                    unified_lambda,
                    event=events.RuleTargetInput.from_object({"action": "prayer-generation-dispatch"})
                )
            ]
        )

        # S3 bucket to store incoming emails
        email_bucket = s3.Bucket(
            self, "EmailBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Grant the unified lambda permissions to read from the email bucket
        email_bucket.grant_read(unified_lambda.role)

        # Add S3 event source to the unified lambda
        unified_lambda.add_event_source(
            lambda_event_sources.S3EventSource(
                email_bucket,
                events=[s3.EventType.OBJECT_CREATED]
            )
        )

        # SES Receipt Rule Set
        rule_set = ses.ReceiptRuleSet(
            self, "ReceiptRuleSet",
            receipt_rule_set_name="ActiveRuleSet",
            drop_spam=True
        )

        rule_set.add_rule(
            "ProcessEmailRule",
            recipients=[app_config['send_email']],
            actions=[
                ses_actions.S3(
                    bucket=email_bucket,
                    object_key_prefix="emails/",
                )
            ],
            enabled=True
        )
        
        # 激活 Rule Set
        from aws_cdk import custom_resources as cr

        # 创建自定义资源来激活 Rule Set
        activate_rule_set = cr.AwsCustomResource(
            self, "ActivateRuleSet",
            on_create=cr.AwsSdkCall(
                service="SES",
                action="setActiveReceiptRuleSet",
                parameters={
                    "RuleSetName": rule_set.receipt_rule_set_name
                },
                physical_resource_id=cr.PhysicalResourceId.of('ActivateSESRuleSet'),
                region=self.region
            ),
            on_delete=cr.AwsSdkCall(
                service="SES", 
                action="setActiveReceiptRuleSet",
                parameters={},  # 空参数会取消激活
                region=self.region
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["ses:SetActiveReceiptRuleSet"],
                    resources=["*"]
                )
            ])
        )

        # 确保在 rule set 创建后再激活
        activate_rule_set.node.add_dependency(rule_set)

        # Add environment variable for the email bucket
        unified_lambda.add_environment("EMAIL_BUCKET_NAME", email_bucket.bucket_name)