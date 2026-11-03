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
COPY --from=frontend /app/dist /app/dist
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}
