"""Pull INDEX / EQUITY / FX OHLC from CMDS into a local parquet panel."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cmds_connect import read_sql

# Calypso-style symbol -> CMDS lookup key (prefer BB_TICKER, else WIND_CODE)
INDEX_UNIVERSE = {
    "SPX": {"bb": "SPX Index"},
    "SX5E": {"bb": "SX5E"},
    "NKY": {"bb": "NKY Index"},
    "KOSPI2": {"bb": "KOSPI2"},
    "HSI": {"bb": "HSI Index"},
    "HSCEI": {"bb": "HSCEI Index"},
    "HSTECH": {"bb": "HSTECH Index"},
    "SHSZ300": {"bb": "SHSZ300 Index", "wind": "000300.SH"},
    "CSI1000": {"bb": "CSI1000 Index", "wind": "000852.SH"},
    "SH000905": {"bb": "SH000905 Index", "wind": "000905.SH"},
    "SSE50": {"bb": "SSE50", "wind": "000016.SH"},
}

EQUITY_UNIVERSE = {
    "700.HK": {"bb": "700 HK", "wind": "0700.HK"},
    "AAPL.US": {"bb": "AAPL US", "wind": "AAPL.O"},
    "600519.CH": {"bb": "600519 C1", "wind": "600519.SH"},
}

# Prefer clean Wind spot series (one row per day)
FX_UNIVERSE = {
    "USD/HKD": {"wind": "USDHKD.FX"},
    "USD/CNY": {"wind": "USDCNY.FX"},
    "USD/CNH": {"wind": "USDCNH.FX"},
    "USD/JPY": {"wind": "USDJPY.FX"},
    "EUR/USD": {"wind": "EURUSD.FX"},
}


def _in_list(values: list[str]) -> str:
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)


def fetch_index(start: str, end: str) -> pd.DataFrame:
    bbs = [v["bb"] for v in INDEX_UNIVERSE.values() if "bb" in v]
    sql = f"""
    SELECT s.BB_TICKER, s.WIND_CODE, s.SEC_ID, e.TRADE_DATE,
           e.OPEN_PRICE, e.CLOSE_PRICE
    FROM CDM.SECURITYCODE s
    JOIN CDM.INDEXEOD e ON e.SEC_ID = s.SEC_ID
    WHERE s.BB_TICKER IN ({_in_list(bbs)})
      AND e.TRADE_DATE BETWEEN '{start}' AND '{end}'
    """
    raw = read_sql(sql)
    bb_to_sym = {v["bb"]: k for k, v in INDEX_UNIVERSE.items() if "bb" in v}
    raw["symbol"] = raw["BB_TICKER"].map(bb_to_sym)
    raw = raw.dropna(subset=["symbol"])
    out = raw.rename(
        columns={
            "TRADE_DATE": "trade_date",
            "OPEN_PRICE": "open",
            "CLOSE_PRICE": "close",
            "BB_TICKER": "bb_ticker",
            "WIND_CODE": "wind_code",
        }
    )
    out["asset_type"] = "INDEX"
    return out[["symbol", "asset_type", "bb_ticker", "wind_code", "trade_date", "open", "close"]]


def fetch_equity(start: str, end: str) -> pd.DataFrame:
    bbs = [v["bb"] for v in EQUITY_UNIVERSE.values()]
    sql = f"""
    SELECT s.BB_TICKER, s.WIND_CODE, s.SEC_ID, e.TRADE_DATE,
           e.OPEN_PRICE, e.CLOSE_PRICE, e.ADJCLOSE_FORWARD
    FROM CDM.SECURITYCODE s
    JOIN CDM.EQUITYEOD e ON e.SEC_ID = s.SEC_ID
    WHERE s.BB_TICKER IN ({_in_list(bbs)})
      AND e.TRADE_DATE BETWEEN '{start}' AND '{end}'
    """
    raw = read_sql(sql)
    bb_to_sym = {v["bb"]: k for k, v in EQUITY_UNIVERSE.items()}
    raw["symbol"] = raw["BB_TICKER"].map(bb_to_sym)
    raw = raw.dropna(subset=["symbol"])
    # prefer adjusted close when present
    close = raw["ADJCLOSE_FORWARD"].where(raw["ADJCLOSE_FORWARD"].notna(), raw["CLOSE_PRICE"])
    out = pd.DataFrame(
        {
            "symbol": raw["symbol"],
            "asset_type": "EQUITY",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WIND_CODE"],
            "trade_date": raw["TRADE_DATE"],
            "open": raw["OPEN_PRICE"],
            "close": close,
        }
    )
    return out


def fetch_fx(start: str, end: str) -> pd.DataFrame:
    winds = [v["wind"] for v in FX_UNIVERSE.values()]
    sql = f"""
    SELECT WINDCODE, BASE_CRNCY, QUOTE_CRNCY, TRADE_DATE, OPEN, CLOSE, BB_TICKER
    FROM CDM.FXEOD
    WHERE WINDCODE IN ({_in_list(winds)})
      AND TRADE_DATE BETWEEN '{start}' AND '{end}'
    """
    raw = read_sql(sql)
    wind_to_sym = {v["wind"]: k for k, v in FX_UNIVERSE.items()}
    raw["symbol"] = raw["WINDCODE"].map(wind_to_sym)
    raw = raw.dropna(subset=["symbol"])
    out = pd.DataFrame(
        {
            "symbol": raw["symbol"],
            "asset_type": "FX",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WINDCODE"],
            "trade_date": raw["TRADE_DATE"],
            "open": raw["OPEN"],
            "close": raw["CLOSE"],
        }
    )
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20190101")
    p.add_argument("--end", default="20261113")
    p.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "data" / "cmds_price_panel.parquet"),
    )
    args = p.parse_args()

    frames = [fetch_index(args.start, args.end), fetch_equity(args.start, args.end), fetch_fx(args.start, args.end)]
    panel = pd.concat(frames, ignore_index=True)
    panel["trade_date"] = panel["trade_date"].astype(str)
    for c in ("open", "close"):
        panel[c] = pd.to_numeric(panel[c], errors="coerce")
    panel = panel.dropna(subset=["close"]).sort_values(["symbol", "trade_date"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out, index=False)

    summary = (
        panel.groupby(["symbol", "asset_type"], as_index=False)
        .agg(n=("close", "size"), dmin=("trade_date", "min"), dmax=("trade_date", "max"))
        .sort_values("symbol")
    )
    print(summary.to_string(index=False))
    print(f"\nWrote {len(panel):,} rows -> {out}")


if __name__ == "__main__":
    main()
