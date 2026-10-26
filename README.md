# Exotic Option Architect — Peter Xikai Wang

CFFEX (HO/IO/MO) JW-SVI vol surfaces + exotic option pricer.

## Stack

- **Frontend**: Vite + React (Google AI Studio origin)
- **Backend**: FastAPI (`/api/*`) reading SQLite
- **DB**: `data/cffex_vol.sqlite` (Wind options + IH/IF/IM futures + JW-SVI)

## Local

```bash
# API
python -m uvicorn backend.app:app --reload --port 8000

# UI (proxies /api -> :8000)
npm install && npm run dev
```

## Deploy Cloud Run

`data/cffex_vol.sqlite` is **gitignored** (142MB > GitHub limit). Deploy from a machine that has the file:

```bash
# one-time
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

./deploy.sh
```

Or:

```bash
gcloud run deploy exotic-option-architect --source . --region us-west1 --allow-unauthenticated --memory 1Gi
```

## Push code to GitHub

```bash
git add -A
git commit -m "Add FastAPI + JW-SVI CFFEX surfaces for HO/IO/MO"
git push origin main
```

Do **not** commit `data/cffex_vol.sqlite`.
