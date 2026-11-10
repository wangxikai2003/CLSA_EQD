#!/usr/bin/env bash
# Push to GitHub + deploy to Cloud Run (us-west1).
set -euo pipefail
cd "$(dirname "$0")"

SERVICE="${SERVICE:-exotic-option-architect-peter-xikaiwang}"
REGION="${REGION:-us-west1}"
PROJECT="${PROJECT:-}"

# --- local machine: gcloud often installed but not on PATH ---
_GCLOUD_CANDIDATES=(
  "${HOME}/google-cloud-sdk-install/google-cloud-sdk/bin"
  "${HOME}/google-cloud-sdk/bin"
  "/opt/homebrew/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/bin"
  "/usr/local/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/bin"
)
for _d in "${_GCLOUD_CANDIDATES[@]}"; do
  if [[ -x "${_d}/gcloud" ]]; then
    export PATH="${_d}:$PATH"
    break
  fi
done
# Prefer Anaconda Python for gcloud (bundled 3.14 can break on this Mac)
if [[ -z "${CLOUDSDK_PYTHON:-}" && -x "${HOME}/anaconda3/bin/python3" ]]; then
  export CLOUDSDK_PYTHON="${HOME}/anaconda3/bin/python3"
fi

if [[ ! -f data/cffex_vol.sqlite ]]; then
  echo "Missing data/cffex_vol.sqlite"
  echo "From repo root run:  bash scripts/sync_db_to_app.sh"
  echo "Or:  python pipeline/build_vol_db.py && python pipeline/materialize_svi_matrices.py"
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud not found on PATH."
  echo "This machine usually has: ${HOME}/google-cloud-sdk-install/google-cloud-sdk/bin/gcloud"
  echo "Or install: brew install --cask google-cloud-sdk"
  echo "See also: ../quant_research/docs/CLOUD_RUN_DEPLOY_HANDOFF.md (sibling repo) or ask the vol_trading agent."
  exit 1
fi

if [[ -z "$PROJECT" ]]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "Set GCP project: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

echo "==> Deploying $SERVICE to $REGION (project=$PROJECT) from local source (includes sqlite)"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 40 \
  --timeout 300 \
  --quiet

# AI Studio mounts GCS over /app/dist — strip so Vite build from image is served
echo "==> Removing AI Studio /app/dist volume mount (if present)"
gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT" \
  --clear-volume-mounts \
  --clear-volumes \
  --quiet

echo "==> Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)'
