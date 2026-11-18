# GCS hot-reload epic

This branch is **not** on `main` and must not be merged as-is.

It preserves the unwired modules that used to live only in `git stash` (`wip before regime2 chart fix`):

- `backend/data_hot_reload.py`
- `backend/api/v1/admin_data.py`
- `docs/HOT_UPDATE_DATA.md`

`main` already has `backend/data_paths.py` (`LIVE_DATA_DIR`, `HOT_FILES`) but `backend/app.py` does **not** start a GCS poll or mount `/api/admin`. Do not `stash pop` the old patch onto `main`: that stash was based on `research/correlation-phase1` and its `app.py` / `App.tsx` hunks drop Corr and fight the current QIS/Workbench tree.

To finish this epic, branch from current `main` and:

1. Add `google-cloud-storage` to `backend/requirements.txt` (keep `pandas<3` and `arch==6.2.0` if Cloud Build still needs those pins).
2. Call `sync_from_gcs` from app startup without changing signal math or Corr loaders.
3. Keep the admin routes token-gated (`DATA_RELOAD_TOKEN`).
4. Add a test that the routers stay mounted (`/api/v1/correlation/universe`, `/api/v1/exotic/presets`, `/api/v1/qis/strategies`).

Research exports from the other stash (regime2 OFAT / force-flat tables) were not committed. They remain in git object `22bd0073b1ab61f937724e7ab4d45ed99fabfb4d` until garbage collection (`scripts/analyze_regime2_force_flat.py` is already on `main`).
