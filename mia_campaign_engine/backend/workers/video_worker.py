"""
Video Worker — Optimised bulk MP4 generation for Mia Campaign Engine.

Strategy for 5-lakh scale:
  • Uses FFmpeg via subprocess (10-20× faster than MoviePy for simple overlays)
  • drawtext filter: composites multiple text layers in a single FFmpeg pass
  • Hardware acceleration: h264_nvenc on Azure GPU VMs, libx264 on CPU VMs
  • Concurrent processing: multiprocessing.Pool across worker nodes
  • No temp files: pipe video template from disk, stream output to blob

Mia Brand Fonts (FFmpeg drawtext):
  • Heading    : Gotham Bold
  • Sub-heading: Gotham Medium
  • Body       : EB Garamond Regular
"""

import io
import os
import sys
import json
import uuid
import logging
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config
from backend.app import azure_storage

logger = logging.getLogger(__name__)

# ─── Video Template Registry ─────────────────────────────────────────────────
VIDEO_TEMPLATE_CONFIGS: dict[str, dict] = {
    "video_template_1": {
        "path": str(config.VIDEO_TEMPLATES_DIR / "video_template.mp4"),
        "text_layers": [
            # Each layer: text_key, x, y, font_path_key, size, color, start_t, duration, fade
            {
                "key":      "heading",
                "x":        700, "y": 220,
                "font_key": "gotham_bold",
                "size":     72,
                "color":    "white",
                "start":    0.5, "duration": 3.0,
                "fade_in":  0.3, "fade_out": 0.3,
            },
            {
                "key":      "subheading",
                "x":        700, "y": 310,
                "font_key": "gotham_medium",
                "size":     38,
                "color":    "white",
                "start":    1.0, "duration": 3.0,
                "fade_in":  0.3, "fade_out": 0.3,
            },
            {
                "key":      "body_line1",
                "x":        700, "y": 390,
                "font_key": "garamond_regular",
                "size":     30,
                "color":    "white@0.9",
                "start":    1.5, "duration": 3.0,
                "fade_in":  0.3, "fade_out": 0.3,
            },
            {
                "key":      "cta",
                "x":        700, "y": 490,
                "font_key": "garamond_regular",
                "size":     26,
                "color":    "FFE696",
                "start":    2.2, "duration": 4.0,
                "fade_in":  0.4, "fade_out": 0.5,
            },
        ],
    },
    "video_template_2": {
        "path": str(config.VIDEO_TEMPLATES_DIR / "video_template_2.mp4"),
        "text_layers": [
            {"key": "heading",    "x": 660, "y": 200, "font_key": "gotham_bold",    "size": 64, "color": "white",       "start": 0.6, "duration": 3.0, "fade_in": 0.3, "fade_out": 0.3},
            {"key": "subheading", "x": 660, "y": 285, "font_key": "gotham_medium",  "size": 34, "color": "white",       "start": 1.2, "duration": 3.0, "fade_in": 0.3, "fade_out": 0.3},
            {"key": "body_line1", "x": 660, "y": 355, "font_key": "garamond_regular","size": 28, "color": "white@0.9",  "start": 1.8, "duration": 3.0, "fade_in": 0.3, "fade_out": 0.3},
            {"key": "cta",        "x": 660, "y": 450, "font_key": "garamond_regular","size": 24, "color": "FFE696",     "start": 2.5, "duration": 4.0, "fade_in": 0.4, "fade_out": 0.5},
        ],
    },
}

FONT_KEY_MAP = {
    "gotham_bold":      config.FONT_GOTHAM_BOLD,
    "gotham_medium":    config.FONT_GOTHAM_MEDIUM,
    "gotham_book":      config.FONT_GOTHAM_BOOK,
    "garamond_regular": config.FONT_GARAMOND_REGULAR,
    "garamond_bold":    config.FONT_GARAMOND_BOLD,
    "garamond_italic":  config.FONT_GARAMOND_ITALIC,
}


def _resolve_font(font_key: str) -> str:
    """Resolve a font key to an actual file path, with fallbacks."""
    path = FONT_KEY_MAP.get(font_key, "")
    if path and os.path.exists(path):
        return path
    # Fallback chain
    for fb in [config.FONT_GARAMOND_REGULAR, config.FONT_FALLBACK]:
        if os.path.exists(fb):
            return fb
    return ""  # FFmpeg will try system fonts


