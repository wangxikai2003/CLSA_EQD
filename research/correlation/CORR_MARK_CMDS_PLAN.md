# corr_mark.py → CMDS 本地跑通方案（不改源码）

目标：在**不修改** `corr_mark.py` 的前提下，新开适配层，用 CMDS 替换其外部数据依赖，先复现 **realized / bumped correlation 核心算法**，再逐步逼近 desk 全链路。

---

## 1. `corr_mark.py` 结构分层

```
┌─────────────────────────────────────────────────────────────┐
│ A. 编排层 main()                                            │
│    mode = EQ.EQ / EQ.IDX / IDX.IDX / EQ.FX / IDX.FX         │
│    dump / email / RFQ / Corr01 risk                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ B. Universe / Axis                                          │
│    Pulse: CORRELATION_MATRIX (旧矩阵轴)                      │
│    Beidou Postgres: ref.t_config_eq (股票/指数名单)           │
│    exclude_list / new_und / override_info                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ C. Pair 构造 get_pair_list / get_pair_list_one              │
│    Calypso→BBG ticker、Region、PX_OPEN/PX_LAST、Pad、Alpha   │
│    FX 合成、proxy_info、跨时区 OPEN/LAST 规则                 │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ D. 行情拉取 (当前 = Bloomberg pdblp)                         │
│    PX_LAST / PX_OPEN + 复权参数                              │
│    FXConv 汇率、BBG_FX 单边合成、future_data CSV (_T170…)     │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ E. 收益与相关核心 (可完全本地化)                              │
│    pad 日 log return: ln(P_t / P_{t-pad})                   │
│    rolling_corr(window) → percentile(alpha) = base_corr     │
│    λ = max(0.5*(1-hl/504),0) → lambda_bump → 输出 corr      │
│    另输出 realized_corr = 滚动相关序列最新值                  │
└─────────────────────────────────────────────────────────────┘
```

**结论**：算法核（E）与 desk 基础设施（A/B/C/D 的 Pulse/Beidou/BBG/RFQ）可分离。CMDS 方案应 **新开脚本复制 E，用 CMDS 替换 D，用可配置宇宙替换 B**。

---

## 2. 源码数据依赖 → CMDS 映射

| corr_mark 依赖 | 用途 | CMDS 对应 | 匹配度 | 备注 |
|----------------|------|-----------|--------|------|
| Pulse `CORRELATION_MATRIX` | 旧矩阵轴 + old corr 对比 | **无直接等价表** | 低 | 首期用「自选宇宙 / INDEXCONSTITUENTS」代替轴；old 对比可省略或读本地 dump |
| Beidou `ref.t_config_eq` | EQ/IDX 名单 | `CDM.SECURITYCODE` + 成分表 | 中 | 用 BB_TICKER / WIND_CODE 建映射；非全量 Beidou |
| Bloomberg `bdh` PX_LAST | 股票/指数收盘 | `EQUITYEOD.CLOSE` / `ADJCLOSE_FORWARD`；`INDEXEOD.CLOSE` | **高** | 优先 `ADJCLOSE_FORWARD`，缺则 CLOSE |
| Bloomberg PX_OPEN | 跨时区对 | `EQUITYEOD.OPEN_PRICE`；`INDEXEOD.OPEN_PRICE` | **高** | 覆盖 US×APAC 等 OPEN/LAST 规则 |
| Bloomberg FX PX_LAST | FX 腿 / FXConv | `FXEOD`（WINDCODE=`USDHKD.FX` 等） | **高** | 已有 `fetch_cmds_price_panel` 通路 |
| `future_data/*_T170.csv` | 指数日内点 | **无** | 低 | Phase1 用 OPEN/CLOSE 近似；不假装有 T170 |
| `adjustmentNormal/Split` | 复权 | `ADJCLOSE_FORWARD` | 中高 | 口径接近但不保证 ≡ BBG |
| Calypso ticker / Beidou 别名 | 命名 | `SECURITYCODE.BB_TICKER` / `WIND_CODE` / `RIC` | 中 | 需显式 mapping 表（新文件维护） |
| Risk Corr01 / RFQ API | 下游 | **不做** | — | 本地方案边界外 |

