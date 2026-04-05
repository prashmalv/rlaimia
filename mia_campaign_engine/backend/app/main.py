"""
Mia Campaign Engine — FastAPI Application Entry Point
Serves API + Dashboard UI (Jinja2 templates)
"""

import sys
import hmac
import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config
from backend.app.database import init_db
from backend.app.routers.jobs      import router as jobs_router
from backend.app.routers.files     import router as files_router
from backend.app.routers.health    import router as health_router
from backend.app.routers.templates import router as templates_router

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Mia Campaign Engine",
    description="Bulk Birthday & Anniversary Greeting Generator — Mia by Tanishq",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Static Files ─────────────────────────────────────────────────────────────
static_dir = Path(__file__).parent.parent.parent / "frontend" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ─── Templates ───────────────────────────────────────────────────────────────
templates_dir = Path(__file__).parent.parent.parent / "frontend" / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# ─── Routers ─────────────────────────────────────────────────────────────────
app.include_router(jobs_router)
app.include_router(files_router)
app.include_router(health_router)
app.include_router(templates_router)

# ─── Startup ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    import os
    logger.info("Starting Mia Campaign Engine...")
    await init_db()
    try:
        from backend.app.azure_storage import ensure_containers
        ensure_containers()
    except Exception as e:
        logger.warning(f"Azure container setup skipped: {e}")

    # Verify image/video templates exist and are readable — shows up in Azure logs
    try:
        from backend.workers.image_worker import TEMPLATE_CONFIGS as IMG_CONFIGS
        from PIL import Image
        for tid, cfg in IMG_CONFIGS.items():
            path = cfg["image_path"]
            if os.path.exists(path):
                try:
                    img = Image.open(path)
                    logger.info(f"[startup] Image template '{tid}': OK — {img.size} {img.mode} @ {path}")
                except Exception as e:
                    logger.error(f"[startup] Image template '{tid}': CORRUPT — {type(e).__name__}: {e} @ {path}")
            else:
                logger.error(f"[startup] Image template '{tid}': MISSING @ {path}")
    except Exception as e:
        logger.warning(f"[startup] Template check failed: {e}")

    try:
        from backend.workers.video_worker import VIDEO_TEMPLATE_CONFIGS
        for tid, cfg in VIDEO_TEMPLATE_CONFIGS.items():
            path = cfg.get("path", "")
            exists = os.path.exists(path)
            logger.info(f"[startup] Video template '{tid}': {'OK' if exists else 'MISSING'} @ {path}")
    except Exception as e:
        logger.warning(f"[startup] Video template check failed: {e}")

    logger.info("Startup complete.")


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _make_token(role: str) -> str:
    """Create a signed token: role:hmac_hex."""
    sig = hmac.new(config.SECRET_KEY.encode(), f"{role}:{config.SECRET_KEY}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{role}:{sig}"

def _verify_token(token: str):
    """Verify token and return role, or None if invalid."""
    if not config.AUTH_ENABLED:
        return "admin"   # auth disabled → everyone is admin
    try:
        role, sig = token.rsplit(":", 1)
        expected = _make_token(role)
        _, expected_sig = expected.rsplit(":", 1)
        if hmac.compare_digest(sig, expected_sig) and role in ("admin", "viewer"):
            return role
    except Exception:
        pass
    return None


# ─── Auth API ────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str

@app.post("/api/auth/login")
async def auth_login(body: LoginRequest):
    if not config.AUTH_ENABLED:
        token = _make_token("admin")
        return {"role": "admin", "token": token}
    if config.ADMIN_PASSWORD and body.password == config.ADMIN_PASSWORD:
        token = _make_token("admin")
        return {"role": "admin", "token": token}
    if config.VIEWER_PASSWORD and body.password == config.VIEWER_PASSWORD:
        token = _make_token("viewer")
        return {"role": "viewer", "token": token}
    return JSONResponse({"error": "Invalid password"}, status_code=401)

@app.get("/api/auth/me")
async def auth_me(request: Request):
    if not config.AUTH_ENABLED:
        return {"role": "admin", "auth_enabled": False}
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    role = _verify_token(token)
    if not role:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return {"role": role, "auth_enabled": True}


# ─── Dashboard Pages ─────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not config.AUTH_ENABLED:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/")
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail(request: Request, campaign_id: str):
    return templates.TemplateResponse("campaign_detail.html", {
        "request": request,
        "campaign_id": campaign_id,
    })


@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    return templates.TemplateResponse("files.html", {"request": request})


@app.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request):
    return templates.TemplateResponse("template_editor.html", {"request": request})


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    return templates.TemplateResponse("reports.html", {"request": request})
