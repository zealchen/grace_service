#!/usr/bin/env python3
import os
import json
import aws_cdk as cdk

from ai_prayer.ai_prayer_stack import AiPrayerStack

def load_config():
    with open('.config.json') as fp:
        return json.load(fp)

config = load_config()
app = cdk.App()
AiPrayerStack(
    app, "AiPrayerStack", 
    app_config=config,
    env=cdk.Environment(account=config['account'], region=config['region']))

app.synth()
