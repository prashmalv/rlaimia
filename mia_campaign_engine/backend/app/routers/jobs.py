"""
Jobs Router — campaign creation, status polling, file upload.
"""

import os
import uuid
import asyncio
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import config
from backend.app.database import get_db, AsyncSessionLocal
from backend.app.models import (
    Campaign, Job, ImageTemplate, CampaignCreate, CampaignOut, JobOut, PaginatedJobs,
    CampaignStatus, JobStatus
)

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])
logger = logging.getLogger(__name__)


# ─── Background campaign processor (runs in API container — has file access) ──

# ─── Phase 1: Image generation (one job) ─────────────────────────────────────

async def _run_image_for_job(
    job: dict, campaign_id: str, custom_template_config: Optional[dict] = None
) -> bool:
    """Generate image for one job. Updates DB. Returns True on success. Never raises."""
    from backend.workers.image_worker import process_image_job
    job_id = job["job_id"]

    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(
            image_status=JobStatus.PROCESSING, updated_at=datetime.utcnow()
        ))
        await db.commit()

    img_ok, blob_key, url, err_msg = False, None, None, None
    try:
        img_res = await asyncio.to_thread(process_image_job, job, campaign_id, None, custom_template_config)
        img_ok  = isinstance(img_res, dict) and any(v.get("status") == "done" for v in img_res.values())
        best    = next((v for v in img_res.values() if v.get("status") == "done"), {}) if isinstance(img_res, dict) else {}
        blob_key, url = best.get("blob_key"), best.get("url")
        if not img_ok and isinstance(img_res, dict):
            err_msg = " | ".join(
                f"IMG[{t}]: {v.get('error') or 'unknown'}"
                for t, v in img_res.items() if v.get("status") != "done"
            )
        elif not img_ok:
            err_msg = f"IMG: {repr(img_res)}"
    except Exception as e:
        err_msg = f"IMG: {type(e).__name__}: {e}"
        logger.error(f"Job {job_id} image phase error: {e}", exc_info=True)

    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(
            image_status=JobStatus.DONE if img_ok else JobStatus.FAILED,
            image_blob_key=blob_key,
            image_url=url,
            error_msg=(err_msg or "")[:500] if err_msg else None,
            updated_at=datetime.utcnow(),
        ))
        await db.commit()

    try:
        import redis as _redis_lib
        r = _redis_lib.from_url(config.REDIS_URL, decode_responses=True)
        r.hincrby(f"campaign:{campaign_id}:progress", "images_done" if img_ok else "images_failed", 1)
    except Exception:
        pass

    return img_ok


# ─── Phase 2: Video generation (one job) ─────────────────────────────────────

async def _run_video_for_job(job: dict, campaign_id: str) -> bool:
    """Generate FFmpeg video for one job. Updates DB. Returns True on success. Never raises."""
    from backend.workers.video_worker import process_video_job
    job_id = job["job_id"]

    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(
            video_status=JobStatus.PROCESSING, updated_at=datetime.utcnow()
        ))
        await db.commit()

    vid_ok, blob_key, url, err_msg = False, None, None, None
    try:
        vid_res = await asyncio.to_thread(process_video_job, job, campaign_id)
        vid_ok  = isinstance(vid_res, dict) and any(v.get("status") == "done" for v in vid_res.values())
        best    = next((v for v in vid_res.values() if v.get("status") == "done"), {}) if isinstance(vid_res, dict) else {}
        blob_key, url = best.get("blob_key"), best.get("url")
        if not vid_ok and isinstance(vid_res, dict):
            err_msg = " | ".join(
                f"VID[{t}]: {v.get('error') or 'unknown'}"
                for t, v in vid_res.items() if v.get("status") != "done"
            )
        elif not vid_ok:
            err_msg = f"VID: {repr(vid_res)}"
    except Exception as e:
        err_msg = f"VID: {type(e).__name__}: {e}"
        logger.error(f"Job {job_id} video phase error: {e}", exc_info=True)

    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(
            video_status=JobStatus.DONE if vid_ok else JobStatus.FAILED,
            video_blob_key=blob_key,
            video_url=url,
            updated_at=datetime.utcnow(),
        ))
        await db.commit()

    try:
        import redis as _redis_lib
        r = _redis_lib.from_url(config.REDIS_URL, decode_responses=True)
        r.hincrby(f"campaign:{campaign_id}:progress", "videos_done" if vid_ok else "videos_failed", 1)
    except Exception:
        pass

    return vid_ok


