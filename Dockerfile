# Exotic Option Architect — frontend + FastAPI + CFFEX JW-SVI SQLite
FROM node:22-alpine AS frontend
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY index.html index.tsx App.tsx types.ts tsconfig.json vite.config.ts metadata.json ./
COPY components ./components
COPY services ./services
COPY utils ./utils
COPY store ./store
COPY hooks ./hooks
RUN npm run build && test -f dist/index.html && ls -la dist

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY data/cffex_vol.sqlite /app/data/cffex_vol.sqlite
COPY data/rates_eod.parquet /app/data/rates_eod.parquet
COPY data/index_spot.parquet /app/data/index_spot.parquet
# Slim commodity futures-only DB (~20MB); full commodity_vol.sqlite (~1.4G) not shipped
COPY data/commodity_fut.sqlite /app/data/commodity_fut.sqlite
COPY data/vol_spread_levels.parquet /app/data/vol_spread_levels.parquet
# Realized Correlation web panels (Market/Spread Corr tabs).
# Runtime reads corr_web_matrices.json (see realized_corr_service). Do not COPY
# data/corr_web_matrices.parquet or data/corr_web_build_manifest.json unless that
# loader is switched off JSON — they are research artifacts, not image inputs.
COPY data/corr_mark_cmds_panel.parquet /app/data/corr_mark_cmds_panel.parquet
COPY data/corr_realized_timeseries.parquet /app/data/corr_realized_timeseries.parquet
COPY data/corr_web_matrices.json /app/data/corr_web_matrices.json
COPY data/corr_mark_universe.csv /app/data/corr_mark_universe.csv
# Runtime imports corr_mark_cmds.py / corr_mark_local.py / corr_mark_cmds_mapping.py
# from this tree (pairwise 500s with "No module named corr_mark_cmds" if they are absent).
# The rest of the Corr research pipeline stays on research/correlation-phase1.
COPY research/correlation /app/research/correlation
# Ship precomputed QIS results (also rebuilt below if missing / stale source)
COPY data/qis_results.sqlite /app/data/qis_results.sqlite
# Pre-bake synth-tenors JSON so first Spread request is near-instant
RUN cd /app/backend && PYTHONPATH=/app/backend python -c "from api.v1.synth_tenors import get_cached_payload, write_disk_cache; p=get_cached_payload(refresh=True); write_disk_cache(p, __import__('pathlib').Path('/app/data/synth_tenors_cache.json.gz')); print('synth cache', len(p.get('products',[])))"
# Refresh quantile cache against image data mtimes; rebase shipped garch_z rows
# to the same source_key (full garch rebuild is heavy; arch pinned for on-demand).
COPY scripts/build_qis_results.py /app/scripts/build_qis_results.py
COPY scripts/rebase_qis_source_key.py /app/scripts/rebase_qis_source_key.py
RUN cd /app && PYTHONPATH=/app/backend python scripts/build_qis_results.py quantile
RUN cd /app && PYTHONPATH=/app/backend python scripts/rebase_qis_source_key.py
COPY --from=frontend /app/dist /app/dist
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}

# deploy-stamp 20261118T005007Z
