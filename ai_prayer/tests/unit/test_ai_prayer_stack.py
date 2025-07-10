import aws_cdk as core
import aws_cdk.assertions as assertions

from ai_prayer.ai_prayer_stack import AiPrayerStack

# example tests. To run these tests, uncomment this file along with the example
# resource in ai_prayer/ai_prayer_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = AiPrayerStack(app, "ai-prayer")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
