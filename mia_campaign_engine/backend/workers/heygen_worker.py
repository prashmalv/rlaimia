"""
Heygen AI Avatar Video Worker
Generates personalized talking-head videos using the Heygen API.

Flow per job:
  1. Campaign creation: upload avatar image → get talking_photo_id (stored on Campaign)
  2. Per job: POST /v2/video/generate with talking_photo_id + personalized script
              OR POST /v2/template/{id}/generate if template mode
  3. Poll GET /v1/video_status.get until completed
  4. Download MP4 → upload to Azure Blob → store URL in DB

Voice modes (priority order):
  a. ElevenLabs voice ID set → generate TTS audio → upload → pass as audio_url to Heygen
  b. Per-campaign heygen_voice_id set → use as Heygen voice_id
  c. Default: env vars HEYGEN_VOICE_ID_MALE / HEYGEN_VOICE_ID_FEMALE based on gender
"""

import io
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

HEYGEN_API_BASE    = "https://api.heygen.com"
HEYGEN_UPLOAD_BASE = "https://upload.heygen.com"   # separate subdomain for file uploads
ELEVENLABS_API_BASE = "https://api.elevenlabs.io"


# ─── Background removal (optional — needs rembg installed) ───────────────────

def remove_background(image_bytes: bytes) -> bytes:
    """
    Remove background from an image using rembg.
    Returns transparent PNG bytes.
    Falls back to original bytes if rembg is not installed.
    """
    try:
        from rembg import remove as rembg_remove
        result = rembg_remove(image_bytes)
        logger.info("[heygen] rembg: background removed successfully")
        return result
    except ImportError:
        logger.warning("[heygen] rembg not installed — skipping background removal. "
                       "Add rembg to requirements.txt to enable.")
        return image_bytes
    except Exception as e:
        logger.warning(f"[heygen] rembg failed ({e}) — using original image")
        return image_bytes


# ─── ElevenLabs TTS ───────────────────────────────────────────────────────────

def elevenlabs_tts(text: str, voice_id: str) -> bytes:
    """
    Generate speech audio from ElevenLabs TTS.
    Returns MP3 bytes.
    """
    api_key = config.ELEVENLABS_API_KEY
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not configured")

    # Trim to ElevenLabs limit (5000 chars for free / 10000 for paid)
    if len(text) > 4900:
        text = text[:4897] + "..."

    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{ELEVENLABS_API_BASE}/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": config.ELEVENLABS_MODEL,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
        )

    if resp.is_error:
        raise RuntimeError(
            f"ElevenLabs TTS failed [{resp.status_code}]: {resp.text[:300]}"
        )

    logger.info(f"[heygen] ElevenLabs TTS: generated {len(resp.content):,} bytes")
    return resp.content


def upload_elevenlabs_audio(audio_bytes: bytes, campaign_id: str, job_id: str) -> str:
    """Upload ElevenLabs audio to Azure Blob and return a long-lived SAS URL."""
    from backend.app.azure_storage import upload_bytes, get_sas_url
    import config as _cfg

    blob_key = f"heygen-audio/{campaign_id}/{job_id}_tts.mp3"
    upload_bytes(
        audio_bytes, blob_key,
        container=_cfg.AZURE_BLOB_CONTAINER_VID,
        content_type="audio/mpeg",
    )
    # 24h SAS — Heygen fetches this during rendering
    return get_sas_url(blob_key, container=_cfg.AZURE_BLOB_CONTAINER_VID, hours=24)


# ─── Upload avatar image → get talking_photo_id ───────────────────────────────

