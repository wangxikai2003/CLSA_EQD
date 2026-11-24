#!/usr/bin/env bash
# Deploy main to Cloud Run (us-west1). Does not push git.
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

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [[ "$BRANCH" != "main" && "${ALLOW_NON_MAIN_DEPLOY:-}" != "1" ]]; then
  echo "Refusing to deploy from '$BRANCH'."
  echo "Cloud Run ships only from main. Set ALLOW_NON_MAIN_DEPLOY=1 to override."
  exit 1
fi

if [[ ! -f data/cffex_vol.sqlite ]]; then
  echo "Missing data/cffex_vol.sqlite (not in git; ~150MB local copy)."
  echo "From the parent vol_trading repo (one level up, not this git root):"
  echo "  cd .. && bash scripts/sync_db_to_app.sh"
  echo "Or rebuild then sync:"
  echo "  cd .. && python pipeline/build_vol_db.py && python pipeline/materialize_svi_matrices.py && bash scripts/sync_db_to_app.sh"
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

PREV="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.latestReadyRevisionName)' 2>/dev/null || true)"

echo "==> Deploying $SERVICE to $REGION (project=$PROJECT) with no traffic shift"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 40 \
  --timeout 600 \
  --no-traffic \
  --quiet

# AI Studio mounts GCS over /app/dist — strip so Vite build from image is served.
# --no-traffic keeps the previous ready revision at 100% until smoke passes.
echo "==> Removing AI Studio /app/dist volume mount (no traffic shift)"
gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT" \
  --clear-volume-mounts \
  --clear-volumes \
  --no-traffic \
  --quiet

REV="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.latestCreatedRevisionName)')"
echo "==> Candidate revision $REV (previous ready: ${PREV:-none})"
gcloud run services update-traffic "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT" \
  --update-tags "smoke=${REV}" \
  --quiet

SMOKE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json | python3 -c '
import json, sys
doc = json.load(sys.stdin)
for item in doc.get("status", {}).get("traffic", []):
    if item.get("tag") == "smoke" and item.get("url"):
        print(item["url"])
        break
')"

if [[ -z "$SMOKE_URL" ]]; then
  echo "Could not resolve smoke tag URL. Traffic stays on ${PREV:-previous revision}."
  exit 1
fi

if ! bash scripts/smoke_live.sh "$SMOKE_URL"; then
  echo "Smoke failed. Traffic stays on ${PREV:-previous revision}."
  gcloud run services update-traffic "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --remove-tags smoke \
    --quiet || true
  exit 1
fi

gcloud run services update-traffic "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT" \
  --to-revisions "${REV}=100" \
  --remove-tags smoke \
  --quiet

echo "==> Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)'
