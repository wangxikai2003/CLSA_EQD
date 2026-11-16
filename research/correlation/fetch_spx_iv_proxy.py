"""Pull long-horizon SPX vol proxy (VIX) + short SPX option IV from CMDS.

- VIX INDEXEOD: ~2000→now (use for ~5y studies as index IV *proxy*)
- OPTIONVOLATILITY for .SPX OPRA: ~2024-08→now (true option IV, short)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from cmds_connect import read_sql

ROOT = Path(__file__).resolve().parent
VIX_SEC_ID = "0IO6357Lfqfh"  # WIND_CODE VIX.GI / RIC .VIX
SPX_SEC_ID = "E0pkktRNAxZJ"


def fetch_vix(start: str, end: str) -> pd.DataFrame:
    sql = f"""
    SELECT TRADE_DATE, OPEN_PRICE, CLOSE_PRICE
    FROM CDM.INDEXEOD
    WHERE SEC_ID = '{VIX_SEC_ID}'
      AND TRADE_DATE BETWEEN '{start}' AND '{end}'
    ORDER BY TRADE_DATE
    """
    df = read_sql(sql)
    df["trade_date"] = df["TRADE_DATE"].astype(str)
    df["vix_close"] = pd.to_numeric(df["CLOSE_PRICE"], errors="coerce")
    df["vix_open"] = pd.to_numeric(df["OPEN_PRICE"], errors="coerce")
    # VIX quoted in vol points; convert to decimal vol
    df["iv_proxy"] = df["vix_close"] / 100.0
    df["source"] = "VIX_INDEXEOD"
    return df[["trade_date", "vix_close", "vix_open", "iv_proxy", "source"]]


def fetch_spx_spot(start: str, end: str) -> pd.DataFrame:
    sql = f"""
    SELECT TRADE_DATE, CLOSE_PRICE
    FROM CDM.INDEXEOD
    WHERE SEC_ID = '{SPX_SEC_ID}'
      AND TRADE_DATE BETWEEN '{start}' AND '{end}'
    """
    df = read_sql(sql)
    df["trade_date"] = df["TRADE_DATE"].astype(str)
    df["spx_close"] = pd.to_numeric(df["CLOSE_PRICE"], errors="coerce")
    return df[["trade_date", "spx_close"]]


def fetch_spx_option_iv_atm(start: str, end: str) -> pd.DataFrame:
    """Daily average of OPRA SPX IMPLIED_VOLATILITY_LAST (light aggregate; not strict ATM)."""
    sql = f"""
    SELECT v.TRADE_DATE,
           AVG(v.IMPLIED_VOLATILITY_LAST) AVG_LAST,
           COUNT(*) N
    FROM CDM.OPTIONVOLATILITY v
    JOIN CDM.OPTIONDESCRIPTION o ON o.SEC_ID = v.SEC_ID
    WHERE o.UNDERLYING = '.SPX'
      AND o.MRKT_ID = 'OPRA'
      AND v.TRADE_DATE BETWEEN '{start}' AND '{end}'
      AND v.IMPLIED_VOLATILITY_LAST IS NOT NULL
    GROUP BY v.TRADE_DATE
    ORDER BY v.TRADE_DATE
    """
    raw = read_sql(sql)
    if raw.empty:
        return pd.DataFrame(columns=["trade_date", "iv_last_avg", "n_quotes", "source"])
    raw["trade_date"] = raw["TRADE_DATE"].astype(str)
    raw["avg_last"] = pd.to_numeric(raw["AVG_LAST"], errors="coerce")
    raw["iv_last_avg"] = np.where(raw["avg_last"] > 3.0, raw["avg_last"] / 100.0, raw["avg_last"])
    raw["n_quotes"] = pd.to_numeric(raw["N"], errors="coerce")
    raw["source"] = "CMDS_SPX_OPRA_LAST_AVG"
    return raw[["trade_date", "iv_last_avg", "n_quotes", "source"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20200101")  # 5y+ window request
    ap.add_argument("--end", default="20261113")
    ap.add_argument("--out_dir", default=str(ROOT / "data"))
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching VIX...", flush=True)
    vix = fetch_vix(args.start, args.end)
    vix_path = out_dir / "spx_iv_proxy_vix.parquet"
    vix.to_parquet(vix_path, index=False)
    print(f"  VIX rows={len(vix):,} {vix['trade_date'].min()}→{vix['trade_date'].max()} -> {vix_path}", flush=True)

    print("Fetching SPX OPRA ATM IV (short history)...", flush=True)
    # option IV only exists ~202408+
    iv_start = max(args.start, "20240801")
    atm = fetch_spx_option_iv_atm(iv_start, args.end)
    atm_path = out_dir / "spx_iv_atm_cmds_opra.parquet"
    atm.to_parquet(atm_path, index=False)
    print(f"  ATM rows={len(atm):,} -> {atm_path}", flush=True)
    if not atm.empty and not vix.empty:
        both = atm.merge(vix[["trade_date", "iv_proxy"]], on="trade_date", how="inner")
        if len(both) > 5:
            corr = both["iv_last_avg"].corr(both["iv_proxy"])
            print(f"  corr(IV last avg, VIX/100) on overlap n={len(both)}: {corr:.3f}", flush=True)


if __name__ == "__main__":
    main()