def upload_talking_photo(image_path: str, remove_bg: bool = False) -> str:
    """
    Upload a local image file to Heygen and return the talking_photo_id.
    If remove_bg=True, background is removed via rembg before uploading.
    This ID is reused for all jobs in the same campaign.
    """
    api_key = config.HEYGEN_API_KEY
    if not api_key:
        raise RuntimeError("HEYGEN_API_KEY not configured — set it via env var")

    with open(image_path, "rb") as f:
        image_data = f.read()

    if remove_bg:
        image_data = remove_background(image_data)
        ct = "image/png"   # rembg always outputs PNG
    else:
        ext = Path(image_path).suffix.lower()
        ct  = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")

    # Heygen upload uses upload.heygen.com with raw binary body (not multipart)
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{HEYGEN_UPLOAD_BASE}/v1/talking_photo",
            headers={"X-Api-Key": api_key, "Content-Type": ct},
            content=image_data,
        )

    if resp.is_error:
        raise RuntimeError(
            f"Heygen talking_photo upload failed [{resp.status_code}]: {resp.text[:500]}"
        )

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


# ─── Build voice payload ───────────────────────────────────────────────────────

def _build_voice_payload(
    script: str,
    voice_id: Optional[str],
    audio_url: Optional[str] = None,
) -> dict:
    """
    Return the Heygen voice object for video/generate payload.
    Priority: audio_url (ElevenLabs) > voice_id > silent fallback.
    """
    if audio_url:
        return {"type": "audio", "audio_url": audio_url}
    return {
        "type": "text",
        "input_text": script,
        "voice_id": voice_id or config.HEYGEN_VOICE_ID,
        "speed": 1.0,
    }


# ─── Submit talking-photo video generation ────────────────────────────────────

def _orientation_dims(orientation: Optional[str]) -> tuple[int, int]:
    """Return (width, height) for a given orientation string."""
    return config.HEYGEN_ORIENTATION_DIMS.get(
        (orientation or "landscape").lower(),
        (config.HEYGEN_VIDEO_W, config.HEYGEN_VIDEO_H),
    )


def create_heygen_video(
    script: str,
    talking_photo_id: str,
    voice_id: Optional[str] = None,
    bg_image_url: Optional[str] = None,
    audio_url: Optional[str] = None,
    orientation: Optional[str] = None,
) -> str:
    """
    Submit a Heygen video generation job using a talking_photo avatar.
    - audio_url: ElevenLabs pre-generated audio (overrides voice_id)
    - bg_image_url: background image URL (overrides solid black)
    Returns video_id for polling.
    """
    api_key = config.HEYGEN_API_KEY
    if not api_key:
        raise RuntimeError("HEYGEN_API_KEY not configured")

    background = (
        {"type": "image", "url": bg_image_url}
        if bg_image_url
        else {"type": "color", "value": "#000000"}
    )

    w, h = _orientation_dims(orientation)
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "talking_photo",
                    "talking_photo_id": talking_photo_id,
                },
                "voice": _build_voice_payload(script, voice_id, audio_url),
                "background": background,
            }
        ],
        "dimension": {"width": w, "height": h},
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{HEYGEN_API_BASE}/v2/video/generate",
            headers={"X-Api-Key": api_key},
            json=payload,
        )

    if resp.is_error:
        raise RuntimeError(
            f"Heygen video/generate failed [{resp.status_code}]: {resp.text[:500]}"
        )

    data = resp.json()
    logger.info(f"[heygen] video/generate response: {data}")
    video_id = data.get("data", {}).get("video_id") or data.get("video_id")
    if not video_id:
        raise RuntimeError(f"Heygen: no video_id in response: {data}")

    logger.info(f"[heygen] Created video job → video_id={video_id}")
    return video_id


# ─── Instant avatar video (bypasses template, uses avatar_id directly) ───────

