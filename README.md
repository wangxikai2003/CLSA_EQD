# Exotic Option Architect（CLSA_EQD）

Vite + React 前端 + FastAPI 后端。可部署主干是 **`main`**。协议见 [AGENTS.md](AGENTS.md)。

上级数据管线在 `../`（`vol_trading`）：`pipeline/` 拉数建库，`scripts/sync_db_to_app.sh` 拷进本目录。本 git 仓库不包含那两个路径。

## 功能矩阵（`main`）

| Tab | 内容 |
|---|---|
| Vanilla | 香草 payoff / Greeks，Strike 轴可切换 |
| Exotics | Exotic Workbench（定价 / solve / Greeks term-structure）；Legacy Exotic Pricer 折在里面 |
| Volatility Surface | JW-SVI / spline 曲面 |
| Market Data | Yield（CN SHIBOR+国债 / US SOFR）、Vol Analytics、T-Quote、Realized Corr |
| Spread | 合成期限价差、Vol spread、Corr 期限/跨资产价差 |
| QIS | Calendar vol：`quantile` / `garch_z` / `regime` / `regime_momentum` / `regime_fade` / `regime2`，以及 `side_mode`、`spread_mode` |

关键 API：

- `GET /api/v1/correlation/universe`
- `GET /api/v1/market/yield-curve`
- `GET /api/v1/exotic/presets`
- `GET /api/v1/qis/strategies`
- `GET /api/v1/qis/backtest/{id}?signal_mode=regime2`

研究用 Corr 全量脚本在分支 `research/correlation-phase1`，不随镜像运行时逻辑走。镜像只 COPY `research/correlation` 的瘦身树和下面列出的 `data/corr_*`。

## 结构

```
CLSA_EQD/
├── backend/app.py
├── services/api_v1/        # 按域拆分的 API client（index.ts 再导出）
├── components/ExoticWorkbench/
├── Dockerfile
├── deploy.sh               # 仅 main；新 revision smoke 通过才切流量
└── data/                   # 见下
```

## 数据

`data/cffex_vol.sqlite`（约 150MB）**不提交**。Dockerfile 的 `COPY` 依赖本机文件。获取方式（在上级 `vol_trading` 根目录，不是本 git 根）：

```bash
cd ..
bash scripts/sync_db_to_app.sh
# 或重建后再同步：
# python pipeline/build_vol_db.py && python pipeline/materialize_svi_matrices.py
# bash scripts/sync_db_to_app.sh
```

镜像还会 COPY：`rates_eod.parquet`、`index_spot.parquet`、`commodity_fut.sqlite`、`vol_spread_levels.parquet`、`qis_results.sqlite`，以及 Corr 运行时文件：

- `data/corr_mark_cmds_panel.parquet`
- `data/corr_realized_timeseries.parquet`
- `data/corr_web_matrices.json`（服务读 JSON，不读同名 parquet）
- `data/corr_mark_universe.csv`

`data/corr_web_matrices.parquet` 与 `data/corr_web_build_manifest.json` 在仓库里，**不进镜像**。

Wind 连接只用环境变量 `WIND_USER`、`WIND_PASSWORD`、`WIND_DSN`。见 [docs/DATABASE_HANDOFF.md](docs/DATABASE_HANDOFF.md)。

## 本地

```bash
# 上级 vol_trading 根目录
bash scripts/sync_db_to_app.sh

# 本目录
python -m uvicorn backend.app:app --reload --port 8000
npm install && npm run dev
```

## 部署

仅 `main`，且同一时间只有一个部署者：

```bash
git switch main && git pull
./deploy.sh
```

脚本先 `--no-traffic` 发布，去掉 AI Studio 对 `/app/dist` 的挂载，再对 candidate revision 跑 `scripts/smoke_live.sh`。失败则保留上一 ready revision 的流量。

- 服务：`exotic-option-architect-peter-xikaiwang`
- 区域：`us-west1`

## Git

不要提交：`data/cffex_vol.sqlite`、`data/synth_tenors_cache.json.gz`、`data/exports/` 产物、`node_modules/`、`dist/`、任何口令。
