# Database Handoff — vol_trading / CLSA_EQD

> 给另一 agent 的交接说明。仓库根目录：`/Users/wangxikai/Desktop/CITICS CLSA/vol_trading`  
> 更新日期：2026-11-13  
> 权威实现：`pipeline/fetch_*.py`、`pipeline/build_*_db.py`、`data/README.md`

---

## 1. 架构总览

```
Wind Oracle (内网)
    ↓  pipeline/fetch_*.py  (oracledb)
data/*.parquet              ← 原始落地
    ↓  pipeline/build_vol_db.py / build_commodity_vol_db.py
data/*.sqlite               ← 真相源
    ↓  scripts/sync_db_to_app.sh
CLSA_EQD/data/*.sqlite      ← 本地 API / Cloud Run 部署用副本
```

- **管线层**只负责拉数、算 IV、写库。  
- **应用层**只读 SQLite（不直连 Wind）。  
- Cloud Run 服务：`exotic-option-architect-peter-xikaiwang`（us-west1），镜像内带 `CLSA_EQD/data/cffex_vol.sqlite`。

---

## 2. Wind Oracle 连接

| 项 | 值 |
|----|-----|
| 驱动 | `oracledb`（需本机 Instant Client；推荐 Anaconda Python） |
| DSN | `10.23.153.15:21010/wind` |
| User | `eqquant` |
| Password | `eqquant`（写死在各 `pipeline/fetch_*.py`；内网账号） |

连接示例（与脚本一致）：

```python
import oracledb
conn = oracledb.connect(
    user="eqquant",
    password="eqquant",
    dsn="10.23.153.15:21010/wind",
)
```

> 仅内网可达。外网 / Cloud Run **不能**直连 Wind。

---

## 3. Wind 源表与拉数脚本

### 3.1 股指期权（CFFEX）— `pipeline/fetch_index_options.py`

| 产品 | Wind 前缀 | parquet |
|------|-----------|---------|
| 上证50 | `HO` | `data/50_index_option.parquet` |
| 沪深300 | `IO` | `data/300_index_option.parquet` |
| 中证1000 | `MO` | `data/1000_index_option.parquet` |

- 描述表：`WIND.CHINAOPTIONDESCRIPTION`  
- 行情表：`WIND.CHINAOPTIONEODPRICES`  
- Join：`S_INFO_WINDCODE`  
- Filter：`S_INFO_EXCHANGE_NAME = 'CFFEX'` 且 `S_INFO_WINDCODE LIKE '{HO|IO|MO}%'`

| Wind 字段 | 落地列 |
|-----------|--------|
| `TRADE_DT` | `trade_date` |
| `S_INFO_WINDCODE` | `证券代码` |
| `S_INFO_NAME` | `证券名称` |
| `S_DQ_CLOSE` | `收盘价` |
| `S_DQ_SETTLE` | `结算价` |
| `S_INFO_CALLPUT` | `合约种类`（`708001000→C`, `708002000→P`） |
| `S_INFO_STRIKEPRICE` | `行权价格` |
| `S_INFO_MATURITYDATE` | `到期日` |

### 3.2 股指期货 — `pipeline/fetch_index_futures.py`

| 产品 | 前缀 | parquet |
|------|------|---------|
| IH | `IH` | `data/50_index_future.parquet` |
| IF | `IF` | `data/300_index_future.parquet` |
| IM | `IM` | `data/1000_index_future.parquet` |

- 表：`WIND.CINDEXFUTURESEODPRICES`  
- 月合约正则：`^{IH|IF|IM}[0-9]{4}\.CFE$`（排除连续合约）

| Wind 字段 | 落地列 |
|-----------|--------|
| `S_INFO_WINDCODE` | `wind_code` |
| `TRADE_DT` | `trade_date` |
| `S_DQ_CLOSE` | `close` |
| `S_DQ_SETTLE` | `settle` |
| `S_DQ_OI` | `oi` |
| `S_DQ_VOLUME` | `volume` |
| code 第 3–6 位 | `contract_ym`（如 `2611`） |

### 3.3 商品期权 — `pipeline/fetch_commodity_options.py` + `commodity_universe.py`

品种：`CU AU AG RU SC M I C SR CF MA TA`（SHFE/INE/DCE/CZCE）。

