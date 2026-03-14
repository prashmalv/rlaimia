#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Creates/updates the worker Container Apps (image + video workers).
# Run this if the main deploy.sh failed before step 10.
#
# Usage:
#   ./infra/create_workers.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATE_FILE="${PROJECT_DIR}/.deploy-state"

# ─── Load state from previous deploy ─────────────────────────────────────────
if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: .deploy-state not found. Run deploy.sh first."
  exit 1
fi
source "$STATE_FILE"

# ─── Required variables ───────────────────────────────────────────────────────
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-f0be2aa8-0c80-4298-860f-1fcd28fcf0f6}"
TENANT_ID="${AZURE_TENANT_ID:-3dec2f61-614c-4d1f-be87-f6f196dfbdf6}"
RG_NAME="mia-campaign-rg"
CONTAINER_ENV="mia-campaign-env"
WORKER_IMG_APP="mia-worker-images"
WORKER_VID_APP="mia-worker-videos"

# Values from state file
ACR_NAME="${ACR_NAME:?'ACR_NAME not in .deploy-state'}"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-${ACR_NAME}.azurecr.io}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:?'STORAGE_ACCOUNT not in .deploy-state'}"
DB_URL_SYNC="${DB_URL_SYNC:-}"
REDIS_URL="${REDIS_URL:-}"
STORAGE_CONN_STR="${STORAGE_CONN_STR:-}"
STORAGE_KEY="${STORAGE_KEY:-}"

# Fetch values if not in state file
if [[ -z "$ACR_LOGIN_SERVER" ]]; then
  ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RG_NAME" --query loginServer -o tsv)
fi
if [[ -z "$STORAGE_KEY" ]]; then
  STORAGE_KEY=$(az storage account keys list --resource-group "$RG_NAME" --account-name "$STORAGE_ACCOUNT" --query "[0].value" -o tsv)
fi
if [[ -z "$STORAGE_CONN_STR" ]]; then
  STORAGE_CONN_STR=$(az storage account show-connection-string --resource-group "$RG_NAME" --name "$STORAGE_ACCOUNT" --query connectionString -o tsv)
fi

ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --resource-group "$RG_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --resource-group "$RG_NAME" --query "passwords[0].value" -o tsv)

# Fetch Redis if not in state
if [[ -z "${REDIS_URL:-}" ]]; then
  echo "  Fetching Redis connection info..."
  REDIS_NAME="mia-campaign-redis"
  REDIS_HOST=$(az redis show --name "$REDIS_NAME" --resource-group "$RG_NAME" --query hostName -o tsv)
  REDIS_KEY=$(az redis list-keys --name "$REDIS_NAME" --resource-group "$RG_NAME" --query primaryKey -o tsv)
  REDIS_URL="rediss://:${REDIS_KEY}@${REDIS_HOST}:6380/0"
  echo "  Redis: $REDIS_HOST"
fi

# Fetch DB URL if not in state
if [[ -z "${DB_URL_SYNC:-}" ]]; then
  echo "  Composing DB URL from state file password..."
  DB_SERVER="mia-campaign-db"
  DB_ADMIN="miaadmin"
  DB_NAME="mia_campaign"
  DB_HOST="${DB_SERVER}.postgres.database.azure.com"
  DB_URL_SYNC="postgresql+psycopg2://${DB_ADMIN}:${DB_PASSWORD}@${DB_HOST}/${DB_NAME}?sslmode=require"
  DB_URL="postgresql+asyncpg://${DB_ADMIN}:${DB_PASSWORD}@${DB_HOST}/${DB_NAME}?sslmode=require"
  echo "  DB host: $DB_HOST"
fi

# Ensure asyncpg URL is always set (needed for API container)
if [[ -z "${DB_URL:-}" ]]; then
  DB_URL=$(echo "$DB_URL_SYNC" | sed 's|+psycopg2|+asyncpg|')
fi

IMAGE="$ACR_LOGIN_SERVER/mia-campaign:latest"

echo ""
echo "  ACR:     $ACR_LOGIN_SERVER"
echo "  Image:   $IMAGE"
echo ""

# ─── Step 1: Rebuild image with fonts + templates baked in ───────────────────
echo "[1/3] Building updated image with fonts and templates..."
echo "  Copying templates from birthday_campaign..."

BIRTHDAY_ASSETS="${PROJECT_DIR}/../birthday_campaign/assets"
mkdir -p "${PROJECT_DIR}/assets/image_templates" "${PROJECT_DIR}/assets/video_templates"

for f in sampleTemplate.jpeg sampleTemplate_2.jpeg; do
  [[ -f "$BIRTHDAY_ASSETS/$f" ]] && cp "$BIRTHDAY_ASSETS/$f" "${PROJECT_DIR}/assets/image_templates/" && echo "  Copied: $f"
done
for f in video_template.mp4 video_template_2.mp4; do
  [[ -f "$BIRTHDAY_ASSETS/$f" ]] && cp "$BIRTHDAY_ASSETS/$f" "${PROJECT_DIR}/assets/video_templates/" && echo "  Copied: $f"
done

echo "  Triggering remote ACR build..."
az acr build \
  --registry "$ACR_NAME" \
  --resource-group "$RG_NAME" \
  --image "mia-campaign:latest" \
  --file "${PROJECT_DIR}/Dockerfile" \
  "$PROJECT_DIR"
echo "  Image updated: $IMAGE"

# ─── Common env vars ──────────────────────────────────────────────────────────
COMMON_ENV=(
  "DATABASE_URL=${DB_URL}"
  "REDIS_URL=${REDIS_URL}"
  "CELERY_BROKER_URL=${REDIS_URL}"
  "CELERY_RESULT_BACKEND=${REDIS_URL}"
  "AZURE_STORAGE_CONN_STR=${STORAGE_CONN_STR}"
  "AZURE_STORAGE_ACCOUNT=${STORAGE_ACCOUNT}"
  "AZURE_STORAGE_KEY=${STORAGE_KEY}"
  "AZURE_BLOB_CONTAINER_IMG=campaign-images"
  "AZURE_BLOB_CONTAINER_VID=campaign-videos"
  "LOG_LEVEL=info"
)

# ─── Step 2: Image Worker ─────────────────────────────────────────────────────
echo "[2/3] Image worker: $WORKER_IMG_APP ..."
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
  echo "  Created."
else
  az containerapp update \
    --name "$WORKER_IMG_APP" \
    --resource-group "$RG_NAME" \
    --image "$IMAGE" \
    --output none
  echo "  Updated."
fi

# ─── Step 3: Video Worker ─────────────────────────────────────────────────────
echo "[3/3] Video worker: $WORKER_VID_APP ..."
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
  echo "  Created."
else
  az containerapp update \
    --name "$WORKER_VID_APP" \
    --resource-group "$RG_NAME" \
    --image "$IMAGE" \
    --output none
  echo "  Updated."
fi

echo ""
echo "✓ Workers deployed. Check status:"
echo "  az containerapp list --resource-group $RG_NAME --query '[].{name:name,status:properties.runningStatus}' -o table"
