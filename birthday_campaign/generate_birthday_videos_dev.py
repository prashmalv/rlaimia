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

# SAFE TEXT AREA (absolute pixels, adjust freely)
TEXT_AREA = {
    "x": 700,
    "y": 220,
    "width": 700,
    "line_height": 120
}

# ===============================
# SAMPLE DEV DATA
# ===============================
people = [
    {
        "first_name": "Lakshmi",
        "offer_text": "A little birthday sparkle just for you."
    }
]

# ===============================
# VIDEO GENERATOR
# ===============================
def generate_birthday_video(person):
    base_clip = VideoFileClip(VIDEO_TEMPLATE)
    w, h = base_clip.size

    x = TEXT_AREA["x"]
    y = TEXT_AREA["y"]
    width = TEXT_AREA["width"]
    lh = TEXT_AREA["line_height"]

    # -------- HAPPY BIRTHDAY (slide in) --------
    birthday_clip = (
        TextClip(
            text="HAPPY BIRTHDAY",
            font=FONT_PATH,
            font_size=78,
            color="white",
            size=(width, lh)
        )
        .with_position(lambda t: (x - 300 + t * 150, y))
        .with_start(0.5)
        .with_duration(2)
    )

    # -------- NAME (fade in, same alignment) --------
    name_clip = (
        TextClip(
            text=person["first_name"].upper(),
            font=FONT_PATH,
            font_size=92,
            color="white",
            size=(width, lh)
        )
        .with_position((x, y + lh))
        .with_start(2.3)
        .with_duration(2.5)
    )

    # -------- OFFER TEXT (soft slide / fade) --------
    offer_clip = (
        TextClip(
            text=person["offer_text"],
            font=FONT_PATH,
            font_size=36,
            color="white",
            size=(width, lh)
        )
        .with_position(lambda t: (x + 40, y + lh * 2))
        .with_start(3.2)
        .with_duration(2.5)
    )

    final = CompositeVideoClip(
        [base_clip, birthday_clip, name_clip, offer_clip]
    )

    output_path = os.path.join(
        OUTPUT_DIR,
        f"{person['first_name']}_birthday.mp4"
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