### CMDS 推荐表

| 表 | 字段 | 角色 |
|----|------|------|
| `CDM.SECURITYCODE` | `SEC_ID, BB_TICKER, WIND_CODE, RIC, ASSET_TYPE` | ticker 解析 |
| `CDM.EQUITYEOD` | `TRADE_DATE, OPEN_PRICE, CLOSE_PRICE, ADJCLOSE_FORWARD` | 个股 OHLC |
| `CDM.INDEXEOD` | `TRADE_DATE, OPEN_PRICE, CLOSE_PRICE` | 指数 OHLC |
| `CDM.FXEOD` | `TRADE_DATE, OPEN, CLOSE, WINDCODE` | 汇率 |
| `CDM.INDEXCONSTITUENTS` | `INDEX_SEC_ID, TRADE_DATE, CONSTITUENT_*` | 可选宇宙（如 NDX/SPX 成分） |

已知可用指数（此前探测）：`SPX Index`, `NDX Index`, `HSI`, `HSCEI`, `HSTECH`, `SHSZ300`, `CSI1000`, `SSE50`, `NKY`, `SX5E` 等。

---

## 3. 核心算法（必须与源码一致，便于对照）

对每一对 `(u1, u2)`：

1. 取价格序列 \(P\)（OPEN 或 LAST，按 pair 规则），可选 FX 换算  
2. \(\mathrm{ret}_t = \ln(P_t / P_{t-\mathrm{pad}})\)（源码 `rolling(pad+1).apply`）  
3. \(c_t = \mathrm{corr}(\mathrm{ret}_{u1}, \mathrm{ret}_{u2})\) over `rolling_win`  
4. 取最近 `el ≈ look_back * 21` 个有效点  
5. `base = percentile(c, alpha)`；若方向 down 则用 `100-alpha`  
6. \(\lambda = \max(0.5(1 - hl/504), 0)\)，`corr = lambda_bump(base, λ, up)`  
7. 截断到 \((-0.979, 0.979)\)；另返回 `realized_corr = c_t` 最新值  

本地新脚本应 **逐行复用这套公式**（`corr_mark_local.py` 已有雏形），不要改 `corr_mark.py`。

---

## 4. 建议实施路径（新代码，不动源码）

### Phase 0 — 映射与探针（先做）
新文件建议：
- `corr_mark_cmds_mapping.py`：Calypso/Beidou 风格代码 → `SEC_ID` / CMDS 表  
- `probe_corr_mark_cmds_coverage.py`：对目标宇宙检查 OPEN/CLOSE/FX 覆盖率与日期区间  

输出：`data/corr_mark_cmds_coverage.json`

### Phase 1 — 最小可跑（IDX.IDX / 小 EQ 集合）
新文件：`corr_mark_cmds.py`（独立入口）

1. 宇宙：配置 YAML/CSV（例如 `SPX,NDX,HSI,HSCEI,SHSZ300` 或 NDX Top50）  
2. 行情：CMDS 拉面板 → `data/corr_mark_cmds_panel.parquet`（可复用/扩展 `fetch_cmds_price_panel`）  
3. Pair：简化版 `get_pair_list`（仅同区 LAST×LAST；跨区 OPEN/LAST 第二步再加）  
4. 调用与源码同构的 `calc_corr`  
5. 输出：`data/corr_mark_cmds_matrix.csv` +  pairwise 明细  

验收：与 `corr_mark_local.py` 在重叠标的上数值一致（同 pad/alpha/window）。

### Phase 2 — 对齐 desk 规则子集
- 接入 OPEN/LAST 跨时区规则（用 CMDS OPEN/CLOSE 近似 `fx_fix_map`）  
- FX 腿：`FXEOD` 合成交叉盘  
- Pad/Alpha 按 Region 默认值（5/99、5/90 等）  
- Proxy 表（BABA↔9988 等）可选  

