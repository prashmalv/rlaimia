#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Mia Campaign Engine — Azure Deployment Script
#
# BEFORE RUNNING THIS SCRIPT, do a tenant-scoped login:
#   az login --tenant 3dec2f61-614c-4d1f-be87-f6f196dfbdf6
#
# Then run:
#   export AZURE_TENANT_ID=3dec2f61-614c-4d1f-be87-f6f196dfbdf6
#   export AZURE_SUBSCRIPTION_ID=f0be2aa8-0c80-4298-860f-1fcd28fcf0f6
#   export AUTHORIZED_EMAIL=your@email.com
#   ./infra/deploy.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
TENANT_ID="${AZURE_TENANT_ID:-}"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
AUTHORIZED_EMAIL="${AUTHORIZED_EMAIL:-}"

LOCATION="centralindia"
RG_NAME="mia-campaign-rg"
REDIS_NAME="mia-campaign-redis"
DB_SERVER="mia-campaign-db"
DB_NAME="mia_campaign"
DB_ADMIN="miaadmin"
APP_NAME="mia-campaign-api"
CONTAINER_ENV="mia-campaign-env"
WORKER_IMG_APP="mia-worker-images"
WORKER_VID_APP="mia-worker-videos"
IMG_CONTAINER="campaign-images"
VID_CONTAINER="campaign-videos"

# ─── State file: persist generated names across re-runs ───────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATE_FILE="${PROJECT_DIR}/.deploy-state"

if [[ -f "$STATE_FILE" ]]; then
  echo "  Loading existing deploy state: $STATE_FILE"
  source "$STATE_FILE"
fi

STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-miacampaign$(openssl rand -hex 4)}"
ACR_NAME="${ACR_NAME:-miacampaignacr$(openssl rand -hex 3)}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 12 | tr -d '/+=')Mia1!}"

# ─── Validate env vars ────────────────────────────────────────────────────────
if [[ -z "$TENANT_ID" || -z "$SUBSCRIPTION_ID" ]]; then
  echo "ERROR: Set these env vars first:"
  echo "  export AZURE_TENANT_ID=3dec2f61-614c-4d1f-be87-f6f196dfbdf6"
  echo "  export AZURE_SUBSCRIPTION_ID=f0be2aa8-0c80-4298-860f-1fcd28fcf0f6"
  exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Mia Campaign Engine — Azure Deployment     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Tenant:       $TENANT_ID"
echo "  Subscription: $SUBSCRIPTION_ID"
echo "  Location:     $LOCATION"
echo "  Resource Grp: $RG_NAME"
echo "  Storage:      $STORAGE_ACCOUNT"
echo ""

# Save state immediately so re-runs reuse the same generated names
cat > "$STATE_FILE" <<STATEOF
STORAGE_ACCOUNT=${STORAGE_ACCOUNT}
ACR_NAME=${ACR_NAME}
DB_PASSWORD=${DB_PASSWORD}
STATEOF
echo "  Names saved to .deploy-state"
echo ""

# ─── [1/10] Verify tenant-scoped session ─────────────────────────────────────
echo "[1/10] Verifying Azure authentication..."

# Get the current tenant from the active account
CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null || echo "")
CURRENT_SUB=$(az account show --query id -o tsv 2>/dev/null || echo "")

echo "  Current tenant:       ${CURRENT_TENANT:-NOT LOGGED IN}"
echo "  Current subscription: ${CURRENT_SUB:-NONE}"

# If tenant doesn't match → force re-login to correct tenant
if [[ "$CURRENT_TENANT" != "$TENANT_ID" ]]; then
  echo ""
  echo "  ⚠  Tenant mismatch! Current: $CURRENT_TENANT, Required: $TENANT_ID"
  echo "  Running: az login --tenant $TENANT_ID"
  echo "  (A browser window will open — log in and come back)"
  echo ""
  az login --tenant "$TENANT_ID" --output none
fi

# Now set the exact subscription
az account set --subscription "$SUBSCRIPTION_ID"

# Final verification
FINAL_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
FINAL_SUB=$(az account show --query id -o tsv 2>/dev/null)
FINAL_NAME=$(az account show --query name -o tsv 2>/dev/null)

