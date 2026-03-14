"""
Templates Router — manage image templates (upload, list, configure text zones, preview).
"""

import io
import json
import shutil
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import config
from backend.app.database import get_db
from backend.app.models import ImageTemplate, TemplateOut

router = APIRouter(prefix="/api/templates", tags=["Templates"])
logger = logging.getLogger(__name__)

# ─── Default text-box config for freshly uploaded templates ──────────────────
_DEFAULT_BOXES = {
    "heading":    {"x": 50,  "y": 50,  "w": 500, "h": 150, "max_pt": 64, "color": "#ffffff"},
    "subheading": {"x": 50,  "y": 220, "w": 500, "h": 80,  "max_pt": 32, "color": "#ffffff"},
    "body":       {"x": 50,  "y": 320, "w": 500, "h": 120, "max_pt": 26, "color": "#ffffff"},
    "cta":        {"x": 50,  "y": 460, "w": 500, "h": 60,  "max_pt": 22, "color": "#ffe696"},
}

# ─── Seed built-in templates ─────────────────────────────────────────────────

async def seed_builtin_templates(db: AsyncSession):
    """Insert built-in templates into the DB on first run (idempotent)."""
    from backend.workers.image_worker import TEMPLATE_CONFIGS

    for tid, cfg in TEMPLATE_CONFIGS.items():
        existing = await db.execute(select(ImageTemplate).where(ImageTemplate.id == tid))
        if existing.scalar_one_or_none():
            continue

        # Convert TEMPLATE_CONFIGS format → our boxes JSON
        boxes = {
            "heading":    {**cfg["heading_box"],    "max_pt": cfg["heading_max_pt"],    "color": _rgb_to_hex(cfg["heading_color"])},
            "subheading": {**cfg["subheading_box"], "max_pt": cfg["subheading_max_pt"], "color": _rgb_to_hex(cfg["heading_color"])},
            "body":       {**cfg["body_box"],       "max_pt": cfg["body_max_pt"],       "color": _rgb_to_hex(cfg["body_color"])},
            "cta":        {**cfg["cta_box"],        "max_pt": cfg["cta_max_pt"],        "color": _rgb_to_hex(cfg["cta_color"])},
        }

        tmpl = ImageTemplate(
            id=tid,
            name=tid.replace("_", " ").title(),
            local_path=cfg["image_path"],
            blob_key=None,
            text_boxes=json.dumps(boxes),
            is_builtin=True,
        )
        db.add(tmpl)

    await db.commit()


def _rgb_to_hex(rgb: tuple) -> str:
    """Convert (R, G, B) tuple to hex color string."""
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


# ─── List templates ───────────────────────────────────────────────────────────

@router.get("", response_model=list[TemplateOut])
async def list_templates(db: AsyncSession = Depends(get_db)):
    await seed_builtin_templates(db)
    result = await db.execute(select(ImageTemplate).order_by(ImageTemplate.is_builtin.desc(), ImageTemplate.created_at))
    return [TemplateOut.model_validate(t) for t in result.scalars().all()]


# ─── Upload new template ──────────────────────────────────────────────────────

