# AGENTS.md — CLSA_EQD 并行开发协议

给所有 coding agent 与人。开工前读完本节。违反协议是功能丢失（Corr / Workbench / Regime2 曾因此从线上消失）的直接原因。

## 单一主干

- **`main` 是唯一可部署 tip**，也是唯一开工基线。
- Cloud Run 服务 `exotic-option-architect-peter-xikaiwang`（us-west1）只从 `main` 发布。`./deploy.sh` 会拒绝其它分支，除非协调者显式设置 `ALLOW_NON_MAIN_DEPLOY=1`。
- 禁止把长寿命 `cursor/*` 当成第二套主干。功能分支寿命应短于 3 天，每天 rebase `origin/main`。
- 算法实验放 `research/<topic>`，**默认不部署**。要上线必须 PR 进 `main`，并带回归测试。
- `research/correlation-phase1` 是研究树，不是运行时。运行时 Corr 只以 `main` 上的瘦身 `research/correlation/` + `data/corr_*` 为准。禁止用该分支整棵 checkout 覆盖本地 `main`。

## 开工

```bash
git fetch origin
git switch -c cursor/<task-id> origin/main
```

1. 在 PR 标题标明 lane：`[qis]` `[corr]` `[exotic]` `[market]` `[platform]`。
2. 只改本 lane 的路径。越界改动在 PR 正文写 **Cross-lane**，并由 platform 负责合并。
3. **禁止 `git stash` 跨分支搬功能。** 未完成的工作 push 到 `wip/<task-id>`。
4. 合并前本地必跑：`npm run build` 与 `PYTHONPATH=backend pytest backend/tests -q`。
5. 同时只允许一个部署执行者。其它 agent 只开 PR。部署后 `./deploy.sh` 会对新 revision 打 tag 做 smoke，失败则**不切流量**。

## 模块所有权

| Lane | 可写 | 需协调才能改 |
|---|---|---|
| qis | `backend/engine/*signal*.py`, `calendar_vol_strategy.py`, `components/dashboard/QisView.tsx`, `backend/tests/test_*signal*` / `test_calendar_vol*` | `App.tsx` 顶栏、`backend/app.py` 路由表 |
| corr | `backend/api/v1/correlation.py`, `realized_corr_service.py`, `*Corr*Panel.tsx`, `data/corr_*`, `services/api_v1/correlation.ts` | 其它 Market 子 tab |
| exotic | `components/ExoticWorkbench/**`, `backend/api/v1/exotic_workbench.py`, `vectorized_engine.py`, `services/api_v1/exotic.ts` | Legacy `ExoticPricer.tsx`（默认只读） |
| market | `YieldCurveView.tsx`, `VolAnalyticsView.tsx`, `RvIvPredictView.tsx`, `VolatilitySurface.tsx`, `StrikeAxis*`, `services/api_v1/market.ts`, `backend/engine/rv_iv_predict.py` | 非 market 的 API client |
| platform | `Dockerfile`, `deploy.sh`, `backend/app.py` 挂载, `.github/`, `backend/data_paths.py`, `services/api_v1/http.ts` | 业务公式 |

共享客户端已按域拆到 `services/api_v1/*.ts`，桶文件是 `services/api_v1/index.ts`。不要再把新接口拼进一个巨型 `api_v1.ts`。

冲突若落在 `services/api_v1/index.ts`、`App.tsx`、`Dockerfile`、`backend/app.py`：禁止机械取一侧，由 platform lane 合并。

## 信号 / Regime 变更

- 新 `signal_mode` 必须带确定性单元测试，并在 `QisView` 文案里说明语义。
- 改变已有仓位含义的开关（例如 stress force-flat）先做 A/B，结果放 `data/exports/sweeps/`（该目录默认不入库），报告进 PR。默认保持「只挡住新开仓」。
- Dockerfile 只预热 quantile cache。garch / regime / regime2 首次请求可能冷启动，UI 需保持可等待，不要在镜像构建里跑全量 garch。

## 数据与密钥

- `data/cffex_vol.sqlite` **不在 git**。从上级仓库 `vol_trading` 同步，见 README「数据」。没有该文件无法 `./deploy.sh`。
- Wind 账号只用环境变量 `WIND_USER` / `WIND_PASSWORD` / `WIND_DSN`。禁止写入仓库。
- 不要提交 `data/synth_tenors_cache.json.gz` 或 `data/exports/` 扫描结果。

## 部署

```bash
git switch main && git pull
./deploy.sh          # 新 revision 0% 流量 → smoke → 才切 100%
```

Smoke 探针：`/api/v1/correlation/universe`、`/api/v1/market/yield-curve`、`/api/v1/exotic/presets`、`/api/v1/qis/strategies`，以及 `equity_3_6_calendar_vol_300?signal_mode=regime2`。
