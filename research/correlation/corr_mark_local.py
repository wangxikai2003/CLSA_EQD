"""
Local realized-correlation prototype (corr_mark core only).

Uses CMDS price panel (or falls back to vol_trading index_spot for A-share trio).
No Pulse / Bloomberg / trader_tool / email / RFQ.
"""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_PANEL = ROOT / "data" / "cmds_price_panel.parquet"
LOCAL_SPOT = Path(
    "/Users/wangxikai/Desktop/CITICS CLSA/vol_trading/CLSA_EQD/data/index_spot.parquet"
)


def lambda_bump(base_corr: float, l: float, up: bool = True) -> float:
    if up:
        return (1.0 - base_corr) * l + base_corr
    return (-1.0 - base_corr) * l + base_corr


def get_lambda(hl: float, max_l: float = 0.5) -> float:
    return max(max_l * (1.0 - hl / 504.0), 0.0)


def penalty_bump(base_corr: float, hl: float, up: bool = True):
    l = get_lambda(hl)
    return l, lambda_bump(base_corr, l, up)


def calc_corr(log_return_one: pd.DataFrame, rolling_window: int, alpha: float, el: int, up: bool):
    rolling_corr = (
        log_return_one.iloc[:, 0].rolling(rolling_window).corr(log_return_one.iloc[:, 1]).dropna()
    )
    if rolling_corr.empty:
        return 0.0, 0.0, 0.0, "empty"
    date_str = f"{rolling_corr.index[0].date()}-{rolling_corr.index[-1].date()}-{rolling_corr.shape[0]}"
    history_length = rolling_corr.shape[0]
    base_corr = float(np.percentile(rolling_corr, alpha))
    l, corr = penalty_bump(base_corr, history_length, up)
    corr = float(np.clip(corr, -0.979, 0.979))
    if abs(corr) < 0.001:
        corr = 0.001
    return l, corr, float(rolling_corr.iloc[-1]), date_str


def load_panel(path: Path) -> pd.DataFrame:
    if path.exists():
        df = pd.read_parquet(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        return df
    if not LOCAL_SPOT.exists():
        raise FileNotFoundError(f"No panel at {path} and no fallback {LOCAL_SPOT}")
    spot = pd.read_parquet(LOCAL_SPOT)
    spot = spot.rename(columns={"wind_code": "symbol", "close": "close"})
    spot["trade_date"] = pd.to_datetime(spot["trade_date"].astype(str))
    spot["open"] = np.nan
    spot["asset_type"] = "INDEX"
    mapping = {"000016.SH": "SSE50", "000300.SH": "SHSZ300", "000852.SH": "CSI1000"}
    spot["symbol"] = spot["symbol"].map(mapping).fillna(spot["symbol"])
    return spot


def build_returns(panel: pd.DataFrame, symbols: list[str], field: str, pad: int) -> pd.DataFrame:
    wide = (
        panel[panel["symbol"].isin(symbols)]
        .pivot_table(index="trade_date", columns="symbol", values=field, aggfunc="last")
        .sort_index()
        .ffill()
    )
    # pad-day log return: log(p_t / p_{t-pad})
    rets = wide.apply(lambda s: np.log(s / s.shift(pad)))
    return rets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--symbols", default="SPX,SX5E,NKY,HSI,HSCEI,HSTECH,SHSZ300,CSI1000,SH000905,SSE50,KOSPI2")
    ap.add_argument("--field", default="close", choices=["close", "open"])
    ap.add_argument("--rolling_win", type=int, default=63)
    ap.add_argument("--look_back_months", type=int, default=63)
    ap.add_argument("--pad", type=int, default=1)
    ap.add_argument("--alpha", type=float, default=93.0)
    ap.add_argument("--up", default="True")
    ap.add_argument("--out", default=str(ROOT / "data" / "corr_matrix_local.csv"))
    args = ap.parse_args()

    panel = load_panel(Path(args.panel))
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    available = sorted(set(panel["symbol"]) & set(symbols))
    missing = sorted(set(symbols) - set(available))
    if missing:
        print("Missing symbols (skipped):", missing)

    rets = build_returns(panel, available, args.field, args.pad)
    end = rets.index.max()
    start = end - pd.DateOffset(months=args.look_back_months + 2)
    rets = rets.loc[start:end]
    expected_length = args.look_back_months * 21
    up = str(args.up).lower() in {"1", "true", "yes", "y"}

    rows = []
    for a, b in combinations(available, 2):
        pair = rets[[a, b]].dropna()
        pair = pair[(pair.abs() > 1e-8).any(axis=1)].iloc[-expected_length:]
        l, corr, realized, date_str = calc_corr(pair, args.rolling_win, args.alpha, expected_length, up)
        rows.append(
            {
                "pair": f"{a}-{b}",
                "symbol1": a,
                "symbol2": b,
                "lambda": round(l, 4),
                "corr": round(corr, 4),
                "realized_last": round(realized, 4),
                "window": date_str,
                "n_obs": len(pair),
            }
        )

    detail = pd.DataFrame(rows).sort_values("pair")
    # square matrix
    mat = pd.DataFrame(1.0, index=available, columns=available)
    for r in rows:
        mat.loc[r["symbol1"], r["symbol2"]] = r["corr"]
        mat.loc[r["symbol2"], r["symbol1"]] = r["corr"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(out, index=False)
    mat_path = out.with_name(out.stem + "_matrix.csv")
    mat.to_csv(mat_path)

    print(detail.to_string(index=False))
    print(f"\nWrote {out}")
    print(f"Wrote {mat_path}")


if __name__ == "__main__":
    main()