def create_heygen_avatar_video(
    script: str,
    avatar_id: str,
    voice_id: Optional[str] = None,
    audio_url: Optional[str] = None,
    orientation: Optional[str] = None,
) -> str:
    """
    Generate a Heygen video using a circle/system avatar (type: "avatar").
    Used when a template has no API variables so the script cannot be injected;
    this API call uses the same avatar_id and fully personalizes the voice.
    Returns video_id for polling.
    """
    api_key = config.HEYGEN_API_KEY
    if not api_key:
        raise RuntimeError("HEYGEN_API_KEY not configured")

    w, h = _orientation_dims(orientation)
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": _build_voice_payload(script, voice_id, audio_url),
            }
        ],
        "dimension": {"width": w, "height": h},
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{HEYGEN_API_BASE}/v2/video/generate",
            headers={"X-Api-Key": api_key},
            json=payload,
        )

    if resp.is_error:
        raise RuntimeError(
            f"Heygen avatar video/generate failed [{resp.status_code}]: {resp.text[:500]}"
        )

    data = resp.json()
    logger.info(f"[heygen] avatar video/generate response: {data}")
    video_id = data.get("data", {}).get("video_id") or data.get("video_id")
    if not video_id:
        raise RuntimeError(f"Heygen: no video_id in response: {data}")

    logger.info(f"[heygen] Created avatar video job → video_id={video_id}")
    return video_id


# ─── Template-based video generation ─────────────────────────────────────────

def _detect_text_variable(template_data: dict) -> Optional[str]:
    """
    Given a Heygen GET /v2/template/{id} response, find the variable name
    that holds the script/voice text.
    Returns the variable name, or None if detection fails.
    """
    variables = (
        template_data.get("data", {}).get("variables")
        or template_data.get("variables")
        or {}
    )
    if not variables:
        return None

    # Prefer variables whose type is "voice" or "text"
    for name, meta in variables.items():
        vtype = (meta.get("type") or "").lower()
        if vtype in ("voice", "text"):
            logger.info(f"[heygen] template var detected: '{name}' (type={vtype})")
            return name

    # Fallback: first variable
    first = next(iter(variables))
    logger.info(f"[heygen] template var fallback: '{first}'")
    return first