- 同股指：`CHINAOPTIONDESCRIPTION` ⋈ `CHINAOPTIONEODPRICES`  
- Filter：`S_INFO_EXCHANGE_NAME = '{exch}'`，`REGEXP_LIKE(S_INFO_WINDCODE, '^{pref}[0-9]')`  
- 落地：`data/{pref}_commodity_option.parquet`（小写，如 `cu_commodity_option.parquet`）  
- 额外列：`成交量`←`S_DQ_VOLUME`，`持仓量`←`S_DQ_OI`，`contract_ym`←`S_INFO_MONTH`

### 3.4 商品期货 — `pipeline/fetch_commodity_futures.py`

- 表：`WIND.CCOMMODITYFUTURESEODPRICES`  
- 落地：`data/{pref}_commodity_future.parquet`  
- 字段：`wind_code, trade_date, close, settle, oi, volume, contract_ym`

### 3.5 利率 — `pipeline/fetch_rates.py` → `data/rates_eod.parquet`

| 系列 | Wind 表 | 代码示例 |
|------|---------|----------|
| SHIBOR | `WIND.SHIBORPRICES`（`B_INFO_RATE`，百分数→/100） | `SHIBORON.IR`（**建库默认**）, `SHIBOR1W/1M/3M/6M/1Y.IR` |
| SOFR / FR007 等 | `WIND.LIBORPRICES` | `SOFR.IR`, `SOFR1M.IR`, …, `FR007.IR`, `DR007.IR` |
| 国债基准收益率 | `WIND.CGBBENCHMARK`（`S_DQ_CLOSE`=%） | `TB10Y.WI`（推荐）, `CGB10Y.WI`, `CGB1Y/5Y.WI` |

parquet 列：`wind_code, trade_date, rate_pct, rate, source_table`。  
建库用 `RATE_CODE` 环境变量选一条写入 sqlite `rates_eod`（默认 `SHIBORON.IR`）。

### 3.6 股指现货（合成期限等）

- 注释来源：`WIND.AINDEXEODPRICES` → `data/index_spot.parquet`  
- 被 `CLSA_EQD/backend/api/v1/synth_tenors.py`、`calendar_vol_strategy.py` 读取  
- 若文件缺失，相关 API / QIS 会报错

### 3.7 其他 Wind 表（历史对话提及，本仓库主路径未强制）

- `WIND.CCOMMODITYFUTURESPOSITIONS`：商品期货会员持仓排名（CMDS 无此表；需从 Wind 抽）

---

## 4. 价格政策（重要）

**Desk / 回测 / 建库统一：只用 Wind `S_DQ_CLOSE`，不用 settle 兜底。**

| 概念 | 实际含义 |
|------|----------|
| `option_iv.mid_price` | **历史列名**；内容 = 期权 `S_DQ_CLOSE`，**不是** bid/ask mid，**不是** settle |
| `option_iv.forward` | 同月期货的 `S_DQ_CLOSE` |
| `futures_eod.close` | 期货收盘 |
| `futures_eod.settle` | 仍入库备查，但定价/回测路径不优先使用 |

实现注释见：`pipeline/build_vol_db.py`、`CLSA_EQD/backend/engine/calendar_vol_strategy.py`。

---

## 5. 本地 SQLite 库（当前盘上）

| 路径 | 约大小 | 用途 |
|------|--------|------|
| `data/cffex_vol.sqlite` | ~160MB | **股指主库** HO/IO/MO（真相源） |
| `CLSA_EQD/data/cffex_vol.sqlite` | 同上 | 应用/部署副本（`sync_db_to_app.sh`） |
| `data/commodity_vol.sqlite` | ~1.2GB | 商品 IV + JW-SVI |
| `CLSA_EQD/data/commodity_fut.sqlite` | ~21MB | 商品期货 EOD 瘦库 |
| `CLSA_EQD/data/qis_results.sqlite` | ~18MB | QIS 回测结果缓存 |

### 5.1 `cffex_vol.sqlite` / `commodity_vol.sqlite` 表结构

**`products`**
- `product TEXT PK`, `name`, `fut_prefix`

**`futures_eod`**
- PK `(product, trade_date, wind_code)`
- `contract_ym`, `settle`, `close`, `oi`, `volume`

**`rates_eod`**
- PK `(wind_code, trade_date)`
- `rate`（小数，如 0.02）

**`option_iv`**
- PK `(product, trade_date, wind_code)`
- `expiry`, `ttm`, `strike`, `cp` (`C`/`P`)
- `forward`, `mid_price`（=close）, `iv`, `log_moneyness`, `r`
- Index：`idx_iv_lookup(product, trade_date)`

