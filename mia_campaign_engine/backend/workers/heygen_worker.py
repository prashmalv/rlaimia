"""
Heygen AI Avatar Video Worker
Generates personalized talking-head videos using the Heygen API.

Flow per job:
  1. Campaign creation: upload avatar image → get talking_photo_id (stored on Campaign)
  2. Per job: POST /v2/video/generate with talking_photo_id + personalized script
  3. Poll GET /v1/video_status.get until completed
  4. Download MP4 → upload to Azure Blob → store URL in DB
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

HEYGEN_API_BASE   = "https://api.heygen.com"
HEYGEN_UPLOAD_BASE = "https://upload.heygen.com"   # separate subdomain for file uploads


# ─── Upload avatar image → get talking_photo_id ───────────────────────────────

def upload_talking_photo(image_path: str) -> str:
    """
    Upload a local image file to Heygen and return the talking_photo_id.
    This ID is reused for all jobs in the same campaign.
    """
    api_key = config.HEYGEN_API_KEY
    if not api_key:
        raise RuntimeError("HEYGEN_API_KEY not configured — set it via env var")

    ext = Path(image_path).suffix.lower()
    ct  = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
           ".webp": "image/webp"}.get(ext, "image/jpeg")

    with open(image_path, "rb") as f:
        image_data = f.read()

    # Heygen upload uses upload.heygen.com with raw binary body (not multipart)
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{HEYGEN_UPLOAD_BASE}/v1/talking_photo",
            headers={"X-Api-Key": api_key, "Content-Type": ct},
            content=image_data,
        )

    if resp.is_error:
        raise RuntimeError(f"Heygen talking_photo upload failed [{resp.status_code}]: {resp.text[:500]}")

    data = resp.json()
    logger.info(f"[heygen] talking_photo upload response: {data}")
    talking_photo_id = (
        data.get("data", {}).get("talking_photo_id")
        or data.get("talking_photo_id")
    )
    if not talking_photo_id:
        raise RuntimeError(f"Heygen: no talking_photo_id in response: {data}")

    logger.info(f"[heygen] Uploaded talking photo → ID={talking_photo_id}")
    return talking_photo_id


# ─── Submit video generation request ─────────────────────────────────────────

def create_heygen_video(
    script: str,
    talking_photo_id: str,
    voice_id: Optional[str] = None,
) -> str:
    """
    Submit a Heygen video generation job.
    Returns video_id for subsequent polling.
    """
    api_key  = config.HEYGEN_API_KEY
    voice_id = voice_id or config.HEYGEN_VOICE_ID

    if not api_key:
        raise RuntimeError("HEYGEN_API_KEY not configured")

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "talking_photo",
                    "talking_photo_id": talking_photo_id,
                    # talking_photo_style omitted → full rectangular avatar (default)
                    # valid values if needed: "circle" only
                },
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": voice_id,
                    "speed": 1.0,
                },
                "background": {
                    "type": "color",
                    "value": "#000000",
                },
            }
        ],
        "dimension": {
            "width": config.HEYGEN_VIDEO_W,
            "height": config.HEYGEN_VIDEO_H,
        },
        # aspect_ratio omitted — null causes Heygen validation error; dimension alone is sufficient
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{HEYGEN_API_BASE}/v2/video/generate",
            headers={"X-Api-Key": api_key},
            json=payload,
        )

    if resp.is_error:
        raise RuntimeError(f"Heygen video/generate failed [{resp.status_code}]: {resp.text[:500]}")

    data     = resp.json()
    logger.info(f"[heygen] video/generate response: {data}")
    video_id = data.get("data", {}).get("video_id") or data.get("video_id")
    if not video_id:
        raise RuntimeError(f"Heygen: no video_id in response: {data}")

    logger.info(f"[heygen] Created video job → video_id={video_id}")
    return video_id


# ─── Poll for completion ──────────────────────────────────────────────────────

def poll_heygen_video(video_id: str) -> str:
    """
    Block-poll until the Heygen video is complete.
    Returns the Heygen-hosted download URL.
    Raises TimeoutError or RuntimeError on failure.
    """
    api_key  = config.HEYGEN_API_KEY
    deadline = time.time() + config.HEYGEN_TIMEOUT
    interval = config.HEYGEN_POLL_SECS

    while time.time() < deadline:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{HEYGEN_API_BASE}/v1/video_status.get",
                headers={"X-Api-Key": api_key},
                params={"video_id": video_id},
            )
        if resp.is_error:
            raise RuntimeError(f"Heygen status check failed [{resp.status_code}]: {resp.text[:200]}")

        data   = resp.json().get("data", {})
        status = data.get("status", "").lower()
        logger.debug(f"[heygen] video_id={video_id} status={status}")

        if status == "completed":
            video_url = data.get("video_url")
            if not video_url:
                raise RuntimeError(f"Heygen completed but no video_url in response: {data}")
            return video_url

        if status in ("failed", "error"):
            err = data.get("error") or data.get("message") or str(data)
            raise RuntimeError(f"Heygen video failed: {err}")

        time.sleep(interval)

    raise TimeoutError(f"Heygen video_id={video_id} did not complete within {config.HEYGEN_TIMEOUT}s")


# ─── Full per-job flow ────────────────────────────────────────────────────────

def process_heygen_job(job: dict, campaign_id: str, talking_photo_id: str, voice_id: Optional[str] = None) -> dict:
    """
    Full Heygen flow for a single job:
    create → poll → download → upload to Azure Blob.

    Returns:
        {"status": "done",   "url": "...", "blob_key": "...", "video_id": "..."}
     or {"status": "failed", "error": "..."}
    """
    from backend.app.azure_storage import upload_bytes, get_sas_url
    import config as _cfg

    job_id = job["job_id"]

    # Build personalized script — prefer message_text (LLM-generated), then fallback
    script = (
        job.get("message_text")
        or job.get("lines", {}).get("body")
        or f"Happy Birthday, {job.get('first_name', 'friend')}! "
           f"Wishing you a wonderful day from the Mia team at Tanishq."
    )
    # Trim to Heygen's 1500-char limit
    if len(script) > 1500:
        script = script[:1497] + "..."

    try:
        logger.info(f"[heygen] Job {job_id}: submitting (script={len(script)} chars, "
                    f"photo={talking_photo_id[:12]}...)")
        video_id  = create_heygen_video(script, talking_photo_id, voice_id=voice_id)

        logger.info(f"[heygen] Job {job_id}: polling video_id={video_id}")
        heygen_url = poll_heygen_video(video_id)

        # Download from Heygen CDN
        logger.info(f"[heygen] Job {job_id}: downloading...")
        with httpx.Client(timeout=180) as client:
            dl_resp = client.get(heygen_url)
        dl_resp.raise_for_status()
        video_bytes = dl_resp.content
        if len(video_bytes) < 1024:
            raise RuntimeError(f"Downloaded video suspiciously small: {len(video_bytes)} bytes")

        # Upload to Azure Blob (video container)
        blob_key = f"{campaign_id}/{job_id}_heygen.mp4"
        upload_bytes(
            video_bytes, blob_key,
            container=_cfg.AZURE_BLOB_CONTAINER_VID,
            content_type="video/mp4",
        )
        url = get_sas_url(blob_key, container=_cfg.AZURE_BLOB_CONTAINER_VID)

        logger.info(f"[heygen] Job {job_id}: done — {len(video_bytes):,} bytes → blob={blob_key}")
        return {"status": "done", "url": url, "blob_key": blob_key, "video_id": video_id}

    except Exception as e:
        logger.error(f"[heygen] Job {job_id} failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)[:300]}