@router.post("", response_model=TemplateOut)
async def upload_template(
    name: str = Form(...),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a template image and register it in the DB with default text zones."""
    if not image.filename:
        raise HTTPException(400, "No image file provided")

    ext = Path(image.filename).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "Only JPG, PNG, or WEBP images are supported")

    # Save to uploads dir
    upload_dir = config.TEMPLATES_UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)

    import uuid
    tmpl_id = str(uuid.uuid4())
    filename = f"{tmpl_id}{ext}"
    local_path = upload_dir / filename

    with open(local_path, "wb") as f:
        shutil.copyfileobj(image.file, f)

    # Also upload to Azure Blob (non-fatal if not configured)
    blob_key: Optional[str] = None
    try:
        from backend.app.azure_storage import upload_bytes
        image_bytes = local_path.read_bytes()
        blob_key = f"__templates__/{filename}"
        upload_bytes(image_bytes, blob_key, container=config.AZURE_BLOB_CONTAINER_IMG, content_type=f"image/{ext.lstrip('.')}")
    except Exception as e:
        logger.warning(f"Template Azure upload skipped: {e}")

    # Default boxes (user will adjust via the visual editor)
    tmpl = ImageTemplate(
        id=tmpl_id,
        name=name,
        local_path=str(local_path),
        blob_key=blob_key,
        text_boxes=json.dumps(_DEFAULT_BOXES),
        is_builtin=False,
    )
    db.add(tmpl)
    await db.commit()
    await db.refresh(tmpl)
    logger.info(f"[templates] Uploaded '{name}' → id={tmpl_id}")
    return TemplateOut.model_validate(tmpl)


# ─── Serve template image ─────────────────────────────────────────────────────

@router.get("/{template_id}/image")
async def get_template_image(template_id: str, db: AsyncSession = Depends(get_db)):
    """Return the raw template image bytes (for displaying in the editor canvas)."""
    result = await db.execute(select(ImageTemplate).where(ImageTemplate.id == template_id))
    tmpl = result.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Template not found")

    img_bytes = _load_image_bytes(tmpl)
    if not img_bytes:
        raise HTTPException(404, "Template image file not found on server")

    ext = Path(tmpl.local_path or "x.jpg").suffix.lower()
    content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext.lstrip("."), "image/jpeg")
    return Response(content=img_bytes, media_type=content_type)


def _load_image_bytes(tmpl: ImageTemplate) -> Optional[bytes]:
    """Load template image bytes: local path first, then Azure Blob."""
    if tmpl.local_path and Path(tmpl.local_path).exists():
        return Path(tmpl.local_path).read_bytes()
    # Try birthday_campaign assets as fallback (dev convenience)
    if tmpl.local_path:
        alt = Path(__file__).parent.parent.parent.parent.parent / "birthday_campaign" / "assets" / Path(tmpl.local_path).name
        if alt.exists():
            return alt.read_bytes()
    # Try Azure Blob
    if tmpl.blob_key:
        try:
            from backend.app.azure_storage import read_blob_bytes
            return read_blob_bytes(tmpl.blob_key, container=config.AZURE_BLOB_CONTAINER_IMG)
        except Exception as e:
            logger.warning(f"Azure blob download failed for template {tmpl.id}: {e}")
    return None


# ─── Save text box configuration ─────────────────────────────────────────────

@router.put("/{template_id}/boxes", response_model=TemplateOut)
async def save_boxes(
    template_id: str,
    boxes: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Save text zone coordinates for a template.
    Body: { heading: {x,y,w,h,max_pt,color}, subheading: ..., body: ..., cta: ... }
    """
    result = await db.execute(select(ImageTemplate).where(ImageTemplate.id == template_id))
    tmpl = result.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Template not found")

    tmpl.text_boxes = json.dumps(boxes)
    await db.commit()
    await db.refresh(tmpl)
    return TemplateOut.model_validate(tmpl)


# ─── Preview rendered image ───────────────────────────────────────────────────

@router.post("/{template_id}/preview")
async def preview_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Render a preview PNG using the template's current text box config with sample text.
    Returns PNG image bytes.
    """
    result = await db.execute(select(ImageTemplate).where(ImageTemplate.id == template_id))
    tmpl = result.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Template not found")

    img_bytes = _load_image_bytes(tmpl)
    if not img_bytes:
        raise HTTPException(404, "Template image not found")

    boxes = tmpl.boxes_dict()
    if not boxes:
        raise HTTPException(400, "No text zones configured yet — draw zones in the editor first")

    # Build a sample job
    sample_job = {
        "first_name": "PRIYA",
        "lines": {
            "heading":    "HAPPY BIRTHDAY, PRIYA!",
            "subheading": "Your Special Day Has Arrived",
            "body":       "Wishing you a day filled with joy, love, and the sparkle of beautiful jewelry from Tanishq.",
            "cta":        "Visit your nearest Tanishq store",
        },
    }

    try:
        import asyncio
        png_bytes = await asyncio.to_thread(_render_preview, img_bytes, boxes, sample_job)
    except Exception as e:
        logger.error(f"Preview render failed: {e}", exc_info=True)
        raise HTTPException(500, f"Preview render failed: {e}")

    return Response(content=png_bytes, media_type="image/png")


def _render_preview(img_bytes: bytes, boxes: dict, job: dict) -> bytes:
    """Render preview synchronously (called via asyncio.to_thread)."""
    import io as _io
    from PIL import Image, ImageDraw
    from backend.workers.image_worker import _draw_text_in_box

    img = Image.open(_io.BytesIO(img_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    lines = job.get("lines", {})

    zone_fonts = {
        "heading":    config.FONT_PLAYFAIR_BOLD,
        "subheading": config.FONT_PLAYFAIR_REGULAR,
        "body":       config.FONT_LATO_REGULAR,
        "cta":        config.FONT_LATO_ITALIC,
    }

    for zone, font_path in zone_fonts.items():
        if zone not in boxes:
            continue
        zb = boxes[zone]
        box = {"x": zb["x"], "y": zb["y"], "w": zb["w"], "h": zb["h"]}
        color = _hex_to_rgb(zb.get("color", "#ffffff"))
        max_pt = int(zb.get("max_pt", 32))
        text = lines.get(zone, "")
        if text:
            _draw_text_in_box(draw, text, box, font_path=font_path, max_size=max_pt, fill=color)

    # Flatten RGBA → RGB
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg

    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue()


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


# ─── Delete template ──────────────────────────────────────────────────────────

@router.delete("/{template_id}")
async def delete_template(template_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ImageTemplate).where(ImageTemplate.id == template_id))
    tmpl = result.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if tmpl.is_builtin:
        raise HTTPException(400, "Cannot delete built-in templates")

    # Delete local file
    if tmpl.local_path and Path(tmpl.local_path).exists():
        try:
            Path(tmpl.local_path).unlink()
        except Exception:
            pass

    await db.delete(tmpl)
    await db.commit()
    return {"ok": True}
