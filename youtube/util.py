import os
import re
import copy
import json
#import fitz
import time
import base64
import logging
#import pymupdf
#from prompt import REQ_ANALYZE, MD_EXTRACT, META_INFO_EXTRACT, PROOFREADING_PROMPT, DOUBLE_CHECK_PROMPT
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def split_pdf_by_pages(pdf_path, output_dir):
    pdf_name = os.path.basename(pdf_path).rsplit('.', 1)[0]
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    output_files = []

    for page_number in range(len(doc)):
        single_page = fitz.open()
        single_page.insert_pdf(doc, from_page=page_number, to_page=page_number)
        output_path = os.path.join(output_dir, f"{pdf_name}-page-{page_number + 1}.pdf")
        single_page.save(output_path)
        single_page.close()
        output_files.append(output_path)

    doc.close()
    return output_files


def upload_directory_to_s3(local_dir, bucket_name, s3_prefix, s3_client):
    pages = []
    for root, _, files in os.walk(local_dir):
        for file in files:
            local_path = os.path.join(root, file)
            s3_key = os.path.join(s3_prefix, file)
            s3_client.upload_file(local_path, bucket_name, s3_key)
            pages.append(s3_key)
    return pages


def pdf_to_image(pdf_path, output_dir, dpi=700):
    pdf_name = os.path.basename(pdf_path).rsplit('.', 1)[0]
    save_name = f'{output_dir}/{pdf_name}.png'
    doc = pymupdf.open(pdf_path)
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)
    pix.save(save_name)
    return save_name


def image_to_md(image_path, client, model_id, model_type):
    retry_cnt = 3
    md_content = ''
    while retry_cnt > 0:
        try:
            md_content = image_to_md_chat(image_path, client, model_id, model_type)
            if not md_content:
                # for unittest only
                return ''
        except Exception as e:
            if 'ThrottlingException' in str(e):
                time.sleep(10)
                LOGGER.error(e)
                continue
            LOGGER.error(e)
            time.sleep(10)
            retry_cnt -= 1
            continue
        break
    if not md_content:
        raise Exception("image to md exception!")
    md_name = os.path.basename(image_path).rsplit('.', 1)[0] + '.md'
    md_path = os.path.join(os.path.dirname(image_path), md_name)
    with open(md_path, 'w') as fp:
        fp.write(md_content)
    return md_path


def format_result(content, type='json'):
    if type == 'json':
        pattern = r'(?P<quote>["\'`]{3})json\s*(?P<json>(\{.*?\}|\[.*?\]))\s*(?P=quote)'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            json_str = match.group("json")
            return json.loads(json_str)
        else:
            return json.loads(content)
    elif type == 'markdown':
        pattern = r'(?P<quote>["\'`]{3})markdown\s+(.*?)(?P=quote)'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(2)
        else:
            return content


def image_to_md_chat(image_path, client, model_id, model_type, max_tokens=20000):
    with open(image_path, 'rb') as image_file:
        image_bytes = image_file.read()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')

    md_content = invoke_model(client, model_id, MD_EXTRACT, attachment=image_base64, model_type=model_type)
    return format_result(md_content, type='markdown')


def invoke_model(client, model_id, prompt, max_tokens=20000, attachment=None, model_type='mistral', temperature=0.9):
    if model_type == 'mistral':
        payload = {
            "messages" : [
                {
                    "role" : "user",
                    "content" : [
                        {
                            "text": prompt,
                            "type": "text"
                        }
                    ]
                }
            ],
            "max_tokens" : max_tokens,
            "temperature": temperature
        }
        if attachment:
            payload['messages'][0]['content'].append({
                "type" : "image_url",
                "image_url" : {
                    "url" : f"data:image/png;base64,{attachment}"
                }
            })
        body = json.dumps(payload)
        response = client.invoke_model(
            modelId=model_id,
            body=body
        )
        response_body = json.loads(response['body'].read())
        return response_body['choices'][0]['message']['content']
    elif model_type == 'claude':
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        }
        if attachment:
            payload['messages'][0]['content'].insert(0, {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": attachment
                }
            })
        # LOGGER.info('input payload:%s', json.dumps(payload))
        response = client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(payload)
        )

        response_body = json.loads(response['body'].read())
        response_content = response_body.pop('content')
        print(response_body)
        return response_content[0]['text']
    elif model_type == 'deepseek':
        # DEEPSEEK invoke_model does not return the text response and reasoning process in one block text!!!
        # # Embed the prompt in DeepSeek-R1's instruction format.
        # formatted_prompt = f"""
        # <｜begin▁of▁sentence｜><｜User｜>{prompt}<｜Assistant｜><think>\n
        # """
        
        # if attachment:
        #     raise Exception('deepseek R1 is none multi-model, could not input attachment.')

        # body = json.dumps({
        #     "prompt": formatted_prompt,
        #     "max_tokens": max_tokens,
        #     "temperature": 0.5,
        #     "top_p": 0.9,
        # })
        # response = client.invoke_model(modelId=model_id, body=body)
        # model_response = json.loads(response["body"].read())
        # choices = model_response["choices"]
        # return choices[0]['text']
        
        response = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": 'user',
                    "content": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            inferenceConfig={
                'maxTokens': max_tokens,
                'temperature': temperature
            }
        )
        response_content = response['output']['message'].pop('content')
        print(response)
        return response_content[0]['text']