echo "  ✓ Active tenant:       $FINAL_TENANT"
echo "  ✓ Active subscription: $FINAL_SUB ($FINAL_NAME)"

if [[ "$FINAL_TENANT" != "$TENANT_ID" || "$FINAL_SUB" != "$SUBSCRIPTION_ID" ]]; then
  echo ""
  echo "  ERROR: Could not switch to the required tenant/subscription."
  echo "  Make sure your account has access to subscription $SUBSCRIPTION_ID"
  echo "  under tenant $TENANT_ID"
  exit 1
fi

# ─── [1b] Register resource providers (required before creating resources) ────
echo ""
echo "  Registering Azure resource providers (one-time per subscription)..."
for PROVIDER in \
  "Microsoft.Storage" \
  "Microsoft.Cache" \
  "Microsoft.DBforPostgreSQL" \
  "Microsoft.ContainerRegistry" \
  "Microsoft.App" \
  "Microsoft.OperationalInsights"
do
  STATUS=$(az provider show --namespace "$PROVIDER" --query "registrationState" -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$STATUS" != "Registered" ]]; then
    echo "    Registering $PROVIDER ..."
    az provider register --namespace "$PROVIDER" --output none
  else
    echo "    $PROVIDER already registered."
  fi
done
echo ""

# ─── [2/10] Resource Group ────────────────────────────────────────────────────
echo "[2/10] Resource group: $RG_NAME ..."
az group create \
  --name "$RG_NAME" \
  --location "$LOCATION" \
  --tags project=mia-campaign environment=production \
  --output none
echo "  Done."

# ─── [3/10] Storage Account ───────────────────────────────────────────────────
echo "[3/10] Storage account: $STORAGE_ACCOUNT ..."

# Check existence without --subscription flag (rely on account set)
STORAGE_EXISTS=$(az storage account list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$STORAGE_ACCOUNT'].name" \
  -o tsv 2>/dev/null || echo "")

if [[ -z "$STORAGE_EXISTS" ]]; then
  echo "  Creating (this may take ~30 seconds)..."
  az storage account create \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --access-tier Hot \
    --https-only true \
    --allow-blob-public-access false \
    --min-tls-version TLS1_2 \
    --output none
  echo "  Created."
else
  echo "  Already exists, skipping."
fi

STORAGE_KEY=$(az storage account keys list \
  --resource-group "$RG_NAME" \
  --account-name "$STORAGE_ACCOUNT" \
  --query "[0].value" -o tsv)

STORAGE_CONN_STR=$(az storage account show-connection-string \
  --resource-group "$RG_NAME" \
  --name "$STORAGE_ACCOUNT" \
  --query connectionString -o tsv)

echo "  Creating blob containers..."
az storage container create \
  --name "$IMG_CONTAINER" \
  --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY" \
  --auth-mode key \
  --output none 2>/dev/null || true

az storage container create \
  --name "$VID_CONTAINER" \
  --account-name "$STORAGE_ACCOUNT" \
  --account-key "$STORAGE_KEY" \
  --auth-mode key \
  --output none 2>/dev/null || true
echo "  Blob containers ready: $IMG_CONTAINER, $VID_CONTAINER"

# Grant authorized user Blob Data Reader
if [[ -n "$AUTHORIZED_EMAIL" ]]; then
  echo "  Granting Storage Blob Data Reader to: $AUTHORIZED_EMAIL ..."
  STORAGE_SCOPE=$(az storage account show \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RG_NAME" \
    --query id -o tsv)
  az role assignment create \
    --role "Storage Blob Data Reader" \
    --assignee "$AUTHORIZED_EMAIL" \
    --scope "$STORAGE_SCOPE" \
    --output none 2>/dev/null && echo "  Access granted." \
    || echo "  (Skipped — assign manually in portal if needed)"
fi

# ─── [4/10] Redis Cache ───────────────────────────────────────────────────────
echo "[4/10] Redis Cache: $REDIS_NAME ..."
REDIS_EXISTS=$(az redis list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$REDIS_NAME'].name" \
  -o tsv 2>/dev/null || echo "")

if [[ -z "$REDIS_EXISTS" ]]; then
  echo "  Creating (takes ~10-15 min — script will wait)..."
  az redis create \
    --name "$REDIS_NAME" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --sku Basic \
    --vm-size c1 \
    --output none
  echo "  Created."
else
  echo "  Already exists, skipping."
fi

REDIS_HOST=$(az redis show \
  --name "$REDIS_NAME" \
  --resource-group "$RG_NAME" \
  --query hostName -o tsv)
REDIS_KEY=$(az redis list-keys \
  --name "$REDIS_NAME" \
  --resource-group "$RG_NAME" \
  --query primaryKey -o tsv)
REDIS_URL="rediss://:${REDIS_KEY}@${REDIS_HOST}:6380/0"
echo "  Redis: $REDIS_HOST"

# ─── [5/10] PostgreSQL ────────────────────────────────────────────────────────
echo "[5/10] PostgreSQL: $DB_SERVER ..."
DB_EXISTS=$(az postgres flexible-server list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$DB_SERVER'].name" \
  -o tsv 2>/dev/null || echo "")

if [[ -z "$DB_EXISTS" ]]; then
  echo "  Creating PostgreSQL server..."
  az postgres flexible-server create \
    --name "$DB_SERVER" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --admin-user "$DB_ADMIN" \
    --admin-password "$DB_PASSWORD" \
    --sku-name Standard_B2ms \
    --tier Burstable \
    --version 16 \
    --storage-size 64 \
    --public-access 0.0.0.0 \
    --output none
  echo "  Created."
else
  echo "  Already exists, skipping."
fi

az postgres flexible-server db create \
  --server-name "$DB_SERVER" \
  --resource-group "$RG_NAME" \
  --database-name "$DB_NAME" \
  --output none 2>/dev/null || echo "  DB already exists."

DB_HOST="${DB_SERVER}.postgres.database.azure.com"
DB_URL="postgresql+asyncpg://${DB_ADMIN}:${DB_PASSWORD}@${DB_HOST}/${DB_NAME}?sslmode=require"
DB_URL_SYNC="postgresql+psycopg2://${DB_ADMIN}:${DB_PASSWORD}@${DB_HOST}/${DB_NAME}?sslmode=require"
echo "  DB host: $DB_HOST"

# ─── [6/10] Container Registry ───────────────────────────────────────────────
echo "[6/10] Container Registry: $ACR_NAME ..."
ACR_EXISTS=$(az acr list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$ACR_NAME'].name" \
  -o tsv 2>/dev/null || echo "")

if [[ -z "$ACR_EXISTS" ]]; then
  az acr create \
    --name "$ACR_NAME" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --sku Basic \
    --admin-enabled true \
    --output none
  echo "  Created."
else
  echo "  Already exists, skipping."
fi

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RG_NAME" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --resource-group "$RG_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --resource-group "$RG_NAME" --query "passwords[0].value" -o tsv)
echo "  ACR: $ACR_LOGIN_SERVER"

# ─── [7/10] Build & Push Docker Image ────────────────────────────────────────
# Uses ACR Tasks (az acr build) — builds remotely on Azure, no local Docker needed.
echo "[7/10] Building Docker image via ACR Tasks (remote build, no Docker Desktop needed)..."
echo "  This uploads your source code to ACR and builds in the cloud (~3-5 min)..."
az acr build \
  --registry "$ACR_NAME" \
  --resource-group "$RG_NAME" \
  --image "mia-campaign:latest" \
  --file "$PROJECT_DIR/Dockerfile" \
  "$PROJECT_DIR"
echo "  Image built and pushed: ${ACR_LOGIN_SERVER}/mia-campaign:latest"

# ─── [8/10] Container Apps Environment ───────────────────────────────────────
echo "[8/10] Container Apps Environment: $CONTAINER_ENV ..."
ENV_EXISTS=$(az containerapp env list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$CONTAINER_ENV'].name" \
  -o tsv 2>/dev/null || echo "")

if [[ -z "$ENV_EXISTS" ]]; then
  az containerapp env create \
    --name "$CONTAINER_ENV" \
    --resource-group "$RG_NAME" \
    --location "$LOCATION" \
    --output none
  echo "  Created."
else
  echo "  Already exists."
fi

# ─── Common env vars for all container apps ───────────────────────────────────
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

# ─── [9/10] API Container App ─────────────────────────────────────────────────
echo "[9/10] API: $APP_NAME ..."
API_EXISTS=$(az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$APP_NAME'].name" \
  -o tsv 2>/dev/null || echo "")

if [[ -z "$API_EXISTS" ]]; then
  az containerapp create \
    --name "$APP_NAME" \
    --resource-group "$RG_NAME" \
    --environment "$CONTAINER_ENV" \
    --image "${ACR_LOGIN_SERVER}/mia-campaign:latest" \
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
  echo "  Created."
else
  az containerapp update \
    --name "$APP_NAME" \
    --resource-group "$RG_NAME" \
    --image "${ACR_LOGIN_SERVER}/mia-campaign:latest" \
    --output none
  echo "  Updated."
fi

API_URL=$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RG_NAME" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

# ─── [10a/10] Image Worker ────────────────────────────────────────────────────
echo "[10a/10] Image worker: $WORKER_IMG_APP ..."
IMG_EXISTS=$(az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$WORKER_IMG_APP'].name" -o tsv 2>/dev/null || echo "")

if [[ -z "$IMG_EXISTS" ]]; then
  az containerapp create \
    --name "$WORKER_IMG_APP" \
    --resource-group "$RG_NAME" \
    --environment "$CONTAINER_ENV" \
    --image "${ACR_LOGIN_SERVER}/mia-campaign:latest" \
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
  az containerapp update --name "$WORKER_IMG_APP" --resource-group "$RG_NAME" \
    --image "${ACR_LOGIN_SERVER}/mia-campaign:latest" --output none
  echo "  Updated."
fi

# ─── [10b/10] Video Worker ────────────────────────────────────────────────────
echo "[10b/10] Video worker: $WORKER_VID_APP ..."
VID_EXISTS=$(az containerapp list \
  --resource-group "$RG_NAME" \
  --query "[?name=='$WORKER_VID_APP'].name" -o tsv 2>/dev/null || echo "")

if [[ -z "$VID_EXISTS" ]]; then
  az containerapp create \
    --name "$WORKER_VID_APP" \
    --resource-group "$RG_NAME" \
    --environment "$CONTAINER_ENV" \
    --image "${ACR_LOGIN_SERVER}/mia-campaign:latest" \
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
  az containerapp update --name "$WORKER_VID_APP" --resource-group "$RG_NAME" \
    --image "${ACR_LOGIN_SERVER}/mia-campaign:latest" --output none
  echo "  Updated."
fi

# ─── Append final values to state file ───────────────────────────────────────
cat >> "$STATE_FILE" <<STATEOF
REDIS_URL=${REDIS_URL}
REDIS_HOST=${REDIS_HOST}
DB_HOST=${DB_HOST}
DB_URL=${DB_URL}
DB_URL_SYNC=${DB_URL_SYNC}
STORAGE_KEY=${STORAGE_KEY}
STORAGE_CONN_STR=${STORAGE_CONN_STR}
ACR_LOGIN_SERVER=${ACR_LOGIN_SERVER}
API_URL=https://${API_URL}
STATEOF

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   DEPLOYMENT COMPLETE                                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Dashboard URL  : https://${API_URL}"
echo "  API Docs       : https://${API_URL}/docs"
echo ""
echo "  Storage Account: $STORAGE_ACCOUNT"
echo "  Redis Cache    : $REDIS_HOST"
echo "  PostgreSQL     : $DB_HOST"
echo "  DB Password    : $DB_PASSWORD"
echo ""
echo "  Credentials saved to: .deploy-state"
echo ""
echo "  Next steps:"
echo "  1. Copy Gotham font files → assets/fonts/gotham/"
echo "  2. Copy your image templates → assets/image_templates/"
echo "  3. Copy your video templates → assets/video_templates/"
echo "  4. Open dashboard: https://${API_URL}"