**`svi_slices`**
- PK `(product, trade_date, side, expiry)`
- `side` ∈ `otm` / `call` / `put`
- Raw SVI：`a,b,rho,m,sigma`
- JW：`jw_v, jw_psi, jw_p, jw_c, jw_vtild`
- `forward, ttm, n_quotes, w_atm, rmse`

**`surface_meta`**
- PK `(product, trade_date, side)`
- `spot_proxy`, `n_slices`, `n_iv`, `r`, `rate_code`

**`svi_matrices`**（股指库有；商品库视是否跑过 materialize）
- PK `(product, trade_date, side)`
- `spot`, `n_rows`, `n_cols`, `matrix_json`（moneyness×TTM 预计算 IV）

当前行数量级（2026-11-13 本机）：
- 股指 `option_iv` ~778k；`svi_slices` ~62k；`svi_matrices` ~11k  
- 商品 `option_iv` ~7.0M；`svi_slices` ~377k

### 5.2 `commodity_fut.sqlite`

- `products(product, name)`  
- `futures_eod(product, trade_date, wind_code, contract_ym, settle, close)` — 无 oi/volume

### 5.3 `qis_results.sqlite`

- `qis_backtest_cache`：策略回测 JSON 缓存  
- 列含：`strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode, source_key, payload, updated_at`

---

## 6. 重建 / 同步命令

在仓库根目录（建议 Anaconda Python）：

```bash
# Wind → parquet
python pipeline/fetch_index_options.py
python pipeline/fetch_index_futures.py
python pipeline/fetch_rates.py
python pipeline/fetch_commodity_options.py
python pipeline/fetch_commodity_futures.py

# parquet → sqlite（股指）
RATE_CODE=SHIBORON.IR python pipeline/build_vol_db.py   # 或 TB10Y.WI
python pipeline/materialize_svi_matrices.py
bash scripts/sync_db_to_app.sh

# 商品
python pipeline/build_commodity_vol_db.py
DB=data/commodity_vol.sqlite python pipeline/materialize_svi_matrices.py
python pipeline/build_commodity_fut_db.py   # → CLSA_EQD/data/commodity_fut.sqlite
```

应用读库路径（后端）：优先 `CLSA_EQD/data/cffex_vol.sqlite`，Docker 内 `/app/data/cffex_vol.sqlite`。

---

## 7. 应用如何用这些库

| 功能 | 库 |
|------|-----|
| Volatility Surface / JW / 产品日历 | `cffex_vol.sqlite` |
| Exotic 定价所用 vol surface | 同上（或 flat vol） |
| Calendar vol / vol spread / synth tenors | `cffex_vol` + 可选 `commodity_*` + `index_spot.parquet` |
| QIS backtest | 读 `cffex_vol`（及 commodity），结果写 `qis_results.sqlite` |

API 入口见根 `README.md`（`/api/products`, `/api/dates`, surface, qis, exotic…）。

---

## 8. 给下一 agent 的注意点

1. **不要**在 Cloud Run / 公网假设能连 Wind；只能用已构建的 sqlite/parquet。  
2. 改价格逻辑时记住：`mid_price` ≠ mid；= **close**。  
3. 重建股指库后必须 `bash scripts/sync_db_to_app.sh`，再 `CLSA_EQD/./deploy.sh` 才会上线新数据。  
4. Exotics 重 MC 并发会打崩 Cloud Run（2CPU/2Gi）；Legacy 打开时会 pause Workbench GTS。  
5. 商品持仓排名等扩展数据：用 Wind `CCOMMODITYFUTURESPOSITIONS`，不要去 CMDS 找镜像表。  
6. 凭证目前明文在 fetch 脚本中；若加固，改为环境变量，但交接时需同步新约定。

---

## 9. 关键文件索引

| 文件 | 内容 |
|------|------|
| `pipeline/fetch_index_options.py` | Wind 股指期权 |
| `pipeline/fetch_index_futures.py` | Wind 股指期货 |
| `pipeline/fetch_commodity_*.py` | Wind 商品 |
| `pipeline/fetch_rates.py` | Wind 利率 |
| `pipeline/commodity_universe.py` | 商品品种表 |
| `pipeline/build_vol_db.py` | 股指 sqlite schema + close-only IV |
| `pipeline/build_commodity_vol_db.py` | 商品 vol 库 |
| `pipeline/materialize_svi_matrices.py` | IV 矩阵预计算 |
| `scripts/sync_db_to_app.sh` | 同步到 CLSA_EQD |
| `data/README.md` | data/ 文件清单 |
| `README.md` | 项目总览与命令 |
