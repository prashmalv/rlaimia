"""
Celery App + All Task Definitions for Mia Campaign Engine.

Task architecture for 5-lakh scale:
  • process_chunk_task: processes TASK_CHUNK_SIZE jobs per task (reduces queue overhead)
  • Each chunk runs image + video generation in parallel threads
  • Results written back to DB via synchronous SQLAlchemy (no async in Celery tasks)
  • Progress counter updated in Redis for real-time dashboard updates
"""

import os
import sys
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import config

import ssl as _ssl
from celery import Celery, group, chord
from celery.utils.log import get_task_logger

# ─── Celery App ──────────────────────────────────────────────────────────────
celery_app = Celery(
    "mia_campaign",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
)

# SSL config for Azure Redis Cache (rediss://) — belt-and-suspenders alongside URL param
_redis_ssl_conf = {"ssl_cert_reqs": _ssl.CERT_NONE} if config.CELERY_BROKER_URL.startswith("rediss://") else {}

celery_app.conf.update(
    task_serializer          = "json",
    result_serializer        = "json",
    accept_content           = ["json"],
    timezone                 = "Asia/Kolkata",
    enable_utc               = True,
    task_track_started       = True,
    task_acks_late           = True,          # Don't ack until task completes
    worker_prefetch_multiplier = 1,           # One task at a time per worker
    task_soft_time_limit     = 300,           # 5 min soft limit per chunk task
    task_time_limit          = 360,           # 6 min hard limit
    result_expires           = 86400,         # Results kept for 24h
    beat_schedule            = {},
    task_routes = {
        "mia.generate_images_chunk": {"queue": "images"},
        "mia.generate_videos_chunk": {"queue": "videos"},
        "mia.process_campaign":      {"queue": "default"},
        "mia.finalize_campaign":     {"queue": "default"},
    },
)

if _redis_ssl_conf:
    celery_app.conf.broker_use_ssl    = _redis_ssl_conf
    celery_app.conf.redis_backend_ssl = _redis_ssl_conf

task_logger = get_task_logger(__name__)

# ─── DB (sync, for use inside Celery tasks) ──────────────────────────────────
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker, Session
from backend.app.models import Campaign, Job, JobStatus, CampaignStatus

import re as _re
_sync_db_url = config.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
_sync_db_url = _re.sub(r'[?&]ssl(?:mode)?=[^&]*', '', _sync_db_url).rstrip('?')
_pg_kwargs   = {"sslmode": "require"} if "postgresql" in _sync_db_url else {"check_same_thread": False}
_sync_engine = create_engine(_sync_db_url, connect_args=_pg_kwargs)
SyncSession  = sessionmaker(bind=_sync_engine, autoflush=False, autocommit=False)


def _get_sync_db() -> Session:
    return SyncSession()


# ─── Redis progress counter ──────────────────────────────────────────────────
import redis as redis_lib

_redis_client: Optional[redis_lib.Redis] = None

def _redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(config.REDIS_URL, decode_responses=True)
    return _redis_client


def _incr_campaign_counter(campaign_id: str, field: str, amount: int = 1):
    try:
        _redis().hincrby(f"campaign:{campaign_id}:progress", field, amount)
    except Exception:
        pass


# ─── Task: Process a chunk of image jobs ─────────────────────────────────────

@celery_app.task(name="mia.generate_images_chunk", bind=True, max_retries=2)
def generate_images_chunk(self, jobs: list[dict], campaign_id: str):
    """
    Generate images for a list of job dicts in one Celery task.
    Each job must have: job_id, first_name, lines, persona, age
    """
    from backend.workers.image_worker import process_image_job

    completed = 0
    failed    = 0
    db = _get_sync_db()

    try:
        for job in jobs:
            job_id = job.get("job_id")
            try:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(image_status=JobStatus.PROCESSING, updated_at=datetime.utcnow())
                )
                db.commit()

                results = process_image_job(job, campaign_id)
                # Take first template result for primary URL
                first_result = next(iter(results.values()), {})

                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        image_status=JobStatus.DONE if first_result.get("status") == "done" else JobStatus.FAILED,
                        image_blob_key=first_result.get("blob_key"),
                        image_url=first_result.get("url"),
                        updated_at=datetime.utcnow(),
                    )
                )
                db.commit()
                completed += 1
                _incr_campaign_counter(campaign_id, "images_done")

            except Exception as e:
                task_logger.error(f"Image job {job_id} failed: {e}")
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(image_status=JobStatus.FAILED, error_msg=str(e)[:500], updated_at=datetime.utcnow())
                )
                db.commit()
                failed += 1
                _incr_campaign_counter(campaign_id, "images_failed")

    finally:
        db.close()

    return {"completed": completed, "failed": failed}


# ─── Task: Process a chunk of video jobs ─────────────────────────────────────

