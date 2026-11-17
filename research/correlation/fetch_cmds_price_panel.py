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
               e.OPEN_PRICE, e.CLOSE_PRICE, e.ADJCLOSE_FORWARD, e.ADJOPEN_FORWARD
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
    adj_open = raw["ADJOPEN_FORWARD"].where(raw["ADJOPEN_FORWARD"].notna(), raw["OPEN_PRICE"])
    adj_src = raw["ADJCLOSE_FORWARD"].notna().map({True: "adj_forward", False: "raw_close"})
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
            "adj_open": pd.to_numeric(adj_open, errors="coerce"),
            "adj_close": pd.to_numeric(adj, errors="coerce"),
            "adj_source": adj_src,
        }
    )


def fetch_index_by_sec_ids(
    sec_id_to_symbol: dict[str, str], start: str, end: str, batch: int = 20
) -> pd.DataFrame:
    sec_ids = list(sec_id_to_symbol.keys())
    frames = []
    for i in range(0, len(sec_ids), batch):
        part = sec_ids[i : i + batch]
        sql = f"""
        SELECT e.SEC_ID, s.BB_TICKER, s.WIND_CODE, e.TRADE_DATE,
               e.OPEN_PRICE, e.CLOSE_PRICE
        FROM CDM.INDEXEOD e
        LEFT JOIN CDM.SECURITYCODE s ON s.SEC_ID = e.SEC_ID
        WHERE e.SEC_ID IN ({_in_list(part)})
          AND e.TRADE_DATE BETWEEN '{start}' AND '{end}'
        """
        frames.append(read_sql(sql))
        print(f"  index batch {i // batch + 1} n={len(part)}", flush=True)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    raw["sec_id"] = raw["SEC_ID"].astype(str)
    raw["symbol"] = raw["sec_id"].map(sec_id_to_symbol)
    return pd.DataFrame(
        {
            "symbol": raw["symbol"],
            "asset_type": "INDEX",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WIND_CODE"],
            "sec_id": raw["sec_id"],
            "trade_date": raw["TRADE_DATE"].astype(str),
            "open": pd.to_numeric(raw["OPEN_PRICE"], errors="coerce"),
            "close": pd.to_numeric(raw["CLOSE_PRICE"], errors="coerce"),
            "adj_open": pd.to_numeric(raw["OPEN_PRICE"], errors="coerce"),
            "adj_close": pd.to_numeric(raw["CLOSE_PRICE"], errors="coerce"),
            "adj_source": "index_close",
        }
    )


def fetch_fx_by_winds(wind_to_symbol: dict[str, str], start: str, end: str) -> pd.DataFrame:
    winds = list(wind_to_symbol.keys())
    if not winds:
        return pd.DataFrame()
    sql = f"""
    SELECT WINDCODE, BB_TICKER, TRADE_DATE, OPEN, CLOSE
    FROM CDM.FXEOD
    WHERE WINDCODE IN ({_in_list(winds)})
      AND TRADE_DATE BETWEEN '{start}' AND '{end}'
    """
    raw = read_sql(sql)
    raw["symbol"] = raw["WINDCODE"].map(wind_to_symbol)
    return pd.DataFrame(
        {
            "symbol": raw["symbol"],
            "asset_type": "FX",
            "bb_ticker": raw["BB_TICKER"],
            "wind_code": raw["WINDCODE"],
            "sec_id": None,
            "trade_date": raw["TRADE_DATE"].astype(str),
            "open": pd.to_numeric(raw["OPEN"], errors="coerce"),
            "close": pd.to_numeric(raw["CLOSE"], errors="coerce"),
            "adj_open": pd.to_numeric(raw["OPEN"], errors="coerce"),
            "adj_close": pd.to_numeric(raw["CLOSE"], errors="coerce"),
            "adj_source": "fx_close",
        }
    )


