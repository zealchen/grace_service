import re
import json
import requests
import subprocess
import os
import re
from typing import List, Set
from urllib.parse import parse_qs, urlparse


def get_subtitles_with_ytdlp(id, language='en', output_dir='./'):
    """
    使用yt-dlp下载字幕 (最稳定的方案)
    需要先安装: pip install yt-dlp
    """
    try:
        # 构建yt-dlp命令
        cmd = [
            'yt-dlp',
            '--write-subs',           # 下载字幕
            '--write-auto-subs',      # 下载自动生成的字幕
            '--sub-langs', f'{language}',  # 字幕语言
            '--skip-download',        # 只下载字幕，不下载视频
            '--output', os.path.join(output_dir, 'tmp.sub'),
            id
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            with open(f'tmp.sub.{language}.vtt') as fp:
                content = fp.read()
            return extract_text_from_webvtt(content)
        else:
            print(f"下载失败: {result.stderr}")
            return ''
            
    except FileNotFoundError:
        print("错误: 未找到yt-dlp，请先安装: pip install yt-dlp")
        return ''
    except Exception as e:
        print(f"错误: {str(e)}")
        return ''
    
    
def extract_text_from_webvtt(webvtt_content: str, 
                             remove_duplicates: bool = True,
                             join_sentences: bool = True) -> str:
    """
    从WebVTT内容中提取纯文本
    
    Args:
        webvtt_content (str): WebVTT格式的字幕内容
        remove_duplicates (bool): 是否去除重复的文本行
        join_sentences (bool): 是否将连续的句子片段合并
    
    Returns:
        str: 提取的纯文本
    """
    lines = webvtt_content.strip().split('\n')
    text_lines = []
    seen_texts = set() if remove_duplicates else None
    
    for line in lines:
        line = line.strip()
        
        # 跳过空行
        if not line:
            continue
            
        # 跳过WebVTT头部信息
        if line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:'):
            continue
            
        # 跳过时间戳行
        if '-->' in line:
            continue
            
        # 跳过cue设置行 (包含align:, position:等)
        if re.match(r'^[\d\w\s]+align:', line) or 'position:' in line:
            continue
            
        # 移除内联时间戳标记 <00:00:00.000><c>...</c>
        cleaned_line = re.sub(r'<[\d:.]+><c>', '', line)
        cleaned_line = re.sub(r'</c>', '', cleaned_line)
        cleaned_line = re.sub(r'<[\d:.]+>', '', cleaned_line)
        
        # 移除其他HTML标签
        cleaned_line = re.sub(r'<[^>]+>', '', cleaned_line)
        
        # 清理空格
        cleaned_line = re.sub(r'\s+', ' ', cleaned_line).strip()
        
        # 如果处理后的行不为空且不重复，则添加
        if cleaned_line and (not remove_duplicates or cleaned_line not in seen_texts):
            text_lines.append(cleaned_line)
            if remove_duplicates:
                seen_texts.add(cleaned_line)
    
    if join_sentences:
        # 合并句子片段
        return join_sentence_fragments(text_lines)
    else:
        return '\n'.join(text_lines)
    

def join_sentence_fragments(text_lines: List[str]) -> str:
    """
    智能合并句子片段
    """
    if not text_lines:
        return ""
    
    result = []
    current_sentence = ""
    
    for line in text_lines:
        if not line:
            continue
            
        # 如果当前行以大写字母开头，且前一句以句号结尾，开始新句子
        if (current_sentence and 
            current_sentence.rstrip().endswith('.') and 
            line[0].isupper()):
            result.append(current_sentence.strip())
            current_sentence = line
        else:
            # 否则继续当前句子
            if current_sentence:
                current_sentence += " " + line
            else:
                current_sentence = line
    
    # 添加最后一句
    if current_sentence:
        result.append(current_sentence.strip())
    
    return '\n'.join(result)


if __name__ == '__main__':
    download_subtitles_with_ytdlp('RweoklWbLsw')
