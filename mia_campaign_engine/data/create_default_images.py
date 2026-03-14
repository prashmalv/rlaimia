"""
Generates default JPEG template images programmatically using PIL + numpy.
Run during Docker build to avoid binary file corruption from macOS
extended attributes during 'az acr build' tar upload.

Uses numpy for fast gradient creation — avoids per-pixel putpixel loops
which would take minutes and risk Docker build timeout.
"""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).parent.parent / "assets" / "image_templates"


def _gradient(width: int, height: int, left_color, right_color) -> Image.Image:
    """Left-to-right gradient using numpy (fast — no per-pixel loops)."""
    left  = np.array(left_color,  dtype=np.float64)
    right = np.array(right_color, dtype=np.float64)
    t     = np.linspace(0, 1, width)                          # shape (W,)
    row   = (left * (1 - t[:, None]) + right * t[:, None]).astype(np.uint8)  # (W, 3)
    arr   = np.broadcast_to(row[None, :, :], (height, width, 3)).copy()      # (H, W, 3)
    return Image.fromarray(arr, "RGB")


def create_template_1():
    """
    1368×684 — warm rose-gold → deep charcoal.
    Text panel is right half (x≥650) per TEMPLATE_CONFIGS["template_1"].
    """
    W, H = 1368, 684
    img  = _gradient(W, H, (175, 115, 95), (38, 30, 28))
    draw = ImageDraw.Draw(img)
    draw.rectangle([637, 0, 643, H], fill=(220, 185, 145))   # subtle divider
    draw.rectangle([0,   H - 5, W, H], fill=(210, 170, 125)) # brand strip

    path = OUT_DIR / "sampleTemplate.jpeg"
    img.save(str(path), format="JPEG", quality=90)
    print(f"Created {path} ({path.stat().st_size:,} bytes)  {W}×{H}")


def create_template_2():
    """
    1600×795 — midnight-blue → dark teal.
    Text panel is right half (x≥580) per TEMPLATE_CONFIGS["template_2"].
    """
    W, H = 1600, 795
    img  = _gradient(W, H, (22, 32, 62), (18, 58, 68))
    draw = ImageDraw.Draw(img)
    draw.rectangle([573, 0, 579, H], fill=(80, 175, 200))    # subtle divider
    draw.rectangle([0,   H - 5, W, H], fill=(75, 170, 195)) # brand strip

    path = OUT_DIR / "sampleTemplate_2.jpeg"
    img.save(str(path), format="JPEG", quality=90)
    print(f"Created {path} ({path.stat().st_size:,} bytes)  {W}×{H}")


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    create_template_1()
    create_template_2()
    print("Done — both image templates created.")
