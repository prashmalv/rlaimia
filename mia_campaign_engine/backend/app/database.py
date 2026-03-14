"""
Async SQLAlchemy engine + session factory.
Supports SQLite (dev) and PostgreSQL (Azure prod).
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
import config
from backend.app.models import Base


# ─── Engine ───────────────────────────────────────────────────────────────────

import re as _re
import ssl as _ssl

_engine_kwargs = {}
if "sqlite" in config.DATABASE_URL:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL (asyncpg): NullPool for Azure Container Apps
    # SSL via ssl.SSLContext — asyncpg does NOT accept 'sslmode' kwarg
    _ssl_ctx = _ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE  # Azure PostgreSQL self-signed/intermediate CA
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs["connect_args"] = {"ssl": _ssl_ctx}

# Strip any sslmode/ssl params from URL — SSL handled via connect_args above
_db_url = _re.sub(r'[?&]ssl(?:mode)?=[^&]*', '', config.DATABASE_URL).rstrip('?')

engine = create_async_engine(
    _db_url,
    echo=False,
    future=True,
    **_engine_kwargs
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ─── Dependency ───────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# ─── Init DB (create tables + migrate new columns) ────────────────────────────

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Run migrations in separate transactions (avoids PostgreSQL cascade-abort)
    await _migrate_add_columns()


async def _migrate_add_columns():
    """
    Safely add new columns to existing tables.
    Uses ADD COLUMN IF NOT EXISTS so each statement is a no-op when the column
    already exists — avoids PostgreSQL aborting the transaction on duplicate-column errors.
    Supported: PostgreSQL 9.6+ and SQLite 3.37+ (both used in prod/dev).
    """
    from sqlalchemy import text

    migrations = [
        # campaigns table
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS heygen_talking_photo_id VARCHAR(200)",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS avatar_voice_gender      VARCHAR(10)",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS generate_images          BOOLEAN DEFAULT TRUE",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS generate_videos          BOOLEAN DEFAULT TRUE",
        "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS image_template_id        VARCHAR(36)",
        # jobs table
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS heygen_video_status   VARCHAR(20)",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS heygen_video_blob_key VARCHAR(500)",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS heygen_video_url      TEXT",
    ]

    # Run each migration in its own transaction so a failure cannot cascade
    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass  # dialect doesn't support IF NOT EXISTS or other edge case
