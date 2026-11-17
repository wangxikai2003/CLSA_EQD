"""
Compute SPX implied vol from option *prices* (not VIX).

Primary source: vol_trading/archive/spx_options.parquet
Black-76 with F = S * exp((r-q)*T); default q=r so F≈S.

Daily series: median IV of OTM near-ATM quotes, TTM in [tmin, tmax] days.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
DEFAULT_PARQUET = Path(
    os.environ.get(
        "SPX_OPTIONS_PARQUET",
        str(Path(__file__).resolve().parent.parent / "vol_trading" / "archive" / "spx_options.parquet"),
    )
)
_pipe = Path(os.environ.get("VOL_TRADING_PIPELINE", "")).expanduser()
if not str(_pipe) or str(_pipe) in (".", "") or not _pipe.exists():
    _pipe = Path(__file__).resolve().parent.parent / "vol_trading" / "pipeline"
if _pipe.exists():
    sys.path.insert(0, str(_pipe))
from vol_lib import implied_vol  # noqa: E402


def _cp(contract_type) -> str | None:
    t = str(contract_type).lower()
    if t.startswith("c"):
        return "C"
    if t.startswith("p"):
        return "P"
    return None


def compute_daily(
    df: pd.DataFrame,
    r: float,
    q: float,
    tmin: int,
    tmax: int,
    moneyness_tol: float,
) -> pd.DataFrame:
    df = df.dropna(subset=["close", "SPX_close", "strike_price", "days_to_strike"]).copy()
    df = df[(df["close"] > 0) & (df["days_to_strike"] >= tmin) & (df["days_to_strike"] <= tmax)]
    df["cp"] = df["contract_type"].map(_cp)
    df = df.dropna(subset=["cp"])
    df["as_of_date"] = pd.to_datetime(df["as_of_date"])

    rows = []
    for dt, day in df.groupby("as_of_date", sort=True):
        spot = float(day["SPX_close"].iloc[0])
        if spot <= 0:
            continue
        day = day.copy()
        day["mny"] = day["strike_price"].astype(float) / spot - 1.0
        day = day[day["mny"].abs() <= moneyness_tol]
        if day.empty:
            continue
        otm = day[
            ((day["cp"] == "C") & (day["strike_price"] >= spot))
            | ((day["cp"] == "P") & (day["strike_price"] < spot))
        ]
        use = otm if len(otm) >= 3 else day

        ivs = []
        for row in use.itertuples(index=False):
            T = float(row.days_to_strike) / 365.0
            if T <= 0:
                continue
            F = spot * np.exp((r - q) * T)
            sig = implied_vol(
                price=float(row.close),
                F=float(F),
                K=float(row.strike_price),
                T=T,
                r=r,
                cp=str(row.cp),
            )
            if sig is not None and 0.01 < sig < 3.0:
                ivs.append(sig)
        if len(ivs) < 3:
            continue
        arr = np.asarray(ivs, dtype=float)
        rows.append(
            {
                "trade_date": pd.Timestamp(dt).strftime("%Y%m%d"),
                "spot": spot,
                "iv_atm": float(np.median(arr)),
                "iv_mean": float(np.mean(arr)),
                "iv_p25": float(np.percentile(arr, 25)),
                "iv_p75": float(np.percentile(arr, 75)),
                "n_quotes": int(len(arr)),
                "source": "spx_options_parquet_black76",
            }
        )
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=str(DEFAULT_PARQUET))
    ap.add_argument("--out", default=str(ROOT / "data" / "spx_iv_computed.parquet"))
    ap.add_argument("--r", type=float, default=0.04)
    ap.add_argument("--q", type=float, default=0.04, help="default = r so F≈S")
    ap.add_argument("--tmin", type=int, default=20)
    ap.add_argument("--tmax", type=int, default=40)
    ap.add_argument("--moneyness", type=float, default=0.02)
    args = ap.parse_args()

    path = Path(args.parquet)
    print(f"Loading {path} ...", flush=True)
    cols = [
        "as_of_date",
        "contract_type",
        "strike_price",
        "days_to_strike",
        "SPX_close",
        "close",
    ]
    table = pq.read_table(path, columns=cols)
    df = table.to_pandas()
    # prefilter TTM band to cut work
    df = df[
        df["days_to_strike"].between(args.tmin, args.tmax)
        & df["close"].notna()
        & (df["close"] > 0)
    ]
    print(f"  filtered rows={len(df):,} dates={df['as_of_date'].nunique()}", flush=True)

    out = compute_daily(df, args.r, args.q, args.tmin, args.tmax, args.moneyness)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    print(out.head(3).to_string(index=False), flush=True)
    print("...", flush=True)
    print(out.tail(5).to_string(index=False), flush=True)
    print(f"\nWrote {len(out)} days {out['trade_date'].min()}→{out['trade_date'].max()} -> {out_path}", flush=True)

    vix_path = ROOT / "data" / "spx_iv_proxy_vix.parquet"
    if vix_path.exists() and len(out):
        vix = pd.read_parquet(vix_path)
        m = out.merge(vix[["trade_date", "iv_proxy"]], on="trade_date", how="inner")
        if len(m) > 5:
            print(
                f"diag only: corr(computed, VIX/100)={m['iv_atm'].corr(m['iv_proxy']):.3f} n={len(m)}",
                flush=True,
            )


if __name__ == "__main__":
    main()
