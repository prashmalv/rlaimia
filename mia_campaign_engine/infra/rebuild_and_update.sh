#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Full fix + rebuild for Mia Campaign Engine.
# Run this once — it will:
#   Step 0: Immediately patch the API container (no rebuild, ~1 min)
#           → dashboard becomes accessible
#   Step 1: Rebuild Docker image with all code fixes (~4-5 min)
#   Step 2: Update all 3 containers with new image
#
# Usage:
#   ./infra/rebuild_and_update.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATE_FILE="${PROJECT_DIR}/.deploy-state"

# ─── Load state ──────────────────────────────────────────────────────────────
if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: .deploy-state not found. Run deploy.sh first."
  exit 1
fi
source "$STATE_FILE"

RG_NAME="mia-campaign-rg"
CONTAINER_ENV="mia-campaign-env"
APP_NAME="mia-campaign-api"
WORKER_IMG_APP="mia-worker-images"
WORKER_VID_APP="mia-worker-videos"
DB_SERVER="mia-campaign-db"
DB_ADMIN="miaadmin"
DB_NAME="mia_campaign"
DB_HOST="${DB_SERVER}.postgres.database.azure.com"
REDIS_NAME="mia-campaign-redis"
IMG_CONTAINER="campaign-images"
VID_CONTAINER="campaign-videos"

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-${ACR_NAME}.azurecr.io}"
IMAGE="${ACR_LOGIN_SERVER}/mia-campaign:latest"

# DB URL — asyncpg format for API (workers auto-convert via celery_app.py line 60)
DB_URL="postgresql+asyncpg://${DB_ADMIN}:${DB_PASSWORD}@${DB_HOST}/${DB_NAME}"

# ─── Fetch credentials not in state file ────────────────────────────────────
if [[ -z "${STORAGE_KEY:-}" ]]; then
  echo "  Fetching storage key..."
  STORAGE_KEY=$(az storage account keys list \
    --resource-group "$RG_NAME" --account-name "$STORAGE_ACCOUNT" \
    --query "[0].value" -o tsv)
fi

if [[ -z "${STORAGE_CONN_STR:-}" ]]; then
  echo "  Fetching storage connection string..."
  STORAGE_CONN_STR=$(az storage account show-connection-string \
    --resource-group "$RG_NAME" --name "$STORAGE_ACCOUNT" \
    --query connectionString -o tsv)
fi

if [[ -z "${REDIS_URL:-}" ]]; then
  echo "  Fetching Redis credentials..."
  REDIS_HOST=$(az redis show --name "$REDIS_NAME" --resource-group "$RG_NAME" --query hostName -o tsv)
  REDIS_KEY=$(az redis list-keys --name "$REDIS_NAME" --resource-group "$RG_NAME" --query primaryKey -o tsv)
  REDIS_URL="rediss://:${REDIS_KEY}@${REDIS_HOST}:6380/0"
fi

ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --resource-group "$RG_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --resource-group "$RG_NAME" --query "passwords[0].value" -o tsv)

echo ""
echo "  ACR:     $ACR_LOGIN_SERVER"
echo "  DB host: $DB_HOST"
echo ""

# ─── Step 0: Immediately fix API env vars (no rebuild) ───────────────────────
# This patches the RUNNING container so the dashboard loads NOW.
# Fixes: wrong DATABASE_URL format + removes LOG_LEVEL (uses config.py default "INFO")
echo "[0/2] Patching API container env vars (takes ~1 min to restart)..."
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RG_NAME" \
  --set-env-vars \
    "DATABASE_URL=${DB_URL}" \
    "REDIS_URL=${REDIS_URL}" \
    "CELERY_BROKER_URL=${REDIS_URL}" \
    "CELERY_RESULT_BACKEND=${REDIS_URL}" \
    "AZURE_STORAGE_CONN_STR=${STORAGE_CONN_STR}" \
    "AZURE_STORAGE_ACCOUNT=${STORAGE_ACCOUNT}" \
    "AZURE_STORAGE_KEY=${STORAGE_KEY}" \
    "AZURE_BLOB_CONTAINER_IMG=${IMG_CONTAINER}" \
    "AZURE_BLOB_CONTAINER_VID=${VID_CONTAINER}" \
  --remove-env-vars "LOG_LEVEL" \
  --output none
echo "  API patched. New revision starting — dashboard should load in ~60 seconds."
echo ""

# ─── Step 1: Rebuild image with all code fixes ───────────────────────────────
echo "[1/2] Building updated image via ACR Tasks (~4-5 min)..."
az acr build \
  --registry "$ACR_NAME" \
  --resource-group "$RG_NAME" \
  --image "mia-campaign:latest" \
  --file "${PROJECT_DIR}/Dockerfile" \
  "$PROJECT_DIR"
echo "  Image built: $IMAGE"
echo ""

