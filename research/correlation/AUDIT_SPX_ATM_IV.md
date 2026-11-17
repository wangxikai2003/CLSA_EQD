# SPX 本地期权链 → ATM IV 审计报告

**审计对象**

| 文件 | 角色 |
|------|------|
| `课题：correlation/compute_spx_iv_from_prices.py` | 当前生产用反算脚本（主） |
| `vol_trading/pipeline/vol_lib.py` | Black-76 + `brentq` 反解 |
| `vol_trading/archive/spx_options.parquet` | 输入链（~2024-06→2026-05） |
| `spx_atm_iv_standard.py` | 本审计附带的重构标准实现 |

**数据事实（实测）**

- 列：`as_of_date, ticker, contract_type, strike_price, expiration_date, days_to_strike, SPX_close, close, delta, OHLC, volume` — **无 bid/ask**
- `ticker` 同时含 `O:SPX...` 与 `O:SPXW...`（SPXW 约 7.6M / SPX 约 2.2M 行）
- `days_to_strike` ∈ **[1, 182]**，与日历日差完全一致（ACT 日计数）
- 20–40D 窗口内，同一 `as_of × expiry` 上 **SPX 与 SPXW 并存** 的样本约 345/6850

---

## 1. 期权合约样本清洗与过滤

### 当前实现

```45:65:课题：correlation/compute_spx_iv_from_prices.py
    df = df.dropna(subset=["close", "SPX_close", "strike_price", "days_to_strike"]).copy()
    df = df[(df["close"] > 0) & (df["days_to_strike"] >= tmin) & (df["days_to_strike"] <= tmax)]
    ...
        day["mny"] = day["strike_price"].astype(float) / spot - 1.0
        day = day[day["mny"].abs() <= moneyness_tol]
        ...
        otm = day[
            ((day["cp"] == "C") & (day["strike_price"] >= spot))
            | ((day["cp"] == "P") & (day["strike_price"] < spot))
        ]
```

- **不过滤 root**：未读 `ticker`，SPX（AM 开盘结算）与 SPXW（PM 收盘结算）混在同一日、同一 `days_to_strike` 桶里反算后取 median。
- **微结构**：仅 `close > 0`。无 bid≤0 / ask&lt;bid / 相对价差 / 内在价值下界（下界只在 `vol_lib.implied_vol` 内对 **已选定的 F** 检查）。
- **极短期**：默认 `tmin=20`，未显式 `DTE<5` 规则（若改小 tmin 会放进噪音）。
- **价格**：使用 **`close`**，不是 mid。

### 问题（严重）

| 项 | 判定 |
|----|------|
| AM/PM 混用 | **Bug / 口径错误** — 同日历到期结算相差约 6.5h，T 与结算价不一致 |
| Mid vs Close | **口径错误** — dump 无 bid/ask 被迫用 close；非同步偏差会进 IV |
| 买卖价差/下界 | **缺失**（部分下界在 solver 侧） |

### 修复要点

见 `spx_atm_iv_standard.py`：`parse_root` 分根处理；`mid_price` 优先 mid；`passes_microstructure`；`min_dte=5`。

---

## 2. 远期 F 与无风险利率 r

### 当前实现

```5:5:课题：correlation/compute_spx_iv_from_prices.py
Black-76 with F = S * exp((r-q)*T); default q=r so F≈S.
```

```72:79:课题：correlation/compute_spx_iv_from_prices.py
            F = spot * np.exp((r - q) * T)
            sig = implied_vol(
                price=float(row.close),
                F=float(F),
                ...
```

- 默认 `--r 0.04 --q 0.04` ⇒ **F ≡ S**（把股息硬抹成与 r 相同）。
- **无 Put-Call Parity 隐含远期**。
- **r 为常数 flat**，无 SOFR/Treasury 曲线，无按 T 插值。
- 模型外壳是 Black-76，但 F 不是市场隐含远期 → Call/Put IV 在同一 K 上易劈叉（代码用 OTM+median 掩盖，而非消除）。

### 问题（最关键误差源 — 严重）

标准 PCP：

\[
F = K^\* + e^{rT}\big(C(K^\*)-P(K^\*)\big),\quad K^\*=\arg\min_K |C-P|
\]

当前用 \(F=S\) 等价于假设 \(q=r\) 且忽略指数贴水期限结构，对 SPX **不正确**。

### 修复要点

`implied_forward_pcp` + `rate_at_T`（可接曲线；缺省仍 flat 但 F 来自 PCP）。反算统一 Black-76(F,K,T,σ,r)。

---

## 3. 剩余期限 T 年化约定

### 当前实现

