"""
Prompt 1.2 — Dynamic SPX sector-ETF & Top-50 weights (no look-ahead).

Clean/Dirty correlation need basket weights ω_i with Σω=1.

A) SPX vs 11 SPDR sectors
   Point-in-time: map each SPX constituent (as of month-end t) → GICS sector →
   SPDR ETF, aggregate approximate FMC weights, normalize.

B) SPX Top 50
   As of each month-end t, rank constituents by weight_t, take top 50, renormalize.
   Use only information available at t (membership + weights known that day).

Data:
  - Preferred local: data/spx_weights_approx.parquet (from build_spx_weights.py)
  - GICS→SPDR map: static (below); optional BBG `GICS_SECTOR_NAME` enrichment
  - BBG alternative documented in PHASE1_IV_AND_WEIGHTS.md

Usage:
  python build_spx_sector_top50_weights.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

# GICS sector name / keyword → SPDR ETF
# (CMDS/BBG sector strings vary; matching is case-insensitive contains)
GICS_TO_SPDR = {
    "information technology": "XLK",
    "technology": "XLK",
    "financials": "XLF",
    "financial": "XLF",
    "energy": "XLE",
    "health care": "XLV",
    "healthcare": "XLV",
    "industrials": "XLI",
    "consumer discretionary": "XLY",
    "consumer staples": "XLP",
    "utilities": "XLU",
    "materials": "XLB",
    "real estate": "XLRE",
    "communication services": "XLC",
    "telecommunications": "XLC",
    "communication": "XLC",
}

SPDR11 = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]


def map_sector_to_spdr(sector: str) -> str | None:
    if sector is None or (isinstance(sector, float) and pd.isna(sector)):
        return None
    s = str(sector).strip().lower()
    for k, etf in GICS_TO_SPDR.items():
        if k in s:
            return etf
    return None


def load_stock_weights(path: Path) -> pd.DataFrame:
    w = pd.read_parquet(path)
    w["trade_date"] = w["trade_date"].astype(str)
    # normalize per day defensively
    w["weight"] = w["weight"] / w.groupby("trade_date")["weight"].transform("sum")
    return w


def attach_sector_from_bbg_template() -> str:
    return """
# Optional Bloomberg enrichment (run where Terminal exists):
import pdblp, pandas as pd
tickers = [f"{t} US Equity" for t in unique_bb_tickers]
with pdblp.bopen() as con:
    ref = con.ref(tickers, ["GICS_SECTOR_NAME", "GICS_SECTOR"])