# ─── Phase 3: AI Avatar video generation (one job) ───────────────────────────

async def _run_heygen_for_job(
    job: dict, campaign_id: str, talking_photo_id: str, voice_id: Optional[str] = None
) -> bool:
    """Generate AI avatar video for one job. Updates DB. Returns True on success. Never raises."""
    from backend.workers.heygen_worker import process_heygen_job
    job_id = job["job_id"]

    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(
            heygen_video_status=JobStatus.PROCESSING, updated_at=datetime.utcnow()
        ))
        await db.commit()

    hey_ok, blob_key, url, err_msg = False, None, None, None
    try:
        hey_res  = await asyncio.to_thread(process_heygen_job, job, campaign_id, talking_photo_id, voice_id)
        hey_ok   = isinstance(hey_res, dict) and hey_res.get("status") == "done"
        blob_key = hey_res.get("blob_key") if hey_ok else None
        url      = hey_res.get("url")      if hey_ok else None
        if not hey_ok:
            err_msg = f"AI_VID: {hey_res.get('error') or repr(hey_res)}" if isinstance(hey_res, dict) else f"AI_VID: {repr(hey_res)}"
    except Exception as e:
        err_msg = f"AI_VID: {type(e).__name__}: {e}"
        logger.error(f"Job {job_id} AI avatar phase error: {e}", exc_info=True)

    async with AsyncSessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(
            heygen_video_status=JobStatus.DONE if hey_ok else JobStatus.FAILED,
            heygen_video_blob_key=blob_key,
            heygen_video_url=url,
            updated_at=datetime.utcnow(),
        ))
        await db.commit()

    try:
        import redis as _redis_lib
        r = _redis_lib.from_url(config.REDIS_URL, decode_responses=True)
        r.hincrby(f"campaign:{campaign_id}:progress", "heygen_done" if hey_ok else "heygen_failed", 1)
    except Exception:
        pass

    return hey_ok


# ─── Finalize: compute overall job status from per-phase results ──────────────