def create_heygen_video_from_template(
    script: str,
    template_id: str,
    voice_id: Optional[str] = None,
    audio_url: Optional[str] = None,
    orientation: Optional[str] = None,
    first_name: Optional[str] = None,
) -> str:
    """
    Generate a video from a Heygen Studio template (V3 variable format).
    The template controls avatar, background, and layout.

    Injects ALL template variables intelligently:
      - Variables named first_name/name/customer_name (text type) → person's first name
      - Variables of type "voice" or named script/message/body → personalized script
      - If audio_url (ElevenLabs) provided → voice variables get audio_url instead

    Returns video_id for polling.
    """
    api_key = config.HEYGEN_API_KEY
    if not api_key:
        raise RuntimeError("HEYGEN_API_KEY not configured")

    # Step 1: fetch template to discover variable names + types
    with httpx.Client(timeout=30) as client:
        tmpl_resp = client.get(
            f"{HEYGEN_API_BASE}/v2/template/{template_id}",
            headers={"X-Api-Key": api_key},
        )
    if tmpl_resp.is_error:
        raise RuntimeError(
            f"Heygen template fetch failed [{tmpl_resp.status_code}]: {tmpl_resp.text[:300]}"
        )

    tmpl_data = tmpl_resp.json()

    # Get template variables (may be empty if template has everything baked in)
    raw_vars = (
        tmpl_data.get("data", {}).get("variables")
        or tmpl_data.get("variables")
        or {}
    )
    if not raw_vars:
        # Template has no registered API variables — Heygen rejects any variable injection.
        # If HEYGEN_AVATAR_ID is configured, bypass the template voice by calling the
        # instant-avatar API directly with the personalized script.
        # This uses the same avatar face but lets us inject any text for the voice.
        avatar_id = config.HEYGEN_AVATAR_ID
        if avatar_id:
            logger.info(
                f"[heygen] template {template_id}: no API vars — using avatar API "
                f"(avatar_id={avatar_id[:12]}...) for personalized voice"
            )
            return create_heygen_avatar_video(
                script, avatar_id, voice_id=voice_id,
                audio_url=audio_url, orientation=orientation,
            )

        # No avatar_id configured — generate template as-is (avatar speaks baked-in script).
        # Name is still shown in Scene 1 via FFmpeg overlay.
        logger.info(f"[heygen] template {template_id}: no API vars, no HEYGEN_AVATAR_ID — generating as-is")
        payload: dict = {"caption": False, "variables": {}}
        with httpx.Client(timeout=30) as _c:
            resp = _c.post(
                f"{HEYGEN_API_BASE}/v2/template/{template_id}/generate",
                headers={"X-Api-Key": api_key},
                json=payload,
            )
        if resp.is_error:
            raise RuntimeError(f"Heygen template/generate failed [{resp.status_code}]: {resp.text[:500]}")
        data = resp.json()
        logger.info(f"[heygen] template/generate response: {data}")
        video_id = data.get("data", {}).get("video_id") or data.get("video_id")
        if not video_id:
            raise RuntimeError(f"Heygen template: no video_id in response: {data}")
        logger.info(f"[heygen] Template video job created -> video_id={video_id}")
        return video_id

    # Step 2: build V3 variables payload — handle ALL template variables
    # Name patterns for known slots
    _FIRST_NAME_VARS = {"first_name", "firstname", "name", "customer_name", "person_name"}
    _SCRIPT_VARS     = {"script", "text", "message", "body", "content", "voice_text", "speech"}

    variables: dict = {}
    for var_name, var_meta in raw_vars.items():
        var_type  = (var_meta.get("type") or "text").lower()
        name_key  = var_name.lower()

        if name_key in _FIRST_NAME_VARS:
            # ── First-name slot — use template's actual type (voice or text) ──
            # If voice type: avatar speaks the name; needs content + voice_id
            # If text type: displayed as text overlay; needs content only
            props: dict = {"content": first_name or ""}
            if var_type == "voice":
                props["voice_id"] = voice_id or config.HEYGEN_VOICE_ID or ""
            variables[var_name] = {
                "name": var_name,
                "type": var_type,
                "properties": props,
            }
            logger.info(f"[heygen] var '{var_name}' (type={var_type}) → first_name='{first_name}'")

        elif var_type == "voice" or name_key in _SCRIPT_VARS:
            # ── Script / voice slot ──────────────────────────────────────────
            if audio_url:
                # ElevenLabs pre-generated audio — keep type as "voice" with audio_url
                variables[var_name] = {
                    "name": var_name,
                    "type": "voice",
                    "properties": {"audio_url": audio_url},
                }
            else:
                props2: dict = {"content": script}
                props2["voice_id"] = voice_id or config.HEYGEN_VOICE_ID or ""
                variables[var_name] = {
                    "name": var_name,
                    "type": var_type,
                    "properties": props2,
                }
            logger.info(f"[heygen] var '{var_name}' (type={var_type}) → script/voice injected")

        else:
            logger.warning(f"[heygen] template var '{var_name}' (type={var_type}) — no mapping, skipping")

    # No dimension override — template defines its own canvas size
    payload: dict = {"caption": False, "variables": variables}

    # Step 3: generate
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{HEYGEN_API_BASE}/v2/template/{template_id}/generate",
            headers={"X-Api-Key": api_key},
            json=payload,
        )

    if resp.is_error:
        raise RuntimeError(
            f"Heygen template/generate failed [{resp.status_code}]: {resp.text[:500]}"
        )

    data = resp.json()
    logger.info(f"[heygen] template/generate response: {data}")
    video_id = data.get("data", {}).get("video_id") or data.get("video_id")
    if not video_id:
        raise RuntimeError(f"Heygen template: no video_id in response: {data}")

    logger.info(f"[heygen] Template video job created → video_id={video_id}")
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
            raise RuntimeError(
                f"Heygen status check failed [{resp.status_code}]: {resp.text[:200]}"
            )

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

    raise TimeoutError(
        f"Heygen video_id={video_id} did not complete within {config.HEYGEN_TIMEOUT}s"
    )


