"""Approximate S&P 500 weights from CMDS (not official S&P IWF).

FMC_hat_i = USSHARE.S_DQ_MV (or FLOAT_A_SHR * close), then w_i = FMC_i / sum(FMC).
Membership from INDEXCONSTITUENTS; official CONSTITUENT_WEIGHT is null for SPX.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from cmds_connect import read_sql

SPX_SEC_ID = "E0pkktRNAxZJ"  # BB_TICKER 'SPX Index'
ROOT = Path(__file__).resolve().parent


def fetch_membership(start: str, end: str, month_ends_only: bool) -> pd.DataFrame:
    sql = f"""
    SELECT e.TRADE_DATE, e.CONSTITUENT_SEC_ID, e.CONSTITUENT_RIC, e.CONSTITUENT_WINDCODE,
           c.BB_TICKER, c.WIND_CODE, c.SEC_NAME
    FROM CDM.INDEXCONSTITUENTS e
    JOIN CDM.SECURITYCODE c ON c.SEC_ID = e.CONSTITUENT_SEC_ID
    WHERE e.INDEX_SEC_ID = '{SPX_SEC_ID}'
      AND e.TRADE_DATE BETWEEN '{start}' AND '{end}'
    """
    m = read_sql(sql)
    m["TRADE_DATE"] = m["TRADE_DATE"].astype(str)
    m["wind_code"] = m["WIND_CODE"].fillna(m["CONSTITUENT_WINDCODE"])
    if month_ends_only:
        d = pd.to_datetime(m["TRADE_DATE"])
        month_end = d.groupby([d.dt.year, d.dt.month]).transform("max")
        m = m.loc[d == month_end].copy()
    return m


def fetch_ussahre(wind_codes: list[str], start: str, end: str) -> pd.DataFrame:
    # batch to avoid huge IN lists
    frames = []
    chunk = 200
    for i in range(0, len(wind_codes), chunk):
        part = [c for c in wind_codes[i : i + chunk] if c]
        if not part:
            continue
        inlist = ",".join("'" + c.replace("'", "''") + "'" for c in part)
        sql = f"""
        SELECT WINDCODE, TRADE_DT, TOT_SHR_TODAY, FLOAT_A_SHR_TODAY,
               S_DQ_CLOSE_TODAY, S_DQ_MV, S_VAL_MV
        FROM CDM.USSHAREEODDERIVATIVEINDICATOR
        WHERE WINDCODE IN ({inlist})
          AND TRADE_DT BETWEEN '{start}' AND '{end}'
        """
        frames.append(read_sql(sql))
    if not frames:
        return pd.DataFrame()
    u = pd.concat(frames, ignore_index=True)
    u["TRADE_DT"] = u["TRADE_DT"].astype(str)
    for c in ("TOT_SHR_TODAY", "FLOAT_A_SHR_TODAY", "S_DQ_CLOSE_TODAY", "S_DQ_MV", "S_VAL_MV"):
        u[c] = pd.to_numeric(u[c], errors="coerce")
    return u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20230801")  # SPX membership history in CMDS
    ap.add_argument("--end", default="20261113")
    ap.add_argument("--monthly", default="True", help="Only month-end membership dates (faster)")
    ap.add_argument(
        "--out",
        default=str(ROOT / "data" / "spx_weights_approx.parquet"),
    )
    args = ap.parse_args()
    monthly = str(args.monthly).lower() in {"1", "true", "yes", "y"}

    print("Fetching SPX membership...", flush=True)
    mem = fetch_membership(args.start, args.end, monthly)
    print(f"  membership rows={len(mem):,} dates={mem['TRADE_DATE'].nunique()} names={mem['CONSTITUENT_SEC_ID'].nunique()}", flush=True)

    winds = sorted({w for w in mem["wind_code"].dropna().astype(str) if w})
    print(f"Fetching USSHARE for {len(winds)} wind codes...", flush=True)
    us = fetch_ussahre(winds, args.start, args.end)
    print(f"  usshare rows={len(us):,}", flush=True)

    # Unified float-adjusted FMC (S&P IWF-style). Do NOT prefer S_DQ_MV (scale mismatch risk).
    us["fmc_old"] = us["S_DQ_MV"]
    miss_old = us["fmc_old"].isna() | (us["fmc_old"] <= 0)
    us.loc[miss_old, "fmc_old"] = (
        us.loc[miss_old, "FLOAT_A_SHR_TODAY"] * us.loc[miss_old, "S_DQ_CLOSE_TODAY"]
    )

    us["fmc"] = us["FLOAT_A_SHR_TODAY"] * us["S_DQ_CLOSE_TODAY"]
    miss = us["fmc"].isna() | (us["fmc"] <= 0)
    us.loc[miss, "fmc"] = us.loc[miss, "TOT_SHR_TODAY"] * us.loc[miss, "S_DQ_CLOSE_TODAY"]
    us["fmc_source"] = np.where(miss, "TOT_SHR*CLOSE", "FLOAT_A_SHR*CLOSE")

    merged = mem.merge(
        us,
        left_on=["TRADE_DATE", "wind_code"],
        right_on=["TRADE_DT", "WINDCODE"],
        how="left",
    )
    merged["fmc"] = pd.to_numeric(merged["fmc"], errors="coerce")
    merged["fmc_old"] = pd.to_numeric(merged["fmc_old"], errors="coerce")
    valid = merged.dropna(subset=["fmc"]).copy()
    valid = valid[valid["fmc"] > 0]
    valid["weight"] = valid["fmc"] / valid.groupby("TRADE_DATE")["fmc"].transform("sum")

    # diagnostic vs old S_DQ_MV-first path
    diag = valid.dropna(subset=["fmc_old"]).copy()
    diag = diag[diag["fmc_old"] > 0]
    if len(diag):
        diag["weight_old"] = diag["fmc_old"] / diag.groupby("TRADE_DATE")["fmc_old"].transform("sum")
        diag["w_diff"] = diag["weight"] - diag["weight_old"]
        latest_dt = diag["TRADE_DATE"].max()
        d0 = diag[diag["TRADE_DATE"] == latest_dt].copy()
        print("\n=== Weight dimension fix diagnostic ===", flush=True)
        print(f"asof={latest_dt} names={len(d0)}", flush=True)
        print(
            f"  max|Δw|={d0['w_diff'].abs().max():.4f} "
            f"mean|Δw|={d0['w_diff'].abs().mean():.4f}",
            flush=True,
        )
        jumps = d0.reindex(d0["w_diff"].abs().sort_values(ascending=False).index).head(8)
        print(
            jumps[["BB_TICKER", "wind_code", "weight_old", "weight", "w_diff", "fmc_source"]]
            .to_string(index=False),
            flush=True,
        )

    out = valid.rename(
        columns={
            "TRADE_DATE": "trade_date",
            "BB_TICKER": "bb_ticker",
            "SEC_NAME": "sec_name",
            "CONSTITUENT_SEC_ID": "constituent_sec_id",
        }
    )[
        [
            "trade_date",
            "bb_ticker",
            "wind_code",
            "sec_name",
            "constituent_sec_id",
            "fmc",
            "weight",
            "S_DQ_CLOSE_TODAY",
            "FLOAT_A_SHR_TODAY",
            "TOT_SHR_TODAY",
            "fmc_source",
        ]
    ]

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)

    cov = (
        mem.groupby("TRADE_DATE")
        .size()
        .to_frame("n_members")
        .join(out.groupby("trade_date").size().to_frame("n_with_weight"))
        .fillna(0)
    )
    cov["coverage"] = cov["n_with_weight"] / cov["n_members"]
    print(cov.tail(8).to_string(), flush=True)
    print(f"mean coverage={cov['coverage'].mean():.3f}", flush=True)
    latest = out[out["trade_date"] == out["trade_date"].max()].nlargest(10, "weight")
    print("\nTop10 weights on", out["trade_date"].max(), flush=True)
    print(latest[["bb_ticker", "weight", "fmc", "fmc_source"]].to_string(index=False), flush=True)
    print(f"\nWrote {len(out):,} rows -> {path}", flush=True)


if __name__ == "__main__":
    main()
