"""
Prompt 1.1 — Exploration: local US IV surface coverage + BBG/CBOE pull catalog.

Checks local sqlite/parquet for SPX/NDX, 11 SPDR sector ETFs, and a Top-50
proxy universe; prints tenor×moneyness/delta style surface coverage.
Also documents Bloomberg pdblp tickers/fields and CBOE COR* indices.

Usage:
  python explore_us_iv_coverage.py
  python explore_us_iv_coverage.py --try_bbg   # only if Terminal + pdblp available
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
VOL = Path("/Users/wangxikai/Desktop/CITICS CLSA/vol_trading")

# --- Universes ---
INDEX = ["SPX", "NDX"]
SPDR11 = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
# illustrative Top-50 mega-cap proxy (NOT dynamic membership; real Top50 from weights script)
TOP50_PROXY = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK/B", "AVGO", "TSLA",
    "LLY", "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "PG", "JNJ",
    "WMT", "ABBV", "NFLX", "BAC", "CRM", "ORCL", "CVX", "KO", "MRK", "AMD",
    "PEP", "TMO", "ACN", "CSCO", "LIN", "MCD", "ADBE", "WFC", "IBM", "GE",
    "ABT", "CAT", "DIS", "QCOM", "INTU", "VZ", "CMCSA", "TXN", "PM", "NOW",
]

TENORS = {
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
}

# Bloomberg standardized DF moneyness IV fields (common desk convention)
BBG_IV_FIELDS = [
    "30DAY_IMPVOL_100.0%MNY_DF",
    "60DAY_IMPVOL_100.0%MNY_DF",
    "90DAY_IMPVOL_100.0%MNY_DF",
    "180DAY_IMPVOL_100.0%MNY_DF",
    "360DAY_IMPVOL_100.0%MNY_DF",
    # deltas (puts often used for skew)
    "30DAY_IMPVOL_25%DELTA_DF",
    "30DAY_IMPVOL_50%DELTA_DF",
    "90DAY_IMPVOL_25%DELTA_DF",
    "90DAY_IMPVOL_50%DELTA_DF",
    # coarser fallbacks
    "HIST_CALL_IMP_VOL",
    "HIST_PUT_IMP_VOL",
]

CBOE_COR = ["COR1M Index", "COR3M Index", "COR6M Index", "COR1Y Index"]


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def check_sqlite(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    con = sqlite3.connect(path)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    info = {"path": str(path), "exists": True, "tables": {}}
    for t in tables:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        info["tables"][t] = {"n": n, "cols": cols}
        # product list if present
        if "product" in cols:
            prods = [r[0] for r in con.execute(f"SELECT DISTINCT product FROM {t} LIMIT 50")]
            info["tables"][t]["products_sample"] = prods
    # US ticker search in option_iv wind_code if table exists
    if "option_iv" in tables:
        like_clauses = " OR ".join(
            [f"wind_code LIKE '%{u}%'" for u in INDEX + SPDR11 + TOP50_PROXY[:10]]
        )
        try:
            hits = con.execute(
                f"SELECT wind_code, COUNT(*) n FROM option_iv WHERE {like_clauses} GROUP BY wind_code LIMIT 30"
            ).fetchall()
            info["us_like_hits_in_option_iv"] = hits
        except Exception as e:
            info["us_like_hits_in_option_iv"] = f"ERR {e}"
    con.close()
    return info


def check_spx_options_parquet(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    cols = pq.read_schema(path).names
    # light date range via as_of_date only
    t = pq.read_table(path, columns=["as_of_date", "days_to_strike", "close", "delta"])
    df = t.to_pandas()
    df["as_of_date"] = pd.to_datetime(df["as_of_date"])
    has_iv = "iv" in cols
    out = {
        "path": str(path),
        "exists": True,
        "columns": cols,
        "has_iv_column": has_iv,
        "n_rows": len(df),
        "dmin": str(df["as_of_date"].min().date()),
        "dmax": str(df["as_of_date"].max().date()),
        "n_dates": int(df["as_of_date"].nunique()),
        "close_nonnull_pct": float(df["close"].notna().mean()),
        "delta_nonnull_pct": float(df["delta"].notna().mean()) if "delta" in df else None,
    }
    # tenor coverage among priced quotes
    priced = df[df["close"].notna() & (df["close"] > 0)].copy()
    if len(priced) and "days_to_strike" in priced:
        for name, days in TENORS.items():
            band = priced[priced["days_to_strike"].between(days * 0.7, days * 1.3)]
            out[f"tenor_{name}_priced_rows"] = int(len(band))
            out[f"tenor_{name}_n_dates"] = int(band["as_of_date"].nunique())
    return out


def check_corr_data_dir(data_dir: Path) -> dict:
    files = {}
    if not data_dir.exists():
        return {"path": str(data_dir), "exists": False}
    for p in sorted(data_dir.glob("*")):
        if p.suffix.lower() in {".parquet", ".csv", ".sqlite"}:
            meta = {"size_mb": round(p.stat().st_size / 1e6, 3)}
            if p.suffix == ".parquet":
                try:
                    df = pd.read_parquet(p)
                    meta["rows"] = len(df)
                    meta["cols"] = list(df.columns)
                    if "trade_date" in df.columns:
                        meta["dmin"] = str(df["trade_date"].min())
                        meta["dmax"] = str(df["trade_date"].max())
                    if "symbol" in df.columns:
                        syms = set(df["symbol"].astype(str))
                        meta["has_SPX"] = "SPX" in syms
                        meta["has_NDX"] = "NDX" in syms
                        meta["spdr_hits"] = sorted(s for s in SPDR11 if s in syms or f"{s} US" in syms)
                except Exception as e:
                    meta["err"] = str(e)
            files[p.name] = meta
    return {"path": str(data_dir), "exists": True, "files": files}


def coverage_matrix_local() -> pd.DataFrame:
    """Qualitative local coverage scorecard (0/1/partial)."""
    rows = []
    computed = ROOT / "data" / "spx_iv_computed.parquet"
    spx_opt = VOL / "archive" / "spx_options.parquet"
    panel = ROOT / "data" / "cmds_price_panel.parquet"
    for name, kind in (
        [("SPX", "index"), ("NDX", "index")]
        + [(x, "spdr") for x in SPDR11]
        + [(x, "top50_proxy") for x in TOP50_PROXY[:15]]  # print sample
    ):
        spot = False
        chain = False
        iv_surface = False
        iv_atm_ts = False
        notes = []
        if panel.exists():
            try:
                syms = set(pd.read_parquet(panel, columns=["symbol"])["symbol"].astype(str))
                spot = name in syms or f"{name}.US" in syms or any(name in s for s in syms)
            except Exception:
                pass
        if name == "SPX":
            chain = spx_opt.exists()
            iv_atm_ts = computed.exists()
            iv_surface = False  # we only store ATM TS locally, not full surface grid
            notes.append("ATM IV TS from price inversion; no local tenor×delta grid DB")
        elif name == "NDX":
            notes.append("spot may exist in CMDS; no local NDX option surface parquet")
        else:
            notes.append("no local US single-name/ETF IV surface sqlite")
        rows.append(
            {
                "ticker": name,
                "bucket": kind,
                "spot_local": int(spot),
                "option_chain_local": int(chain),
                "atm_iv_ts_local": int(iv_atm_ts),
                "full_surface_local": int(iv_surface),
                "notes": "; ".join(notes),
            }
        )
    return pd.DataFrame(rows)


def bbg_ticker(sym: str, bucket: str) -> str:
    if bucket == "index":
        return f"{sym} Index"
    # ETFs and equities
    if "/" in sym:  # BRK/B
        return f"{sym.replace('/', '/')} US Equity"
    return f"{sym} US Equity"


def print_bbg_catalog() -> None:
    section("Bloomberg pdblp catalog (for missing local surfaces)")
    print(
        """
