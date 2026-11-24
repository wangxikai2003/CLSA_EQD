# Overseas Vol RV + US Correlation (CMDS)

## Confirmed universe policy

| Bucket | Instrument | Notes |
|--------|------------|--------|
| Overseas gold | **COMEX GC** futures options | Not LBMA fixing alone |
| Overseas crude | **NYMEX CL** futures options | WTI |
| US equity Vol RV | **CME ES** futures options | Hedge leg = **ES futures** (`front_fut` / `ESc1`), never SPX/NDX spot |
| US implied/realized corr | NDX top-N + SPDR11 OPRA | Equity/ETF options + EOD panels |

## ETL

```bash
cd vol_trading/pipeline
python fetch_cmds_overseas_derivatives.py --products GC,CL,ES
python build_overseas_vol_db.py --products GC,CL,ES
```

Outputs:

- `vol_trading/data/{gc,cl,es}_overseas_{future,option}.parquet`
- `vol_trading/data/commodity_vol_{gc,cl,es}.sqlite` (+ copies under `CLSA_EQD/data/`)
- Combined `overseas_vol.sqlite`

## Vol RV registration

- Backend: `VOL_RV_UNIVERSES` keys `GC` / `CL` / `ES` in `csi300_vol_rv_strategy.py`
  - Aliases: `comex`→GC, `spx`/`emini`→ES, etc.
  - `spot_mode=front_fut`; continuous `ESc1` preferred when dated near months are absent in CMDS
- API / QIS UI: `QisVolRvUniverse` includes `GC|CL|ES`; QisView lists COMEX GC / E-mini ES
- Hot files: `commodity_vol_{gc,cl,es}.sqlite` in `data_paths.HOT_FILES`

### ES caveat (CMDS)

CMDS drops expired dated ES futures masters. Local dated futures currently start ~2026H1; near-month option forwards fall back to continuous **ESc1** (available from ~2025-01-07). Near expiries can be strike-sparse — the engine prefers ATM strikes with both C/P and walks deferred expiries until a long strangle can be built.

Smoke (Asia/Shanghai wall times of last local run): ES 2025-01→09 ≈ 183 days; GC/CL also open vintages on 2025 H1 windows.

## Correlation pulls

```bash
cd 课题：correlation
# SPDR11 (done): equity options EOD
python fetch_cmds_us_equity_options.py --universe spdr11 --tag spdr11 --start 20240901 --end 20261123
# NDX top30 OPRA
python fetch_cmds_us_equity_options.py --universe ndx_top30 --tag ndx30 --start 20240901 --end 20261123 --max-contracts 80 --top-n 30 --skip-eod
# Realized corr panel → CLSA_EQD/data/us_realized_corr_timeseries.parquet
python compute_us_realized_corr.py --panel data/us_equity_eod_corr.parquet --out ...
# Implied corr surface (monthly sample; --min-names scales with --top-n)
python ndx_implied_corr.py --dates 20240901,20241001,... --tenors 30,60,90 --top-n 30 --min-names 15
```

Note: OPRA equity option `root` is often null — `iv_nodes_by_expiry` fills `EQ` so groupby does not drop all slices.

Artifacts under `CLSA_EQD/data/`: `us_equity_option_eod_{spdr11,ndx30}.parquet`, `us_realized_corr_timeseries.parquet`, `ndx_implied_corr_surface.parquet`.