def _resolve_bb_sec_ids(bb_tickers: list[str]) -> pd.DataFrame:
    if not bb_tickers:
        return pd.DataFrame(columns=["BB_TICKER", "SEC_ID", "WIND_CODE"])
    frames = []
    for i in range(0, len(bb_tickers), 80):
        chunk = bb_tickers[i : i + 80]
        frames.append(
            read_sql(
                f"""
                SELECT BB_TICKER, SEC_ID, WIND_CODE
                FROM CDM.SECURITYCODE
                WHERE BB_TICKER IN ({_in_list(chunk)})
                """
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_corr_mark_universe_panel(universe_csv: str | Path, start: str, end: str) -> pd.DataFrame:
    """Pull panel for active rows in corr_mark_universe.csv (Calypso symbols as keys)."""
    from corr_mark_cmds_mapping import (
        FORCE_PROXY,
        PROXY_INFO,
        active_symbols,
        fx_windcode,
        load_universe,
    )

    uni = load_universe(universe_csv)
    active = active_symbols(uni, ["IDX", "EQ", "FX"])

    frames: list[pd.DataFrame] = []

    idx = active[active["type"] == "IDX"]
    # one sec_id may map to two calypso aliases (KOSPI2/KOSPI200) — duplicate series
    sec_to_syms: dict[str, list[str]] = {}
    for _, r in idx.iterrows():
        sid = str(r["sec_id"])
        if not sid:
            continue
        sec_to_syms.setdefault(sid, []).append(r["calypso"])
    if sec_to_syms:
        # fetch once per sec_id then explode aliases
        base = fetch_index_by_sec_ids({sid: sid for sid in sec_to_syms}, start, end)
        parts = []
        for sid, syms in sec_to_syms.items():
            sub = base[base["sec_id"] == sid]
            for sym in syms:
                s = sub.copy()
                s["symbol"] = sym
                parts.append(s)
        if parts:
            frames.append(pd.concat(parts, ignore_index=True))

    eq = active[active["type"] == "EQ"]
    # sec_id → list of calypso aliases (600804.CH / 600804.C1)
    sec_to_cals: dict[str, list[str]] = {}
    for _, r in eq.iterrows():
        sid = str(r["sec_id"])
        if not sid:
            continue
        sec_to_cals.setdefault(sid, []).append(r["calypso"])

    # proxy / force targets priced under mapped calypso-like symbols
    extra_bb = []
    bb_to_symbol = {}
    for _src, (bb, _fx) in PROXY_INFO.items():
        extra_bb.append(bb)
        parts = bb.split()
        if len(parts) >= 2:
            bb_to_symbol[bb] = f"{parts[0]}.{parts[1]}"
    from corr_mark_cmds_mapping import FORCE_PROXY_BB_FALLBACKS

    for _src, (bb, _fx, _ccy) in FORCE_PROXY.items():
        bbs = [bb] + list(FORCE_PROXY_BB_FALLBACKS.get(_src, []))
        for bb_one in bbs:
            extra_bb.append(bb_one)
            parts = bb_one.split()
            if len(parts) >= 2:
                # 300750 C2 → 300750.CH; COIN US → COIN.US; 688256 CG/C1 → 688256.CH
                suf = parts[1]
                if suf in ("C1", "C2", "CH", "CG"):
                    bb_to_symbol[bb_one] = f"{parts[0]}.CH"
                else:
                    bb_to_symbol[bb_one] = f"{parts[0]}.{suf}"

    sec_map = _resolve_bb_sec_ids(sorted(set(extra_bb)))
    for _, r in sec_map.iterrows():
        bb = str(r["BB_TICKER"])
        sid = str(r["SEC_ID"])
        sym = bb_to_symbol.get(bb)
        if sym:
            sec_to_cals.setdefault(sid, [])
            if sym not in sec_to_cals[sid]:
                sec_to_cals[sid].append(sym)

    if sec_to_cals:
        eq_panel = fetch_equity_by_sec_ids(list(sec_to_cals.keys()), start, end, batch=40)
        parts = []
        for sid, syms in sec_to_cals.items():
            sub = eq_panel[eq_panel["sec_id"] == sid]
            for sym in syms:
                s = sub.copy()
                s["symbol"] = sym
                parts.append(s)
        if parts:
            frames.append(pd.concat(parts, ignore_index=True))

    fx = active[active["type"] == "FX"]
    wind_to_sym: dict[str, str] = {}
    invert_jobs: list[tuple[str, str]] = []  # (calypso, invert_of_calypso)
    for _, r in fx.iterrows():
        if r["invert_of"]:
            invert_jobs.append((r["calypso"], r["invert_of"]))
        else:
            w = r["fx_wind"] or fx_windcode(r["calypso"])
            wind_to_sym[w] = r["calypso"]
    if wind_to_sym:
        frames.append(fetch_fx_by_winds(wind_to_sym, start, end))

    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if panel.empty:
        return panel

    # synthesize inverted FX
    if invert_jobs and len(panel):
        inv_frames = []
        for cal, src in invert_jobs:
            src_df = panel[panel["symbol"] == src].copy()
            if src_df.empty:
                continue
            src_df["symbol"] = cal
            for c in ("open", "close", "adj_open", "adj_close"):
                if c in src_df.columns:
                    src_df[c] = 1.0 / src_df[c]
            src_df["adj_source"] = "fx_invert"
            src_df["wind_code"] = f"INV({src})"
            inv_frames.append(src_df)
        if inv_frames:
            panel = pd.concat([panel, *inv_frames], ignore_index=True)

    panel["trade_date"] = panel["trade_date"].astype(str)
    for c in ("open", "close", "adj_open", "adj_close"):
        if c in panel.columns:
            panel[c] = pd.to_numeric(panel[c], errors="coerce")
    return panel.dropna(subset=["adj_close"]).sort_values(["symbol", "trade_date"])


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
    p.add_argument(
        "--corr-mark-universe",
        default="",
        help="path to corr_mark_universe.csv — pulls active IDX/EQ/FX into --out",
    )
    args = p.parse_args()

    if args.corr_mark_universe:
        panel = fetch_corr_mark_universe_panel(args.corr_mark_universe, args.start, args.end)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(out, index=False)
        summary = (
            panel.groupby(["symbol", "asset_type"], as_index=False)
            .agg(n=("adj_close", "size"), dmin=("trade_date", "min"), dmax=("trade_date", "max"))
            .sort_values("symbol")
        )
        print(summary.to_string(index=False))
        print(f"\nWrote {len(panel):,} rows -> {out}")
        return

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