# ─── Post-process: burn first_name text overlay on Scene 1 ───────────────────

def _overlay_name_on_video(
    video_bytes: bytes,
    first_name: str,
    job_id: str,
    scene1_duration: float = 4.0,
) -> bytes:
    """
    Burn first_name text onto Scene 1 (first N seconds) of the Heygen video.

    Matches the Heygen Studio text-element settings exactly:
      Font  : Lato Regular, size 195
      Color : #FFF090 (100% opacity)
      Align : center (horizontal)
      Position on 1080×1920 canvas: x_center=540, y=1013, w=939, h=250

    FFmpeg drawtext uses the video's actual pixel dimensions, which should match
    the Heygen canvas (1080×1920 for portrait HD templates).
    Returns modified video bytes.
    """
    import subprocess
    import tempfile
    import os

    name_escaped = first_name.replace("'", "").replace(":", "").replace("\\", "")

    # Lato Regular matches the Heygen text element font
    font_regular = config.FONT_LATO_REGULAR or config.FONT_PLAYFAIR_BOLD or config.FONT_LATO_ITALIC
    if not font_regular or not os.path.exists(font_regular):
        raise RuntimeError(f"[heygen] name overlay: font not found at {font_regular!r}")

    # Heygen canvas is 1080×1920 for this portrait template.
    # Coordinates from Heygen Studio: fontsize=195, y=1013, center-aligned, color=#FFF090.
    # fontsize must be a plain integer — FFmpeg drawtext does not eval expressions in fontsize
    # on all versions. y uses (h/1920)*1013 so it scales if Heygen outputs at a different res.
    font_size = 195
    vf = (
        f"drawtext=fontfile='{font_regular}'"
        f":text='{name_escaped}'"
        f":x=(w-text_w)/2"
        f":y=(h/1920)*1013"
        f":fontsize={font_size}"
        f":fontcolor=#FFF090"
        f":enable='between(t,0,{scene1_duration})'"
    )
    logger.info(f"[heygen] overlay: font={font_regular!r} size={font_size} name={name_escaped!r}")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fin:
        fin.write(video_bytes)
        in_path = fin.name

    out_path = in_path.replace(".mp4", "_overlay.mp4")

    try:
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-vf", vf,
            "-c:a", "copy",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg overlay failed: {result.stderr.decode()[-300:]}")

        with open(out_path, "rb") as f:
            out_bytes = f.read()

        logger.info(f"[heygen] Job {job_id}: name overlay done — {len(out_bytes):,} bytes")
        return out_bytes
    finally:
        for p in (in_path, out_path):
            try:
                import os
                os.unlink(p)
            except Exception:
                pass


# ─── Full per-job flow ────────────────────────────────────────────────────────

