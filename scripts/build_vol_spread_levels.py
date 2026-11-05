#!/usr/bin/env python3
"""Precompute RV/IV constant-tenor series for Spread → Vol panel.

Writes CLSA_EQD/data/vol_spread_levels.parquet (shipped in Docker).
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OUT = ROOT / "data" / "vol_spread_levels.parquet"

# Month labels → trading-day windows (≈21 trading days per month)
RV_WINDOWS = [
    ("1M", 21),
    ("3M", 63),
    ("6M", 126),
    ("12M", 252),
]
# IV target TTM in years ≈ trading days / 252
IV_TENORS = [(lab, d / 252.0) for lab, d in RV_WINDOWS]

# K/F moneyness for skew history
MONEY = {
    "ATM": 1.00,
    "OTM": 0.95,  # put wing
    "ITM": 1.05,  # call wing
}

EQUITY = [
    {"public": "IH", "db": "HO", "asset_class": "equity"},
    {"public": "IF", "db": "IO", "asset_class": "equity"},
    {"public": "IM", "db": "MO", "asset_class": "equity"},
]
COMMODITY = [
    {"public": p, "db": p, "asset_class": "commodity"}
    for p in ["CU", "AU", "AG", "RU", "SC", "M", "I", "C", "SR", "CF", "MA", "TA"]
]


def raw_svi_w(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    x = k - m
    return a + b * (rho * x + math.sqrt(x * x + sigma * sigma))


def svi_iv(k: float, ttm: float, a, b, rho, m, sigma, jw_v=None) -> float:
    if ttm <= 1e-8:
        return float("nan")
    if abs(k) < 1e-12 and jw_v is not None and pd.notna(jw_v):
        return math.sqrt(max(float(jw_v), 0.0))
    w = raw_svi_w(k, a, b, rho, m, sigma)
    return math.sqrt(max(w, 1e-12) / ttm)


def front_settle(conn: sqlite3.Connection, db_product: str) -> pd.Series:
    """Near-month settle (min contract_ym each day) for RV."""
    df = pd.read_sql(
        """
        SELECT trade_date, contract_ym, settle
          FROM futures_eod
         WHERE product=? AND settle IS NOT NULL AND settle > 0
         ORDER BY trade_date, contract_ym
        """,
        conn,
        params=(db_product,),
    )
    if df.empty:
        return pd.Series(dtype=float)
    df = df.sort_values(["trade_date", "contract_ym"])
    front = df.groupby("trade_date", as_index=False).first()
    s = front.set_index("trade_date")["settle"].astype(float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def rv_series(settle: pd.Series) -> Dict[str, pd.Series]:
    rets = np.log(settle).diff()
    out = {}
    for lab, w in RV_WINDOWS:
        rv = rets.rolling(w, min_periods=max(5, w // 2)).std() * math.sqrt(252)
        out[lab] = rv.dropna()
    return out


def iv_constant_maturity(slices: pd.DataFrame) -> Dict[str, Dict[str, pd.Series]]:
    """Return {tenor: {ATM/ITM/OTM: series}} from daily SVI slices (otm side)."""
    if slices.empty:
        return {lab: {m: pd.Series(dtype=float) for m in MONEY} for lab, _ in IV_TENORS}

    rows = []
    for (td, g) in slices.groupby("trade_date"):
        g = g.sort_values("ttm")
        if len(g) < 1:
            continue
        ttms = g["ttm"].to_numpy(float)
        for lab, tgt in IV_TENORS:
            # nearest or linear interp in TTM for params / IV
            if tgt <= ttms[0]:
                r0 = g.iloc[0]
                use = [r0]
                wts = [1.0]
            elif tgt >= ttms[-1]:
                r0 = g.iloc[-1]
                use = [r0]
                wts = [1.0]
            else:
                i = int(np.searchsorted(ttms, tgt))
                r0, r1 = g.iloc[i - 1], g.iloc[i]
                t0, t1 = float(r0.ttm), float(r1.ttm)
                w0 = (t1 - tgt) / (t1 - t0) if t1 != t0 else 1.0
                use = [r0, r1]
                wts = [w0, 1.0 - w0]
            for mny_name, kf in MONEY.items():
                k = math.log(kf)
                ivs = []
                for r in use:
                    ivs.append(
                        svi_iv(
                            k,
                            float(r.ttm) if len(use) == 1 else tgt,
                            float(r.a),
                            float(r.b),
                            float(r.rho),
                            float(r.m),
                            float(r.sigma),
                            getattr(r, "jw_v", None) if abs(k) < 1e-12 else None,
                        )
                    )
                if len(ivs) == 1:
                    iv = ivs[0]
                else:
                    iv = wts[0] * ivs[0] + wts[1] * ivs[1]
                if iv == iv and iv > 0:
                    rows.append({"d": str(td)[:10], "tenor": lab, "mny": mny_name, "iv": float(iv)})

    if not rows:
        return {lab: {m: pd.Series(dtype=float) for m in MONEY} for lab, _ in IV_TENORS}
    df = pd.DataFrame(rows)
    out: Dict[str, Dict[str, pd.Series]] = {}
    for lab, _ in IV_TENORS:
        out[lab] = {}
        for mny in MONEY:
            sub = df[(df["tenor"] == lab) & (df["mny"] == mny)].set_index("d")["iv"]
            out[lab][mny] = sub.sort_index()
    return out


def load_svi(conn: sqlite3.Connection, db_product: str) -> pd.DataFrame:
    return pd.read_sql(
        """
        SELECT trade_date, ttm, a, b, rho, m, sigma, jw_v
          FROM svi_slices
         WHERE product=? AND side='otm'
         ORDER BY trade_date, ttm
        """,
        conn,
        params=(db_product,),
    )


def process_universe(conn: sqlite3.Connection, universe: list) -> list:
    records = []
    for u in universe:
        pub, db, ac = u["public"], u["db"], u["asset_class"]
        print(f"  {pub} (db={db}) …", flush=True)
        settle = front_settle(conn, db)
        if settle.empty:
            print("    no futures")
        else:
            for lab, ser in rv_series(settle).items():
                for d, v in ser.items():
                    records.append(
                        {
                            "product": pub,
                            "db_key": db,
                            "asset_class": ac,
                            "family": "RV",
                            "tenor": lab,
                            "moneyness": "",
                            "d": pd.Timestamp(d).strftime("%Y-%m-%d"),
                            "v": float(v),
                        }
                    )
        try:
            slices = load_svi(conn, db)
        except Exception as e:
            print("    svi fail", e)
            slices = pd.DataFrame()
        if not slices.empty:
            ivs = iv_constant_maturity(slices)
            for lab, by_m in ivs.items():
                for mny, ser in by_m.items():
                    for d, v in ser.items():
                        records.append(
                            {
                                "product": pub,
                                "db_key": db,
                                "asset_class": ac,
                                "family": "IV",
                                "tenor": lab,
                                "moneyness": mny,
                                "d": str(d)[:10],
                                "v": float(v),
                            }
                        )
        print(f"    rows so far {len(records):,}")
    return records


def main():
    eq_db = ROOT / "data" / "cffex_vol.sqlite"
    cm_db = REPO / "data" / "commodity_vol.sqlite"
    if not eq_db.exists():
        sys.exit(f"missing {eq_db}")
    records = []
    print("=== equity ===")
    con = sqlite3.connect(str(eq_db))
    records.extend(process_universe(con, EQUITY))
    con.close()

    if cm_db.exists() and cm_db.stat().st_size > 0:
        print("=== commodity ===")
        con = sqlite3.connect(str(cm_db))
        records.extend(process_universe(con, COMMODITY))
        con.close()
    else:
        print("commodity_vol.sqlite not found — equity only")

    df = pd.DataFrame(records)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"wrote {OUT} rows={len(df):,} size={OUT.stat().st_size/1e6:.1f}MB")
    print(df.groupby(["asset_class", "family"]).size())


if __name__ == "__main__":
    main()
