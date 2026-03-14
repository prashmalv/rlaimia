from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.VideoClip import TextClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
import os

# ===============================
# CONFIG
# ===============================
VIDEO_TEMPLATE = "assets/video_template.mp4"
FONT_PATH = "assets/PlayfairDisplay-Bold.ttf"
OUTPUT_DIR = "output/videos"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# SAFE TEXT AREA
TEXT_AREA = {
    "x": 700,
    "y": 220,
    "width": 700,
    "line_height": 110
}

# ===============================
# SAMPLE DEV DATA
# ===============================
people = [
    {
        "first_name": "Lakshmi",
        "messages": [
            "HAPPY BIRTHDAY",
            "LAKSHMI",
            "Enjoy a special birthday surprise",
            "20% OFF specially for you",
            "Valid upto 10 days from your birthday"
        ]
    }
]

# ===============================
# TEXT CLIP FACTORY
# ===============================
def create_text_clip(text, start, duration, x, y, width, font_size):
    return (
        TextClip(
            text=text,
            font=FONT_PATH,
            font_size=font_size,
            color="white",
            size=(width, 120)
        )
        .with_position((x, y))
        .with_start(start)
        .with_duration(duration)
    )

# ===============================
# VIDEO GENERATOR
# ===============================
def generate_birthday_video(person):
    base = VideoFileClip(VIDEO_TEMPLATE)

    x = TEXT_AREA["x"]
    y = TEXT_AREA["y"]
    width = TEXT_AREA["width"]
    lh = TEXT_AREA["line_height"]

    clips = [base]

    start_time = 0.6

    for idx, msg in enumerate(person["messages"]):
        font_size = 80 if idx <= 1 else 36
        clip = create_text_clip(
            msg,
            start=start_time,
            duration=1.8,
            x=x,
            y=y + (idx * lh),
            width=width,
            font_size=font_size
        )
        clips.append(clip)
        start_time += 1.6  # stagger timing

    final = CompositeVideoClip(clips)

    output_path = os.path.join(
        OUTPUT_DIR,
        f"{person['first_name']}_birthday_multi.mp4"
    )

    final.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac"
    )

    print(f"Generated video: {output_path}")

# ===============================
# RUN DEV TEST
# ===============================
for person in people:
    generate_birthday_video(person)