async def _finalize_job_status(job_id: str, has_heygen: bool) -> None:
    """Read image/video/heygen statuses from DB → compute and save overall job status."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return

        # Only count phases that were actually run (skip SKIPPED ones)
        all_ok = []
        if job.image_status != JobStatus.SKIPPED:
            all_ok.append(job.image_status == JobStatus.DONE)
        if job.video_status != JobStatus.SKIPPED:
            all_ok.append(job.video_status == JobStatus.DONE)
        if has_heygen:
            all_ok.append(job.heygen_video_status == JobStatus.DONE)

        if not all_ok:
            overall = JobStatus.DONE   # all phases were skipped — nothing to fail
        elif all(all_ok):
            overall = JobStatus.DONE
        elif any(all_ok):
            overall = JobStatus.PARTIAL
        else:
            overall = JobStatus.FAILED

        # Accumulate error labels from failed phases
        err_parts = []
        if job.image_status  == JobStatus.FAILED: err_parts.append("image failed")
        if job.video_status  == JobStatus.FAILED: err_parts.append("video failed")
        if has_heygen and job.heygen_video_status == JobStatus.FAILED: err_parts.append("AI avatar failed")

        # Preserve any detailed error already stored on the job
        detail = job.error_msg or ""
        summary = " | ".join(err_parts)
        final_err = (f"{summary} — {detail}" if detail else summary) or None

        await db.execute(update(Job).where(Job.id == job_id).values(
            status=overall,
            error_msg=final_err[:500] if final_err else None,
            updated_at=datetime.utcnow(),
        ))
        await db.commit()


async def _process_campaign_bg(
    campaign_id: str,
    person_file: str,
    template_file: str,
    phase_override: Optional[str],
    talking_photo_id: Optional[str] = None,
    voice_gender: Optional[str] = None,
    generate_images: bool = True,
    generate_videos: bool = True,
    image_template_id: Optional[str] = None,
):
    """
    Reads person XLSX, builds job records, inserts into DB, then runs
    image/video generation directly in the API container (no Celery broker needed).
    """
    import pandas as pd
    from backend.workers.message_worker import load_templates, build_person_record

    async with AsyncSessionLocal() as db:
        try:
            # Mark campaign RUNNING
            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(status=CampaignStatus.RUNNING, updated_at=datetime.utcnow())
            )
            await db.commit()

            # Load templates (None → hardcoded defaults, no file I/O)
            await asyncio.to_thread(load_templates, template_file)

            # Read persons xlsx
            people_df = await asyncio.to_thread(pd.read_excel, person_file, engine="openpyxl")
            people_df.columns = people_df.columns.str.strip()
            name_col = next(
                (c for c in people_df.columns if c.lower().strip() in ["name", "full name", "customer name"]),
                people_df.columns[0],
            )

            # Build job records and insert into DB
            all_jobs = []
            for _, row in people_df.iterrows():
                record = build_person_record(row.to_dict(), name_col, template_file, phase_override)
                if not record:
                    continue
                job_id = str(uuid.uuid4())
                record["job_id"] = job_id

                db_job = Job(
                    id=job_id,
                    campaign_id=campaign_id,
                    person_name=record["person_name"],
                    first_name=record["first_name"],
                    age=record["age"],
                    persona=record["persona"],
                    dob=record["dob"],
                    phase=record["phase"],
                    message_text=record["message_text"],
                    status=JobStatus.QUEUED,
                    image_status=JobStatus.QUEUED  if generate_images else JobStatus.SKIPPED,
                    video_status=JobStatus.QUEUED  if generate_videos else JobStatus.SKIPPED,
                )
                db.add(db_job)
                all_jobs.append(record)

            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(total_jobs=len(all_jobs))
            )
            await db.commit()

            logger.info(f"Campaign {campaign_id}: {len(all_jobs)} jobs created")

            if not all_jobs:
                await db.execute(
                    update(Campaign)
                    .where(Campaign.id == campaign_id)
                    .values(
                        status=CampaignStatus.FAILED,
                        error_msg="No matching jobs created — check persona values match template or verify phase selection",
                    )
                )
                await db.commit()
                return

        except Exception as e:
            logger.error(f"Campaign {campaign_id} setup failed: {e}", exc_info=True)
            try:
                await db.execute(
                    update(Campaign)
                    .where(Campaign.id == campaign_id)
                    .values(status=CampaignStatus.FAILED, error_msg=str(e)[:500])
                )
                await db.commit()
            except Exception:
                pass
            return

    # ── Load custom image template config (if selected) ──────────────────────
    custom_template_config: Optional[dict] = None
    if image_template_id:
        try:
            async with AsyncSessionLocal() as _tdb:
                from sqlalchemy import select as _sel
                _res = await _tdb.execute(_sel(ImageTemplate).where(ImageTemplate.id == image_template_id))
                tmpl = _res.scalar_one_or_none()
            if tmpl:
                import json as _json
                boxes = tmpl.boxes_dict()
                # Build config dict compatible with image_worker.generate_image(custom_config=...)
                custom_template_config = {
                    "image_path": tmpl.local_path or "",
                    "blob_key":   tmpl.blob_key,
                    "heading_box":    {k: boxes["heading"][k]    for k in ("x","y","w","h")} if "heading"    in boxes else {"x":50,"y":50,"w":500,"h":150},
                    "subheading_box": {k: boxes["subheading"][k] for k in ("x","y","w","h")} if "subheading" in boxes else {"x":50,"y":220,"w":500,"h":80},
                    "body_box":       {k: boxes["body"][k]       for k in ("x","y","w","h")} if "body"       in boxes else {"x":50,"y":320,"w":500,"h":120},
                    "cta_box":        {k: boxes["cta"][k]        for k in ("x","y","w","h")} if "cta"        in boxes else {"x":50,"y":460,"w":500,"h":60},
                    "heading_max_pt":    int(boxes.get("heading",    {}).get("max_pt", 64)),
                    "subheading_max_pt": int(boxes.get("subheading", {}).get("max_pt", 32)),
                    "body_max_pt":       int(boxes.get("body",       {}).get("max_pt", 26)),
                    "cta_max_pt":        int(boxes.get("cta",        {}).get("max_pt", 22)),
                    "heading_color":  boxes.get("heading",    {}).get("color", "#ffffff"),
                    "body_color":     boxes.get("body",       {}).get("color", "#ffffff"),
                    "cta_color":      boxes.get("cta",        {}).get("color", "#ffe696"),
                }
                logger.info(f"Campaign {campaign_id}: using custom template '{tmpl.name}' (id={image_template_id})")
            else:
                logger.warning(f"Campaign {campaign_id}: image_template_id={image_template_id} not found — using built-in templates")
        except Exception as e:
            logger.error(f"Campaign {campaign_id}: failed to load custom template: {e}")

    # ── Guard: avatar-only campaign but talking_photo_id is None → fail early ──
    if not generate_images and not generate_videos and not talking_photo_id:
        async with AsyncSessionLocal() as db:
            await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(
                status=CampaignStatus.FAILED,
                error_msg="AI Avatar setup failed — HEYGEN_API_KEY not set or avatar upload error. "
                          "Check server logs and environment variables.",
                updated_at=datetime.utcnow(),
            ))
            await db.commit()
        logger.error(f"Campaign {campaign_id}: avatar-only but talking_photo_id=None — marking FAILED")
        return

    # ── Resolve voice_id for Heygen (if avatar requested) ────────────────────
    voice_id: Optional[str] = None
    if talking_photo_id:
        voice_id = (
            config.HEYGEN_VOICE_ID_MALE or config.HEYGEN_VOICE_ID
            if voice_gender == "male"
            else config.HEYGEN_VOICE_ID_FEMALE or config.HEYGEN_VOICE_ID
        )

    # ── Phase 1: Images — all jobs in parallel ────────────────────────────────
    if generate_images:
        logger.info(f"Campaign {campaign_id}: Phase 1 — images ({len(all_jobs)} jobs)")
        img_sem = asyncio.Semaphore(4)
        async def _img(job):
            async with img_sem:
                return await _run_image_for_job(job, campaign_id, custom_template_config)
        await asyncio.gather(*[_img(j) for j in all_jobs])
    else:
        logger.info(f"Campaign {campaign_id}: Phase 1 — images SKIPPED (not selected)")

    # ── Phase 2: Videos — all jobs in parallel ────────────────────────────────
    if generate_videos:
        logger.info(f"Campaign {campaign_id}: Phase 2 — videos ({len(all_jobs)} jobs)")
        vid_sem = asyncio.Semaphore(2)
        async def _vid(job):
            async with vid_sem:
                return await _run_video_for_job(job, campaign_id)
        await asyncio.gather(*[_vid(j) for j in all_jobs])
    else:
        logger.info(f"Campaign {campaign_id}: Phase 2 — videos SKIPPED (not selected)")

    # ── Phase 3: AI Avatar Videos — all jobs in parallel (if enabled) ─────────
    if talking_photo_id:
        logger.info(f"Campaign {campaign_id}: Phase 3 — AI avatar videos ({len(all_jobs)} jobs)")
        hey_sem = asyncio.Semaphore(2)
        async def _hey(job):
            async with hey_sem:
                return await _run_heygen_for_job(job, campaign_id, talking_photo_id, voice_id)
        await asyncio.gather(*[_hey(j) for j in all_jobs])

    # ── Finalize: compute overall status per job from phase results ───────────
    await asyncio.gather(*[
        _finalize_job_status(j["job_id"], bool(talking_photo_id)) for j in all_jobs
    ])

    # ── Mark campaign COMPLETED ───────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        try:
            from sqlalchemy import func as _func
            done_count = (await db.execute(
                select(_func.count(Job.id))
                .where(Job.campaign_id == campaign_id, Job.status == JobStatus.DONE)
            )).scalar() or 0
            partial_count = (await db.execute(
                select(_func.count(Job.id))
                .where(Job.campaign_id == campaign_id, Job.status == JobStatus.PARTIAL)
            )).scalar() or 0
            fail_count = (await db.execute(
                select(_func.count(Job.id))
                .where(Job.campaign_id == campaign_id, Job.status == JobStatus.FAILED)
            )).scalar() or 0
            total = len(all_jobs)
            # Count DONE + PARTIAL as "completed" for progress display
            completed = done_count + partial_count
            pct = round(completed / total * 100, 1) if total else 0
            final_status = (
                CampaignStatus.COMPLETED if done_count == total
                else CampaignStatus.COMPLETED if completed > 0
                else CampaignStatus.FAILED
            )
            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(
                    status=final_status,
                    completed_jobs=completed,
                    failed_jobs=fail_count,
                    progress_pct=pct,
                    updated_at=datetime.utcnow(),
                )
            )
            await db.commit()
            logger.info(f"Campaign {campaign_id}: done={done_count} partial={partial_count} failed={fail_count}")
        except Exception as e:
            logger.error(f"Campaign {campaign_id} finalize failed: {e}")


# ─── Upload + Create Campaign ────────────────────────────────────────────────

@router.post("/campaign", response_model=CampaignOut)
async def create_campaign(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    event_type: str = Form("birthday"),
    person_file: UploadFile = File(...),
    template_file: Optional[UploadFile] = File(None),
    phase_override: Optional[str] = Form(None),     # T_DAY | T_MINUS_10 | ALL
    generate_images: Optional[str] = Form(None),    # "true" | "false"
    generate_videos: Optional[str] = Form(None),    # "true" | "false"
    generate_avatar: Optional[str] = Form(None),    # "true" | "false"
    image_template_id: Optional[str] = Form(None),  # ID of selected image template
    avatar_file: Optional[UploadFile] = File(None), # Heygen avatar image (jpg/png)
    heygen_avatar_id: Optional[str] = Form(None),   # Pre-existing Heygen talking_photo_id
    avatar_voice_gender: Optional[str] = Form(None), # "male" | "female"
    db: AsyncSession = Depends(get_db),
):
    """
    Upload person XLSX + optional template XLSX and start a campaign.
    Optionally upload an avatar image (or provide a pre-registered avatar ID) to generate
    AI avatar videos for every person in the campaign.
    """
    # Resolve media type selections (checkbox sends "true"; absence = default True for img/vid, False for avatar)
    gen_images = generate_images != "false"   # True unless explicitly "false"
    gen_videos = generate_videos != "false"   # True unless explicitly "false"
    gen_avatar = generate_avatar == "true"    # False unless explicitly "true"

    campaign_id = str(uuid.uuid4())
    upload_dir  = config.UPLOADS_DIR / campaign_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Save person file
    person_path = upload_dir / "persons.xlsx"
    with open(person_path, "wb") as f:
        shutil.copyfileobj(person_file.file, f)

    # Save template file (or signal to use hardcoded defaults)
    tmpl_path_str: Optional[str] = None
    if template_file and template_file.filename:
        tmpl_path = upload_dir / "templates.xlsx"
        with open(tmpl_path, "wb") as f:
            shutil.copyfileobj(template_file.file, f)
        tmpl_path_str = str(tmpl_path)

    # ── Heygen: resolve talking_photo_id (only when gen_avatar is selected) ──
    talking_photo_id: Optional[str] = (heygen_avatar_id or None) if gen_avatar else None

    if gen_avatar and avatar_file and avatar_file.filename and not talking_photo_id:
        # Save the avatar image locally
        ext = Path(avatar_file.filename).suffix or ".jpg"
        avatar_path = upload_dir / f"avatar{ext}"
        with open(avatar_path, "wb") as f:
            shutil.copyfileobj(avatar_file.file, f)

        if config.HEYGEN_API_KEY:
            try:
                from backend.workers.heygen_worker import upload_talking_photo
                talking_photo_id = await asyncio.to_thread(upload_talking_photo, str(avatar_path))
                logger.info(f"Campaign {campaign_id}: Heygen avatar uploaded → {talking_photo_id}")
            except Exception as e:
                upload_err = str(e)
                logger.error(f"Campaign {campaign_id}: Heygen avatar upload failed: {upload_err}")
                if not gen_images and not gen_videos:
                    # Avatar-only campaign — fail immediately with visible error
                    raise HTTPException(status_code=400,
                        detail=f"AI Avatar upload to Heygen failed: {upload_err[:200]}. "
                               "Check HEYGEN_API_KEY and voice IDs, or also select Image/Video generation.")
                # Otherwise non-fatal — images/videos will still run
        else:
            if not gen_images and not gen_videos:
                raise HTTPException(status_code=400,
                    detail="HEYGEN_API_KEY is not configured on this server. "
                           "Set it as an environment variable to enable AI Avatar generation.")
            logger.warning(f"Campaign {campaign_id}: avatar_file provided but HEYGEN_API_KEY not set — skipping Heygen")

    # Create DB record
    campaign = Campaign(
        id=campaign_id,
        name=name,
        event_type=event_type,
        person_file=str(person_path),
        template_file=tmpl_path_str or "default",
        status=CampaignStatus.PENDING,
        generate_images=gen_images,
        generate_videos=gen_videos,
        heygen_talking_photo_id=talking_photo_id,
        avatar_voice_gender=avatar_voice_gender or None,
        image_template_id=image_template_id or None,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    # Schedule background processing
    background_tasks.add_task(
        _process_campaign_bg,
        campaign_id,
        str(person_path),
        tmpl_path_str,
        phase_override,
        talking_photo_id,
        avatar_voice_gender or None,
        gen_images,
        gen_videos,
        image_template_id or None,
    )

    return CampaignOut.model_validate(campaign)


# ─── List Campaigns ───────────────────────────────────────────────────────────

@router.get("/campaign", response_model=list[CampaignOut])
async def list_campaigns(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Campaign).order_by(Campaign.created_at.desc()).limit(limit).offset(offset)
    )
    return [CampaignOut.model_validate(c) for c in result.scalars().all()]


# ─── Get Campaign ─────────────────────────────────────────────────────────────

@router.get("/campaign/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    # Sync progress from Redis inline (avoid dispatching to unmonitored Celery queue)
    try:
        import redis as _redis_lib
        r = _redis_lib.from_url(config.REDIS_URL, decode_responses=True)
        progress = r.hgetall(f"campaign:{campaign_id}:progress")
        if progress and campaign.total_jobs and campaign.total_jobs > 0:
            images_done   = int(progress.get("images_done",   0))
            videos_done   = int(progress.get("videos_done",   0))
            images_failed = int(progress.get("images_failed", 0))
            videos_failed = int(progress.get("videos_failed", 0))
            completed = min(images_done, videos_done)
            pct = round(completed / campaign.total_jobs * 100, 1)
            status = CampaignStatus.COMPLETED if completed >= campaign.total_jobs else CampaignStatus.RUNNING
            await db.execute(
                update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(
                    completed_jobs=completed,
                    failed_jobs=max(images_failed, videos_failed),
                    progress_pct=pct,
                    status=status,
                    updated_at=datetime.utcnow(),
                )
            )
            await db.commit()
            await db.refresh(campaign)
    except Exception:
        pass

    return CampaignOut.model_validate(campaign)


# ─── List Jobs ────────────────────────────────────────────────────────────────

@router.get("/campaign/{campaign_id}/jobs", response_model=PaginatedJobs)
async def list_jobs(
    campaign_id: str,
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size
    q = select(Job).where(Job.campaign_id == campaign_id)
    if status:
        q = q.where(Job.status == status)

    count_q = select(func.count()).where(Job.campaign_id == campaign_id)
    if status:
        count_q = count_q.where(Job.status == status)

    total   = (await db.execute(count_q)).scalar()
    results = (await db.execute(q.offset(offset).limit(page_size).order_by(Job.created_at))).scalars().all()

    return PaginatedJobs(
        total=total,
        page=page,
        page_size=page_size,
        items=[JobOut.model_validate(j) for j in results],
    )


# ─── Get single job ───────────────────────────────────────────────────────────

@router.get("/job/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    return JobOut.model_validate(job)


# ─── Pause / Resume / Cancel campaign ────────────────────────────────────────

@router.post("/campaign/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id)
        .values(status=CampaignStatus.PAUSED, updated_at=datetime.utcnow())
    )
    await db.commit()
    return {"status": "paused"}


@router.post("/campaign/{campaign_id}/resume")
async def resume_campaign(
    background_tasks: BackgroundTasks,
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    tmpl = campaign.template_file if campaign.template_file and campaign.template_file != "default" else None
    background_tasks.add_task(
        _process_campaign_bg,
        campaign_id,
        campaign.person_file,
        tmpl,
        None,
        campaign.heygen_talking_photo_id,
        campaign.avatar_voice_gender,
        campaign.generate_images if campaign.generate_images is not None else True,
        campaign.generate_videos if campaign.generate_videos is not None else True,
    )

    await db.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id)
        .values(status=CampaignStatus.RUNNING, updated_at=datetime.utcnow())
    )
    await db.commit()
    return {"status": "resumed"}


# ─── Campaign stats for dashboard ────────────────────────────────────────────

@router.get("/campaign/{campaign_id}/stats")
async def campaign_stats(campaign_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")

    # Per-status job counts
    rows = (await db.execute(
        select(Job.status, func.count(Job.id))
        .where(Job.campaign_id == campaign_id)
        .group_by(Job.status)
    )).all()

    status_counts = {row[0]: row[1] for row in rows}
    img_done = (await db.execute(
        select(func.count(Job.id))
        .where(Job.campaign_id == campaign_id, Job.image_status == JobStatus.DONE)
    )).scalar()
    vid_done = (await db.execute(
        select(func.count(Job.id))
        .where(Job.campaign_id == campaign_id, Job.video_status == JobStatus.DONE)
    )).scalar()
    heygen_done = (await db.execute(
        select(func.count(Job.id))
        .where(Job.campaign_id == campaign_id, Job.heygen_video_status == JobStatus.DONE)
    )).scalar()

    return {
        "campaign_id":    campaign_id,
        "name":           campaign.name,
        "status":         campaign.status,
        "total":          campaign.total_jobs,
        "completed":      campaign.completed_jobs,
        "failed":         campaign.failed_jobs,
        "progress_pct":   campaign.progress_pct,
        "images_done":    img_done,
        "videos_done":    vid_done,
        "heygen_done":    heygen_done,
        "heygen_enabled": bool(campaign.heygen_talking_photo_id),
        "by_status":      status_counts,
    }


# ─── Debug: test image generation inline ─────────────────────────────────────

@router.get("/debug/image-test")
async def debug_image_test():
    """
    Run a quick image generation test (no Azure upload) and return diagnostics.
    Useful for checking if templates and fonts load correctly in the container.
    """
    import os
    import traceback
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    import config as _cfg

    results = {}

    # 1. Template file existence
    try:
        from backend.workers.image_worker import TEMPLATE_CONFIGS
        for tid, tcfg in TEMPLATE_CONFIGS.items():
            path = tcfg["image_path"]
            exists = os.path.exists(path)
            size   = os.path.getsize(path) if exists else None
            results[f"template_{tid}_path"]   = path
            results[f"template_{tid}_exists"] = exists
            results[f"template_{tid}_size"]   = size
            if exists:
                try:
                    from PIL import Image as _PIL
                    img = _PIL.open(path)
                    results[f"template_{tid}_pil"] = f"OK — {img.size} {img.mode}"
                except Exception as e:
                    results[f"template_{tid}_pil"] = f"CORRUPT: {e}"
    except Exception as e:
        results["template_check_error"] = traceback.format_exc()

    # 2. Font existence + loadability
    for fname, fpath in [
        ("playfair_bold",    getattr(_cfg, "FONT_PLAYFAIR_BOLD", "")),
        ("playfair_regular", getattr(_cfg, "FONT_PLAYFAIR_REGULAR", "")),
        ("lato_regular",     getattr(_cfg, "FONT_LATO_REGULAR", "")),
        ("lato_italic",      getattr(_cfg, "FONT_LATO_ITALIC", "")),
        ("garamond_regular", getattr(_cfg, "FONT_GARAMOND_REGULAR", "")),
        ("garamond_italic",  getattr(_cfg, "FONT_GARAMOND_ITALIC", "")),
    ]:
        results[f"font_{fname}_path"]   = fpath
        exists = os.path.exists(fpath) if fpath else False
        results[f"font_{fname}_exists"] = exists
        if exists:
            try:
                from PIL import ImageFont as _IF
                _IF.truetype(fpath, 20)
                results[f"font_{fname}_pil"] = "OK"
            except Exception as e:
                results[f"font_{fname}_pil"] = f"CORRUPT: {e}"

    # 3. Attempt actual image generation (no upload)
    try:
        from backend.workers.image_worker import generate_image
        test_job = {
            "first_name": "Test",
            "lines": {
                "heading":    "HAPPY BIRTHDAY, TEST.",
                "subheading": "Wishing you joy",
                "body":       "From the Mia team",
                "cta":        "Visit us today",
            },
        }
        png_bytes = generate_image(test_job, template_id="template_1")
        results["generate_image_template_1"] = f"OK — {len(png_bytes):,} bytes"
    except Exception as e:
        results["generate_image_template_1"] = f"FAILED: {traceback.format_exc()}"

    try:
        from backend.workers.image_worker import generate_image
        png_bytes = generate_image(test_job, template_id="template_2")
        results["generate_image_template_2"] = f"OK — {len(png_bytes):,} bytes"
    except Exception as e:
        results["generate_image_template_2"] = f"FAILED: {traceback.format_exc()}"

    return results


# ─── Debug: test Heygen API key + voice ID ────────────────────────────────────

@router.get("/debug/heygen-test")
async def debug_heygen_test():
    """
    Verify Heygen API key is valid and voice IDs are reachable.
    Call GET /api/jobs/debug/heygen-test from the browser or curl.
    """
    import traceback
    import httpx
    results = {}

    # 1. Config dump (safe — only shows prefix)
    results["heygen_api_key_set"]    = bool(config.HEYGEN_API_KEY)
    results["heygen_api_key_prefix"] = (config.HEYGEN_API_KEY[:8] + "...") if config.HEYGEN_API_KEY else "NOT SET"
    results["voice_id_default"]      = config.HEYGEN_VOICE_ID or "(empty)"
    results["voice_id_female"]       = config.HEYGEN_VOICE_ID_FEMALE or "(empty)"
    results["voice_id_male"]         = config.HEYGEN_VOICE_ID_MALE or "(not set)"

    if not config.HEYGEN_API_KEY:
        results["status"] = "FAILED — HEYGEN_API_KEY not set"
        return results

    # 2. Ping Heygen API — list talking photos (low-cost read-only call)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.heygen.com/v1/talking_photo.list",
                headers={"X-Api-Key": config.HEYGEN_API_KEY},
            )
        results["api_ping_status"] = resp.status_code
        if resp.is_error:
            results["api_ping_error"] = resp.text[:300]
            results["status"] = "FAILED — API key rejected or Heygen unreachable"
        else:
            data = resp.json()
            photos = data.get("data", {}).get("talking_photo_list", [])
            results["api_ping_ok"]          = True
            results["existing_photo_count"] = len(photos)
            results["existing_photo_ids"]   = [p.get("talking_photo_id") for p in photos[:5]]
            results["status"]               = "OK — API key valid"
    except Exception as e:
        results["api_ping_error"] = str(e)
        results["status"]         = "FAILED — connection error"

    # 3. Check voice IDs via voice list endpoint
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.heygen.com/v2/voices",
                headers={"X-Api-Key": config.HEYGEN_API_KEY},
            )
        if resp.ok:
            voices = resp.json().get("data", {}).get("voices", [])
            voice_ids = {v.get("voice_id") for v in voices}
            results["voice_id_default_valid"] = config.HEYGEN_VOICE_ID in voice_ids if voice_ids else "unknown"
            results["total_voices_available"] = len(voice_ids)
            results["sample_voice_ids"]       = list(voice_ids)[:5]
        else:
            results["voice_list_error"] = f"{resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        results["voice_list_error"] = str(e)

    return results
