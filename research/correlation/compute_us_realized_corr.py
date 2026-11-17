"""
Realized correlation engines aligned to implied tenors.

Windows (trading days) ↔ calendar tenors:
  21→30D, 42→60D, 63→90D, 126→180D, 252→360D

Outputs:
  rho_real_eq          equal-weighted average pairwise corr
  rho_real_clean       clean-equivalent weighted corr
  rho_real_dirty       dirty-equivalent = σ_B² / (Σ w σ)²
  mode                 contemporaneous | forward

Usage:
  python compute_us_realized_corr.py \
    --panel data/cmds_us_equity_panel.parquet \
    --weights-from-cmds-ndx \
    --index-panel data/cmds_price_panel.parquet \
    --out data/ndx_realized_corr.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from cmds_connect import read_sql

ROOT = Path(__file__).resolve().parent
NDX_SEC_ID = "6NL341kna8pu"

WINDOW_MAP = {
    21: 30,
    42: 60,
    63: 90,
    126: 180,
    252: 360,
}


def load_panel(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["trade_date"] = df["trade_date"].astype(str)
    px = df["adj_close"] if "adj_close" in df.columns else df["close"]
    df["px"] = pd.to_numeric(px, errors="coerce")
    if "sec_id" not in df.columns:
        raise SystemExit("panel needs sec_id")
    df["sec_id"] = df["sec_id"].astype(str)
    return df.dropna(subset=["px", "trade_date", "sec_id"])


def fetch_ndx_weights_range(start: str, end: str) -> pd.DataFrame:
    sql = f"""
    SELECT TRADE_DATE, CONSTITUENT_SEC_ID, CONSTITUENT_WEIGHT
    FROM CDM.INDEXCONSTITUENTS
    WHERE INDEX_SEC_ID='{NDX_SEC_ID}'
      AND TRADE_DATE BETWEEN '{start}' AND '{end}'
      AND CONSTITUENT_WEIGHT IS NOT NULL
    """
    w = read_sql(sql)
    w["trade_date"] = w["TRADE_DATE"].astype(str)
    w["sec_id"] = w["CONSTITUENT_SEC_ID"].astype(str)
    w["weight"] = pd.to_numeric(w["CONSTITUENT_WEIGHT"], errors="coerce")
    w = w.dropna(subset=["weight"])
    w["weight"] = w["weight"] / w.groupby("trade_date")["weight"].transform("sum")
    return w[["trade_date", "sec_id", "weight"]]


def load_weights_file(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    sec = cols.get("constituent_sec_id") or cols.get("sec_id")
    dt = cols.get("trade_date")
    wcol = cols.get("weight")
    if not sec or not dt or not wcol:
        raise SystemExit("weights file needs trade_date, sec_id/constituent_sec_id, weight")
    out = pd.DataFrame(
        {
            "trade_date": df[dt].astype(str),
            "sec_id": df[sec].astype(str),
            "weight": pd.to_numeric(df[wcol], errors="coerce"),
        }
    ).dropna()
    out["weight"] = out["weight"] / out.groupby("trade_date")["weight"].transform("sum")
    return out


def load_index_returns(index_panel: Path, symbol: str = "NDX") -> pd.Series:
    df = pd.read_parquet(index_panel)
    df["trade_date"] = df["trade_date"].astype(str)
    if "symbol" in df.columns:
        df = df[df["symbol"] == symbol]
    px = pd.to_numeric(df.get("adj_close", df["close"]), errors="coerce")
    s = pd.Series(px.values, index=pd.to_datetime(df["trade_date"].values)).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return np.log(s / s.shift(1)).dropna()


def pairwise_metrics(
    R: np.ndarray,
    w: np.ndarray,
) -> tuple[float, float, float, float]:
    """
    R: (W, n) returns; w: (n,) weights (renormalized).
    Returns eq_corr, clean_corr, dirty_corr, basket_vol(ann. optional raw).
    """
    n = R.shape[1]
    if n < 3 or R.shape[0] < 5:
        return np.nan, np.nan, np.nan, np.nan
    # vols
    sig = R.std(axis=0, ddof=1)
    if np.any(~np.isfinite(sig)) or np.any(sig <= 0):
        return np.nan, np.nan, np.nan, np.nan
    C = np.corrcoef(R, rowvar=False)
    if not np.all(np.isfinite(C)):
        return np.nan, np.nan, np.nan, np.nan

    # equal-weight average pairwise
    iu = np.triu_indices(n, k=1)
    rho_eq = float(np.mean(C[iu]))

    # clean-equivalent
    # num = Σ_{i≠j} w_i w_j σ_i σ_j ρ_ij = (wσ)' Corr (wσ) - Σ (wσ)^2
    ws = w * sig
    quad = float(ws @ C @ ws)
    diag = float(np.sum(ws * ws))
    den = float((np.sum(ws)) ** 2 - diag)
    rho_clean = float((quad - diag) / den) if den > 1e-16 else np.nan

    # basket variance from weighted returns
    br = R @ w
    var_b = float(np.var(br, ddof=1))
    sum_ws = float(np.sum(ws))
    rho_dirty = float(var_b / (sum_ws ** 2)) if sum_ws > 1e-16 else np.nan
    return rho_eq, rho_clean, rho_dirty, float(np.sqrt(var_b))


def compute_realized(
    panel: pd.DataFrame,
    weights: pd.DataFrame,
    windows: list[int],
    modes: list[str],
    index_rets: pd.Series | None = None,
) -> pd.DataFrame:
    # wide price matrix
    px = panel.pivot_table(index="trade_date", columns="sec_id", values="px", aggfunc="last")
    px.index = pd.to_datetime(px.index)
    px = px.sort_index()
    rets = np.log(px / px.shift(1))

    # weight as-of map: for each day use latest weight date <= t
    w_dates = sorted(weights["trade_date"].unique())
    w_by_dt = {d: g.set_index("sec_id")["weight"] for d, g in weights.groupby("trade_date")}

    rows = []
    dates = list(rets.index)
    for i, dt in enumerate(dates):
        ds = dt.strftime("%Y%m%d")
        # pick weights
        w_asof = None
        for wd in w_dates:
            if wd <= ds:
                w_asof = wd
        if w_asof is None:
            continue
        wser = w_by_dt[w_asof]
        names = [c for c in rets.columns if c in wser.index]
        if len(names) < 10:
            continue
        w = wser.reindex(names).astype(float).values
        w = w / np.nansum(w)

        for W in windows:
            for mode in modes:
                if mode == "contemporaneous":
                    if i + 1 < W:
                        continue
                    block = rets.iloc[i - W + 1 : i + 1][names]
                else:  # forward-looking [t, t+W]
                    if i + W >= len(dates):
                        continue
                    block = rets.iloc[i : i + W + 1][names]
                R = block.dropna(axis=1, how="any").values
                keep = [names[j] for j, c in enumerate(names) if c in block.dropna(axis=1, how="any").columns]
                if len(keep) < 10:
                    continue
                # realign weights to kept cols
                w2 = wser.reindex(keep).astype(float).values
                w2 = w2 / np.nansum(w2)
                R = block[keep].dropna(axis=0, how="any").values
                if R.shape[0] < max(5, W // 3):
                    continue
                eq, clean, dirty, sig_b = pairwise_metrics(R, w2)
                # optional dirty using index realized var
                if index_rets is not None and mode == "contemporaneous":
                    ir = index_rets.loc[block.index.min() : block.index.max()].dropna()
                    if len(ir) >= max(5, W // 3):
                        sig_i = float(ir.std(ddof=1))
                        # recompute dirty with index vol vs member vols on same window
                        sig = block[keep].std(axis=0, ddof=1).values
                        sum_ws = float(np.sum(w2 * sig))
                        if sum_ws > 1e-16:
                            dirty = float((sig_i ** 2) / (sum_ws ** 2))
                rows.append(
                    {
                        "trade_date": ds,
                        "window": int(W),
                        "tenor_days": WINDOW_MAP.get(int(W), int(W)),
                        "mode": mode,
                        "rho_real_eq": eq,
                        "rho_real_clean": clean,
                        "rho_real_dirty": dirty,
                        "n_names": len(keep),
                        "weight_asof": w_asof,
                    }
                )
        if (i + 1) % 50 == 0:
            print(f"  processed {i+1}/{len(dates)} days", flush=True)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(ROOT / "data" / "cmds_us_equity_panel.parquet"))
    ap.add_argument("--weights", default="", help="weights parquet/csv; else --weights-from-cmds-ndx")
    ap.add_argument("--weights-from-cmds-ndx", action="store_true")
    ap.add_argument("--index-panel", default=str(ROOT / "data" / "cmds_price_panel.parquet"))
    ap.add_argument("--index-symbol", default="NDX")
    ap.add_argument("--windows", default="21,42,63")
    ap.add_argument("--modes", default="contemporaneous,forward")
    ap.add_argument("--out", default=str(ROOT / "data" / "ndx_realized_corr.parquet"))
    args = ap.parse_args()

    panel = load_panel(Path(args.panel))
    start, end = panel["trade_date"].min(), panel["trade_date"].max()
    if args.weights_from_cmds_ndx:
        print(f"Fetching NDX weights {start}→{end}...", flush=True)
        weights = fetch_ndx_weights_range(start, end)
    elif args.weights:
        weights = load_weights_file(Path(args.weights))
    else:
        raise SystemExit("Provide --weights or --weights-from-cmds-ndx")

    index_rets = None
    ip = Path(args.index_panel)
    if ip.exists():
        try:
            index_rets = load_index_returns(ip, args.index_symbol)
        except Exception as e:
            print(f"index returns skipped: {e}", flush=True)

    windows = [int(x) for x in args.windows.split(",")]
    modes = [x.strip() for x in args.modes.split(",") if x.strip()]
    print(f"Computing realized corr windows={windows} modes={modes}", flush=True)
    out = compute_realized(panel, weights, windows, modes, index_rets=index_rets)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    out.to_csv(out_path.with_suffix(".csv"), index=False)
    print(out.groupby(["tenor_days", "mode"])["rho_real_clean"].describe().round(4).to_string())
    print(f"\nWrote {len(out):,} rows -> {out_path}")


if __name__ == "__main__":
    main()
