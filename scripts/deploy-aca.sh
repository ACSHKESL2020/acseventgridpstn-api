#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/deploy-aca.sh \
#     --rg <resource-group> \
#     --env <aca-env-name> \
#     --app <aca-app-name> \
#     --acr <acr-name> \
#     --image nodeexpress-acs:latest \
#     [--location <azure-location>] \
#     [--use-mi] [--user-mi-client-id <GUID>] \
#     [--tag <tag>] \
#     [--acr-build] [--acr-use-admin]
#
# Notes:
# - Requires: az cli, docker login to ACR
# - Reads local .env for values and sets ACA env vars and secrets.
# - If --use-mi is set, ACS_ENDPOINT is used (if present). Otherwise ACS_CONNECTION_STRING is set as a secret.

RG=""
ENV_NAME=""
APP_NAME=""
ACR_NAME=""
IMAGE_NAME="nodeexpress-acs:latest"
LOCATION=""
USE_MI=false
USER_MI_CLIENT_ID=""
TAG="latest"
ACR_BUILD=false
ACR_USE_ADMIN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rg) RG="$2"; shift 2;;
    --env) ENV_NAME="$2"; shift 2;;
    --app) APP_NAME="$2"; shift 2;;
    --acr) ACR_NAME="$2"; shift 2;;
    --image) IMAGE_NAME="$2"; shift 2;;
    --location) LOCATION="$2"; shift 2;;
    --use-mi) USE_MI=true; shift 1;;
    --user-mi-client-id) USER_MI_CLIENT_ID="$2"; USE_MI=true; shift 2;;
    --tag) TAG="$2"; shift 2;;
  --acr-build) ACR_BUILD=true; shift 1;;
  --acr-use-admin) ACR_USE_ADMIN=true; shift 1;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "$RG" || -z "$ENV_NAME" || -z "$APP_NAME" || -z "$ACR_NAME" ]]; then
  echo "Missing required args. See usage in script header." >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT_DIR"

# Load .env if present and export variables for this script's environment
if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env || true
  set +o allexport
fi

# Resolve ACR login server
ACR_LOGIN_SERVER=$(az acr show -n "$ACR_NAME" --query loginServer -o tsv)
IMAGE_REF="${ACR_LOGIN_SERVER}/${IMAGE_NAME%:*}:${TAG}"

# Build and push image (supports remote ACR build)
if [[ "$ACR_BUILD" == true ]]; then
  echo "Building remotely with ACR: ${IMAGE_REF}"
  az acr build -r "$ACR_NAME" -t "${IMAGE_NAME%:*}:${TAG}" .
else
  if ! docker info >/dev/null 2>&1; then
    echo "Docker not available; falling back to ACR remote build." >&2
    az acr build -r "$ACR_NAME" -t "${IMAGE_NAME%:*}:${TAG}" .
  else
    echo "Building image locally: ${IMAGE_REF}"
    docker build -t "$IMAGE_REF" .
    echo "Pushing: ${IMAGE_REF}"
    docker push "$IMAGE_REF"
  fi
fi

# Build env var list (non-secrets)
ENV_VARS=(
  "PORT=8080"
  "TRUST_PROXY=true"
)

# From code references
[[ -n "${CALLBACK_URI_HOST:-}" ]] && ENV_VARS+=("CALLBACK_URI_HOST=${CALLBACK_URI_HOST}")
[[ -n "${RECORDING_FORMAT:-}" ]] && ENV_VARS+=("RECORDING_FORMAT=${RECORDING_FORMAT}")
[[ -n "${RECORDING_CHANNEL_TYPE:-}" ]] && ENV_VARS+=("RECORDING_CHANNEL_TYPE=${RECORDING_CHANNEL_TYPE}")
[[ -n "${RECORDING_SUPPRESS_8522_WARN:-}" ]] && ENV_VARS+=("RECORDING_SUPPRESS_8522_WARN=${RECORDING_SUPPRESS_8522_WARN}")
[[ -n "${RECORDING_USE_BYOS:-}" ]] && ENV_VARS+=("RECORDING_USE_BYOS=${RECORDING_USE_BYOS}")
[[ -n "${AZURE_STORAGE_ACCOUNT:-}" ]] && ENV_VARS+=("AZURE_STORAGE_ACCOUNT=${AZURE_STORAGE_ACCOUNT}")
[[ -n "${AZURE_STORAGE_CONTAINER:-}" ]] && ENV_VARS+=("AZURE_STORAGE_CONTAINER=${AZURE_STORAGE_CONTAINER}")
[[ -n "${AZURE_STORAGE_SAS_TOKEN:-}" ]] && ENV_VARS+=("AZURE_STORAGE_SAS_TOKEN=${AZURE_STORAGE_SAS_TOKEN}")
[[ -n "${AZURE_STORAGE_CONNECTION_STRING:-}" ]] && ENV_VARS+=("AZURE_STORAGE_CONNECTION_STRING=${AZURE_STORAGE_CONNECTION_STRING}")

