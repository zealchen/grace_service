import os
import asyncio
import boto3
import click
import json
import copy
import gradio as gr
from typing import List
from asyncio import Semaphore
import util
from botocore.client import Config
from googleapiclient.discovery import build

API_KEY = 'AIzaSyCGSIxn2T11d8cqgXH3V7CWFk9-8nkbLQU'
YOUTUBE_CLIENT = build('youtube', 'v3', developerKey=API_KEY)


REGION = 'us-east-1'
SESSION = boto3.Session(profile_name="default", region_name=REGION)
custom_config = Config(connect_timeout=840, read_timeout=840)
CLIENT = SESSION.client('bedrock-runtime', config=custom_config)
modelARN_DEEPSEEK_R1_V1 = 'arn:aws:bedrock:us-east-1:471112955155:inference-profile/us.deepseek.r1-v1:0'


AI_EXTRACT_PROMPT = f"""Human:
You are a helpful assistant that extracts the main points from a YouTube video. The output is in markdown format.
There is the comment list:
{{comment_list}}

Assistant:
"""

@click.group()
def cli():
    """A CLI app with summarize and comment commands."""
    pass



def invoke_bedrock_sync(comment_list) -> str:
    comment = "\n".join(comment_list)
    prompt = AI_EXTRACT_PROMPT.replace('{comment_list}', comment)
    return util.invoke_model(CLIENT, modelARN_DEEPSEEK_R1_V1, prompt, max_tokens=20000, attachment=None, model_type='deepseek', temperature=0.1)


async def invoke_bedrock_async(comment_list, sem: Semaphore) -> str:
    async with sem:
        return await asyncio.to_thread(invoke_bedrock_sync, comment_list)


async def run_bedrock_prompts(prompt_list: List[str], concurrency: int = 10) -> str:
    sem = Semaphore(concurrency)
    
    comment_list = []
    batch = []
    for idx, item in enumerate(prompt_list):
        if (idx + 1) % 100 == 0:
            comment_list.append(copy.deepcopy(batch))
            batch.clear()
        else:
            batch.append(item)
    if batch:
        comment_list.append(batch)
    tasks = [invoke_bedrock_async(batch, sem) for batch in comment_list]
    results = await asyncio.gather(*tasks)
    return results


def merge_comment_results(comment_results):
    prompts = f"""Human:
    You are a helpful assistant that merges the comment results into a single result. The output is in markdown format.
    The comment results are:
    {"\n".join(comment_results)}
    
    Assistant:
    """
    return util.invoke_model(CLIENT, modelARN_DEEPSEEK_R1_V1, prompts, max_tokens=20000, attachment=None, model_type='deepseek', temperature=0.1)

def get_comments(video_id):
    comments = []
    next_page_token = None

    while True:
        response = YOUTUBE_CLIENT.commentThreads().list(
            part='snippet,replies',
            videoId=video_id,
            maxResults=100,
            pageToken=next_page_token
        ).execute()

        for item in response['items']:
            comment = item['snippet']['topLevelComment']['snippet']['textDisplay']
            if len(comment.split('<a href="https://www.youtube.com/watch?v=')) > 5:
                print('skip table of contents')
                continue
            else:
                print(comment)
            comments.append(comment)

            if item['snippet']['totalReplyCount'] > 0:
                parent_id = item['id']
                replies = get_replies(parent_id)
                comments.extend(replies)

        next_page_token = response.get('nextPageToken')
        if not next_page_token:
            break

    return comments

def get_replies(parent_id):
    replies = []
    next_page_token = None

    while True:
        response = YOUTUBE_CLIENT.comments().list(
            part='snippet',
            parentId=parent_id,
            maxResults=100,
            pageToken=next_page_token
        ).execute()

        for item in response['items']:
            reply = item['snippet']['textDisplay']
            replies.append(reply)

        next_page_token = response.get('nextPageToken')
        if not next_page_token:
            break

    return replies


def get_transcript(id):
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        print('start get transcript')
        transcript = YouTubeTranscriptApi.get_transcript(id)
        print(transcript)
        result = ''
        for entry in transcript:
            result += entry['text'] + '\n'
        return result
    except Exception as e:
        print(f"get transcript failed: {e}")
        return None
    
