import os
import json
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
    aws_s3_deployment as s3_deployment,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    RemovalPolicy,
    Duration,
    CfnOutput,
)
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_lambda import Architecture
from aws_cdk.aws_ecr_assets import Platform
from constructs import Construct


class AiPrayerStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, app_config, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # DynamoDB Table to store user data
        users_table = dynamodb.Table(
            self, "UsersTable",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
            removal_policy=RemovalPolicy.DESTROY,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST
        )

        # DynamoDB Table to store user's feelings
        feelings_table = dynamodb.Table(
            self, "FeelingsTable",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            removal_policy=RemovalPolicy.DESTROY,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST
        )

        # S3 Bucket for prayer audio files
        prayers_bucket = s3.Bucket(
            self, "PrayersBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # S3 Bucket for the frontend website
        web_bucket = s3.Bucket(
            self, "WebBucket",
            website_index_document="index.html",
            website_error_document="index.html",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False
            )
        )

        # Add a bucket policy to allow public read access
        web_bucket.add_to_resource_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[web_bucket.arn_for_objects("*")],
            principals=[iam.AnyPrincipal()]
        ))

        # SES Email Identity
        ses.EmailIdentity(
            self, "EmailIdentity",
            identity=ses.Identity.email(app_config['send_email'])
        )

        # IAM Role for Lambda
        lambda_role = iam.Role(
            self, "LambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )
        users_table.grant_read_write_data(lambda_role)
        feelings_table.grant_read_write_data(lambda_role)
        prayers_bucket.grant_read_write(lambda_role)
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"]
        ))
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["*"]
        ))

        # API Gateway
        api = apigateway.RestApi(
            self, "PrayerApi",
            rest_api_name="Prayer Service API",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=['Content-Type', 'X-Amz-Date', 'Authorization', 'X-Api-Key', 'X-Amz-Security-Token']
            ),
            default_method_options=apigateway.MethodOptions(
                request_parameters={"method.request.header.Access-Control-Allow-Origin": True}
            )
        )

        # Unified Lambda Function
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
                "USERS_TABLE_NAME": users_table.table_name,
                "FEELINGS_TABLE_NAME": feelings_table.table_name,
                "PRAYERS_BUCKET_NAME": prayers_bucket.bucket_name,
                "LOOKBACK_DAYS": "365",
                "OPENAI_API_KEY": app_config['openai_api_key'],
                "SEND_EMAIL": app_config['send_email'],
                "ADMIN_EMAIL": app_config['admin_email'],
            },
        )

        # API Gateway to Lambda Integration
        lambda_integration = apigateway.LambdaIntegration(unified_lambda)
        api.root.add_resource("signup").add_method("POST", lambda_integration)
        api.root.add_resource("verify").add_method("GET", lambda_integration)
        api.root.add_resource("journal").add_method("POST", lambda_integration)
        api.root.add_resource("unsubscribe").add_method("GET", lambda_integration)
        api.root.add_resource("feedback").add_method("POST", lambda_integration)

        # SQS for prayer requests
        prayer_request_queue = sqs.Queue(
            self, "PrayerRequestQueue",
            visibility_timeout=Duration.minutes(5),
            retention_period=Duration.days(4),
        )
        prayer_request_queue.grant_send_messages(unified_lambda)
        prayer_request_queue.grant_consume_messages(lambda_role)
        unified_lambda.add_event_source(lambda_event_sources.SqsEventSource(prayer_request_queue))
        
        # Update Lambda environment with SQS URL
        unified_lambda.add_environment("PRAYER_REQUEST_QUEUE_URL", prayer_request_queue.queue_url)

        # --- Unverified User Reporter Lambda ---
        reporter_lambda = PythonFunction(
            self, "ReporterLambda",
            entry=os.path.join(os.path.dirname(__file__), "..", "lambda"),
            index="unverified_user_reporter.py",
            handler="handler",
            runtime=_lambda.Runtime.PYTHON_3_9,
            timeout=Duration.minutes(1),
            environment={
                "USERS_TABLE_NAME": users_table.table_name,
                "ADMIN_EMAIL": app_config['admin_email'],
                "SEND_EMAIL": app_config['send_email']
            }
        )
        users_table.grant_read_data(reporter_lambda)
        reporter_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail"],
            resources=["*"]
        ))

        # EventBridge Rules
        check_in_rule = events.Rule(
            self, "CheckInRule",
            schedule=events.Schedule.cron(minute="0", hour="21"),
            targets=[targets.LambdaFunction(
                unified_lambda,
                event=events.RuleTargetInput.from_object({
                    "action": "check-in",
                    "api_gateway_url": api.url,
                    "web_bucket_url": web_bucket.bucket_website_url
                })
            )]
        )
        
        prayer_dispatch_rule = events.Rule(
            self, "PrayerDispatchRule",
            schedule=events.Schedule.cron(minute="0", hour="12"),
            targets=[targets.LambdaFunction(
                unified_lambda,
                event=events.RuleTargetInput.from_object({
                    "action": "prayer-generation-dispatch",
                    "api_gateway_url": api.url
                })
            )]
        )

        reporter_rule = events.Rule(
            self, "ReporterRule",
            schedule=events.Schedule.cron(minute="0", hour="13"), # Runs daily at 1 PM UTC
            targets=[targets.LambdaFunction(reporter_lambda)]
        )

        # Deploy web assets to S3
        s3_deployment.BucketDeployment(
            self, "DeployWebsite",
            sources=[s3_deployment.Source.asset(os.path.join(os.path.dirname(__file__), "..", "lambda", "web"))],
            destination_bucket=web_bucket,
            prune=False,
            memory_limit=1024
        )

        # Create config.js and deploy to S3
        s3_deployment.BucketDeployment(
            self, "DeployConfig",
            sources=[s3_deployment.Source.data(
                "config.js",
                f"window.config = {{ apiGatewayUrl: '{api.url.rstrip('/')}' }};"
            )],
            destination_bucket=web_bucket,
            prune=False
        )

        # Outputs
        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "WebsiteUrl", value=web_bucket.bucket_website_url)

        # --- Custom Domain Configuration ---
        domain_name = "graceful.cloud"

        # ACM Certificate
        certificate = acm.Certificate(
            self, "Certificate",
            domain_name=domain_name,
            subject_alternative_names=[f"prayer.{domain_name}"],
            validation=acm.CertificateValidation.from_dns() # This will require manual DNS validation
        )

        # CloudFront Distribution
        distribution = cloudfront.CloudFrontWebDistribution(
            self, "CloudFrontDistribution",
            origin_configs=[
                cloudfront.SourceConfiguration(
                    s3_origin_source=cloudfront.S3OriginConfig(
                        s3_bucket_source=web_bucket
                    ),
                    behaviors=[
                        cloudfront.Behavior(
                            is_default_behavior=True,
                            path_pattern="/prayer/*"
                        )
                    ]
                )
            ],
            viewer_certificate=cloudfront.ViewerCertificate.from_acm_certificate(
                certificate,
                aliases=[domain_name, f"prayer.{domain_name}"]
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100
        )

        CfnOutput(self, "DistributionDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "CertificateArn", value=certificate.certificate_arn)
