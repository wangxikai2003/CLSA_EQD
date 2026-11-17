# corr_mark 原标的 × CMDS 覆盖明细

**原则**：不改变 `corr_mark.py` 标的集合；本文件只盘点「源码里能恢复的标的」在 CMDS 里有没有、对应字段有没有。

**重要边界**：生产宇宙来自两处外部库，本机**没有 dump**：

| 源 | 作用 | 本机状态 |
|----|------|----------|
| Pulse `CORRELATION_MATRIX`（CurveName=`Corr_*`） | 各 mode 矩阵轴 + old corr | **无** → 全量轴名单未知 |
| Beidou `ref.t_config_eq` | EQ/IDX 全名单 | **无** → 全量 Beidou 未知 |

因此本清单的标的范围 = **源码硬编码可恢复子集**（`exclude_list` / `proxy_info` / `force_proxy` / `override_info` / `beidou_to_calypso` / pair 规则里的指数 + `currency_map` 隐含 FX），**不是** desk 生产全量。

探针日期：2026-11-17；EOD 窗口：`20180101–20261231`；库：`10.23.129.18:21010/cmds`。

明细 CSV：

- `data/corr_mark_cmds_security_coverage.csv`
- `data/corr_mark_cmds_fx_coverage.csv`
- `data/corr_mark_cmds_proxy_coverage.csv`
- `data/corr_mark_internal_baskets.json`

---

## 0. 总览

| 类别 | 可恢复数 | CMDS 有证券主档 | 有可用日线 | 说明 |
|------|----------|-----------------|------------|------|
| 指数 IDX | 13 | **13/13** | 13（KOSPI2 历史短） | 全覆盖 |
| 个股/ETF EQ | 66 | **56/66** | 55（`113519.CH` 无 EOD） | 10 只无主档（多为退市/已 exclude） |
| FX 对 | 21 | — | **15/21** 直接有；6 只可用倒数为 1/x | 交叉盘可合成 |
| exclude 内部篮子 | 99 | **0** | 0 | 本就在 `exclude_list`，不做计算 |
| Pulse/Beidou 全量轴 | 未知 | 未知 | 未知 | 需 desk dump 才能继续盘 |

---

## 1. 指数（IDX）— 标的有无

pair 规则 + override 中出现的指数（含 `KOSPI200↔KOSPI2` 别名）：

| Calypso | CMDS BB_TICKER | WIND | N(日) | 区间 | OPEN | CLOSE | 结论 |
|---------|----------------|------|-------|------|------|-------|------|
| SPX | SPX Index | SPX.GI | 2230 | 2018–2026 | ✓ | ✓ | **有，够用** |
| NDX | NDX Index | NDX.GI | 2230 | 2018–2026 | ✓ | ✓ | **有，够用** |
| SX5E | SX5E | — | 2271 | 2018–2026 | ✓ | ✓ | **有，够用** |
| NKY | NKY Index | N225.GI | 2164 | 2018–2026 | ✓ | ✓ | **有，够用** |
| HSI | HSI Index | HSI.HI | 2183 | 2018–2026 | ✓ | ✓ | **有，够用** |
| HSCEI | HSCEI Index | HSCEI.HI | 2183 | 2018–2026 | ✓ | ✓ | **有，够用** |
| HSTECH | HSTECH Index | HSTECH.HI | 2096 | 2018–2026 | 部分(1465/2096) | ✓ | **有；OPEN 不完整** |
| SHSZ300 | SHSZ300 Index | 000300.SH | 2151 | 2018–2026 | ✓ | ✓ | **有，够用** |
| CSI1000 | CSI1000 Index | 000852.SH | 2151 | 2018–2026 | ✓ | ✓ | **有，够用** |
| SH000905 | SH000905 Index | 000905.SH | 2151 | 2018–2026 | ✓ | ✓ | **有，够用** |
| SSE50 | SSE50 | 000016.SH | 2151 | 2018–2026 | ✓ | ✓ | **有，够用** |
| KOSPI2 / KOSPI200 | KOSPI2 | — | 559 | **仅 2023-12 起** | ✓ | ✓ | **有，历史偏短** |

