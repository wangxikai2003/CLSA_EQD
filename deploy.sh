#!/usr/bin/env bash
# Push to GitHub + deploy to Cloud Run (us-west1).
set -euo pipefail
cd "$(dirname "$0")"

SERVICE="${SERVICE:-exotic-option-architect}"
REGION="${REGION:-us-west1}"
PROJECT="${PROJECT:-}"

if [[ ! -f data/cffex_vol.sqlite ]]; then
  echo "Missing data/cffex_vol.sqlite — copy from vol_trading/data first."
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud not found. Install: brew install --cask google-cloud-sdk"
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
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --quiet

echo "==> Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)'
