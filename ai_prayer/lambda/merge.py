from pydub import AudioSegment

# 加载两个 MP3 文件
prayer = AudioSegment.from_file("prayer-2025-07-14T16-31-17.975126.mp3")
background = AudioSegment.from_file("bg.mp3")

# 设置背景音乐音量（可调节，比如 -10 分贝）
# background = background - 10

# 确保背景音乐长度 ≥ 祷告音频
if len(background) < len(prayer):
    background = background * (len(prayer) // len(background) + 1)

# 截取背景音乐为与祷告音频相同长度
background = background[:len(prayer)]

# 合并两个音轨
combined = prayer.overlay(background)

# 导出合成后的音频
combined.export("combined_prayer.mp3", format="mp3")

print("✅ 合成完成，保存为 combined_prayer.mp3")