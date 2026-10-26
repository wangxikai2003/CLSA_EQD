# Exotic Option Architect — frontend + FastAPI + CFFEX JW-SVI SQLite
FROM node:22-alpine AS frontend
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY index.html index.tsx App.tsx types.ts tsconfig.json vite.config.ts metadata.json ./
COPY components ./components
COPY services ./services
COPY utils ./utils
RUN npm run build && test -f dist/index.html && ls -la dist

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY data/cffex_vol.sqlite /app/data/cffex_vol.sqlite
COPY --from=frontend /app/dist /app/dist
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}