**IDX 缺失标的：无。**

---

## 2. 个股 / ETF（EQ）— 标的有无

可恢复 EQ = `exclude_list` 中带后缀者 ∪ `proxy`/`force_proxy`/`override`/`beidou_to_calypso` 中的股票代码（去掉 `*.INDEX`）。

### 2.1 CMDS 有主档 + 日线可用（可跑 realized corr）

**历史够（N≥1000）且 OPEN/CLOSE 齐：**

`700.HK`, `2015.HK`, `3939.HK`, `600804.CH`/`600804.C1`, `6837.HK`, `9618.HK`, `9626.HK`, `9866.HK`, `9868.HK`, `9888.HK`, `9988.HK`, `9999.HK`,  
`AAPL.US`, `BABA.US`, `BIDU.US`, `BILI.US`, `EWY.OC`, `IWM.US`, `JD.US`, `LI.US`, `META.US`, `MRVL.US`, `MU.US`, `NIO.US`, `NTES.US`, `NVDA.US`, `PDD.US`, `QQQ.US`, `SOXL.US`, `SOXX.US`, `SPY.US`, `SPY.UP`, `SQ.US`, `XLE.US`, `XLY.US`, `XPEV.US`

### 2.2 有主档但历史短 / 质量差（标的「有」，长窗相关不稳）

| Calypso | 问题 |
|---------|------|
| `3750.HK` | 仅约 371 日（2025-05 起） |
| `3308.HK` | 仅约 76 日 |
| `2979.HK` | 仅约 51 日 |
| `688795.CH` | 仅约 228 日 |
| `CRCL.US` | 约 365 日 |
| `DRAM.US` | 约 158 日 |
| `BITA.US` | 约 107 日 |
| `SKHY.US` / `SKHYV.US` | 极短（≤90 / 1 日） |
| `FNGA.US` | 约 52 日且已断更 |
| `GOLD.US` / `B.US` | 短或 CLOSE 缺口 |
| `FB.US` | 主档命中异常短序列（META 别名后应优先用 `META.US`） |
| `BGNE.US` | 至 2024-12 断更 |
| `JOYY.US` / `YY.US` / `XYZ.US` / `ROG.SW` | HIST_SHORT |

### 2.3 有主档但无 EOD

| Calypso | 说明 |
|---------|------|
| `113519.CH` | SECURITYCODE 有（`113519 CG`），`EQUITYEOD` 空 → **不能算价** |

### 2.4 CMDS 无主档（标的「没有」）

均为源码 `exclude_list` 中的市场代码（生产本就不参与计算，但若「不改标的集合」仍要记账）：

| Calypso | 判断 |
|---------|------|
| `1169.HK`, `1886.HK`, `67.HK`, `885.HK` | 港股代码在 CMDS 未命中 |
| `1844972D.HK` | 临时/合成代码，无主档 |
| `ATVI.US`, `JOBS.US`, `SINA.US`, `TWTR.US`, `WUBA.US` | 退市/并购后无主档 |

### 2.5 Proxy / Force 目标标的

| 规则 | 目标 | CMDS |
|------|------|------|
| ADR↔港股 proxy（BABA/BIDU/…） | 对应 HK/US | **全部有** |
| `3750.HK` → `300750 C2` + HKDCNH | 有 |
| `3308.HK` → `300308 C2` + HKDCNH | 有 |
| `CRCL.US` → `COIN US` | 有 |
| `SKHY.US` → `MU US` | 有 |
| `688795.CH` → `688256 CH` | **字面 `CH` 无**；`688256 C1`/`CG` **有**且 EOD 完整（需映射时用 C1，不改源码则可在新脚本映射表处理） |

---

## 3. FX — 标的有无

`currency_map` + proxy FXConv 隐含的直盘：

| 状态 | 对 |
|------|-----|
| **CMDS `FXEOD` 直接有** | `USD/HKD`, `USD/CNY`, `USD/CNH`, `USD/JPY`, `USD/KRW`, `USD/SGD`, `USD/CHF`, `USD/EUR`, `USD/GBP`, `USD/AUD`, `EUR/USD`, `GBP/USD`, `AUD/USD`, `JPY/USD`, `HKD/CNH` |
| **无直盘、可用 1/x 合成** | `HKD/USD`, `CNY/USD`, `CNH/USD`, `KRW/USD`, `SGD/USD`, `CHF/USD` |
| **日内时点 FX（T170/F160…）** | **无**（见第 5 节） |