# Voice Live envs
[[ -n "${AZURE_AGENT_ENDPOINT:-}" ]] && ENV_VARS+=("AZURE_AGENT_ENDPOINT=${AZURE_AGENT_ENDPOINT}")
[[ -n "${AGENT_PROJECT_NAME:-}" ]] && ENV_VARS+=("AGENT_PROJECT_NAME=${AGENT_PROJECT_NAME}")
[[ -n "${AGENT_ID:-}" ]] && ENV_VARS+=("AGENT_ID=${AGENT_ID}")
[[ -n "${INTERRUPTION_COOLDOWN_SEC:-}" ]] && ENV_VARS+=("INTERRUPTION_COOLDOWN_SEC=${INTERRUPTION_COOLDOWN_SEC}")
[[ -n "${TTS_STOP_TAIL_MS:-}" ]] && ENV_VARS+=("TTS_STOP_TAIL_MS=${TTS_STOP_TAIL_MS}")
[[ -n "${SESSION_VOICE_NAME:-}" ]] && ENV_VARS+=("SESSION_VOICE_NAME=${SESSION_VOICE_NAME}")
[[ -n "${SESSION_VOICE_TEMPERATURE:-}" ]] && ENV_VARS+=("SESSION_VOICE_TEMPERATURE=${SESSION_VOICE_TEMPERATURE}")

# Client credentials for Azure AI token minting
[[ -n "${AZURE_TENANT_ID:-}" ]] && ENV_VARS+=("AZURE_TENANT_ID=${AZURE_TENANT_ID}")
[[ -n "${AZURE_CLIENT_ID:-}" ]] && ENV_VARS+=("AZURE_CLIENT_ID=${AZURE_CLIENT_ID}")
if [[ -n "${AZURE_CLIENT_SECRET:-}" ]]; then
  SECRET_ARGS+=("azure-client-secret=${AZURE_CLIENT_SECRET}")
  ENV_VARS+=("AZURE_CLIENT_SECRET=secretref:azure-client-secret")
fi

# ACS auth path selection
SECRET_ARGS=()
if [[ "$USE_MI" == true ]]; then
  # Managed identity path: prefer ACS_ENDPOINT
  if [[ -n "${ACS_ENDPOINT:-}" ]]; then
    ENV_VARS+=("ACS_ENDPOINT=${ACS_ENDPOINT}")
  fi
  if [[ -n "$USER_MI_CLIENT_ID" ]]; then
    ENV_VARS+=("AZURE_CLIENT_ID=${USER_MI_CLIENT_ID}")
  fi
else
  # Secret-based connection string
  if [[ -n "${ACS_CONNECTION_STRING:-}" ]]; then
    SECRET_ARGS+=("acs-conn=${ACS_CONNECTION_STRING}")
    ENV_VARS+=("ACS_CONNECTION_STRING=secretref:acs-conn")
  fi
fi

# Optional ACCESS_TOKEN: prefer MI in ACA; but if present, put in a secret
if [[ -n "${ACCESS_TOKEN:-}" ]]; then
  SECRET_ARGS+=("agent-access-token=${ACCESS_TOKEN}")
  ENV_VARS+=("ACCESS_TOKEN=secretref:agent-access-token")
fi

# Convert arrays to CLI
ENV_CLI=()
for kv in "${ENV_VARS[@]}"; do
  ENV_CLI+=("--set-env-vars" "$kv")
done

