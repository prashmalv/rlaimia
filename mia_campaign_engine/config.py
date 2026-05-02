"""
Mia Campaign Engine — Central Configuration
All settings loaded from environment variables with sensible defaults.
"""

import os
from pathlib import Path

# ─── Base Paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"

# ─── Font Paths (Mia Brand) ────────────────────────────────────────────────────
# Resolves actual font file — tries multiple known filenames, picks first found.

def _resolve_font(*candidates: str) -> str:
    """Return the first existing font path from candidates list."""
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]  # return first anyway; image_worker handles missing gracefully

_G = str(FONTS_DIR / "gotham")
_R = str(FONTS_DIR / "garamond")

# Gotham Bold — heading (GothamBold.ttf present, Gotham-Bold.otf as alternate)
FONT_GOTHAM_BOLD   = os.getenv("FONT_GOTHAM_BOLD",   _resolve_font(
    f"{_G}/GothamBold.ttf", f"{_G}/Gotham-Bold.otf", f"{_G}/Gotham-Black.otf"
))

# Gotham Medium — sub-heading
FONT_GOTHAM_MEDIUM = os.getenv("FONT_GOTHAM_MEDIUM", _resolve_font(
    f"{_G}/GothamMedium.ttf", f"{_G}/GothamMedium_1.ttf", f"{_G}/Gotham-Bold.otf"
))

# Gotham Book — CTA / lighter weight
FONT_GOTHAM_BOOK   = os.getenv("FONT_GOTHAM_BOOK",   _resolve_font(
    f"{_G}/GothamBook.ttf", f"{_G}/GothamLight.ttf", f"{_G}/Gotham-Light.otf"
))

# EB Garamond — body text (variable font covers all weights)
FONT_GARAMOND_REGULAR = os.getenv("FONT_GARAMOND_REGULAR", _resolve_font(
    f"{_R}/EBGaramond-Regular.ttf",
    f"{_R}/EBGaramond-VariableFont_wght.ttf",
))

FONT_GARAMOND_BOLD    = os.getenv("FONT_GARAMOND_BOLD", _resolve_font(
    f"{_R}/EBGaramond-Bold.ttf",
    f"{_R}/EBGaramond-VariableFont_wght.ttf",
))

FONT_GARAMOND_ITALIC  = os.getenv("FONT_GARAMOND_ITALIC", _resolve_font(
    f"{_R}/EBGaramond-Italic.ttf",
    f"{_R}/EBGaramond-Italic-VariableFont_wght.ttf",
))

# Fallback (if no brand fonts found at all)
FONT_FALLBACK = os.getenv("FONT_FALLBACK", _resolve_font(
    f"{_G}/GothamBook.ttf",
    f"{_R}/EBGaramond-VariableFont_wght.ttf",
))

# ─── Open-source web fonts (downloaded clean in Docker — no corruption risk) ──
# Playfair Display — elegant serif; replaces Gotham for heading/subheading
_PF = str(FONTS_DIR / "playfair")
_LT = str(FONTS_DIR / "lato")

FONT_PLAYFAIR_BOLD    = os.getenv("FONT_PLAYFAIR_BOLD",    f"{_PF}/PlayfairDisplay-Bold.ttf")
FONT_PLAYFAIR_REGULAR = os.getenv("FONT_PLAYFAIR_REGULAR", f"{_PF}/PlayfairDisplay-Regular.ttf")

# Lato — clean sans-serif; replaces EB Garamond for body text
FONT_LATO_REGULAR     = os.getenv("FONT_LATO_REGULAR",     f"{_LT}/Lato-Regular.ttf")
FONT_LATO_BOLD        = os.getenv("FONT_LATO_BOLD",        f"{_LT}/Lato-Bold.ttf")
FONT_LATO_ITALIC      = os.getenv("FONT_LATO_ITALIC",      f"{_LT}/Lato-Italic.ttf")