---

## 4. 数据字段：有 vs 没有

### 4.1 corr_mark 实际用到的行情字段

| 源码需求 | CMDS 对应 | 有无 | 备注 |
|----------|-----------|------|------|
| BBG `PX_LAST`（股） | `EQUITYEOD.CLOSE_PRICE` | **有** | 美股/港股/A 股均有样本 |
| BBG `PX_OPEN`（股） | `EQUITYEOD.OPEN_PRICE` | **有** | |
| BBG 复权（adjustmentNormal/Split） | `ADJCLOSE_FORWARD` / `ADJFACTOR_FORWARD` | **港股/A 股有；美股样本为 0** | 美股用 CLOSE 近似，与 BBG 复权可能系统性偏差 |
| BBG `PX_LAST`（指数） | `INDEXEOD.CLOSE_PRICE` | **有** | |
| BBG `PX_OPEN`（指数） | `INDEXEOD.OPEN_PRICE` | **多数有；HSTECH 部分缺失** | |
| BBG FX `PX_LAST` | `FXEOD.CLOSE` | **有**（Wind 直盘） | |
| BBG FX OPEN | `FXEOD.OPEN` | **有** | |
| `FXConv`（如 USDHKD） | `USDHKD.FX` 等 | **有** | |
| `BBG_FX` 单腿合成交叉盘 | USD 直盘两腿 | **可合成** | |
| `_{T170/T153/...}` future_data CSV | — | **无** | SPX×亚太等特殊场次无法复现 |
| `fx_time_map` 时点币种（`USDHKD T170 Curncy`） | — | **无** | 只能用日频 OPEN/CLOSE 近似 |
| Pulse 旧相关矩阵值 | — | **无** | 无法做 new vs old |
| Beidou 全名单 | — | **无** | |
| Risk Corr01 / RFQ | — | **不做** | |

### 4.2 CMDS 表字段可用性（与 corr 相关）

**`EQUITYEOD` 有：** `OPEN_PRICE`, `CLOSE_PRICE`, `HIGH/LOW`, `VOLUME`, `ADJCLOSE_FORWARD`, `ADJOPEN_FORWARD`, `ADJFACTOR_FORWARD`, `SPLIT_FACTOR` …  
**实务：** HK/CH 复权字段填充好；**US 的 ADJ* 在探针样本中为空**。

**`INDEXEOD` 有：** `OPEN_PRICE`, `CLOSE_PRICE`, … **无** 复权字段（指数一般不需要）。

**`FXEOD` 有：** `OPEN/HIGH/LOW/CLOSE`, `BID/ASK/MID`, `WINDCODE`；**无** Bloomberg 场次后缀序列。

**`SECURITYCODE` 有：** `SEC_ID`, `BB_TICKER`, `WIND_CODE`, `RIC`, `ASSET_TYPE`（可用于 Calypso→CMDS 映射）。

---

## 5. 非行情「数据」缺口（与标的无关但挡住全链路）

| 数据 | 有无 | 影响 |
|------|------|------|
| Pulse 轴名单 + 旧 corr | 无 | 不能 1:1 复现生产矩阵尺寸与 diff |
| Beidou `t_config_eq` | 无 | 不能保证 EQ/IDX 宇宙完整 |
| `future_data/*_T170.csv` | 无 | `use_future_data` 路径与跨时区特殊场次 |
| `exclude_list` 内部篮子（99 个，如 `CCINZ*`/`GMAT*`） | 无且本就不算 | 保持 exclude 即可 |
| 邮件 / RFQ / Corr01 | 无 | 本地方案边界外 |

---

## 6. 按运行 mode 的可用性判断（在「可恢复子集」上）

