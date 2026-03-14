from PIL import Image, ImageDraw, ImageFont
import os

# ===============================
# GLOBAL CONFIG
# ===============================
FONT_PATH = "assets/PlayfairDisplay-Bold.ttf"
OUTPUT_DIR = "output/images"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===============================
# TEMPLATE CONFIGURATION
# ===============================
TEMPLATES = {
    "template_1": {
        "image": "assets/sampleTemplate.jpeg",
        "heading_box": {"x": 650, "y": 40, "width": 550, "height": 200},
        "subtext_box": {"x": 650, "y": 200, "width": 550, "height": 140},
        "cta_box": {"x": 650, "y": 380, "width": 550, "height": 100},
        "heading_max_font": 80,
        "subtext_max_font": 34,
        "cta_max_font": 30
    },

    # ADD MORE TEMPLATES LIKE THIS
    "template_2": {
        "image": "assets/sampleTemplate_2.jpeg",
        "heading_box": {"x": 580, "y": 150, "width": 520, "height": 180},
        "subtext_box": {"x": 580, "y": 350, "width": 520, "height": 120},
        "cta_box": {"x": 580, "y": 490, "width": 520, "height": 80},
        "heading_max_font": 72,
        "subtext_max_font": 32,
        "cta_max_font": 28
    }
}

# ===============================
# SAMPLE DEV DATA (TEMP)
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
# AUTO-FIT TEXT IN BOX
# ===============================
def draw_text_in_box(draw, text, box, max_font_size, fill):
    font_size = max_font_size

    while font_size > 10:
        font = load_font(font_size)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=6)

        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        if text_width <= box["width"] and text_height <= box["height"]:
            draw.multiline_text(
                (box["x"], box["y"]),
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
def generate_birthday_image(person, template_name):
    template = TEMPLATES[template_name]
    img = Image.open(template["image"]).convert("RGBA")
    draw = ImageDraw.Draw(img)

    white = (255, 255, 255)

    heading_text = f"HAPPY BIRTHDAY,\n{person['first_name'].upper()}."
    sub_text = person["offer_text"]
    cta_text = person["cta_text"]

    draw_text_in_box(
        draw,
        heading_text,
        template["heading_box"],
        template["heading_max_font"],
        white
    )

    draw_text_in_box(
        draw,
        sub_text,
        template["subtext_box"],
        template["subtext_max_font"],
        white
    )

    draw_text_in_box(
        draw,
        cta_text,
        template["cta_box"],
        template["cta_max_font"],
        white
    )

    output_path = os.path.join(
        OUTPUT_DIR,
        f"{person['first_name']}_{template_name}.png"
    )

    img.save(output_path)
    print(f"Generated: {output_path}")

# ===============================
# RUN DEV TEST
# ===============================
for template_name in TEMPLATES:
    for person in people:
        generate_birthday_image(person, template_name)
