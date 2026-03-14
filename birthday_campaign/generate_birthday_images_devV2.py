from PIL import Image, ImageDraw, ImageFont
import os

# ===============================
# CONFIG (ONLY TUNE THESE)
# ===============================
TEMPLATE_IMAGE = "assets/sampleTemplate.jpeg"
OUTPUT_DIR = "output/images"
FONT_PATH = "assets/PlayfairDisplay-Bold.ttf"

# 🔥 TEXT SAFE ZONES (CHANGE THESE)
HEADING_BOX = {
    "x": 650,
    "y": 40,
    "width": 550,
    "height": 200
}

SUBTEXT_BOX = {
    "x": 650,
    "y": 200,
    "width": 550,
    "height": 140
}

CTA_BOX = {
    "x": 650,
    "y": 380,
    "width": 550,
    "height": 100
}

# FONT SIZE LIMITS
HEADING_MAX_FONT = 80
SUBTEXT_MAX_FONT = 34
CTA_MAX_FONT = 30

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===============================
# SAMPLE DEV DATA
# ===============================
people = [
    {
        "first_name": "Lakshmi",
        "offer_text": "Enjoy a special birthday surprise",
        "cta_text": "20% OFF Specially for you upto 10 days of your birthday."
    }
]

# ===============================
# FONT LOADER
# ===============================
def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except:
        return ImageFont.load_default()

# ===============================
# AUTO-FIT TEXT INSIDE BOX
# ===============================
def draw_text_in_box(draw, text, box, max_font_size, fill):
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    font_size = max_font_size

    while font_size > 10:
        font = load_font(font_size)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=6)

        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        if text_width <= w and text_height <= h:
            draw.multiline_text(
                (x, y),
                text,
                font=font,
                fill=fill,
                spacing=6,
                align="left"
            )
            return

        font_size -= 2

# ===============================
# IMAGE GENERATOR
# ===============================
def generate_birthday_image(person):
    img = Image.open(TEMPLATE_IMAGE).convert("RGBA")
    draw = ImageDraw.Draw(img)

    white = (255, 255, 255)

    heading_text = f"HAPPY BIRTHDAY,\n{person['first_name'].upper()}."
    offer_text = person["offer_text"]
    cta_text = person["cta_text"]

    draw_text_in_box(
        draw,
        heading_text,
        HEADING_BOX,
        HEADING_MAX_FONT,
        white
    )

    draw_text_in_box(
        draw,
        offer_text,
        SUBTEXT_BOX,
        SUBTEXT_MAX_FONT,
        white
    )

    draw_text_in_box(
        draw,
        cta_text,
        CTA_BOX,
        CTA_MAX_FONT,
        white
    )

    output_path = os.path.join(
        OUTPUT_DIR,
        f"{person['first_name']}_birthday.png"
    )
    img.save(output_path)

    print(f"Generated image: {output_path}")

# ===============================
# RUN DEV TEST
# ===============================
for person in people:
    generate_birthday_image(person)
