"""
SQLAlchemy ORM models + Pydantic schemas for Mia Campaign Engine
"""

import uuid
import json
from datetime import datetime
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Text, ForeignKey, Index, Boolean
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ─── Enums ────────────────────────────────────────────────────────────────────

class CampaignStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    PAUSED    = "paused"
    COMPLETED = "completed"
    FAILED    = "failed"

class JobStatus(str, Enum):
    QUEUED     = "queued"
    PROCESSING = "processing"
    DONE       = "done"
    PARTIAL    = "partial"   # one of image/video succeeded, the other failed
    FAILED     = "failed"
    SKIPPED    = "skipped"

class EventType(str, Enum):
    BIRTHDAY    = "birthday"
    ANNIVERSARY = "anniversary"


# ─── ORM Models ───────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Image Template ───────────────────────────────────────────────────────────

class ImageTemplate(Base):
    __tablename__ = "image_templates"

    id         = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name       = Column(String(200), nullable=False)
    local_path = Column(String(500), nullable=True)   # filesystem path (API container)
    blob_key   = Column(String(500), nullable=True)   # Azure Blob key for workers
    text_boxes = Column(Text, nullable=True)          # JSON: per-zone coordinates + colors
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def boxes_dict(self) -> dict:
        if self.text_boxes:
            try:
                return json.loads(self.text_boxes)
            except Exception:
                pass
        return {}


class Campaign(Base):
    __tablename__ = "campaigns"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name           = Column(String(200), nullable=False)
    event_type     = Column(String(20), default=EventType.BIRTHDAY)
    person_file    = Column(String(500))
    template_file  = Column(String(500))
    status         = Column(String(20), default=CampaignStatus.PENDING)
    total_jobs     = Column(Integer, default=0)
    completed_jobs = Column(Integer, default=0)
    failed_jobs    = Column(Integer, default=0)
    skipped_jobs   = Column(Integer, default=0)
    progress_pct   = Column(Float, default=0.0)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    error_msg      = Column(Text, nullable=True)

    # Media type selection — which phases to run
    generate_images         = Column(Boolean, default=True)   # run image generation
    generate_videos         = Column(Boolean, default=True)   # run video generation
    # (AI avatar is controlled by heygen_talking_photo_id being set)

    # Image template selection (None = use all built-in TEMPLATE_CONFIGS)
    image_template_id = Column(String(36), nullable=True)  # FK to image_templates.id

    # Heygen AI Avatar Video — optional per-campaign
    heygen_talking_photo_id  = Column(String(200), nullable=True)  # ID from Heygen after avatar upload
    avatar_voice_gender      = Column(String(10),  nullable=True)  # "male" | "female" | None
    heygen_bg_image_url      = Column(Text,        nullable=True)  # SAS URL of background image uploaded by user
    heygen_video_template_id = Column(String(100), nullable=True)  # Heygen template ID (overrides bg+avatar defaults)
    # Video orientation: "landscape" (1280×720) | "portrait" (720×1280) | "square" (720×720)
    video_orientation        = Column(String(20),  nullable=True, default="landscape")

    # Per-campaign voice overrides (override env-var defaults)
    heygen_voice_id          = Column(String(100), nullable=True)  # Heygen voice_id override for this campaign
    elevenlabs_voice_id      = Column(String(100), nullable=True)  # ElevenLabs voice ID → TTS audio → Heygen audio mode

    # Post-campaign report
    report_blob_key          = Column(String(500), nullable=True)  # blob key of Excel report
    report_url               = Column(Text,        nullable=True)  # SAS URL of Excel report (refreshed on access)

    jobs           = relationship("Job", back_populates="campaign", lazy="dynamic")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_campaign_id", "campaign_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_person_name", "person_name"),
    )

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id    = Column(String(36), ForeignKey("campaigns.id"), nullable=False)

    # Person details
    person_name    = Column(String(200))
    first_name     = Column(String(100))
    age            = Column(Integer)
    persona        = Column(String(100))
    dob            = Column(String(20))     # stored as ISO date string
    phase          = Column(String(20))     # T_DAY | T_MINUS_10

    # Generated content
    message_text   = Column(Text, nullable=True)
    template_id    = Column(String(50), nullable=True)

    # Image
    image_status   = Column(String(20), default=JobStatus.QUEUED)
    image_blob_key = Column(String(500), nullable=True)  # blob path in Azure
    image_url      = Column(Text, nullable=True)         # SAS or CDN URL

    # Video (FFmpeg)
    video_status   = Column(String(20), default=JobStatus.QUEUED)
    video_blob_key = Column(String(500), nullable=True)
    video_url      = Column(Text, nullable=True)

    # Heygen AI Avatar Video
    heygen_video_status   = Column(String(20), nullable=True)   # None = not requested
    heygen_video_blob_key = Column(String(500), nullable=True)
    heygen_video_url      = Column(Text, nullable=True)

    # Overall
    status         = Column(String(20), default=JobStatus.QUEUED)
    error_msg      = Column(Text, nullable=True)
    celery_task_id = Column(String(200), nullable=True)

    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    campaign       = relationship("Campaign", back_populates="jobs")


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name: str
    event_type: EventType = EventType.BIRTHDAY

class TemplateOut(BaseModel):
    id: str
    name: str
    local_path: Optional[str] = None
    blob_key: Optional[str] = None
    text_boxes: Optional[str] = None   # raw JSON string
    is_builtin: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class CampaignOut(BaseModel):
    id: str
    name: str
    event_type: str
    status: str
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    skipped_jobs: int
    progress_pct: float
    created_at: datetime
    updated_at: datetime
    error_msg: Optional[str] = None
    generate_images: bool = True
    generate_videos: bool = True
    heygen_talking_photo_id: Optional[str] = None
    avatar_voice_gender: Optional[str] = None
    image_template_id: Optional[str] = None
    heygen_bg_image_url: Optional[str] = None
    heygen_video_template_id: Optional[str] = None
    video_orientation: Optional[str] = "landscape"
    heygen_voice_id: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    report_blob_key: Optional[str] = None
    report_url: Optional[str] = None

    class Config:
        from_attributes = True

class JobOut(BaseModel):
    id: str
    campaign_id: str
    person_name: Optional[str]
    first_name: Optional[str]
    age: Optional[int]
    persona: Optional[str]
    phase: Optional[str]
    status: str
    image_status: str
    image_url: Optional[str]
    video_status: str
    video_url: Optional[str]
    heygen_video_status: Optional[str] = None
    heygen_video_url: Optional[str] = None
    message_text: Optional[str]
    error_msg: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class PaginatedJobs(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[JobOut]

class DashboardStats(BaseModel):
    total_campaigns: int
    running_campaigns: int
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    queued_jobs: int
    images_generated: int
    videos_generated: int
    ai_avatar_generated: int = 0
    overall_progress_pct: float