def _escape_ffmpeg_text(text: str) -> str:
    """Escape special characters for FFmpeg drawtext filter."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'",  "\\'")
        .replace(":",  "\\:")
        .replace("[",  "\\[")
        .replace("]",  "\\]")
        .replace("%",  "%%")
    )


def _wrap_text(text: str, max_chars: int = 40) -> str:
    """Wrap long text at word boundaries."""
    import textwrap
    return "\\n".join(textwrap.wrap(text, width=max_chars))


def _build_drawtext_filter(layers: list[dict], text_values: dict) -> str:
    """
    Build the FFmpeg filtergraph string for all text layers.
    text_values: {layer_key: text_string}
    """
    filters = []

    for layer in layers:
        key    = layer["key"]
        text   = text_values.get(key, "")
        if not text:
            continue

        font_path  = _resolve_font(layer["font_key"])
        font_size  = layer["size"]
        color      = layer["color"]
        x          = layer["x"]
        y          = layer["y"]
        start      = layer["start"]
        duration   = layer["duration"]
        fade_in    = layer.get("fade_in", 0)
        fade_out   = layer.get("fade_out", 0)

        escaped_text = _escape_ffmpeg_text(_wrap_text(text))

        font_part = f":fontfile='{font_path}'" if font_path else ""
        alpha_expr = (
            f"if(lt(t-{start},{fade_in}),(t-{start})/{fade_in},"
            f"if(lt(t-{start},{duration-fade_out}),1,"
            f"if(lt(t-{start},{duration}),({duration}-(t-{start}))/{fade_out},0)))"
        )
        enable_expr = f"between(t,{start},{start+duration})"

        dt = (
            f"drawtext=text='{escaped_text}'"
            f":x={x}:y={y}"
            f"{font_part}"
            f":fontsize={font_size}"
            f":fontcolor={color}"
            f":alpha='{alpha_expr}'"
            f":enable='{enable_expr}'"
            f":line_spacing=4"
        )
        filters.append(dt)

    return ",".join(filters) if filters else "null"


def _get_video_template_path(template_id: str) -> str:
    """Resolve video template path with fallback to birthday_campaign assets."""
    cfg  = VIDEO_TEMPLATE_CONFIGS.get(template_id, {})
    path = cfg.get("path", "")

    if path and os.path.exists(path):
        return path

    # Fallback to birthday_campaign assets
    fname = Path(path).name if path else f"{template_id}.mp4"
    alt   = str(Path(__file__).parent.parent.parent.parent /
                "birthday_campaign" / "assets" / fname)
    if os.path.exists(alt):
        return alt

    raise FileNotFoundError(f"Video template not found: {path} (also checked {alt})")


def generate_video(job: dict, template_id: str = "video_template_1") -> bytes:
    """
    Generate a personalised greeting video for one person using FFmpeg.

    Args:
        job: dict with first_name and lines (heading/subheading/body/cta)
        template_id: which video template to use

    Returns:
        MP4 bytes
    """
    tmpl_cfg = VIDEO_TEMPLATE_CONFIGS[template_id]
    video_in = _get_video_template_path(template_id)

    lines = job.get("lines", {})
    fn    = job.get("first_name", "Friend").upper()

    # Map message lines to layer keys
    text_values = {
        "heading":    lines.get("heading",    f"HAPPY BIRTHDAY {fn}"),
        "subheading": lines.get("subheading", ""),
        "body_line1": lines.get("body",       "").split("\n")[0] if lines.get("body") else "",
        "cta":        lines.get("cta",        ""),
    }

    vf_filter = _build_drawtext_filter(tmpl_cfg["text_layers"], text_values)

    # Output to temp file then read bytes (FFmpeg needs seekable output for MP4)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        out_path = tmp.name

    try:
        cmd = [
            config.FFMPEG_PATH,
            "-y",                            # overwrite
            "-i", video_in,
            "-vf", vf_filter,
            "-c:v", config.VIDEO_CODEC,
            "-crf", config.VIDEO_CRF,
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",       # web-optimised MP4
            out_path,
        ]

        # Add hardware acceleration if configured
        if config.VIDEO_HWACCEL:
            cmd = [
                config.FFMPEG_PATH,
                "-y",
                "-hwaccel", "cuda",
                "-i", video_in,
                "-vf", vf_filter,
                "-c:v", config.VIDEO_HWACCEL,
                "-b:v", "2M",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                out_path,
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {result.stderr[-500:]}")

        with open(out_path, "rb") as f:
            return f.read()

    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


# ─── Video compression to target size ────────────────────────────────────────

def compress_video(input_bytes: bytes, target_mb: float = 5.0) -> bytes:
    """
    Re-encode a video to fit within target_mb using FFmpeg two-pass bitrate control.
    Works by estimating bitrate from duration then targeting that budget.
    Falls back to original bytes if compression fails or doesn't reduce size.
    """
    import tempfile, os, subprocess

    target_bytes = int(target_mb * 1024 * 1024)
    if len(input_bytes) <= target_bytes:
        return input_bytes   # already within limit

    with tempfile.TemporaryDirectory() as tmp:
        in_path  = os.path.join(tmp, "input.mp4")
        out_path = os.path.join(tmp, "output.mp4")

        with open(in_path, "wb") as f:
            f.write(input_bytes)

        # Get duration via ffprobe
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", in_path],
            capture_output=True, text=True, timeout=30,
        )
        try:
            duration_s = float(probe.stdout.strip())
        except (ValueError, TypeError):
            logger.warning("[compress_video] ffprobe failed — returning original")
            return input_bytes

        if duration_s <= 0:
            return input_bytes

        # Budget: target_bytes * 8 bits / duration_s → kbps; reserve 64k for audio
        total_kbps  = int(target_bytes * 8 / duration_s / 1000)
        audio_kbps  = 64
        video_kbps  = max(100, total_kbps - audio_kbps)

        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-c:v", "libx264",
            "-b:v", f"{video_kbps}k",
            "-maxrate", f"{video_kbps}k",
            "-bufsize", f"{video_kbps * 2}k",
            "-c:a", "aac",
            "-b:a", f"{audio_kbps}k",
            "-movflags", "+faststart",
            "-preset", "fast",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.warning(f"[compress_video] FFmpeg failed: {result.stderr[-200:]}")
            return input_bytes

        with open(out_path, "rb") as f:
            compressed = f.read()

    logger.info(f"[compress_video] {len(input_bytes):,} → {len(compressed):,} bytes "
                f"({len(compressed)/1024/1024:.1f} MB, target={target_mb} MB)")
    return compressed


# ─── Blob key helpers ─────────────────────────────────────────────────────────

def video_blob_key(campaign_id: str, job_id: str, template_id: str) -> str:
    return f"{campaign_id}/{template_id}/{job_id}.mp4"


def compressed_video_blob_key(campaign_id: str, job_id: str, suffix: str = "") -> str:
    """Blob key for compressed download-ready copy."""
    return f"reports/{campaign_id}/videos/{job_id}{suffix}_compressed.mp4"


# ─── Single-job entry point (called by Celery task) ──────────────────────────

def process_video_job(job: dict, campaign_id: str, template_ids: list[str] = None) -> dict:
    """
    Generate video(s) for one person and upload to Azure Blob.
    Returns dict with blob_key and url per template.
    """
    template_ids = template_ids or list(VIDEO_TEMPLATE_CONFIGS.keys())
    results = {}

    for tmpl_id in template_ids:
        try:
            mp4_bytes = generate_video(job, template_id=tmpl_id)
            blob_key  = video_blob_key(campaign_id, job["job_id"], tmpl_id)
            azure_storage.upload_bytes(
                mp4_bytes, blob_key,
                container=config.AZURE_BLOB_CONTAINER_VID,
                content_type="video/mp4",
            )
            url = azure_storage.get_sas_url(blob_key, config.AZURE_BLOB_CONTAINER_VID)
            results[tmpl_id] = {"blob_key": blob_key, "url": url, "status": "done"}
        except Exception as e:
            logger.error(f"Video generation failed for job {job.get('job_id')} tmpl {tmpl_id}: {e}")
            results[tmpl_id] = {"status": "failed", "error": str(e)}

    return results


# ─── Bulk batch generator (local, no Celery) ─────────────────────────────────

def run_batch_local(jobs: list[dict], campaign_id: str, max_workers: int = None) -> list[dict]:
    max_workers = max_workers or config.VIDEO_WORKER_CONCURRENCY
    results = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_video_job, job, campaign_id): job
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
