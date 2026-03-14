from PIL import Image, ImageDraw, ImageFont
import os

# ===============================
# CONFIG
# ===============================
TEMPLATE_IMAGE = "assets/sampleTemplate.jpeg"
OUTPUT_DIR = "output/images"
FONT_PATH = "assets/PlayfairDisplay-Bold.ttf"  # Similar to sample image
IMAGE_WIDTH = 1400  # Approx, not strict

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ===============================
# SAMPLE DEV DATA (TEMP)
# ===============================
# Later this will come from XLSX pipeline
people = [
    {
        "first_name": "Lakshmi",
        "offer_text": "Enjoy a special birthday surprise 🎁",
        "cta_text": "Shop Now"
    }
]

# ===============================
# LOAD FONT (SAFE FALLBACK)
# ===============================
def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except:
        return ImageFont.load_default()

# ===============================
# IMAGE GENERATOR
# ===============================
def generate_birthday_image(person):
    img = Image.open(TEMPLATE_IMAGE).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Fonts
    heading_font = load_font(92)
    sub_font = load_font(36)
    cta_font = load_font(32)

    # Text
    heading = f"HAPPY BIRTHDAY,\n{person['first_name'].upper()}."
    offer = person["offer_text"]
    cta = person["cta_text"]

    # Positions (tuned for your template)
    heading_x, heading_y = 720, 140
    offer_x, offer_y = 720, 360
    cta_x, cta_y = 720, 420

    # Colors (white like sample)
    white = (255, 255, 255)

    # Draw text
    draw.multiline_text(
        (heading_x, heading_y),
        heading,
        font=heading_font,
        fill=white,
        spacing=10,
        align="left"
    )

    draw.text(
        (offer_x, offer_y),
        offer,
        font=sub_font,
        fill=white
    )

    draw.text(
        (cta_x, cta_y),
        cta,
        font=cta_font,
        fill=white
    )

    # Save
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
