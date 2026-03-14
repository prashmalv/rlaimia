"""Health check and dashboard stats router."""

import sys
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from backend.app.database import get_db
from backend.app.models import Campaign, Job, DashboardStats, CampaignStatus, JobStatus

router = APIRouter(prefix="/api", tags=["Health"])


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@router.get("/stats", response_model=DashboardStats)
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    total_campaigns   = (await db.execute(select(func.count(Campaign.id)))).scalar() or 0
    running_campaigns = (await db.execute(
        select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.RUNNING)
    )).scalar() or 0

    total_jobs     = (await db.execute(select(func.count(Job.id)))).scalar() or 0
    completed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.image_status == JobStatus.DONE))).scalar() or 0
    failed_jobs    = (await db.execute(select(func.count(Job.id)).where(Job.image_status == JobStatus.FAILED))).scalar() or 0
    queued_jobs    = (await db.execute(select(func.count(Job.id)).where(Job.image_status == JobStatus.QUEUED))).scalar() or 0
    images_done    = completed_jobs
    videos_done    = (await db.execute(select(func.count(Job.id)).where(Job.video_status == JobStatus.DONE))).scalar() or 0
    ai_avatar_done = (await db.execute(select(func.count(Job.id)).where(Job.heygen_video_status == JobStatus.DONE))).scalar() or 0

    pct = round(completed_jobs / total_jobs * 100, 1) if total_jobs > 0 else 0.0

    return DashboardStats(
        total_campaigns=total_campaigns,
        running_campaigns=running_campaigns,
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        queued_jobs=queued_jobs,
        images_generated=images_done,
        videos_generated=videos_done,
        ai_avatar_generated=ai_avatar_done,
        overall_progress_pct=pct,
    )