def requirement_analyze(req, md_path, client, model_id, model_type) -> json:
    with open(md_path, 'r') as fp:
        content = fp.read()
    prompt = REQ_ANALYZE.replace("{req}", req).replace("{content}", content)
    retry_cnt = 3
    while retry_cnt > 0:
        try:
            content = invoke_model(client, model_id, prompt, model_type=model_type)
            LOGGER.info('output:%s', content)
            return format_result(content)
        except Exception as e:
            if 'ThrottlingException' in str(e):
                time.sleep(10)
                LOGGER.error(e)
                continue
            LOGGER.error(e)
            time.sleep(10)
            retry_cnt -= 1
            continue
    return {
        'result': '',
        'rationale': ''
    }


def meta_info_extract(md_path, client, model_id):
    with open(md_path, 'r') as fp:
        content = fp.read()
    prompt = META_INFO_EXTRACT.replace("{content}", content)
    retry_cnt = 3
    while retry_cnt > 0:
        try:
            content = invoke_model(client, model_id, prompt, model_type='deepseek')
            return format_result(content)
        except Exception as e:
            if 'ThrottlingException' in str(e):
                time.sleep(10)
                LOGGER.error(e)
                continue
            LOGGER.error(e)
            time.sleep(10)
            retry_cnt -= 1
            continue
    raise Exception("get meta info failed!")


def proofreading_analyze(req_desc, random_hash, para_path, client, model_id, model_type='deepseek'):
    """return:
    [
        {
            "randomHash": "xxx",
            "desc": "the description of the requirement",
            "result": [
                {
                    "finding": "the sentence that violate the requirement",
                    "correction": "the correct text",
                    "rationale": "the reason regarding this correction"
                }
            ]
        }
    ]
    """
    with open(para_path) as fp:
        content = fp.read()
    
    prompt = PROOFREADING_PROMPT.replace("{req}", req_desc).replace("{content}", content)
    retry_cnt = 3
    model_result = []
    while retry_cnt > 0:
        try:
            model_result = invoke_model(client, model_id, prompt, model_type=model_type, temperature=0.1)
            model_result = format_result(model_result)
            break
        except Exception as e:
            if 'ThrottlingException' in str(e):
                time.sleep(10)
                LOGGER.error(e)
                continue
            LOGGER.error(e)
            time.sleep(10)
            retry_cnt -= 1
            continue
    
    model_result2 = copy.deepcopy(model_result)
    if model_result:
        model_result2 = filter_out_result(prompt, model_result, keys=['finding', 'correction', 'rationale'], key_pair=('finding', 'correction'), org_key='finding')
        if model_result2:
            model_result2 = double_check_result(model_result2, req_desc, client, model_id, model_type)
            if model_result2:
                model_result2 = filter_out_result(prompt, model_result2, keys=['finding', 'correction', 'rationale'], key_pair=('finding', 'correction'), org_key='finding')
    if len(model_result) != len(model_result2):
        LOGGER.info("Get a catch!! %s, \n %s", json.dumps(model_result), json.dumps(model_result2))

    result = [
        {
            "randomHash": random_hash,
            "desc": req_desc,
            "result": model_result2
        }
    ]
    return result


def filter_out_result(prompt, result, keys=[], key_pair=None, org_key=''):
    result2 = []
    for item in result:
        bad_case = False
        for k in keys:
            if k not in item:
                bad_case = True
                break
        if not bad_case and key_pair and item[key_pair[0]] == item[key_pair[1]]:
            bad_case = True
        if not bad_case and org_key and prompt.find(item[org_key]) == -1:
            bad_case = True 
        if not bad_case:
            result2.append(item)
    return result2


def double_check_result(model_result, req_desc, client, model_id, model_type='deepseek'):
    prompt = DOUBLE_CHECK_PROMPT.replace("{req}", req_desc).replace("{content}", json.dumps(model_result))
    retry_cnt = 3
    model_result2 = []
    while retry_cnt > 0:
        try:
            model_result2 = invoke_model(client, model_id, prompt, model_type=model_type, temperature=0.1)
            return format_result(model_result2)
        except Exception as e:
            if 'ThrottlingException' in str(e):
                time.sleep(10)
                LOGGER.error(e)
                continue
            LOGGER.info('model_result2:%s', json.dumps(model_result2))
            LOGGER.error(e)
            time.sleep(10)
            retry_cnt -= 1
            continue
    LOGGER.error("double_check_result retry failed!")
    return model_result
    
def extract_sections_by_first_heading(md_text):
    lines = md_text.splitlines()
    sections = []
    current_section = None

    for line in lines:
        heading_match = re.match(r'^# (.+)', line)
        if heading_match:
            if current_section:
                sections.append(current_section)
            current_section = {
                "first_heading": heading_match.group(1).strip(),
                "body": ""
            }
        elif current_section:
            current_section["body"] += line + "\n"

    if current_section:
        current_section["body"] = current_section["body"].strip()
        sections.append(current_section)

    return sections


if __name__ == '__main__':
    import boto3
    file = 'workspace-files-21537-7546_AER Report_1741114794 (2)-page-1.pdf'
    modelARN_Claude37_v1 = 'arn:aws:bedrock:us-east-1:471112955155:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0'
    CLIENT = boto3.Session().client('bedrock-runtime', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    print(image_to_md_chat(file, CLIENT, modelARN_Claude37_v1, 'claude'))