# merge onto month-end membership by ticker
"""


def build_top50(weights: pd.DataFrame, k: int = 50) -> pd.DataFrame:
    """Point-in-time Top-k by weight on each trade_date; renormalize."""
    rows = []
    for dt, g in weights.groupby("trade_date"):
        top = g.nlargest(k, "weight").copy()
        top["weight_top"] = top["weight"] / top["weight"].sum()
        top["rank"] = range(1, len(top) + 1)
        top["basket"] = f"TOP{k}"
        rows.append(top)
    out = pd.concat(rows, ignore_index=True)
    return out


def build_sector_weights(
    weights: pd.DataFrame,
    sector_map: pd.DataFrame,
) -> pd.DataFrame:
    """
    sector_map columns: bb_ticker (or wind_code), gics_sector
    Aggregate stock weights → SPDR ω, fill missing sectors with 0, renormalize.
    """
    m = weights.merge(sector_map, on="bb_ticker", how="left")
    m["spdr"] = m["gics_sector"].map(map_sector_to_spdr)
    # drop unmapped from sector basket (or keep as OTHER then drop)
    mapped = m.dropna(subset=["spdr"])
    sec = (
        mapped.groupby(["trade_date", "spdr"], as_index=False)["weight"]
        .sum()
        .rename(columns={"weight": "weight_raw"})
    )
    # complete SPDR grid
    dates = sorted(sec["trade_date"].unique())
    grid = pd.MultiIndex.from_product([dates, SPDR11], names=["trade_date", "spdr"]).to_frame(index=False)
    sec = grid.merge(sec, on=["trade_date", "spdr"], how="left").fillna({"weight_raw": 0.0})
    sec["weight"] = sec["weight_raw"] / sec.groupby("trade_date")["weight_raw"].transform("sum")
    # if all zero (no sector map), leave NaN
    sec.loc[sec.groupby("trade_date")["weight_raw"].transform("sum") <= 0, "weight"] = pd.NA
    return sec


def synthetic_sector_from_name(weights: pd.DataFrame) -> pd.DataFrame:
    """
    Fallback when GICS missing: crude keyword map on sec_name (weak; for pipeline test only).
    Prefer BBG GICS.
    """
    name = weights["sec_name"].fillna("").str.lower()
    # extremely rough — marks most as unknown
    sector = pd.Series(["Unknown"] * len(weights), index=weights.index)
    rules = [
        (["semi", "software", " techn", "chip", "cloud", "apple", "microsoft", "nvidia"], "Information Technology"),
        (["bank", "capital", "insurance", "financial", "jpmorgan", "visa", "mastercard"], "Financials"),
        (["oil", "energy", "petroleum", "exxon", "chevron"], "Energy"),
        (["pharma", "health", "biotech", "drug", "lilly", "unitedhealth"], "Health Care"),
        (["industrial", "aerospace", "caterpillar", "ge "], "Industrials"),
        (["retail", "amazon", "tesla", "home depot", "mcdonald"], "Consumer Discretionary"),
        (["staple", "beverage", "procter", "walmart", "costco", "coca"], "Consumer Staples"),
        (["utilit", "electric"], "Utilities"),
        (["material", "chemical", "mining"], "Materials"),
        (["real estate", "reit"], "Real Estate"),
        (["media", "telecom", "alphabet", "meta", "netflix", "disney", "comcast"], "Communication Services"),
    ]
    for keys, lab in rules:
        mask = False
        for k in keys:
            mask = mask | name.str.contains(k, regex=False)
        sector = sector.where(~mask, lab)
    smap = weights[["bb_ticker"]].copy()
    smap["gics_sector"] = sector.values
    # one row per ticker (last)
    smap = smap.drop_duplicates("bb_ticker", keep="last")
    return build_sector_weights(weights, smap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--weights",
        default=str(ROOT / "data" / "spx_weights_approx.parquet"),
        help="point-in-time stock weights from build_spx_weights.py",
    )
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--out_dir", default=str(ROOT / "data"))
    args = ap.parse_args()

    wpath = Path(args.weights)
    if not wpath.exists():
        raise FileNotFoundError(
            f"{wpath} missing. Run: python build_spx_weights.py --monthly True"
        )
    w = load_stock_weights(wpath)
    print(f"Loaded stock weights: {len(w):,} rows, dates={w['trade_date'].nunique()}", flush=True)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    top = build_top50(w, k=args.top)
    top_path = out_dir / f"spx_top{args.top}_weights.parquet"
    top.to_parquet(top_path, index=False)
    # integrity
    chk = top.groupby("trade_date")["weight_top"].sum()
    print(f"Top{args.top}: wrote {top_path}  weight_sum~1 check min/max={chk.min():.6f}/{chk.max():.6f}", flush=True)
    latest = top[top["trade_date"] == top["trade_date"].max()].nsmallest(5, "rank")
    print("Latest Top5:", flush=True)
    print(latest[["trade_date", "rank", "bb_ticker", "weight_top"]].to_string(index=False), flush=True)

    # Sector: name-heuristic fallback (replace with BBG GICS when available)
    print("\nBuilding sector weights with name-heuristic GICS fallback (replace with BBG)...", flush=True)
    sec = synthetic_sector_from_name(w)
    sec_path = out_dir / "spx_spdr11_weights_approx.parquet"
    sec.to_parquet(sec_path, index=False)
    print(f"Sector SPDR11: wrote {sec_path}", flush=True)
    last = sec[sec["trade_date"] == sec["trade_date"].max()].sort_values("weight", ascending=False)
    print(last.to_string(index=False), flush=True)
    print(attach_sector_from_bbg_template())


if __name__ == "__main__":
    main()