Standard approach (desk-style constant-maturity DF moneyness / delta IV):
  con.bdh(tickers=[...], flds=[field], start_date=YYYYMMDD, end_date=YYYYMMDD)

Index examples:
  SPX Index, NDX Index

Sector ETF examples:
  XLK US Equity, XLF US Equity, ...

Single-name:
  AAPL US Equity, MSFT US Equity, ...

Preferred fields (try in order; availability varies by asset):
"""
    )
    for f in BBG_IV_FIELDS:
        print(f"  - {f}")
    print(
        """
Notes:
  - %MNY_DF = delta-forward / forward-moneyness standardized surface points
  - HIST_*_IMP_VOL = coarser history; use only if DF fields empty
  - Batch: group by field; pdblp can choke on huge ticker×field products — chunk 50 tickers
  - Requires Bloomberg Terminal + `pip install pdblp` + localhost BLPAPI
"""
    )
    print("CBOE implied correlation indices (dirty/clean benchmark):")
    for t in CBOE_COR:
        print(f"  - {t}  field=PX_LAST")


def try_bbg_smoke(start: str = "20240101", end: str = "20241231") -> None:
    section("Bloomberg smoke test")
    try:
        import pdblp
    except ImportError:
        print("pdblp not installed — skip live pull")
        return
    tickers = ["SPX Index", "XLK US Equity", "AAPL US Equity"] + CBOE_COR
    fields = ["30DAY_IMPVOL_100.0%MNY_DF", "PX_LAST"]
    try:
        with pdblp.bopen(timeout=10000) as con:
            for fld in fields:
                try:
                    df = con.bdh(tickers=tickers, flds=fld, start_date=start, end_date=end)
                    print(f"OK {fld}: shape={getattr(df, 'shape', None)}")
                    print(df.tail(2))
                except Exception as e:
                    print(f"FAIL {fld}: {e}")
    except Exception as e:
        print(f"Cannot open Bloomberg: {e}")


def write_bbg_pull_template(out: Path) -> None:
    """Generate a ready-to-run pull script skeleton (does not execute)."""
    code = '''\
"""Batch-pull US IV points + CBOE COR* via pdblp. Run on a machine with Bloomberg."""
import pdblp
import pandas as pd

INDEX = ["SPX Index", "NDX Index"]
SPDR = [f"{x} US Equity" for x in
        ["XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLRE","XLC"]]
# fill TOP50 with point-in-time list from weights script
TOP50 = [f"{x} US Equity" for x in ["AAPL","MSFT","NVDA","AMZN","META"]]

FIELDS = [
    "30DAY_IMPVOL_100.0%MNY_DF",
    "90DAY_IMPVOL_100.0%MNY_DF",
    "180DAY_IMPVOL_100.0%MNY_DF",
    "360DAY_IMPVOL_100.0%MNY_DF",
    "30DAY_IMPVOL_25%DELTA_DF",
    "90DAY_IMPVOL_25%DELTA_DF",
]
COR = ["COR1M Index", "COR3M Index", "COR6M Index", "COR1Y Index"]

def chunk(xs, n=40):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

def pull(start="20190101", end="20261113"):
    frames = []
    with pdblp.bopen(timeout=20000) as con:
        for fld in FIELDS:
            for part in chunk(INDEX + SPDR + TOP50):
                try:
                    df = con.bdh(tickers=part, flds=fld, start_date=start, end_date=end)
                    long = df.stack(level=0).reset_index()
                    long.columns = ["date", "ticker", "value"]
                    long["field"] = fld
                    frames.append(long)
                except Exception as e:
                    print("skip", fld, part[:2], e)
        cor = con.bdh(tickers=COR, flds="PX_LAST", start_date=start, end_date=end)
        c = cor.stack(level=0).reset_index()
        c.columns = ["date", "ticker", "value"]
        c["field"] = "PX_LAST"
        frames.append(c)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet("data/bbg_us_iv_cor_panel.parquet", index=False)
    return out

if __name__ == "__main__":
    pull()
'''
    out.write_text(code)
    print(f"Wrote BBG pull template -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--try_bbg", action="store_true")
    ap.add_argument("--out_json", default=str(ROOT / "data" / "explore_us_iv_coverage_report.json"))
    args = ap.parse_args()

    report = {}

    section("1) Local sqlite (China vol DBs — expect NO US surfaces)")
    for rel in [
        VOL / "data" / "cffex_vol.sqlite",
        VOL / "data" / "commodity_vol.sqlite",
        VOL / "CLSA_EQD" / "data" / "cffex_vol.sqlite",
    ]:
        info = check_sqlite(rel)
        report[rel.name] = info
        print(f"\n{rel.name}: exists={info.get('exists')}")
        if info.get("exists"):
            print("  tables:", list(info["tables"].keys()))
            if info["tables"].get("products"):
                print("  products sample:", info["tables"]["products"].get("products_sample"))
            print("  US-like hits in option_iv:", info.get("us_like_hits_in_option_iv"))

    section("2) Local SPX option chain parquet")
    spx_info = check_spx_options_parquet(VOL / "archive" / "spx_options.parquet")
    report["spx_options_parquet"] = spx_info
    print(json.dumps(spx_info, indent=2, default=str))

    section("3) correlation/data artifacts")
    art = check_corr_data_dir(ROOT / "data")
    report["corr_data"] = art
    print(json.dumps(art, indent=2, default=str))

    section("4) Coverage scorecard (local)")
    score = coverage_matrix_local()
    report["scorecard"] = score.to_dict(orient="records")
    print(score.to_string(index=False))
    print(
        "\nVERDICT: Local DBs do NOT store standardized US IV surfaces for NDX / SPDR11 / Top50."
        "\n          SPX has option prices + computed ATM IV TS only (~2y), not a full tenor×delta surface table."
    )

    print_bbg_catalog()
    tmpl = ROOT / "pull_bbg_us_iv_cor_template.py"
    write_bbg_pull_template(tmpl)

    if args.try_bbg:
        try_bbg_smoke()

    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport JSON -> {out}")


if __name__ == "__main__":
    main()