# ─── Font name → path resolver (used by template editor font picker) ─────────
# Maps the short font key stored in boxes JSON → actual TTF path on disk.
# Keys are lowercase identifiers used in the template editor dropdown.
FONT_MAP: dict[str, str] = {
    "playfair_bold":    FONT_PLAYFAIR_BOLD,
    "playfair_regular": FONT_PLAYFAIR_REGULAR,
    "lato_regular":     FONT_LATO_REGULAR,
    "lato_bold":        FONT_LATO_BOLD,
    "lato_italic":      FONT_LATO_ITALIC,
    "garamond_regular": FONT_GARAMOND_REGULAR,
    "garamond_bold":    FONT_GARAMOND_BOLD,
    "garamond_italic":  FONT_GARAMOND_ITALIC,
    # Gotham — included in map; will be empty path on Docker (excluded), _get_font falls back gracefully
    "gotham_bold":      FONT_GOTHAM_BOLD,
    "gotham_medium":    FONT_GOTHAM_MEDIUM,
    "gotham_book":      FONT_GOTHAM_BOOK,
}

# Default fonts per zone (used when boxes JSON has no "font" key)
FONT_ZONE_DEFAULTS: dict[str, str] = {
    "heading":    FONT_PLAYFAIR_BOLD,
    "subheading": FONT_PLAYFAIR_REGULAR,
    "body":       FONT_LATO_REGULAR,
    "cta":        FONT_LATO_ITALIC,
}


def zone_font_path(zone: str, font_key: str | None) -> str:
    """Resolve font path for a template zone. Returns config path for that zone."""
    if font_key and font_key in FONT_MAP:
        return FONT_MAP[font_key]
    return FONT_ZONE_DEFAULTS.get(zone, FONT_LATO_REGULAR)


# ─── Image Templates ──────────────────────────────────────────────────────────
# Map of template_id → template config (override via JSON in env or extend in code)
IMAGE_TEMPLATES_DIR   = ASSETS_DIR / "image_templates"
VIDEO_TEMPLATES_DIR   = ASSETS_DIR / "video_templates"
TEMPLATES_UPLOAD_DIR  = UPLOADS_DIR / "image_templates"   # user-uploaded template images

# ─── Azure ────────────────────────────────────────────────────────────────────
AZURE_TENANT_ID           = os.getenv("AZURE_TENANT_ID", "")
AZURE_SUBSCRIPTION_ID     = os.getenv("AZURE_SUBSCRIPTION_ID", "")
AZURE_RESOURCE_GROUP      = os.getenv("AZURE_RESOURCE_GROUP", "mia-campaign-rg")
AZURE_STORAGE_ACCOUNT     = os.getenv("AZURE_STORAGE_ACCOUNT", "miacampaignstore")
AZURE_STORAGE_KEY         = os.getenv("AZURE_STORAGE_KEY", "")
AZURE_STORAGE_CONN_STR    = os.getenv("AZURE_STORAGE_CONN_STR", "")
AZURE_BLOB_CONTAINER_IMG  = os.getenv("AZURE_BLOB_CONTAINER_IMG", "campaign-images")
AZURE_BLOB_CONTAINER_VID  = os.getenv("AZURE_BLOB_CONTAINER_VID", "campaign-videos")
AZURE_CDN_BASE_URL        = os.getenv("AZURE_CDN_BASE_URL", "")  # Optional CDN

# ─── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{BASE_DIR}/mia_campaign.db"   # Local dev default
)
# For Azure PostgreSQL:
# DATABASE_URL=postgresql+asyncpg://user:pass@host.postgres.database.azure.com/mia_campaign

# ─── Redis / Celery ───────────────────────────────────────────────────────────

def _redis_ssl_url(url: str) -> str:
    """redis-py 5.x requires ssl_cert_reqs param in rediss:// URLs."""
    if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}ssl_cert_reqs=CERT_NONE"
    return url

REDIS_URL             = _redis_ssl_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
CELERY_BROKER_URL     = _redis_ssl_url(os.getenv("CELERY_BROKER_URL",     os.getenv("REDIS_URL", "redis://localhost:6379/0")))
CELERY_RESULT_BACKEND = _redis_ssl_url(os.getenv("CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/0")))

