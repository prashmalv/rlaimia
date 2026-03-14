"""
Image Worker — Optimised bulk image generation for Mia Campaign Engine.

Performance design for 5-lakh scale:
  • Templates loaded once per process and cached in memory (dict)
  • Fonts cached by (path, size) key — no repeated disk reads
  • ProcessPoolExecutor for CPU-bound Pillow operations
  • Azure Blob upload in-memory (no temp files)
  • Processes ~500 images/min per core on a modern CPU

Image Fonts (open-source, downloaded clean in Docker):
  • Heading / Sub-heading : Playfair Display Bold / Regular  (elegant serif)
  • Body text             : Lato Regular                     (clean sans-serif)
  • CTA                   : Lato Italic                      (via _get_font fallback)
  Note: Gotham TTFs are excluded from Docker build (macOS corruption risk)
"""

import io
import os
import sys
import logging
import textwrap
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config
from backend.app import azure_storage

logger = logging.getLogger(__name__)

# ─── Template Registry ───────────────────────────────────────────────────────
# Each template defines pixel boxes where text is drawn.
# Values are calibrated for a 1200×628 (landscape) template image.
# Override per-template in TEMPLATE_CONFIGS below.

TEMPLATE_CONFIGS: dict[str, dict] = {
    "template_1": {
        "image_path": str(config.IMAGE_TEMPLATES_DIR / "sampleTemplate.jpeg"),
        "heading_box":    {"x": 650, "y":  40, "w": 550, "h": 160},
        "subheading_box": {"x": 650, "y": 210, "w": 550, "h": 100},
        "body_box":       {"x": 650, "y": 325, "w": 550, "h": 130},
        "cta_box":        {"x": 650, "y": 470, "w": 550, "h":  80},
        "heading_max_pt": 72,
        "subheading_max_pt": 36,
        "body_max_pt":    28,
        "cta_max_pt":     24,
        "heading_color":  (255, 255, 255),
        "body_color":     (255, 255, 255),
        "cta_color":      (255, 230, 150),
    },
    "template_2": {
        "image_path": str(config.IMAGE_TEMPLATES_DIR / "sampleTemplate_2.jpeg"),
        "heading_box":    {"x": 580, "y": 150, "w": 520, "h": 180},
        "subheading_box": {"x": 580, "y": 345, "w": 520, "h":  80},
        "body_box":       {"x": 580, "y": 440, "w": 520, "h": 120},
        "cta_box":        {"x": 580, "y": 575, "w": 520, "h":  60},
        "heading_max_pt": 64,
        "subheading_max_pt": 32,
        "body_max_pt":    26,
        "cta_max_pt":     22,
        "heading_color":  (255, 255, 255),
        "body_color":     (240, 240, 240),
        "cta_color":      (255, 220, 120),
    },
}

# ─── In-process caches ───────────────────────────────────────────────────────
_template_cache: dict[str, Image.Image] = {}   # template_id → PIL Image (RGB)


def _get_template_image(template_id: str) -> Image.Image:
    """Load and cache a template image (once per process)."""
    if template_id not in _template_cache:
        cfg = TEMPLATE_CONFIGS.get(template_id)
        if not cfg:
            raise ValueError(f"Unknown template_id: {template_id}")
        path = cfg["image_path"]

        img = None
        # Try primary path
        if os.path.exists(path):
            try:
                img = Image.open(path).convert("RGBA")
            except Exception as e:
                logger.warning(f"Failed to open template '{path}': {e} — trying fallbacks")

        # Try birthday_campaign assets as secondary fallback
        if img is None:
            alt = str(Path(__file__).parent.parent.parent.parent /
                      "birthday_campaign" / "assets" /
                      Path(path).name)
            if os.path.exists(alt):
                try:
                    img = Image.open(alt).convert("RGBA")
                except Exception as e:
                    logger.warning(f"Failed to open fallback template '{alt}': {e}")

        # Last resort: generate a solid-colour placeholder in-memory
        if img is None:
            w = cfg.get("heading_box", {}).get("x", 650) * 2
            h = 700
            logger.warning(f"Using generated solid-colour placeholder for template '{template_id}'")
            import numpy as np
            left  = np.array([40, 32, 30], dtype=np.float64)
            right = np.array([25, 20, 18], dtype=np.float64)
            t     = np.linspace(0, 1, w)
            row   = (left * (1 - t[:, None]) + right * t[:, None]).astype(np.uint8)
            arr   = np.broadcast_to(row[None, :, :], (h, w, 3)).copy()
            img   = Image.fromarray(arr, "RGB").convert("RGBA")

        _template_cache[template_id] = img
        logger.debug(f"Loaded template image: {template_id}")
    return _template_cache[template_id].copy()   # always return a fresh copy


