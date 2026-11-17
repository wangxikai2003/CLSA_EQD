# NDX 隐含平均相关性曲面（CMDS 建模）

## 模型

常相关假设下的指数方差分解：

\[
\sigma_I^2=\sum_i w_i^2\sigma_i^2+\rho\Big[\big(\sum_i w_i\sigma_i\big)^2-\sum_i w_i^2\sigma_i^2\Big]
\]

\[
\rho=\frac{\sigma_I^2-\sum_i w_i^2\sigma_i^2}{\big(\sum_i w_i\sigma_i\big)^2-\sum_i w_i^2\sigma_i^2}
\]

对每个交易日 × 目标期限 \(T\)（30/60/90D）× spot moneyness \(K/S\in\{0.95,1.00,1.05\}\)：

1. 取官方 `INDEXCONSTITUENTS` 权重（默认 Top50 并再归一化）
2. NDX / 成份：OPRA mid（优先）+ PCP 隐含远期 + Black-76；若 `IMPLIED_VOLATILITY_LAST` 足够密则用之
3. 在目标 DTE 带内选最接近到期日，OTM 翼插值到目标 \(K\)

脚本：`ndx_implied_corr.py`

## Pilot 结果（2024-09 → 2026-11，15 个交易日）

输出：`data/ndx_implied_corr_surface.parquet`（135 点全部 `status=ok`）

| tenor | mny | median ρ | mean ρ |
|------:|----:|---------:|-------:|
| 30D | 0.95 | 0.353 | 0.366 |
| 30D | 1.00 | 0.263 | 0.271 |
| 30D | 1.05 | 0.184 | 0.193 |
| 60D | 0.95 | 0.373 | 0.369 |
| 60D | 1.00 | 0.286 | 0.281 |
| 60D | 1.05 | 0.222 | 0.215 |
| 90D | 0.95 | 0.354 | 0.362 |
| 90D | 1.00 | 0.278 | 0.290 |
| 90D | 1.05 | 0.248 | 0.233 |

形态符合常见隐含相关偏斜：**低行权价（偏 put）ρ 更高，高行权价 ρ 更低**。

最新样本日 `20261106` ATM：30D ρ≈0.37，60D≈0.39，90D≈0.34。

## 运行

```bash
export CMDS_USER=... CMDS_PASSWORD=...
# 或设置 CMDS_CREDS_DOCX
/Users/wangxikai/anaconda3/bin/python ndx_implied_corr.py --pilot \
  --tenors 30,60,90 --mny 0.95,1.00,1.05 --top-n 50
```

`--top-n 0` 使用全部成份（更慢、更全）。

## 局限

- 历史深度受 CMDS 期权 mid 覆盖限制（约 2024-09 起）
- Top50 近似全指数（权重覆盖通常很高，但非 100 只全样本）
- 预存 `30D/60D/90D` IV 字段不可用；恒定期限靠挂牌到期插值而非交易所官方合成期货
- 未分离 NDX AM vs NDXW PM（当前过滤 `UNDERLYING='.NDX'`）
