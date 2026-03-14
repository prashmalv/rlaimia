"""
Azure Blob Storage helper — upload files, generate SAS URLs, list blobs.
Falls back gracefully to local filesystem when Azure is not configured.
"""

import os
import io
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ─── Detect if Azure is configured ───────────────────────────────────────────
_AZURE_AVAILABLE = bool(config.AZURE_STORAGE_CONN_STR or config.AZURE_STORAGE_KEY)

if _AZURE_AVAILABLE:
    try:
        from azure.storage.blob import (
            BlobServiceClient,
            BlobClient,
            ContainerClient,
            ContentSettings,
            generate_blob_sas,
            BlobSasPermissions,
        )
        from azure.core.exceptions import ResourceExistsError
        _blob_service_client: Optional[BlobServiceClient] = None
    except ImportError:
        logger.warning("azure-storage-blob not installed. Falling back to local storage.")
        _AZURE_AVAILABLE = False


def _get_client() -> "BlobServiceClient":
    global _blob_service_client
    if _blob_service_client is None:
        if config.AZURE_STORAGE_CONN_STR:
            _blob_service_client = BlobServiceClient.from_connection_string(
                config.AZURE_STORAGE_CONN_STR
            )
        else:
            _blob_service_client = BlobServiceClient(
                account_url=f"https://{config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net",
                credential=config.AZURE_STORAGE_KEY,
            )
    return _blob_service_client


def ensure_containers():
    """Create blob containers if they don't exist."""
    if not _AZURE_AVAILABLE:
        return
    client = _get_client()
    for container_name in [
        config.AZURE_BLOB_CONTAINER_IMG,
        config.AZURE_BLOB_CONTAINER_VID,
    ]:
        try:
            client.create_container(container_name)
            logger.info(f"Created container: {container_name}")
        except Exception:
            pass  # already exists


def upload_bytes(data: bytes, blob_key: str, container: str, content_type: str = "application/octet-stream") -> str:
    """
    Upload raw bytes to Azure Blob.
    Returns: blob_key (use get_sas_url() to get a temporary URL).
    Falls back to local file save if Azure not configured.
    """
    if _AZURE_AVAILABLE:
        client = _get_client()
        blob_client = client.get_blob_client(container=container, blob=blob_key)
        blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        logger.debug(f"Uploaded blob: {container}/{blob_key}")
    else:
        # Local fallback: save under uploads/
        local_path = config.UPLOADS_DIR / container / blob_key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        logger.debug(f"Saved locally: {local_path}")
    return blob_key


def upload_file(local_path: str, blob_key: str, container: str, content_type: str = "application/octet-stream") -> str:
    """Upload a local file to Azure Blob."""
    with open(local_path, "rb") as f:
        return upload_bytes(f.read(), blob_key, container, content_type)


def get_sas_url(blob_key: str, container: str, hours: int = None) -> str:
    """
    Generate a time-limited SAS URL for a blob.
    Returns local serve URL if Azure not configured.
    """
    hours = hours or config.SAS_TOKEN_HOURS

    if _AZURE_AVAILABLE:
        expiry = datetime.now(timezone.utc) + timedelta(hours=hours)
        sas_token = generate_blob_sas(
            account_name=config.AZURE_STORAGE_ACCOUNT,
            container_name=container,
            blob_name=blob_key,
            account_key=config.AZURE_STORAGE_KEY,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        if config.AZURE_CDN_BASE_URL:
            return f"{config.AZURE_CDN_BASE_URL.rstrip('/')}/{container}/{blob_key}?{sas_token}"
        return (
            f"https://{config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net"
            f"/{container}/{blob_key}?{sas_token}"
        )
    else:
        # Local dev: return API serve URL
        return f"/api/files/serve/{container}/{blob_key}"


def delete_blob(blob_key: str, container: str):
    """Delete a blob from Azure Storage."""
    if _AZURE_AVAILABLE:
        client = _get_client()
        blob_client = client.get_blob_client(container=container, blob=blob_key)
        blob_client.delete_blob(delete_snapshots="include")
    else:
        local_path = config.UPLOADS_DIR / container / blob_key
        if local_path.exists():
            local_path.unlink()


def list_blobs(container: str, prefix: str = "", limit: int = 1000) -> list[dict]:
    """List blobs in a container with optional prefix filter."""
    results = []
    if _AZURE_AVAILABLE:
        client = _get_client()
        container_client = client.get_container_client(container)
        for i, blob in enumerate(container_client.list_blobs(name_starts_with=prefix)):
            if i >= limit:
                break
            results.append({
                "name": blob.name,
                "size": blob.size,
                "last_modified": blob.last_modified,
                "url": get_sas_url(blob.name, container),
            })
    else:
        base = config.UPLOADS_DIR / container
        if base.exists():
            for f in sorted(base.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                if f.is_file() and (not prefix or str(f.relative_to(base)).startswith(prefix)):
                    rel = str(f.relative_to(base))
                    results.append({
                        "name": rel,
                        "size": f.stat().st_size,
                        "last_modified": datetime.fromtimestamp(f.stat().st_mtime),
                        "url": f"/api/files/serve/{container}/{rel}",
                    })
    return results


def delete_blobs_by_prefix(prefix: str, container: str) -> int:
    """Delete all blobs whose name starts with `prefix`. Returns count deleted."""
    count = 0
    if _AZURE_AVAILABLE:
        client = _get_client()
        container_client = client.get_container_client(container)
        for blob in list(container_client.list_blobs(name_starts_with=prefix)):
            try:
                container_client.delete_blob(blob.name, delete_snapshots="include")
                count += 1
            except Exception as e:
                logger.warning(f"Could not delete blob {blob.name}: {e}")
    else:
        base = config.UPLOADS_DIR / container
        if base.exists():
            for f in list(base.rglob("*")):
                if f.is_file() and str(f.relative_to(base)).startswith(prefix):
                    try:
                        f.unlink()
                        count += 1
                    except Exception:
                        pass
    logger.info(f"Deleted {count} blobs with prefix '{prefix}' from container '{container}'")
    return count


def read_blob_bytes(blob_key: str, container: str) -> bytes:
    """Read blob content as bytes (for local preview)."""
    if _AZURE_AVAILABLE:
        client = _get_client()
        blob_client = client.get_blob_client(container=container, blob=blob_key)
        return blob_client.download_blob().readall()
    else:
        local_path = config.UPLOADS_DIR / container / blob_key
        return local_path.read_bytes()