# ─── Common env vars (applied to all 3 containers) ───────────────────────────
# LOG_LEVEL=info: uvicorn/celery need lowercase; main.py uses .upper() for Python logging
COMMON_ENV=(
  "DATABASE_URL=${DB_URL}"
  "REDIS_URL=${REDIS_URL}"
  "CELERY_BROKER_URL=${REDIS_URL}"
  "CELERY_RESULT_BACKEND=${REDIS_URL}"
  "AZURE_STORAGE_CONN_STR=${STORAGE_CONN_STR}"
  "AZURE_STORAGE_ACCOUNT=${STORAGE_ACCOUNT}"
  "AZURE_STORAGE_KEY=${STORAGE_KEY}"
  "AZURE_BLOB_CONTAINER_IMG=${IMG_CONTAINER}"
  "AZURE_BLOB_CONTAINER_VID=${VID_CONTAINER}"
  "LOG_LEVEL=info"
)

# Optionally inject HEYGEN_API_KEY if set in environment
if [[ -n "${HEYGEN_API_KEY:-}" ]]; then
  COMMON_ENV+=("HEYGEN_API_KEY=${HEYGEN_API_KEY}")
  echo "  HEYGEN_API_KEY detected — AI Avatar Video will be enabled."
fi
if [[ -n "${HEYGEN_VOICE_ID:-}" ]]; then
  COMMON_ENV+=("HEYGEN_VOICE_ID=${HEYGEN_VOICE_ID}")
fi
if [[ -n "${HEYGEN_VOICE_ID_MALE:-}" ]]; then
  COMMON_ENV+=("HEYGEN_VOICE_ID_MALE=${HEYGEN_VOICE_ID_MALE}")
fi
if [[ -n "${HEYGEN_VOICE_ID_FEMALE:-}" ]]; then
  COMMON_ENV+=("HEYGEN_VOICE_ID_FEMALE=${HEYGEN_VOICE_ID_FEMALE}")
fi

# ─── Step 2: Update all 3 Container Apps ─────────────────────────────────────
echo "[2/2] Updating all Container Apps with new image..."

# API
API_EXISTS=$(az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$APP_NAME'].name" -o tsv 2>/dev/null || echo "")

if [[ -z "$API_EXISTS" ]]; then
  az containerapp create \
    --name "$APP_NAME" \
    --resource-group "$RG_NAME" \
    --environment "$CONTAINER_ENV" \
    --image "$IMAGE" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --command "/entrypoint.sh" --args "api" \
    --target-port 8000 \
    --ingress external \
    --cpu 1.0 --memory 2.0Gi \
    --min-replicas 1 --max-replicas 3 \
    --env-vars "${COMMON_ENV[@]}" \
    --output none
  echo "  API: Created."
else
  az containerapp update \
    --name "$APP_NAME" \
    --resource-group "$RG_NAME" \
    --image "$IMAGE" \
    --set-env-vars "${COMMON_ENV[@]}" \
    --output none
  echo "  API: Updated."
fi

# Image worker
IMG_EXISTS=$(az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$WORKER_IMG_APP'].name" -o tsv 2>/dev/null || echo "")

if [[ -z "$IMG_EXISTS" ]]; then
  az containerapp create \
    --name "$WORKER_IMG_APP" \
    --resource-group "$RG_NAME" \
    --environment "$CONTAINER_ENV" \
    --image "$IMAGE" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --command "/entrypoint.sh" --args "worker-images" \
    --cpu 4.0 --memory 8.0Gi \
    --min-replicas 1 --max-replicas 20 \
    --env-vars "${COMMON_ENV[@]}" "IMAGE_WORKER_CONCURRENCY=8" \
    --output none
  echo "  Image worker: Created."
else
  az containerapp update \
    --name "$WORKER_IMG_APP" \
    --resource-group "$RG_NAME" \
    --image "$IMAGE" \
    --set-env-vars "${COMMON_ENV[@]}" "IMAGE_WORKER_CONCURRENCY=8" \
    --output none
  echo "  Image worker: Updated."
fi

# Video worker
VID_EXISTS=$(az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$WORKER_VID_APP'].name" -o tsv 2>/dev/null || echo "")

if [[ -z "$VID_EXISTS" ]]; then
  az containerapp create \
    --name "$WORKER_VID_APP" \
    --resource-group "$RG_NAME" \
    --environment "$CONTAINER_ENV" \
    --image "$IMAGE" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --command "/entrypoint.sh" --args "worker-videos" \
    --cpu 4.0 --memory 8.0Gi \
    --min-replicas 1 --max-replicas 10 \
    --env-vars "${COMMON_ENV[@]}" "VIDEO_WORKER_CONCURRENCY=4" \
    --output none
  echo "  Video worker: Created."
else
  az containerapp update \
    --name "$WORKER_VID_APP" \
    --resource-group "$RG_NAME" \
    --image "$IMAGE" \
    --set-env-vars "${COMMON_ENV[@]}" "VIDEO_WORKER_CONCURRENCY=4" \
    --output none
  echo "  Video worker: Updated."
fi

# ─── Done ────────────────────────────────────────────────────────────────────
API_URL=$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RG_NAME" \
  --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || echo "")

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   ALL DONE — System fully deployed                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
if [[ -n "$API_URL" ]]; then
  echo "  Dashboard: https://${API_URL}"
  echo "  API health: https://${API_URL}/api/health"
  echo "  API docs:   https://${API_URL}/docs"
fi
echo ""
echo "  Container status:"
az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[].{Name:name, Status:properties.runningStatus, Revision:properties.latestRevisionName}" \
  -o table