### Phase 3 — 可选增强（仍不碰源码）
- 若有 desk dump 的 `CORRELATION_MATRIX` CSV，做 old vs new diff（模拟 Pulse）  
- 成分动态宇宙：`INDEXCONSTITUENTS` point-in-time  
- 与 `ndx_implied_corr` 的 realized 引擎对照（窗口定义不同，需文档说明）

---

## 5. 明确做不到 / 需降级的部分

| 能力 | 处理 |
|------|------|
| 完整 Beidou 全市场 EQ 轴 | 用 CMDS 成分/自选名单；规模可控 |
| Calypso 旧矩阵曲线名 | 本地无；对比靠外部 dump |
| Bloomberg 精确复权与点位字段 `_T170` | OPEN/CLOSE 近似；文档标注偏差 |
| RFQ / Corr01 / 邮件 | 不实现 |
| `eqdquant` / `trader_tool` / `pdblp` | 新脚本零依赖这些包 |

---

## 6. 推荐默认参数（与源码惯例对齐）

| 参数 | 典型值 | 含义 |
|------|--------|------|
| `look_back` | 24（月） | `el ≈ 24*21` 有效长度 |
| `rolling_win` | 63 | 滚动相关窗 |
| `pad` | 1 或 5 | log-return 跨度 |
| `alpha` | 93 / 99 / 90 | 分位数（按 pair 类型） |
| `corr_lambda` | CLI 传入 | 最终再 bump（矩阵汇总层） |

---

## 7. 建议的新文件清单（尚未实现，仅方案）

| 文件 | 职责 |
|------|------|
| `CORR_MARK_CMDS_PLAN.md` | 本文 |
| `corr_mark_cmds_mapping.py` | ticker→CMDS SEC_ID |
| `probe_corr_mark_cmds_coverage.py` | 覆盖率探针 |
| `corr_mark_cmds.py` | 主入口：拉数 + pair + calc_corr + 矩阵 |
| `data/corr_mark_universe.csv` | 可编辑宇宙 |
| `data/corr_mark_cmds_panel.parquet` | 行情缓存 |
| `data/corr_mark_cmds_matrix.csv` | 结果 |

---

## 8. 建议的下一步（确认后执行）

1. **先做 Phase 0**：选定宇宙（例如亚太指数 8 只 + 美股 Top20），跑覆盖率探针，确认 CMDS 日线是否够 `look_back`。  
2. **再做 Phase 1**：实现 `corr_mark_cmds.py`，与现有 `corr_mark_local.py` 交叉验证。  
3. 确认无误后再加 OPEN/LAST 与 FX（Phase 2）。

**一句话**：`corr_mark` 的“相关计算核”可用 CMDS 的指数/个股/FX 日线完整复现；缺的是 Pulse 旧矩阵轴、Beidou 全名单、BBG 日内点位与下游 RFQ——本地方案用可配置宇宙 + OPEN/CLOSE 近似即可先跑起来。

---

## 附录：标的/字段覆盖盘点

详见 [`CORR_MARK_CMDS_COVERAGE.md`](CORR_MARK_CMDS_COVERAGE.md) 与 `data/corr_mark_cmds_*_coverage.csv`（2026-11-17 探针）。

## 附录：已实现文件（v1）

| 文件 | 状态 |
|------|------|
| `data/corr_mark_universe.csv` | 已生成 |
| `corr_mark_cmds_mapping.py` | 已实现 |
| `probe_corr_mark_cmds_coverage.py` | 已实现 |
| `build_corr_mark_universe.py` | 已实现 |
| `fetch_cmds_price_panel.py --corr-mark-universe` | 已扩展 |
| `corr_mark_cmds.py` | 已实现（`--simple-last` / `--rules`） |
| `data/corr_mark_cmds_panel.parquet` | 已拉取 |
| `data/corr_mark_cmds_matrix*.csv` + `run_manifest.json` | 已产出 |
