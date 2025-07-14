import yt_dlp

url = "https://www.youtube.com/watch?v=96spRGttVyA"

ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloaded_audio.%(ext)s',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])

print("✅ 音频已下载并转换为 MP3：downloaded_audio.mp3")

