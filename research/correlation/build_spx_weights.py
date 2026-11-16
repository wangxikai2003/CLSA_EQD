"""Approximate S&P 500 weights from CMDS (not official S&P IWF).

FMC_hat_i = USSHARE.S_DQ_MV (or FLOAT_A_SHR * close), then w_i = FMC_i / sum(FMC).
Membership from INDEXCONSTITUENTS; official CONSTITUENT_WEIGHT is null for SPX.
"""

from __future__ import annotations

import argparse
from pathlib import Path

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

    # Prefer S_DQ_MV; else float shares * close (shares in 万股 → keep relative scale)
    us["fmc"] = us["S_DQ_MV"]
    miss = us["fmc"].isna() | (us["fmc"] <= 0)
    us.loc[miss, "fmc"] = us.loc[miss, "FLOAT_A_SHR_TODAY"] * us.loc[miss, "S_DQ_CLOSE_TODAY"]

    merged = mem.merge(
        us,
        left_on=["TRADE_DATE", "wind_code"],
        right_on=["TRADE_DT", "WINDCODE"],
        how="left",
    )
    merged["fmc"] = pd.to_numeric(merged["fmc"], errors="coerce")
    # drop names without FMC that day
    valid = merged.dropna(subset=["fmc"]).copy()
    valid = valid[valid["fmc"] > 0]
    valid["weight"] = valid["fmc"] / valid.groupby("TRADE_DATE")["fmc"].transform("sum")

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
        ]
    ]

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)

    # coverage report
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
    print(latest[["bb_ticker", "weight", "fmc"]].to_string(index=False), flush=True)
    print(f"\nWrote {len(out):,} rows -> {path}", flush=True)


if __name__ == "__main__":
    main()
