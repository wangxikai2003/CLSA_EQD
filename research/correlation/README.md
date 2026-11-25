# Correlation 研究子项目

US equity / SPX 相关性与 IV 研究进度，挂在 `CLSA_EQD` 的 `research/correlation-phase1` 分支下。

## 进度摘要（截至 CMDS corr_mark v1）

### A. Phase 1（NDX / SPX IV · 权重 · 实现相关）

- **价格面板**：CMDS Oracle（JDBC/`ojdbc8.jar` 本地）→ `fetch_cmds_price_panel.py` / `corr_mark_local.py`
- **IV**：Black-76 ATM IV → `compute_spx_iv_from_prices.py`；VIX 对照 → `fetch_spx_iv_proxy.py`
- **权重**：`build_spx_weights.py` / Top50·SPDR11
- **NDX 隐含相关**：AM/PM、Clean/Dirty → `ndx_implied_corr.py`
- **US 实现相关面板**：`compute_us_realized_corr.py` + `validate_phase1_pipeline.py`

### B. corr_mark → CMDS（realized / bumped corr）✅ 已落地

不改 desk 源码 `corr_mark.py`，新开适配层：

| 能力 | 状态 | 关键文件 |
|------|------|----------|
| 标的覆盖盘点 | ✅ | `CORR_MARK_CMDS_COVERAGE.md`、`data/corr_mark_universe.csv` |
| 映射 / 探针 | ✅ | `corr_mark_cmds_mapping.py`、`probe_corr_mark_cmds_coverage.py` |
| 拉数面板 | ✅ | `fetch_cmds_price_panel.py --corr-mark-universe`（parquet 本地，不入库） |
| IDX.IDX / EQ.EQ | ✅ | `corr_mark_cmds.py --simple-last` → `data/corr_mark_cmds_matrix_*.csv` |
| 跨时区 OPEN/LAST、FX、proxy | ✅ | `corr_mark_cmds.py --rules` → `*_rules.csv` |
| 与 `corr_mark_local` 交叉验证 | ✅ | IDX 数值一致 |

**已知降级**：无 Pulse/Beidou 生产全量轴；美股无 `ADJCLOSE_FORWARD`；无 `_T170`/时点 FX；不做 RFQ/邮件。

详见：`CORR_MARK_CMDS_PLAN.md`、`CORR_MARK_CMDS_COVERAGE.md` §8。

## 目录说明

| 路径 | 说明 |
|------|------|
| `*.py` | 脚本（desk `corr_mark.py` + CMDS 本地替代） |
| `data/` | 宇宙 CSV、覆盖表、矩阵与 manifest；完整 parquet 本地生成 |
| `lib/` | 本地放 `ojdbc8.jar`（gitignore，不入库） |
| `参考资料/` | 仅 README 索引 |

## 本地运行（CMDS corr_mark）

```bash
export CMDS_HOST=... CMDS_PORT=21010 CMDS_SERVICE=cmds
export CMDS_USER=... CMDS_PASSWORD=...   # 或 CMDS_CREDS_DOCX
# lib/ojdbc8.jar 需自备

python fetch_cmds_price_panel.py \
  --corr-mark-universe data/corr_mark_universe.csv \
  --out data/corr_mark_cmds_panel.parquet

python corr_mark_cmds.py --mode IDX.IDX --simple-last
python corr_mark_cmds.py --mode EQ.EQ --simple-last
python corr_mark_cmds.py --mode EQ.EQ --rules
```

## 下一步（未完成）

1. 若有 Pulse `CORRELATION_MATRIX` / Beidou `t_config_eq` dump → 扩写 `corr_mark_universe.csv` 对齐生产轴
2. 全成份日频全样本 + Web API（NDX/SPX 研究链路）
3. BBG COR* / 官方 IV 字段对照；更长历史期权价

## Web 上线（Realized Corr）

一键数据：`python build_web_realized_data.py --skip-cmds-fetch`  
产出在 `CLSA_EQD/data/`：`corr_web_matrices.json`、`corr_realized_timeseries.parquet`、`corr_mark_cmds_panel.parquet`。

后端：`GET/POST /api/v1/correlation/*`（universe / pairwise / matrix / spreads）  
前端：Market Data → **Corr**；Spread → **Corr** 子 Tab。

## Web 上线（NDX Implied / CRP）

离线（课题树）：`ndx_implied_corr.py` → `compute_us_realized_corr.py` → `validate_phase1_pipeline.py`  
Web 导出：`python build_web_implied_data.py` → `CLSA_EQD/data/corr_ndx_implied_crp.parquet`  

后端：`GET /api/v1/correlation/implied-crp/meta`、`GET .../implied-crp/series`  
前端：Spread → Corr → 数据源 **Implied vs Realized (CRP)**（NDX Top50 / NDXP）。
