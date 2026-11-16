# Phase 1 — US Implied / Realized Correlation：IV 探查 + 权重方案

对应 Prompt **1.1**（本地覆盖度 + BBG/CBOE 拉取）与 **1.2**（动态权重、无前视偏差）。

---

## 1.1 本地覆盖度结论（先读这个）

| 标的 | 本地现货 | 期权链 | 按 Tenor×Delta/Mny 的 IV 曲面库 | ATM IV 时间序列 |
|------|----------|--------|----------------------------------|-----------------|
| **SPX** | 有（CMDS panel / INDEXEOD） | 有 `archive/spx_options.parquet`（~2024-06→2026-05） | **无**标准化曲面表 | 有 `data/spx_iv_computed.parquet`（自算） |
| **NDX** | CMDS 可查 | **无**本地 dump | **无** | **无** |
| **11 SPDR** XLK…XLC | **无**本地 IV/链 | **无** | **无** | **无** |
| **Top 50 个股** | 部分现货可从 CMDS 拉 | **无**完整曲面库 | **无** | 个别短样本（如 AAPL OPRA IV） |

本地 `cffex_vol.sqlite` / `commodity_vol.sqlite` 只有 **中国股指/商品** `option_iv`（tenor×moneyness 有），**不含美股**。

探查脚本：

```bash
cd "课题：correlation"
python explore_us_iv_coverage.py           # 写 data/explore_us_iv_coverage_report.json
python explore_us_iv_coverage.py --try_bbg # 有 Terminal 时冒烟
```

---

## 1.1 Bloomberg / CBOE 批量拉取

### 标准化 IV（曲面点 → 面板）

| 类型 | Ticker 例 | 推荐 Field（按优先级） |
|------|-----------|------------------------|
| 指数 | `SPX Index`, `NDX Index` | `30DAY_IMPVOL_100.0%MNY_DF`, `90DAY_…`, `180DAY_…`, `360DAY_…` |
| 行业 ETF | `XLK US Equity`, … | 同上；可加 `30DAY_IMPVOL_25%DELTA_DF` 等 |
| 个股 | `AAPL US Equity` | 同上；若空则 `HIST_CALL_IMP_VOL` / `HIST_PUT_IMP_VOL` |

Tenor 映射：1M→30DAY，3M→90DAY，6M→180DAY，1Y→360DAY。  
Moneyness：`100.0%MNY_DF` ≈ ATM forward；Skew：`25%DELTA_DF`。

模板脚本（需本机 Bloomberg）：[`pull_bbg_us_iv_cor_template.py`](pull_bbg_us_iv_cor_template.py)  
（由 `explore_us_iv_coverage.py` 自动生成）

```python
with pdblp.bopen(timeout=20000) as con:
    df = con.bdh(
        tickers=["SPX Index", "XLK US Equity", "AAPL US Equity"],
        flds="90DAY_IMPVOL_100.0%MNY_DF",
        start_date="20190101",
        end_date="20261113",
    )
```

分块：每批 ≤40 ticker；每个 field 单独 `bdh`，避免字段×标的爆炸。

### CBOE 官方隐含相关

| Index | Field | 用途 |
|-------|-------|------|
| `COR1M Index` | `PX_LAST` | 1M implied corr benchmark |
| `COR3M Index` | `PX_LAST` | 3M |
| `COR6M Index` | `PX_LAST` | 6M |
| `COR1Y Index` | `PX_LAST` | 1Y |

与 Clean/Dirty 自建序列对照时，对齐交易日历后比较水平与变化。

---

## 1.2 权重方案（Σω=1，无前视偏差）

### A. SPX ↔ 11 SPDR 行业 ETF

**目标：** 每个再平衡日 \(t\) 得到 \(\omega_s(t)\)，\(s\in\{\mathrm{XLK},\ldots,\mathrm{XLC}\}\)，\(\sum_s\omega_s=1\)。

**推荐流水线（点位时间一致）：**

1. **成分 + 权重（时点 \(t\)）**  
   - 本地：`build_spx_weights.py` → `spx_weights_approx.parquet`（月末 FMC 近似）  
   - 或 BBG：`INDX_MEMBERS` / `INDX_MWEIGHT` on `SPX Index` as-of \(t\)
2. **行业映射**  
   - BBG `GICS_SECTOR_NAME` → SPDR（XLK…XLC）  
   - 脚本内静态字典见 `build_spx_sector_top50_weights.py`
3. **聚合** \(\omega_s(t)=\sum_{i\in s} w_i(t)\)，再对 11 个 ETF **重新归一化**（未映射权重丢掉或进 OTHER 后丢弃，需在笔记中说明）。

**不要用：** 今日 ETF AUM 回填历史；不要用“当前”GICS 覆盖五年前成分而不做时点映射。

**公开数据替代：** 月度 S&P 部门权重白皮书 / ETF 持仓文件质量参差，仅作校验。

### B. SPX Top 50（防前视）

对每个月末 \(t\)：

1. 仅使用 **\(t\) 日（或 \(t\) 月末）** 的成分名单与权重 \(w_i(t)\)  
2. 按 \(w_i(t)\) 降序取前 50  
3. \(\omega_i^{\mathrm{Top50}}(t)=w_i(t)/\sum_{j\in\mathrm{Top50}(t)} w_j(t)\)  
4. 下一期内（到 \(t+1\) 再平衡前）冻结名单与权重，或按研究设定每日用最新权重但 **名单仍冻结到再平衡**（两种口径写进参数）

**禁止：** 用样本期末 Top50 回测全样本；用“今日 SPX 成分”生成历史篮子。

脚本：

```bash
python build_spx_weights.py --monthly True          # 若尚未有近似权重
python build_spx_sector_top50_weights.py --top 50
# → data/spx_top50_weights.parquet
# → data/spx_spdr11_weights_approx.parquet  (无 BBG 时用名称启发式，需替换为 GICS)
```

### Clean vs Dirty 与权重的关系（提示）

- **Dirty（指数期权 vs 单名/ETF 期权）**：篮子 IV 用 \(\sum\omega_i\sigma_i^2\) 或加权平均 IV（按你定义的 Dirty 公式），\(\omega\) 用上表。  
- **Clean**：还需实现相关 / 协方差结构；权重同样必须时点对齐。  
- CBOE `COR*M` 为市场报价基准，权重方法论与官方成分以 CBOE 文档为准，与自建 SPDR/Top50 篮子不必相同。

---

## 建议阶段一门禁

1. 跑通 `explore_us_iv_coverage.py`，确认本地缺口（预期：仅 SPX 链/ATM）。  
2. 有 BBG 的机器上跑 `pull_bbg_us_iv_cor_template.py`，落 `bbg_us_iv_cor_panel.parquet`。  
3. 跑权重脚本；用 BBG GICS 替换名称启发式。  
4. 再进入 Clean/Dirty 相关度量实现。
