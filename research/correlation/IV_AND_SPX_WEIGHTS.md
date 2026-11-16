# IV 资源盘点 + SPX 权重（官方公式与自算）

更新日期：2026-11-13  
约束：deepvol 大概率不可用；SPX 权重自行估算；IV 目标长度约 **5 年**。

---

## 1. IV 资源

### 本地文件

| 资源 | 路径 | 长度 | 备注 |
|------|------|------|------|
| SPX 期权链 | `vol_trading/archive/spx_options.parquet` | ~2024-06 → 2026-05（~2 年） | 有 close/delta，**无 iv 列**，可反算 |
| 中国股指 IV | `vol_trading/data/cffex_vol.sqlite` | ~2019-12 → 2026-10 | 与美股无关 |
| 现货 | `课题：correlation/data/cmds_price_panel.parquet` | SPX 等约 2019 起 | 无 IV |

### 远程

| 源 | 状态 |
|----|------|
| CMDS `OPTIONVOLATILITY` / `OPTIONEOD` | 单名（如 AAPL OPRA）有短样本；**SPX 指数 IV 历史很弱**（见探测日志） |
| deepvol `172.22.157.62:3306` | TCP 通，MySQL 会话易断；**不作为主源** |
| Wind | 有 `VIX.GI` 等指数；**不是** SPX 期权曲面 IV；美股期权链本账号通常不够 |

### IV：自算（不用 VIX 作主序列）

| 序列 | 文件 | 长度 | 性质 |
|------|------|------|------|
| **自算 ATM IV（主）** | `data/spx_iv_computed.parquet` | 取决于 `archive/spx_options.parquet`（约 **2024-06→2026-05**） | Black-76 从期权价反算；近 ATM、TTM 20–40 日、OTM 优先 |
| 原始期权价 | `vol_trading/archive/spx_options.parquet` | 同上 | close + spot；脚本 `compute_spx_iv_from_prices.py` |
| VIX /100 | `data/spx_iv_proxy_vix.parquet` | 2020→今 | **仅诊断对照**，不作主 IV |
| CMDS 预存 IV | `data/spx_iv_atm_cmds_opra.parquet` | ~2024-08→今 | 供应商 IV；本课题优先自算价 |

**长度现实：** 现有可反算的 SPX 期权价历史约 **2 年**，到不了 5 年。要更长只能另找更早的期权价 dump / Vendor。

deepvol：已放弃作主源。

---

## 2. SPX 官方权重

来源：S&P U.S. Indices Methodology + Float Adjustment Methodology。

\[
\mathrm{FMC}_i = P_i \times S_i \times \mathrm{IWF}_i,\quad
w_i = \frac{\mathrm{FMC}_i}{\sum_j \mathrm{FMC}_j}
\]

- \(P\)：价格  
- \(S\)：总股本  
- \(\mathrm{IWF}\)：Investable Weight Factor = 可投资流通股 / 总股本（剔除战略持股等）  

指数水平：\(\sum P_j S_j \mathrm{IWF}_j / \mathrm{Divisor}\)。

CMDS `INDEXCONSTITUENTS.CONSTITUENT_WEIGHT` 对 SPX **为空**，不能直接用官方权重序列。

---

## 3. 自算权重（本课题采用）

**近似 FMC（非官方 IWF）：**

\[
\widehat{\mathrm{FMC}}_i = P_i \times \widetilde{S}^{\mathrm{float}}_i
\quad\text{或}\quad
P_i \times S^{\mathrm{total}}_i \times \frac{\mathrm{FLOAT}}{\mathrm{TOTAL}}
\]

数据优先：

1. `INDEXCONSTITUENTS`：每日成分 `CONSTITUENT_SEC_ID`  
2. `EQUITYEOD.CLOSE_PRICE`：\(P_i\)  
3. `USSHAREEODDERIVATIVEINDICATOR`（按 `WINDCODE`）：`FLOAT_A_SHR_TODAY` / `TOT_SHR_TODAY`  

若流通股本缺失，退回总股本或等权。  
**明确标注**：\(\hat w\) ≠ S&P 官方权重，仅供研究。

脚本：`build_spx_weights.py` → `data/spx_weights_approx.parquet`（默认 **月度** 月末；覆盖率约 98%+）

---

## 4. 相关脚本索引

- `cmds_connect.py` — CMDS JDBC  
- `fetch_cmds_price_panel.py` — 指数/个股/FX 现货  
- `corr_mark_local.py` — realised corr 原型  
- `build_spx_weights.py` — SPX 近似权重（`--monthly True/False`）  
- `compute_spx_iv_from_prices.py` — **从期权价反算 ATM IV（主）**  
- `fetch_spx_iv_proxy.py` — VIX 仅作对照  