def summarize_transcript(text):
    prompt = f"""Human:
    You are a helpful assistant that summarizes a transcript of a YouTube video. The output is in markdown format.
    The transcript is:
    {text}
    
    Assistant:
    """
    return util.invoke_model(CLIENT, modelARN_DEEPSEEK_R1_V1, prompt, max_tokens=20000, attachment=None, model_type='deepseek', temperature=0.1)
    

def summarize(video_url):
    from youtube_transcript import get_subtitles_with_ytdlp
    video_id = video_url.split('?v=', 1)[1].split('&')[0]
    
    transcript_path = os.path.join('datas', f"{video_id}_transcript.txt")
    summarize_path = os.path.join('datas', f"{video_id}_summarize.txt")
    if os.path.exists(transcript_path):
        with open(transcript_path, 'r') as fp:
            text = fp.read()
    else:
        text = get_subtitles_with_ytdlp(video_url)
        with open(transcript_path, 'w') as fp:
            fp.write(text)
    if not text:
        return 'get transcript failed!'

    if os.path.exists(summarize_path):
        with open(summarize_path, 'r') as fp:
            result = fp.read()
    else:
        result = summarize_transcript(text)
        result = util.format_result(result, type='markdown')
        with open(summarize_path, 'w') as fp:
            fp.write(result)
    return result


def comment(id):
    comments = get_comments(id)
    if comments:
        print(comments)
        with open('comments.txt', 'w') as fp:
            fp.writelines(comments)
    results = asyncio.run(run_bedrock_prompts(comments, concurrency=5))
    if len(results) == 1:
        return util.format_result(results[0], type='markdown')
    else:
        return util.format_result(merge_comment_results(results), type='markdown')


def summarize_comments(video_url):
    video_id = video_url.split('?v=', 1)[1].split('&')[0]
    return comment(video_id)

def summarize_captions(video_url):
    video_id = video_url.split('?v=', 1)[1].split('&')[0]
    return summarize(video_url)

def embed_youtube(url):
    iframe = f"""
    <iframe width="560" height="315"
        src="{url}"
        frameborder="0"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowfullscreen>
    </iframe>
    """
    return iframe

with gr.Blocks() as demo:
    gr.Markdown("## 🎥 YouTube Summary Assistant")

    caption_sum = gr.State()
    comment_sum = gr.State()
    
    video_id_input = gr.Textbox(label="YouTube Video URL", placeholder="https://www.youtube.com/watch?v=jvKf6zXrNO4&t=2s")
    show_btn = gr.Button("Show Video")
    video_html = gr.HTML()
    
    show_btn.click(embed_youtube, inputs=video_id_input, outputs=video_html)

    with gr.Row():
        caption_btn = gr.Button("Summarize Captions")
        comment_btn = gr.Button("Summarize Comments")

    with gr.Row():
        with gr.Column():
            caption_output = gr.Markdown()
        with gr.Column():
            comment_output = gr.Markdown()

    comment_btn.click(fn=summarize_comments, inputs=video_id_input, outputs=[comment_output, comment_sum])
    caption_btn.click(fn=summarize_captions, inputs=video_id_input, outputs=[caption_output, caption_sum])

    gr.Markdown("## 🤔 Ask me anything about the video!")
    with gr.Row():
        with gr.Column(scale=1):
            chat_context = gr.Radio(
                choices=["Captions", "Comments"],
                label="Choose a context",
                value="Captions"
            )
        with gr.Column(scale=9):
            chatbot = gr.Chatbot(height=300)
            msg = gr.Textbox(label="Ask a question")
            send_btn = gr.Button("Send")

    def chat(message, history, context_type, caption_summary, comment_summary):
        if context_type == "Captions":
            context = caption_summary
        else:
            context = comment_summary

        if not context:
            return [("Sorry, you need to summarize the content first.", "")]

        prompt = f"""Human:
        You are a helpful assistant. Please answer the following question based on the provided context.
        Context:
        {context}

        Question: {message}

        Assistant:
        """
        response = util.invoke_model(CLIENT, modelARN_DEEPSEEK_R1_V1, prompt, max_tokens=2000, attachment=None, model_type='deepseek', temperature=0.1)
        history.append((message, response))
        return history

    send_btn.click(chat, inputs=[msg, chatbot, chat_context, caption_sum, comment_sum], outputs=chatbot)
    msg.submit(chat, inputs=[msg, chatbot, chat_context, caption_sum, comment_sum], outputs=chatbot)


demo.launch()