def process_heygen_job(
    job: dict,
    campaign_id: str,
    talking_photo_id: str,
    voice_id: Optional[str] = None,
    bg_image_url: Optional[str] = None,
    video_template_id: Optional[str] = None,
    elevenlabs_voice_id: Optional[str] = None,
    orientation: Optional[str] = None,
) -> dict:
    """
    Full Heygen flow for a single job: create → poll → download → Azure Blob.

    Modes (checked in order):
    1. video_template_id → Heygen Studio template (bg + avatar baked in)
    2. talking_photo_id + bg_image_url → custom background behind avatar
    3. talking_photo_id only → solid black background
    4. elevenlabs_voice_id → ElevenLabs TTS audio injected into any mode above

    Returns:
        {"status": "done",   "url": "...", "blob_key": "...", "video_id": "..."}
     or {"status": "failed", "error": "..."}
    """
    from backend.app.azure_storage import upload_bytes, get_sas_url
    import config as _cfg

    job_id     = job["job_id"]
    first_name = job.get("first_name") or ""

    # Build personalized script — prefer message_text (LLM-generated), then fallback
    if video_template_id:
        # Template mode: personalized Mia birthday script
        script = (
            job.get("message_text")
            or (
                f"Hi, {first_name}. [excited] A very happy birthday from all of us at Mia by Tanishq. "
                f"We hope your day is filled with moments that make your heart happy and a year ahead "
                f"that brings you confidence, joy, and beautiful surprises. [warmly] Gentle reminder "
                f"that you are precious every day. Once again, a very happy birthday from all of us."
            )
        )
    else:
        script = (
            job.get("message_text")
            or job.get("lines", {}).get("body")
            or f"Happy Birthday, {first_name or 'friend'}! "
               f"Wishing you a wonderful day from the Mia team at Tanishq."
        )
    # Trim to Heygen's 1500-char limit
    if len(script) > 1500:
        script = script[:1497] + "..."

    try:
        # ── Generate ElevenLabs audio if voice ID provided ──────────────────
        audio_url: Optional[str] = None
        if elevenlabs_voice_id and _cfg.ELEVENLABS_API_KEY:
            logger.info(f"[heygen] Job {job_id}: generating ElevenLabs TTS (voice={elevenlabs_voice_id})")
            try:
                audio_bytes = elevenlabs_tts(script, elevenlabs_voice_id)
                audio_url   = upload_elevenlabs_audio(audio_bytes, campaign_id, job_id)
                logger.info(f"[heygen] Job {job_id}: ElevenLabs audio uploaded → {audio_url[:60]}...")
            except Exception as e:
                logger.warning(f"[heygen] Job {job_id}: ElevenLabs failed ({e}), falling back to Heygen TTS")

        # ── Submit video ─────────────────────────────────────────────────────
        if video_template_id:
            logger.info(f"[heygen] Job {job_id}: template={video_template_id} orientation={orientation}")
            try:
                video_id = create_heygen_video_from_template(
                    script, video_template_id, voice_id=voice_id,
                    audio_url=audio_url, orientation=orientation,
                    first_name=first_name,
                )
            except RuntimeError as tmpl_err:
                err_str = str(tmpl_err)
                # Template has empty scripts (no variables defined in Heygen Studio) —
                # fall back to talking_photo mode if an avatar ID is available
                if ("empty scripts" in err_str or "empty_scripts" in err_str) and talking_photo_id:
                    logger.warning(
                        f"[heygen] Job {job_id}: template {video_template_id} has empty scripts "
                        f"(no variables defined in Heygen Studio) — falling back to talking_photo mode. "
                        f"To use the template properly, open it in Heygen Studio and add a voice variable."
                    )
                    photo_hint = talking_photo_id[:12]
                    mode = "ElevenLabs audio" if audio_url else ("bg_image" if bg_image_url else "solid_bg")
                    logger.info(f"[heygen] Job {job_id}: fallback photo={photo_hint}... mode={mode} orientation={orientation}")
                    video_id = create_heygen_video(
                        script, talking_photo_id, voice_id=voice_id,
                        bg_image_url=bg_image_url, audio_url=audio_url, orientation=orientation,
                    )
                else:
                    raise
        else:
            photo_hint = (talking_photo_id or "")[:12]
            mode = "ElevenLabs audio" if audio_url else ("bg_image" if bg_image_url else "solid_bg")
            logger.info(f"[heygen] Job {job_id}: photo={photo_hint}... mode={mode} orientation={orientation}")
            video_id = create_heygen_video(
                script, talking_photo_id, voice_id=voice_id,
                bg_image_url=bg_image_url, audio_url=audio_url, orientation=orientation,
            )

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

        # Overlay first_name text on Scene 1 (first few seconds) via FFmpeg
        if first_name and video_template_id:
            try:
                video_bytes = _overlay_name_on_video(video_bytes, first_name, job_id)
            except Exception as ov_err:
                logger.error(f"[heygen] Job {job_id}: text overlay FAILED — {ov_err}", exc_info=True)
                # Continue with original video; name won't show but video is still usable

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