# ─── Worker Concurrency ───────────────────────────────────────────────────────
IMAGE_WORKER_CONCURRENCY = int(os.getenv("IMAGE_WORKER_CONCURRENCY", "8"))
VIDEO_WORKER_CONCURRENCY = int(os.getenv("VIDEO_WORKER_CONCURRENCY", "4"))
TASK_CHUNK_SIZE          = int(os.getenv("TASK_CHUNK_SIZE", "500"))  # jobs per Celery task

# ─── Video Settings ───────────────────────────────────────────────────────────
VIDEO_FPS          = int(os.getenv("VIDEO_FPS", "24"))
VIDEO_CODEC        = os.getenv("VIDEO_CODEC", "libx264")
VIDEO_CRF          = os.getenv("VIDEO_CRF", "23")          # 18=high quality, 28=smaller
VIDEO_HWACCEL      = os.getenv("VIDEO_HWACCEL", "")         # e.g. "h264_nvenc" for GPU
FFMPEG_PATH        = os.getenv("FFMPEG_PATH", "ffmpeg")

# ─── API / Auth ───────────────────────────────────────────────────────────────
SECRET_KEY         = os.getenv("SECRET_KEY", "changeme-use-strong-key-in-prod")
API_TOKEN          = os.getenv("API_TOKEN", "")              # Bearer token for API access
CORS_ORIGINS       = os.getenv("CORS_ORIGINS", "*").split(",")

# Dashboard login (leave blank to disable auth — open access)
ADMIN_PASSWORD     = os.getenv("ADMIN_PASSWORD", "")   # admin: can delete, create
VIEWER_PASSWORD    = os.getenv("VIEWER_PASSWORD", "")  # viewer: browse + download only
AUTH_ENABLED       = bool(ADMIN_PASSWORD)               # auto-enabled when password set

# ─── SAS Token TTL ────────────────────────────────────────────────────────────
SAS_TOKEN_HOURS    = int(os.getenv("SAS_TOKEN_HOURS", "72"))  # 3 days default

# ─── Heygen AI Avatar Video ───────────────────────────────────────────────────
HEYGEN_API_KEY     = os.getenv("HEYGEN_API_KEY", "")
# Voice IDs — set HEYGEN_VOICE_ID_MALE / FEMALE via env to pick gender per campaign.
# Default female: Sarah (en-US). Male must be set explicitly via env var.
HEYGEN_VOICE_ID        = os.getenv("HEYGEN_VOICE_ID",        "2d5b0e6cf36f460aa7fc47e3eee4ba54")
HEYGEN_VOICE_ID_FEMALE = os.getenv("HEYGEN_VOICE_ID_FEMALE", "2d5b0e6cf36f460aa7fc47e3eee4ba54")
HEYGEN_VOICE_ID_MALE   = os.getenv("HEYGEN_VOICE_ID_MALE",   "")   # Set via env; falls back to HEYGEN_VOICE_ID
# Avatar ID used for instant-video fallback when template has no API variables.
# Set to the Heygen circle-avatar ID used in the template so the same face is used.
HEYGEN_AVATAR_ID       = os.getenv("HEYGEN_AVATAR_ID", "")
HEYGEN_VIDEO_W     = int(os.getenv("HEYGEN_VIDEO_W", "1280"))
HEYGEN_VIDEO_H     = int(os.getenv("HEYGEN_VIDEO_H", "720"))

# Orientation presets — override per campaign via video_orientation field
HEYGEN_ORIENTATION_DIMS = {
    "landscape": (1280, 720),
    "portrait":  (720, 1280),
    "square":    (720, 720),
}
HEYGEN_POLL_SECS   = int(os.getenv("HEYGEN_POLL_SECS", "5"))    # seconds between polls
HEYGEN_TIMEOUT     = int(os.getenv("HEYGEN_TIMEOUT",   "300"))   # max wait per video (s)

# ─── ElevenLabs TTS (optional — used instead of Heygen built-in TTS) ──────────
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
# Model: eleven_multilingual_v2 for multi-language, eleven_monolingual_v1 for English-only
ELEVENLABS_MODEL   = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO")
