# corr_mark 本地数据说明

## 已打通

- **CMDS Oracle**（JDBC + `lib/ojdbc8.jar`）：`10.23.129.18:21010/cmds`
- thin `oracledb` 不可用（`DPY-3010`）；请用 `cmds_connect.py`
- 凭证：环境变量 `CMDS_USER` / `CMDS_PASSWORD`（或 `CMDS_CREDS_DOCX`）；勿提交密码

## 覆盖（2026-11-13 探测）

| 类型 | 标的 | 备注 |
|------|------|------|
| INDEXEOD | SPX, SX5E, NKY, HSI, HSCEI, HSTECH, SHSZ300, CSI1000, SH000905, SSE50 | OPEN+CLOSE 齐全；HSTECH 早期 OPEN 略缺 |
| INDEXEOD | KOSPI2 | 仅约 2023-12 起 |
| EQUITYEOD | 700 HK, AAPL US, 600519 等 | OPEN/CLOSE/复权可用 |
| FXEOD | USDHKD/USDCNY/USDCNH/USDJPY/EURUSD（`.FX` Wind 序列） | 优先 `WINDCODE='…FX'`；`USDCNY.FX` 历史在约 2025-02 后变稀/断档，可用 `USDCNH.FX` 或 `.IB` 序列再评估 |

## 仍无法从 CMDS / 本地 vol DB 获得

- Pulse / Calypso `CORRELATION_MATRIX`（new vs old 对比）
- Beidou / Postgres 完整 EQ universe 与 Risk Corr01
- Bloomberg 专有字段与日内 future（`_T170` 等）
- desk RFQ 回写、邮件

## 用法

```bash
# 1) 拉价格面板
/Users/wangxikai/anaconda3/bin/python fetch_cmds_price_panel.py

# 2) 本地相关矩阵（默认 close, pad=1, alpha=93, rolling=63）
/Users/wangxikai/anaconda3/bin/python corr_mark_local.py
```

产物：`data/cmds_price_panel.parquet`、`data/corr_matrix_local.csv`、`data/corr_matrix_local_matrix.csv`
