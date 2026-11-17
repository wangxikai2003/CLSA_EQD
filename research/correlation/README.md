# Correlation 研究子项目

US equity / SPX 相关性与 IV 研究进度，挂在 `CLSA_EQD` 的 `research/correlation-phase1` 分支下。

## 进度摘要（Phase 1）

- **价格面板**：CMDS Oracle（JDBC/`ojdbc8.jar` 本地）拉取 SPX 成分日线 → `fetch_cmds_price_panel.py` / `corr_mark_local.py`
- **IV**：不以 VIX 为主；从 `spx_options.parquet` 用 Black-76 反算 ATM IV（约 2 年）→ `compute_spx_iv_from_prices.py`；VIX 仅作对照 → `fetch_spx_iv_proxy.py`
- **权重**：CMDS `CONSTITUENT_WEIGHT` 为空，用 USSHARE FMC/流通股本估算日度权重 → `build_spx_weights.py`；Top50 / SPDR11 近似 → `build_spx_sector_top50_weights.py`
- **覆盖盘点**：本机 US IV 缺口 → `explore_us_iv_coverage.py` + `data/explore_us_iv_coverage_report.json`
- **BBG 模板**：`pull_bbg_us_iv_cor_template.py`（需终端 Bloomberg）

详见：`DATA_NOTES.md`、`IV_AND_SPX_WEIGHTS.md`、`PHASE1_IV_AND_WEIGHTS.md`。

## 目录说明

| 路径 | 说明 |
|------|------|
| `*.py` | 脚本（desk `corr_mark.py` + 本地替代） |
| `data/` | 仅提交小型报告/样例矩阵；完整 parquet 本地生成 |
| `lib/` | 本地放 `ojdbc8.jar`（gitignore，不入库） |
| `参考资料/` | 仅 README 索引 |

## 本地运行要点

1. 将 Oracle JDBC jar 放到 `lib/ojdbc8.jar`
2. CMDS 账号走环境变量 / 本地 handbook，**不要**写入仓库
3. `python fetch_cmds_price_panel.py` → `python corr_mark_local.py` 等

## NDX 隐含相关（已建模）

- 脚本：`ndx_implied_corr.py`（CMDS OPRA mid / LAST + 官方权重 → tenor×moneyness ρ 曲面）
- 说明：`NDX_IMPLIED_CORR_MODEL.md`
- 结果：`data/ndx_implied_corr_surface.parquet`（pilot：15 日 × 30/60/90D × 0.95/1.00/1.05）

## 下一步（未完成）

- 全成份（`--top-n 0`）与更密日期网格
- BBG 正式 GICS / IV / COR* 对照
- Clean / Dirty correlation 模块对齐 desk `corr_mark.py`
- 更长历史期权价（>2y）若数据源可用
