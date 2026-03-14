#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Mia Campaign Engine — Container Entrypoint
# Usage: CMD ["api"]  OR  CMD ["worker-images"]  OR  CMD ["worker-videos"]
# ─────────────────────────────────────────────────────────────────────────────

set -e
MODE="${1:-api}"
LOG_LEVEL="${LOG_LEVEL:-info}"
LOG_LEVEL="${LOG_LEVEL,,}"   # force lowercase (uvicorn/celery require lowercase)

echo "Starting Mia Campaign Engine in mode: $MODE"

case "$MODE" in
  api)
    echo "Starting FastAPI server..."
    exec uvicorn backend.app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers "${API_WORKERS:-2}" \
      --log-level "${LOG_LEVEL:-info}"
    ;;

  worker-images)
    echo "Starting Celery image worker..."
    exec celery -A backend.workers.celery_app worker \
      -Q images \
      -c "${IMAGE_WORKER_CONCURRENCY:-8}" \
      --loglevel="${LOG_LEVEL:-info}" \
      -n "mia-image-worker@%h"
    ;;

  worker-videos)
    echo "Starting Celery video worker..."
    exec celery -A backend.workers.celery_app worker \
      -Q videos \
      -c "${VIDEO_WORKER_CONCURRENCY:-4}" \
      --loglevel="${LOG_LEVEL:-info}" \
      -n "mia-video-worker@%h"
    ;;

  worker-all)
    echo "Starting Celery all-queue worker..."
    exec celery -A backend.workers.celery_app worker \
      -Q images,videos,default \
      -c "${IMAGE_WORKER_CONCURRENCY:-4}" \
      --loglevel="${LOG_LEVEL:-info}" \
      -n "mia-worker@%h"
    ;;

  flower)
    echo "Starting Flower monitoring..."
    exec celery -A backend.workers.celery_app flower \
      --port=5555 \
      --broker="${CELERY_BROKER_URL}"
    ;;

  beat)
    echo "Starting Celery beat scheduler..."
    exec celery -A backend.workers.celery_app beat \
      --loglevel="${LOG_LEVEL:-info}"
    ;;

  *)
    echo "Unknown mode: $MODE. Valid: api | worker-images | worker-videos | worker-all | flower | beat"
    exit 1
    ;;
esac
