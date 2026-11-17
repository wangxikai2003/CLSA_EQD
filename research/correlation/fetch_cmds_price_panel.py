"""Pull INDEX / EQUITY / FX OHLC from CMDS into a local parquet panel.

Also supports dynamic US equity universes via --universe-file or NDX constituents.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cmds_connect import read_sql

ROOT = Path(__file__).resolve().parent
NDX_SEC_ID = "6NL341kna8pu"

INDEX_UNIVERSE = {
    "SPX": {"bb": "SPX Index"},
    "NDX": {"bb": "NDX Index"},
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
            "SEC_ID": "sec_id",
        }
    )
    out["asset_type"] = "INDEX"
    out["adj_close"] = out["close"]
    return out[
        ["symbol", "asset_type", "bb_ticker", "wind_code", "sec_id", "trade_date", "open", "close", "adj_close"]
    ]


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
    close = raw["ADJCLOSE_FORWARD"].where(raw["ADJCLOSE_FORWARD"].notna(), raw["CLOSE_PRICE"])
    return pd.DataFrame(
        {
            "symbol": raw["symbol"],
            "asset_type": "EQUITY",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WIND_CODE"],
            "sec_id": raw["SEC_ID"],
            "trade_date": raw["TRADE_DATE"],
            "open": raw["OPEN_PRICE"],
            "close": raw["CLOSE_PRICE"],
            "adj_close": close,
        }
    )


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
    return pd.DataFrame(
        {
            "symbol": raw["symbol"],
            "asset_type": "FX",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WINDCODE"],
            "sec_id": None,
            "trade_date": raw["TRADE_DATE"],
            "open": raw["OPEN"],
            "close": raw["CLOSE"],
            "adj_close": raw["CLOSE"],
        }
    )


def load_universe_sec_ids(universe_file: str | None, index_sec_id: str | None, start: str, end: str) -> pd.DataFrame:
    """Return DataFrame with columns sec_id, symbol(optional), weight(optional)."""
    if universe_file:
        path = Path(universe_file)
        df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
        cols = {c.lower(): c for c in df.columns}
        sec_col = cols.get("constituent_sec_id") or cols.get("sec_id") or cols.get("secid")
        if not sec_col:
            raise SystemExit(f"universe file missing sec_id column: {list(df.columns)}")
        out = pd.DataFrame({"sec_id": df[sec_col].astype(str)})
        if "wind_code" in cols:
            out["wind_code"] = df[cols["wind_code"]].astype(str)
        if "weight" in cols:
            out["weight"] = pd.to_numeric(df[cols["weight"]], errors="coerce")
        return out.drop_duplicates("sec_id")

    if index_sec_id:
        sql = f"""
        SELECT DISTINCT CONSTITUENT_SEC_ID AS SEC_ID, CONSTITUENT_WINDCODE AS WIND_CODE
        FROM CDM.INDEXCONSTITUENTS
        WHERE INDEX_SEC_ID = '{index_sec_id}'
          AND TRADE_DATE BETWEEN '{start}' AND '{end}'
        """
        raw = read_sql(sql)
        return pd.DataFrame(
            {
                "sec_id": raw["SEC_ID"].astype(str),
                "wind_code": raw["WIND_CODE"].astype(str),
            }
        ).drop_duplicates("sec_id")
    return pd.DataFrame(columns=["sec_id"])


def fetch_equity_by_sec_ids(sec_ids: list[str], start: str, end: str, batch: int = 200) -> pd.DataFrame:
    frames = []
    for i in range(0, len(sec_ids), batch):
        part = sec_ids[i : i + batch]
        if not part:
            continue
        print(f"  equity batch {i // batch + 1}/{(len(sec_ids) - 1) // batch + 1} n={len(part)}", flush=True)
        sql = f"""
        SELECT e.SEC_ID, s.BB_TICKER, s.WIND_CODE, e.TRADE_DATE,
               e.OPEN_PRICE, e.CLOSE_PRICE, e.ADJCLOSE_FORWARD
        FROM CDM.EQUITYEOD e
        LEFT JOIN CDM.SECURITYCODE s ON s.SEC_ID = e.SEC_ID
        WHERE e.SEC_ID IN ({_in_list(part)})
          AND e.TRADE_DATE BETWEEN '{start}' AND '{end}'
        """
        frames.append(read_sql(sql))
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    adj = raw["ADJCLOSE_FORWARD"].where(raw["ADJCLOSE_FORWARD"].notna(), raw["CLOSE_PRICE"])
    return pd.DataFrame(
        {
            "symbol": raw["WIND_CODE"].fillna(raw["BB_TICKER"]).astype(str),
            "asset_type": "EQUITY",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WIND_CODE"],
            "sec_id": raw["SEC_ID"].astype(str),
            "trade_date": raw["TRADE_DATE"].astype(str),
            "open": pd.to_numeric(raw["OPEN_PRICE"], errors="coerce"),
            "close": pd.to_numeric(raw["CLOSE_PRICE"], errors="coerce"),
            "adj_close": pd.to_numeric(adj, errors="coerce"),
        }
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20190101")
    p.add_argument("--end", default="20261113")
    p.add_argument(
        "--out",
        default=str(ROOT / "data" / "cmds_price_panel.parquet"),
    )
    p.add_argument(
        "--universe-file",
        default="",
        help="parquet/csv with constituent_sec_id (e.g. spx_top50_weights.parquet)",
    )
    p.add_argument(
        "--ndx-constituents",
        action="store_true",
        help="pull all historical NDX constituent SEC_IDs in [start,end]",
    )
    p.add_argument(
        "--us-equity-out",
        default=str(ROOT / "data" / "cmds_us_equity_panel.parquet"),
        help="output path when pulling dynamic US equity universe",
    )
    p.add_argument("--batch", type=int, default=200)
    p.add_argument("--skip-default", action="store_true", help="skip default index/equity/fx panel")
    args = p.parse_args()

    frames = []
    if not args.skip_default:
        frames = [
            fetch_index(args.start, args.end),
            fetch_equity(args.start, args.end),
            fetch_fx(args.start, args.end),
        ]
        panel = pd.concat(frames, ignore_index=True)
        panel["trade_date"] = panel["trade_date"].astype(str)
        for c in ("open", "close", "adj_close"):
            if c in panel.columns:
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

    if args.universe_file or args.ndx_constituents:
        index_id = NDX_SEC_ID if args.ndx_constituents else None
        uni = load_universe_sec_ids(
            args.universe_file or None, index_id, args.start, args.end
        )
        sec_ids = sorted(uni["sec_id"].dropna().astype(str).unique().tolist())
        print(f"\nDynamic US equity universe n={len(sec_ids)}", flush=True)
        eq = fetch_equity_by_sec_ids(sec_ids, args.start, args.end, batch=args.batch)
        eq = eq.dropna(subset=["adj_close"]).sort_values(["sec_id", "trade_date"])
        us_out = Path(args.us_equity_out)
        us_out.parent.mkdir(parents=True, exist_ok=True)
        eq.to_parquet(us_out, index=False)
        print(
            f"US equity panel rows={len(eq):,} names={eq['sec_id'].nunique()} "
            f"{eq['trade_date'].min()}→{eq['trade_date'].max()} -> {us_out}",
            flush=True,
        )


if __name__ == "__main__":
    main()
