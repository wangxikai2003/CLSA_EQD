# Exotic Option Architect（CLSA_EQD）

Vite + React 前端 + FastAPI 后端。读取本目录 `data/cffex_vol.sqlite`（由上级仓库 `pipeline/` 构建）。

上级总览：[../README.md](../README.md)

## 结构

```
CLSA_EQD/
├── backend/app.py          # FastAPI：/api/* + 生产态静态 dist
├── backend/api/v1/market.py  # yield / vol-cone / tquote / synth-tenors
├── components/             # UI（VolatilitySurface、SpreadView、Exotic…）
├── services/               # api.ts、api_v1.ts、volatilityService.ts
├── data/cffex_vol.sqlite   # 部署用 DB 副本（勿提交 Git）
├── Dockerfile              # 多阶段：npm build + Python
└── deploy.sh               # gcloud run deploy + 清除 AI Studio 挂载
```

**Spread tab**：用挂牌期货实际 DTM 线性配比合成 3M/6M/9M/18M，支持跨期 / 跨品种 spread 与 spread-of-spread。API：`GET /api/v1/market/synth-tenors`。

## 本地

先确保 DB 已同步：

```bash
# 在 vol_trading 根目录
bash scripts/sync_db_to_app.sh
```

```bash
# API
python -m uvicorn backend.app:app --reload --port 8000

# UI（vite 代理 /api → :8000）
npm install && npm run dev
```

## 部署

```bash
# 需本机已有 data/cffex_vol.sqlite（~150MB，gitignore）
./deploy.sh
```

- 服务：`exotic-option-architect-peter-xikaiwang`
- 区域：`us-west1`
- 部署后会 `--clear-volumes`，避免 AI Studio GCS 覆盖 `/app/dist`

## Git

本目录为独立 git 仓库时可单独 push。**不要**提交：

- `data/cffex_vol.sqlite*`
- `node_modules/`、`dist/`
