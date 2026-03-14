"""
Files Router — browse, preview, download generated images & videos.
Supports both Azure Blob (prod) and local filesystem (dev).
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
import io

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import config
from backend.app import azure_storage

router = APIRouter(prefix="/api/files", tags=["Files"])
logger = logging.getLogger(__name__)


# ─── List files ──────────────────────────────────────────────────────────────

@router.get("/images")
async def list_images(
    campaign_id: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
):
    prefix = f"{campaign_id}/" if campaign_id else ""
    blobs  = azure_storage.list_blobs(config.AZURE_BLOB_CONTAINER_IMG, prefix=prefix, limit=limit)
    return {"count": len(blobs), "files": blobs}


@router.get("/videos")
async def list_videos(
    campaign_id: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
):
    prefix = f"{campaign_id}/" if campaign_id else ""
    blobs  = azure_storage.list_blobs(config.AZURE_BLOB_CONTAINER_VID, prefix=prefix, limit=limit)
    return {"count": len(blobs), "files": blobs}


@router.get("/avatar_videos")
async def list_avatar_videos(
    campaign_id: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
):
    """List only AI avatar video blobs (files ending with _heygen.mp4)."""
    prefix   = f"{campaign_id}/" if campaign_id else ""
    all_vids = azure_storage.list_blobs(config.AZURE_BLOB_CONTAINER_VID, prefix=prefix, limit=limit * 5)
    filtered = [b for b in all_vids if b["name"].endswith("_heygen.mp4")][:limit]
    return {"count": len(filtered), "files": filtered}


# ─── Get SAS URL for a specific file ─────────────────────────────────────────

@router.get("/url/image/{blob_key:path}")
async def image_url(blob_key: str, hours: int = Query(72)):
    url = azure_storage.get_sas_url(blob_key, config.AZURE_BLOB_CONTAINER_IMG, hours=hours)
    return {"url": url, "blob_key": blob_key}


@router.get("/url/video/{blob_key:path}")
async def video_url(blob_key: str, hours: int = Query(72)):
    url = azure_storage.get_sas_url(blob_key, config.AZURE_BLOB_CONTAINER_VID, hours=hours)
    return {"url": url, "blob_key": blob_key}


# ─── Serve file directly (local dev fallback) ────────────────────────────────

@router.get("/serve/{container}/{blob_key:path}")
async def serve_file(container: str, blob_key: str):
    """
    Serve a file directly from local storage.
    Used in dev mode when Azure is not configured.
    """
    local_path = config.UPLOADS_DIR / container / blob_key
    if not local_path.exists():
        raise HTTPException(404, f"File not found: {blob_key}")

    suffix = local_path.suffix.lower()
    media_type_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(suffix, "application/octet-stream")
    return FileResponse(str(local_path), media_type=media_type)


# ─── Inline preview (stream from blob) ───────────────────────────────────────

@router.get("/preview/image/{blob_key:path}")
async def preview_image(blob_key: str):
    """Stream image bytes inline for dashboard preview."""
    try:
        data = azure_storage.read_blob_bytes(blob_key, config.AZURE_BLOB_CONTAINER_IMG)
    except Exception as e:
        raise HTTPException(404, f"Image not found: {e}")

    suffix = Path(blob_key).suffix.lower()
    content_type = "image/png" if suffix == ".png" else "image/jpeg"
    return StreamingResponse(io.BytesIO(data), media_type=content_type)


@router.get("/preview/video/{blob_key:path}")
async def preview_video(blob_key: str):
    """Stream video bytes inline for dashboard preview."""
    try:
        data = azure_storage.read_blob_bytes(blob_key, config.AZURE_BLOB_CONTAINER_VID)
    except Exception as e:
        raise HTTPException(404, f"Video not found: {e}")
    return StreamingResponse(io.BytesIO(data), media_type="video/mp4")


# ─── Bulk download info ───────────────────────────────────────────────────────

@router.get("/bulk-urls/{campaign_id}")
async def bulk_sas_urls(
    campaign_id: str,
    media_type: str = Query("images", regex="^(images|videos|avatar_videos)$"),
    hours: int = Query(72),
    limit: int = Query(1000, le=10000),
):
    """
    Return SAS URLs for all files in a campaign.
    Authorized users can use these links to download at bulk.
    media_type: images | videos | avatar_videos (AI avatar videos only)
    """
    container = (
        config.AZURE_BLOB_CONTAINER_IMG if media_type == "images"
        else config.AZURE_BLOB_CONTAINER_VID
    )
    blobs = azure_storage.list_blobs(container, prefix=f"{campaign_id}/", limit=limit * (5 if media_type == "avatar_videos" else 1))

    # Filter to heygen-only blobs when avatar_videos requested
    if media_type == "avatar_videos":
        blobs = [b for b in blobs if b["name"].endswith("_heygen.mp4")][:limit]

    # Refresh SAS URLs
    for blob in blobs:
        blob["url"] = azure_storage.get_sas_url(blob["name"], container, hours=hours)

    return {
        "campaign_id": campaign_id,
        "media_type":  media_type,
        "count":       len(blobs),
        "sas_ttl_hrs": hours,
        "files":       blobs,
    }


# ─── Delete campaign files (admin only) ──────────────────────────────────────

def _require_admin(x_mia_role: str = Header(default="")):
    """Dependency: require admin role header."""
    if x_mia_role != "admin":
        raise HTTPException(403, "Admin access required")


@router.delete("/campaign/{campaign_id}")
async def delete_campaign_files(
    campaign_id: str,
    media_type: str = Query("all", regex="^(images|videos|all)$"),
    _: None = Depends(_require_admin),
):
    """
    Delete all generated files for a campaign from blob storage.
    media_type: images | videos | all
    """
    results = {}
    prefix = f"{campaign_id}/"

    if media_type in ("images", "all"):
        count = await asyncio.to_thread(
            azure_storage.delete_blobs_by_prefix, prefix, config.AZURE_BLOB_CONTAINER_IMG
        )
        results["images_deleted"] = count

    if media_type in ("videos", "all"):
        count = await asyncio.to_thread(
            azure_storage.delete_blobs_by_prefix, prefix, config.AZURE_BLOB_CONTAINER_VID
        )
        results["videos_deleted"] = count

    return {"campaign_id": campaign_id, "media_type": media_type, **results}