| Mode | 标的侧 | 数据侧 | 能否先跑 |
|------|--------|--------|----------|
| `IDX.IDX` | 13/13 有 | OPEN/CLOSE 基本齐；KOSPI2 短；HSTECH OPEN 部分缺 | **可以**（注意 KOSPI/HSTECH） |
| `EQ.EQ`（硬编码子集） | 56/66 有主档；~40 只有够长历史 | 美股无 ADJ；退市 10 只无 | **可以跑子集**；全量未知 |
| `EQ.IDX` | 同上组合 | 同上 | **子集可以** |
| `EQ.FX` / `IDX.FX` | FX 直盘 15 + 倒数 6 | 无 T170 时点 FX | **日频近似可以**；与 desk 时点相关会偏 |
| 含 `_T170` 的特殊 pair | — | 无 | **不能对齐 desk** |

---

## 7. 结论（针对「标的不想改变」）

1. **指数轴（源码写死的那批）在 CMDS 全部还在**，日线可支撑 realized corr；例外是 **KOSPI2 历史短**、**HSTECH OPEN 不齐**。  
2. **硬编码能点名的 EQ**：约 **85% 有主档**；退市/垃圾代码 10 只没有；另有一批新股/短历史标的「有但不够长窗」。  
3. **FX**：常用 USD 直盘都有；反向对用倒数即可；**没有** desk 的场次 FX。  
4. **真正拦「不改标的」全量复现的**，不是 CMDS 缺几只蓝筹，而是 **Pulse 矩阵轴 + Beidou 全名单本机没有**——在拿到 dump 之前，无法证明「生产每一个标的」在 CMDS 的去留。  
5. **字段级最大偏差**：美股 **无 `ADJCLOSE_FORWARD`**；以及完全缺失的 **`_T170` / 时点 FX**。

下一步若继续：请提供一份 Pulse `CORRELATION_MATRIX` 或 Beidou `t_config_eq` 的 CSV dump，我可以按**完整生产轴**再跑一轮 CMDS 一对一覆盖表。

---

## 8. v1 落地纳入 / 跳过名单（已实现）

宇宙文件：[`data/corr_mark_universe.csv`](data/corr_mark_universe.csv)  
入口：`corr_mark_cmds.py`（不改 `corr_mark.py`）

| status | 含义 | 规模（约） |
|--------|------|------------|
| `active` IDX | 纳入计算（矩阵用 KOSPI2，跳过重复别名 KOSPI200） | 13 行 / 算 12 |
| `active` EQ | 纳入 EQ.EQ | 43 |
| `active` FX | 直盘或 `1/x` 倒数 | 21 |
| `skip_no_security` | 无主档 / 无 EOD | EQ 11 |
| `skip_short_hist` | N&lt;500，名单保留不算 | EQ 12 |
| `skip_exclude_basket` | 内部篮子 | 99 |

### 已跑通结果

| Mode | 结果 | 文件 |
|------|------|------|
| IDX.IDX LAST×LAST | 66/66 ok；与 `corr_mark_local` 数值一致 | `corr_mark_cmds_matrix_IDX_IDX.csv` / `run_manifest.json` |
| EQ.EQ LAST×LAST | 903/903 ok；美股 `raw_close`×32、港/A `adj_forward`×13 | `corr_mark_cmds_matrix_EQ_EQ.csv` |
| IDX/EQ + `--rules` | 跨时区 OPEN/LAST + proxy | `*_rules.csv` |
| IDX.FX / EQ.FX `--rules` | 252 / 903 ok | `*_FX_rules.csv` |

### 重跑命令

```bash
cd "课题：correlation"
export CMDS_HOST=10.23.129.18 CMDS_PORT=21010 CMDS_SERVICE=cmds
export CMDS_CREDS_DOCX="/path/to/CMDS数据库.docx"

python fetch_cmds_price_panel.py \
  --corr-mark-universe data/corr_mark_universe.csv \
  --out data/corr_mark_cmds_panel.parquet

python corr_mark_cmds.py --mode IDX.IDX --simple-last
python corr_mark_cmds.py --mode EQ.EQ --simple-last
python corr_mark_cmds.py --mode IDX.IDX --rules
python corr_mark_cmds.py --mode EQ.FX --rules
```