```69:69:课题：correlation/compute_spx_iv_from_prices.py
            T = float(row.days_to_strike) / 365.0
```

- **日历日 / 365** — 与 BBG/CBOE 常用 ACT/365 一致。
- `days_to_strike` 与 `(expiry - as_of).days` 实测差为 0。
- **未**做 AM 09:30 vs PM 16:00 的日内小时修正。
- **未**使用 bus/252（此项正确）。

### 问题（中等）

混用 AM/PM 时，即使同为 ACT/365，有效到期时刻差 ~6.5h，短端 T 相对误差不可忽略。分根后应对 SPX/SPXW 使用不同 settlement hour（重构已做可选小时年化）。

---

## 4. 单到期日 ATM 提取

### 当前实现

- Moneyness：`K/S - 1`，阈值 `|mny|≤2%` — **Spot moneyness**（接近 BBG `100%MNY_DF` 的 K=S 口径，但不是精确插到 K=S）。
- 优先 OTM（相对 **spot**，不是 F）：`C if K≥S`，`P if K<S`。
- 聚合：对通过过滤的 IV 取 **median**，**无**在目标行权价两侧做 IV 或 total-variance 插值。
- 多到期日混在 `tmin–tmax` 天桶内一起 median → 不是“单一到期日 ATM”再插期限。

### 问题（中–高）

| 项 | 判定 |
|----|------|
| ATM 定义 | 近似 spot，但未插值到精确 K=S 或 K=F |
| OTM 相对 S 而非 F | 在有显著贴水时选错翼 |
| 插值空间 | 无插值；median 混多到期 |

### 修复要点

按 expiry 切片 → PCP F → OTM 相对 F → 在 IV 空间插到 `K_target`（`atm_mode=spot|forward`）。

---

## 5. 跨到期固定期限插值（30D / 90D / 360D）

### 当前实现

- **没有**恒定期限曲面：用 DTE∈[20,40] 的横截面 median 当作“约 30D ATM”。
- **没有** \(\sigma^2 T\) 线性插值。
- **1Y 为空的原因（主因）**：parquet **`days_to_strike` 上限 182**，原始 dump 截断了 LEAPS；脚本 `tmax=40` 也会挡远月，但即使放开 tmax，360D 仍无法从该文件插值得到（两侧缺 \(T_2\ge 360/365\)）。

### 问题（严重 — 方法论 + 数据）

恒定期限必须：

\[
\sigma^2(T^\*)T^\* = w_1\frac{T_2-T^\*}{T_2-T_1}+w_2\frac{T^\*-T_1}{T_2-T_1},\quad w_i=\sigma_i^2 T_i
\]

直接对 σ 线性或对 20–40D 乱桶 median 都会产生期限偏差。  
**360D 空**：数据截断为主，代码 `tmax=40` 为次要；重构对越界 **返回 NaN、禁止硬外推**。

---

## 6. 数值求解器稳健性

### 当前实现（`vol_lib.implied_vol`）

- **Brentq**（非 Newton，非 Let's Be Rational）。
- 价格低于贴现内在价值 → `None`。
- 区间无根 → 扩展 hi 后仍失败 → `None`。
- 异常 → `None`。
- 调用方过滤 `0.01 < sig < 3.0`，**不会把失败写成 0.0**。

### 问题（低）

- Brentq 对深虚值/极小 vega 可能失败 → 正确跳过；深 ITM 仍可能数值难受（应用 OTM 翼缓解）。
- 未用 Jäckel LBR（速度/稳健性可再升，非正确性硬伤）。

---

## 总评

| # | 环节 | 现状 | 严重度 |
|---|------|------|--------|
| 1 | 过滤 / AM-PM / Mid | 混用 AM/PM；close 非 mid | 高 |
| 2 | F 与 r | F≈S；flat r；无 PCP | **关键** |
| 3 | T | ACT/365 ✓；缺结算时刻 | 中 |
| 4 | 单日 ATM | spot 邻域 median；无精确插值 | 中高 |
| 5 | 恒定期限 | 无 TV 插值；1Y 因 max DTE=182 | 高 |
| 6 | Solver | brentq→None ✓ | 低 |

与 VIX 的高相关（历史诊断 ~0.98）**不能**证明微观口径正确，只说明水平因子大致同向。

---

## 重构入口

```bash
cd "课题：correlation"
python spx_atm_iv_standard.py --root SPXW --atm-mode spot --tenors 30,90,180,360
```

依赖：本地 `vol_lib.implied_vol`；若未来 dump 含 `bid`/`ask` 会自动改用 mid。  
生产级还需：官方 SOFR/ OIS 曲线文件、`CMDS_USER` 无关；更长 DTE 的期权 dump。