_custom_template_cache: dict[str, Image.Image] = {}  # local_path → PIL Image


def _get_custom_template_image(local_path: str, blob_key: Optional[str] = None) -> Image.Image:
    """
    Load a user-uploaded template image.
    Tries local path first, then Azure Blob (for Celery workers on separate containers).
    Cached by local_path key.
    """
    cache_key = local_path or blob_key or "unknown"
    if cache_key not in _custom_template_cache:
        img = None

        if local_path and os.path.exists(local_path):
            try:
                img = Image.open(local_path).convert("RGBA")
            except Exception as e:
                logger.warning(f"Failed to open custom template '{local_path}': {e}")

        if img is None and blob_key:
            try:
                import io as _io
                from backend.app import azure_storage
                data = azure_storage.read_blob_bytes(blob_key, container=config.AZURE_BLOB_CONTAINER_IMG)
                img = Image.open(_io.BytesIO(data)).convert("RGBA")
                logger.info(f"Loaded custom template from Azure Blob: {blob_key}")
            except Exception as e:
                logger.warning(f"Failed to load custom template from blob '{blob_key}': {e}")

        if img is None:
            logger.warning(f"Custom template not found — using 1200×628 placeholder")
            img = Image.new("RGBA", (1200, 628), (40, 32, 30, 255))

        _custom_template_cache[cache_key] = img

    return _custom_template_cache[cache_key].copy()


@lru_cache(maxsize=512)
def _get_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    """
    Load and cache a font (LRU, max 512 entries).
    Catches OSError on each candidate so corrupt files (az acr build tar corruption)
    don't block the fallback chain.
    """
    for candidate in [font_path, config.FONT_FALLBACK, config.FONT_GARAMOND_REGULAR]:
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except Exception as e:
            logger.warning(
                f"Font '{candidate}' unreadable ({type(e).__name__}: {e}) — trying next fallback"
            )
    logger.warning("All font candidates failed — using PIL built-in default font")
    return ImageFont.load_default()


def _best_font_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    box_w: int,
    box_h: int,
    max_size: int,
    min_size: int = 10,
    spacing: int = 6,
) -> tuple[ImageFont.FreeTypeFont, str]:
    """
    Binary-search the largest font size where `text` fits in (box_w × box_h).
    Returns (font, wrapped_text).
    """
    lo, hi = min_size, max_size
    best_font  = _get_font(font_path, min_size)
    best_text  = text

    while lo <= hi:
        mid  = (lo + hi) // 2
        font = _get_font(font_path, mid)

        # Wrap text to fit width
        avg_char_w = max(1, font.getlength("M"))
        chars_per_line = max(1, int(box_w / avg_char_w))
        wrapped = "\n".join(textwrap.wrap(text, width=chars_per_line))

        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=spacing)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        if tw <= box_w and th <= box_h:
            best_font = font
            best_text = wrapped
            lo = mid + 1
        else:
            hi = mid - 1

    return best_font, best_text


def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: dict,
    font_path: str,
    max_size: int,
    fill: tuple,
    spacing: int = 6,
    align: str = "left",
):
    """Draw text inside a bounding box, auto-sizing font to fit."""
    if not text:
        return
    font, wrapped = _best_font_size(
        draw, text, font_path,
        box["w"], box["h"], max_size, spacing=spacing
    )
    draw.multiline_text(
        (box["x"], box["y"]),
        wrapped,
        font=font,
        fill=fill,
        spacing=spacing,
        align=align,
    )


# ─── Core Generator ──────────────────────────────────────────────────────────

