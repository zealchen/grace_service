import whisper
import gradio as gr
import os

# 加载 Whisper 模型（建议使用 small 或 base 提高速度）
model = whisper.load_model("base")  # 可改为 "small", "medium", "large"

def transcribe_m4a(audio_file):
    if audio_file is None:
        return "Please upload an audio file."
    
    # Whisper 支持大多数音频格式，包括 m4a
    result = model.transcribe(audio_file)
    return result["text"]

# Gradio 界面
app = gr.Interface(
    fn=transcribe_m4a,
    inputs=gr.Audio(label="Upload .m4a file", type="filepath"),
    outputs=gr.Textbox(label="Transcribed Text"),
    title="Whisper M4A Transcriber",
    description="Upload a .m4a file and get English transcription using OpenAI Whisper."
)

if __name__ == "__main__":
    app.launch()