@celery_app.task(name="mia.generate_videos_chunk", bind=True, max_retries=2)
def generate_videos_chunk(self, jobs: list[dict], campaign_id: str):
    """
    Generate videos for a list of job dicts in one Celery task.
    """
    from backend.workers.video_worker import process_video_job

    completed = 0
    failed    = 0
    db = _get_sync_db()

    try:
        for job in jobs:
            job_id = job.get("job_id")
            try:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(video_status=JobStatus.PROCESSING, updated_at=datetime.utcnow())
                )
                db.commit()

                results = process_video_job(job, campaign_id)
                first_result = next(iter(results.values()), {})

                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        video_status=JobStatus.DONE if first_result.get("status") == "done" else JobStatus.FAILED,
                        video_blob_key=first_result.get("blob_key"),
                        video_url=first_result.get("url"),
                        updated_at=datetime.utcnow(),
                    )
                )
                db.commit()
                completed += 1
                _incr_campaign_counter(campaign_id, "videos_done")

            except Exception as e:
                task_logger.error(f"Video job {job_id} failed: {e}")
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(video_status=JobStatus.FAILED, error_msg=str(e)[:500], updated_at=datetime.utcnow())
                )
                db.commit()
                failed += 1
                _incr_campaign_counter(campaign_id, "videos_failed")

    finally:
        db.close()

    return {"completed": completed, "failed": failed}


# ─── Task: Kick off entire campaign ──────────────────────────────────────────

@celery_app.task(name="mia.process_campaign", bind=True)
def process_campaign(self, campaign_id: str, person_file: str, template_file: str, phase_override: str = None):
    """
    Master task: reads XLSX, generates all jobs, dispatches chunks to workers.
    """
    import pandas as pd
    from backend.workers.message_worker import load_templates, build_person_record

    db = _get_sync_db()
    try:
        # Update campaign to RUNNING
        db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.RUNNING, updated_at=datetime.utcnow())
        )
        db.commit()

        # Load templates
        load_templates(template_file)

        # Read persons
        people_df = pd.read_excel(person_file)
        people_df.columns = people_df.columns.str.strip()
        name_col = next(
            (c for c in people_df.columns if c.lower().strip() in ["name", "full name", "customer name"]),
            people_df.columns[0]
        )

        # Build job records
        all_jobs = []
        for _, row in people_df.iterrows():
            record = build_person_record(row.to_dict(), name_col, template_file, phase_override)
            if not record:
                continue
            job_id = str(uuid.uuid4())
            record["job_id"] = job_id

            # Insert into DB
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
                image_status=JobStatus.QUEUED,
                video_status=JobStatus.QUEUED,
            )
            db.add(db_job)
            all_jobs.append(record)

        db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(total_jobs=len(all_jobs))
        )
        db.commit()

        task_logger.info(f"Campaign {campaign_id}: {len(all_jobs)} jobs created")

        # Dispatch in chunks
        chunk_size = config.TASK_CHUNK_SIZE
        img_tasks  = []
        vid_tasks  = []

        for i in range(0, len(all_jobs), chunk_size):
            chunk = all_jobs[i : i + chunk_size]
            # Serialize only what workers need (not DB objects)
            serializable = [
                {k: v for k, v in j.items() if k != "lines" or True}
                for j in chunk
            ]
            img_tasks.append(generate_images_chunk.s(serializable, campaign_id))
            vid_tasks.append(generate_videos_chunk.s(serializable, campaign_id))

        # Launch all image + video chunks concurrently
        group(img_tasks + vid_tasks).delay()

        task_logger.info(f"Campaign {campaign_id}: dispatched {len(img_tasks)} image chunks + {len(vid_tasks)} video chunks")

    except Exception as e:
        task_logger.error(f"Campaign {campaign_id} failed: {e}")
        db.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(status=CampaignStatus.FAILED, error_msg=str(e)[:500])
        )
        db.commit()
    finally:
        db.close()


# ─── Task: Update campaign progress (called periodically) ────────────────────

@celery_app.task(name="mia.sync_campaign_progress")
def sync_campaign_progress(campaign_id: str):
    """Sync Redis progress counters → DB campaign record."""
    db = _get_sync_db()
    try:
        progress = _redis().hgetall(f"campaign:{campaign_id}:progress")
        images_done   = int(progress.get("images_done",   0))
        videos_done   = int(progress.get("videos_done",   0))
        images_failed = int(progress.get("images_failed", 0))
        videos_failed = int(progress.get("videos_failed", 0))

        completed = min(images_done, videos_done)

        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if campaign and campaign.total_jobs > 0:
            pct = round(completed / campaign.total_jobs * 100, 1)
            status = CampaignStatus.COMPLETED if completed >= campaign.total_jobs else CampaignStatus.RUNNING
            db.execute(
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
            db.commit()
    finally:
        db.close()