def generate_image(job: dict, template_id: str = "template_1", custom_config: Optional[dict] = None) -> bytes:
    """
    Generate a personalised greeting image for one person.

    Args:
        job: dict with keys: first_name, lines (heading/subheading/body/cta)
        template_id: which built-in template to use (ignored if custom_config is set)
        custom_config: optional dict with keys:
            image_path, blob_key (optional),
            heading_box/subheading_box/body_box/cta_box (each {x,y,w,h}),
            heading_max_pt, subheading_max_pt, body_max_pt, cta_max_pt,
            heading_color, body_color, cta_color (each RGB tuple or "#rrggbb")

    Returns:
        PNG image as bytes (ready for Azure Blob upload)
    """
    if custom_config:
        cfg = custom_config
        img = _get_custom_template_image(
            cfg.get("image_path", ""),
            blob_key=cfg.get("blob_key"),
        )
        # Normalise hex colors to RGB tuples if needed
        for key in ("heading_color", "body_color", "cta_color"):
            val = cfg.get(key, (255, 255, 255))
            if isinstance(val, str):
                cfg[key] = _hex_to_rgb_tuple(val)
    else:
        cfg  = TEMPLATE_CONFIGS[template_id]
        img  = _get_template_image(template_id)
    draw = ImageDraw.Draw(img)

    lines = job.get("lines", {})
    fn    = job.get("first_name", "Friend").upper()

    heading_text    = lines.get("heading", f"HAPPY BIRTHDAY, {fn}.")
    subheading_text = lines.get("subheading", "")
    body_text       = lines.get("body", "")
    cta_text        = lines.get("cta", "")

    # Heading — Playfair Display Bold (elegant serif; Gotham if available)
    _draw_text_in_box(
        draw, heading_text,
        cfg["heading_box"],
        font_path=config.FONT_PLAYFAIR_BOLD,
        max_size=cfg["heading_max_pt"],
        fill=cfg["heading_color"],
    )

    # Sub-heading — Playfair Display Regular
    _draw_text_in_box(
        draw, subheading_text,
        cfg["subheading_box"],
        font_path=config.FONT_PLAYFAIR_REGULAR,
        max_size=cfg["subheading_max_pt"],
        fill=cfg["heading_color"],
    )

    # Body — Lato Regular (clean, readable sans-serif)
    _draw_text_in_box(
        draw, body_text,
        cfg["body_box"],
        font_path=config.FONT_LATO_REGULAR,
        max_size=cfg["body_max_pt"],
        fill=cfg["body_color"],
    )

    # CTA — Lato Italic (or EB Garamond Italic via fallback)
    _draw_text_in_box(
        draw, cta_text,
        cfg["cta_box"],
        font_path=config.FONT_LATO_ITALIC,
        max_size=cfg["cta_max_pt"],
        fill=cfg["cta_color"],
    )

    # Flatten RGBA → RGB for JPEG or keep as PNG
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()


# ─── Blob key helpers ────────────────────────────────────────────────────────

def image_blob_key(campaign_id: str, job_id: str, template_id: str) -> str:
    return f"{campaign_id}/{template_id}/{job_id}.png"


def _hex_to_rgb_tuple(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


# ─── Single-job entry point (called by Celery task) ──────────────────────────

def process_image_job(
    job: dict,
    campaign_id: str,
    template_ids: list[str] = None,
    custom_template_config: Optional[dict] = None,
) -> dict:
    """
    Generate image(s) for one person and upload to Azure Blob.
    If custom_template_config is provided, uses that single custom template.
    Otherwise iterates over template_ids (defaults to all built-in TEMPLATE_CONFIGS).
    Returns dict with image_blob_key and image_url per template.
    """
    results = {}

    if custom_template_config:
        # Single custom template
        tmpl_id = "custom"
        try:
            png_bytes = generate_image(job, custom_config=custom_template_config)
            bkey      = image_blob_key(campaign_id, job["job_id"], tmpl_id)
            azure_storage.upload_bytes(
                png_bytes, bkey,
                container=config.AZURE_BLOB_CONTAINER_IMG,
                content_type="image/png",
            )
            url = azure_storage.get_sas_url(bkey, config.AZURE_BLOB_CONTAINER_IMG)
            results[tmpl_id] = {"blob_key": bkey, "url": url, "status": "done"}
        except Exception as e:
            logger.error(f"Custom image generation failed for job {job.get('job_id')}: {e}", exc_info=True)
            results[tmpl_id] = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    else:
        template_ids = template_ids or list(TEMPLATE_CONFIGS.keys())
        for tmpl_id in template_ids:
            try:
                png_bytes = generate_image(job, template_id=tmpl_id)
                bkey      = image_blob_key(campaign_id, job["job_id"], tmpl_id)
                azure_storage.upload_bytes(
                    png_bytes, bkey,
                    container=config.AZURE_BLOB_CONTAINER_IMG,
                    content_type="image/png",
                )
                url = azure_storage.get_sas_url(bkey, config.AZURE_BLOB_CONTAINER_IMG)
                results[tmpl_id] = {"blob_key": bkey, "url": url, "status": "done"}
            except Exception as e:
                logger.error(f"Image generation failed for job {job.get('job_id')} tmpl {tmpl_id}: {e}", exc_info=True)
                results[tmpl_id] = {"status": "failed", "error": f"{type(e).__name__}: {e}"}

    return results


# ─── Bulk batch generator (for local direct run, no Celery) ──────────────────

def run_batch_local(jobs: list[dict], campaign_id: str, max_workers: int = None) -> list[dict]:
    """
    Process a batch of jobs in parallel using ProcessPoolExecutor.
    Suitable for running locally or on a single VM.
    Returns list of result dicts.
    """
    max_workers = max_workers or config.IMAGE_WORKER_CONCURRENCY
    results = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_image_job, job, campaign_id): job
            for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                results.append({"job_id": job["job_id"], "results": result})
            except Exception as e:
                results.append({"job_id": job.get("job_id"), "error": str(e)})

    return results
