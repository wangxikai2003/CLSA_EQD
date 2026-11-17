# corr_mark 本地数据说明

## 已打通

- **CMDS Oracle**（JDBC + `lib/ojdbc8.jar`）：主机/端口经 `CMDS_HOST` / `CMDS_PORT` / `CMDS_SERVICE`
- thin `oracledb` 不可用（`DPY-3010`）；请用 `cmds_connect.py`
- 凭证：环境变量 `CMDS_USER` / `CMDS_PASSWORD`（或 `CMDS_CREDS_DOCX`）；勿提交密码

## 覆盖（2026-11-13 探测）

| 类型 | 标的 | 备注 |
|------|------|------|
| INDEXEOD | SPX, NDX, SX5E, NKY, HSI, HSCEI, HSTECH, SHSZ300, CSI1000, SH000905, SSE50 | OPEN+CLOSE 齐全；HSTECH 早期 OPEN 略缺 |
| INDEXEOD | KOSPI2 | 仅约 2023-12 起 |
| EQUITYEOD | 700 HK, AAPL US, 600519 等；可用 `--ndx-constituents` 批量拉成分 | OPEN/CLOSE/复权可用 |
| FXEOD | USDHKD/USDCNY/USDCNH/USDJPY/EURUSD（`.FX` Wind 序列） | 优先 `WINDCODE='…FX'` |

## 仍无法从 CMDS / 本地 vol DB 获得

- Pulse / Calypso `CORRELATION_MATRIX`（new vs old 对比）
- Beidou / Postgres 完整 EQ universe 与 Risk Corr01
- Bloomberg 专有字段与日内 future（`_T170` 等）
- desk RFQ 回写、邮件

## 用法

```bash
# 1) 拉价格面板
python fetch_cmds_price_panel.py

# 1b) NDX 历史成分日线（实现相关用）
python fetch_cmds_price_panel.py --skip-default --ndx-constituents \
  --start 20240101 --end 20261113

# 2) 本地相关矩阵（默认 close, pad=1, alpha=93, rolling=63）
python corr_mark_local.py
```

产物：`data/cmds_price_panel.parquet`、`data/cmds_us_equity_panel.parquet`、`data/corr_matrix_local.csv`