SEC_UPDATE_CLI=()
if [[ ${#SECRET_ARGS[@]} -gt 0 ]]; then
  SEC_JOINED=$(printf "%s " "${SECRET_ARGS[@]}" | sed 's/ $//')
  SEC_UPDATE_CLI=("--replace-secrets" "$SEC_JOINED")
fi
# Ensure ACA managed environment exists
if ! az containerapp env show -g "$RG" -n "$ENV_NAME" >/dev/null 2>&1; then
  echo "Creating ACA environment $ENV_NAME in resource group $RG"
  if [[ -z "$LOCATION" ]]; then
    LOCATION=$(az group show -g "$RG" --query location -o tsv)
  fi
  az containerapp env create -g "$RG" -n "$ENV_NAME" -l "$LOCATION" --query properties.provisioningState -o tsv
fi

EXISTS=true
if ! az containerapp show -g "$RG" -n "$APP_NAME" >/dev/null 2>&1; then
  EXISTS=false
fi

if [[ "$EXISTS" == false ]]; then
  echo "Creating Container App $APP_NAME"
  CREATE_ARGS=(
    -g "$RG" -n "$APP_NAME"
    --environment "$ENV_NAME"
    --image "$IMAGE_REF"
    --ingress external --target-port 8080
  )
  if [[ "$ACR_USE_ADMIN" == true ]]; then
    az acr update -n "$ACR_NAME" --admin-enabled true >/dev/null
    ACR_USER=$(az acr credential show -n "$ACR_NAME" --query username -o tsv)
    ACR_PASS=$(az acr credential show -n "$ACR_NAME" --query passwords[0].value -o tsv)
    CREATE_ARGS+=( --registry-server "$ACR_LOGIN_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" )
  fi
  az containerapp create "${CREATE_ARGS[@]}" --query systemData -o none
else
  # If app exists but provisioning failed, delete and recreate with credentials
  PROV=$(az containerapp show -g "$RG" -n "$APP_NAME" --query properties.provisioningState -o tsv || echo "Unknown")
  if [[ "$PROV" != "Succeeded" ]]; then
    echo "Deleting failed Container App $APP_NAME (state=$PROV) to recreate with registry credentials"
    az containerapp delete -g "$RG" -n "$APP_NAME" -y
    echo "Recreating Container App $APP_NAME"
    CREATE_ARGS=(
      -g "$RG" -n "$APP_NAME"
      --environment "$ENV_NAME"
      --image "$IMAGE_REF"
      --ingress external --target-port 8080
    )
    if [[ "$ACR_USE_ADMIN" == true ]]; then
      az acr update -n "$ACR_NAME" --admin-enabled true >/dev/null
      ACR_USER=$(az acr credential show -n "$ACR_NAME" --query username -o tsv)
      ACR_PASS=$(az acr credential show -n "$ACR_NAME" --query passwords[0].value -o tsv)
      CREATE_ARGS+=( --registry-server "$ACR_LOGIN_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" )
    fi
  az containerapp create "${CREATE_ARGS[@]}" --query systemData -o none
  fi
fi

# Configure registry auth (optional admin path for quickstart) if app is provisioned
if [[ "$ACR_USE_ADMIN" == true ]]; then
  echo "Configuring registry credentials for ${ACR_LOGIN_SERVER} via admin user"
  az acr update -n "$ACR_NAME" --admin-enabled true >/dev/null
  ACR_USER=$(az acr credential show -n "$ACR_NAME" --query username -o tsv)
  ACR_PASS=$(az acr credential show -n "$ACR_NAME" --query passwords[0].value -o tsv)
  az containerapp registry set -g "$RG" -n "$APP_NAME" --server "$ACR_LOGIN_SERVER" --username "$ACR_USER" --password "$ACR_PASS" >/dev/null || true
fi

# Assign identity
if [[ "$USE_MI" == true ]]; then
  if [[ -n "$USER_MI_CLIENT_ID" ]]; then
    echo "Assigning user-assigned identity"
    az containerapp identity assign -g "$RG" -n "$APP_NAME" --user-assigned "${USER_MI_CLIENT_ID}" >/dev/null
  else
    echo "Assigning system-assigned identity"
    az containerapp identity assign -g "$RG" -n "$APP_NAME" --system-assigned >/dev/null
  fi
fi

# Apply secrets (use secret set to avoid quoting issues)
if [[ ${#SECRET_ARGS[@]} -gt 0 ]]; then
  SECRET_SET_ARGS=( -g "$RG" -n "$APP_NAME" )
  for s in "${SECRET_ARGS[@]}"; do
    SECRET_SET_ARGS+=( -s "$s" )
  done
  az containerapp secret set "${SECRET_SET_ARGS[@]}" >/dev/null
fi

echo "Updating Container App env and image..."
az containerapp update -g "$RG" -n "$APP_NAME" \
  --image "$IMAGE_REF" \
  ${ENV_CLI[@]} \
  --query properties.configuration -o table

echo "Done. App URL:"
az containerapp show -g "$RG" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv
