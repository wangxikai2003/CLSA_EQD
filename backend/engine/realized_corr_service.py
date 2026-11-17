"""In-memory Realized Correlation engine (panel + precomputed series)."""

from __future__ import annotations

import json
import math
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CORR_DIR = ROOT / "research" / "correlation"
if str(CORR_DIR) not in sys.path:
    sys.path.insert(0, str(CORR_DIR))

PANEL_CANDIDATES = [
    ROOT / "data" / "corr_mark_cmds_panel.parquet",
    CORR_DIR / "data" / "corr_mark_cmds_panel.parquet",
]
TS_CANDIDATES = [
    ROOT / "data" / "corr_realized_timeseries.parquet",
]
MAT_CANDIDATES = [
    ROOT / "data" / "corr_web_matrices.json",
]

_lock = threading.Lock()
_panel: pd.DataFrame | None = None
_ts: pd.DataFrame | None = None
_matrices: dict | None = None


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def load_panel(force: bool = False) -> pd.DataFrame:
    global _panel
    with _lock:
        if _panel is not None and not force:
            return _panel
        path = _first_existing(PANEL_CANDIDATES)
        if path is None:
            raise FileNotFoundError("corr_mark_cmds_panel.parquet missing — run build_web_realized_data.py")
        df = pd.read_parquet(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        for c in ("open", "close", "adj_open", "adj_close"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        _panel = df
        return _panel


def load_timeseries(force: bool = False) -> pd.DataFrame | None:
    global _ts
    with _lock:
        if _ts is not None and not force:
            return _ts
        path = _first_existing(TS_CANDIDATES)
        if path is None:
            return None
        df = pd.read_parquet(path)
        df["trade_date"] = df["trade_date"].astype(str)
        _ts = df
        return _ts


def load_matrices(force: bool = False) -> dict | None:
    global _matrices
    with _lock:
        if _matrices is not None and not force:
            return _matrices
        path = _first_existing(MAT_CANDIDATES)
        if path is None:
            return None
        _matrices = json.loads(path.read_text(encoding="utf-8"))
        return _matrices


def _percentiles(arr: np.ndarray) -> dict[str, float]:
    if arr.size == 0:
        return {k: float("nan") for k in ("p10", "p25", "p50", "p75", "p90")}
    qs = np.nanpercentile(arr, [10, 25, 50, 75, 90])
    return {"p10": float(qs[0]), "p25": float(qs[1]), "p50": float(qs[2]), "p75": float(qs[3]), "p90": float(qs[4])}


def _desk_mark(rolling: pd.Series, alpha: float = 93.0, up: bool = True) -> tuple[float, float, float]:
    from corr_mark_local import calc_corr

    if rolling.empty or len(rolling) < 10:
        return float("nan"), float("nan"), float("nan")
    # fabricate 2-col frame for calc_corr API: use rolling as synthetic paired path
    # Instead call percentile + lambda bump directly
    from corr_mark_local import get_lambda, lambda_bump

    base = float(np.percentile(rolling.dropna().values, alpha if up else 100 - alpha))
    hl = float(rolling.dropna().shape[0])
    l = get_lambda(hl)
    bumped = lambda_bump(base, l, up)
    bumped = float(np.clip(bumped, -0.979, 0.979))
    if abs(bumped) < 0.001:
        bumped = 0.001
    realized = float(rolling.dropna().iloc[-1])
    return realized, bumped, l


def pairwise(
    symbol1: str,
    symbol2: str,
    rolling_win: int = 63,
    lookback_months: int = 24,
    use_rules: bool = True,
    pad_override: int | None = None,
) -> dict[str, Any]:
    from corr_mark_cmds import compute_pair, pair_rules, fx_pair_specs, build_price_wide, pad_log_returns

    panel = load_panel()
    # Prefer precomputed timeseries when simple close-close
    ts = load_timeseries()
    dates: list[str] = []
    rolling_corr: list[float | None] = []
    rule_desc = "Close-Close | Pad=1 | Alpha=93"
    pad = pad_override if pad_override is not None else 1
    alpha = 93.0

    if not use_rules and ts is not None:
        key_a, key_b = sorted([symbol1, symbol2])
        pair = f"{key_a}-{key_b}"
        sub = ts[(ts["pair"] == pair) & (ts["rolling_win"] == rolling_win)].sort_values("trade_date")
        if len(sub) == 0:
            pair = f"{symbol1}-{symbol2}"
            sub = ts[(ts["pair"] == pair) & (ts["rolling_win"] == rolling_win)].sort_values("trade_date")
        if len(sub):
            dates = sub["trade_date"].tolist()
            rolling_corr = [None if pd.isna(v) else float(v) for v in sub["rho"].tolist()]
    if not dates:
        # live from panel
        if use_rules:
            if "/" in symbol1 or "/" in symbol2:
                eq = symbol1 if "/" not in symbol1 else symbol2
                fx = symbol2 if "/" in symbol2 else symbol1
                specs = fx_pair_specs(eq, fx, pad_override, None)
            else:
                specs = pair_rules(symbol1, symbol2, pad_override, None, use_proxy=True)
        else:
            specs = [
                {
                    "legs": [
                        {"calypso": symbol1, "field": "close", "pad": pad, "alpha": alpha},
                        {"calypso": symbol2, "field": "close", "pad": pad, "alpha": alpha},
                    ],
                    "pad": pad,
                    "alpha": alpha,
                }
            ]
        spec = specs[0]
        pad = int(spec["pad"])
        alpha = float(spec["alpha"])
        f1 = spec["legs"][0].get("field", "close")
        f2 = spec["legs"][1].get("field", "close")
        rule_desc = f"{symbol1} {f1} vs {symbol2} {f2} | Pad={pad}D | Alpha={alpha:g}"
        # proxy note
        for leg in spec["legs"]:
            if leg.get("proxy_symbol"):
                rule_desc += f" | Proxy: {leg['proxy_symbol']}"

        wide1 = build_price_wide(panel, [symbol1], f1)
        wide2 = build_price_wide(panel, [symbol2], f2)
        if symbol1 not in wide1.columns or symbol2 not in wide2.columns:
            raise KeyError(f"missing prices for {symbol1}/{symbol2}")
        px = pd.concat([wide1[symbol1], wide2[symbol2]], axis=1, keys=["a", "b"]).sort_index().ffill()
        r0 = pad_log_returns(px["a"], pad)
        r1 = pad_log_returns(px["b"], pad)
        df = pd.concat([r0, r1], axis=1, keys=["a", "b"]).dropna()
        end = df.index.max()
        start = end - pd.DateOffset(months=lookback_months + 2)
        df = df.loc[start:end]
        rc = df["a"].rolling(rolling_win).corr(df["b"]).dropna()
        dates = [d.strftime("%Y-%m-%d") for d in rc.index]
        rolling_corr = [float(v) for v in rc.values]

    arr = np.array([v for v in rolling_corr if v is not None], dtype=float)
    bands = _percentiles(arr)
    ser = pd.Series(arr)
    realized_last, desk_mark, lam = _desk_mark(ser, alpha=alpha, up=True)

    return {
        "symbol1": symbol1,
        "symbol2": symbol2,
        "rolling_win": rolling_win,
        "lookback_months": lookback_months,
        "use_rules": use_rules,
        "pad": pad,
        "alpha": alpha,
        "rule_desc": rule_desc,
        "dates": dates,
        "rolling_corr": rolling_corr,
        "bands": bands,
        "realized_last": None if math.isnan(realized_last) else realized_last,
        "desk_mark": None if math.isnan(desk_mark) else desk_mark,
        "lambda": None if math.isnan(lam) else lam,
        "n_obs": len(dates),
    }


def matrix(
    symbols: list[str],
    rolling_win: int = 63,
    metric: str = "realized",
    use_rules: bool = True,
    lookback_months: int = 24,
) -> dict[str, Any]:
    syms = [s for s in symbols if s]
    n = len(syms)
    grid: list[list[float | None]] = [[None] * n for _ in range(n)]
    details: list[dict[str, Any]] = []
    for i, a in enumerate(syms):
        grid[i][i] = 1.0
        for j in range(i + 1, n):
            b = syms[j]
            try:
                pw = pairwise(a, b, rolling_win=rolling_win, lookback_months=lookback_months, use_rules=use_rules)
            except Exception as ex:
                details.append({"symbol1": a, "symbol2": b, "ok": False, "error": str(ex)})
                continue
            if metric == "desk_mark":
                val = pw.get("desk_mark")
            elif metric == "diff":
                r, d = pw.get("realized_last"), pw.get("desk_mark")
                val = (d - r) if r is not None and d is not None else None
            else:
                val = pw.get("realized_last")
            grid[i][j] = grid[j][i] = val
            details.append(
                {
                    "symbol1": a,
                    "symbol2": b,
                    "ok": True,
                    "realized_last": pw.get("realized_last"),
                    "desk_mark": pw.get("desk_mark"),
                    "rule_desc": pw.get("rule_desc"),
                    "value": val,
                }
            )
    return {"symbols": syms, "metric": metric, "matrix": grid, "details": details, "rolling_win": rolling_win}


def spreads(
    mode: str,
    symbol1: str,
    symbol2: str,
    symbol3: str | None = None,
    symbol4: str | None = None,
    short_win: int = 21,
    long_win: int = 126,
) -> dict[str, Any]:
    """Term spread or cross-pair spread on realized rolling series."""
    if mode == "term":
        a = pairwise(symbol1, symbol2, rolling_win=short_win, use_rules=False)
        b = pairwise(symbol1, symbol2, rolling_win=long_win, use_rules=False)
        map_a = dict(zip(a["dates"], a["rolling_corr"]))
        map_b = dict(zip(b["dates"], b["rolling_corr"]))
        dates = sorted(set(map_a) & set(map_b))
        series = []
        for d in dates:
            va, vb = map_a[d], map_b[d]
            if va is None or vb is None:
                continue
            series.append({"d": d, "v": vb - va, "short": va, "long": vb})
        label = f"ρ({long_win}D)−ρ({short_win}D) {symbol1}-{symbol2}"
    else:
        if not symbol3 or not symbol4:
            raise ValueError("cross mode needs symbol3, symbol4")
        a = pairwise(symbol1, symbol2, rolling_win=long_win, use_rules=False)
        b = pairwise(symbol3, symbol4, rolling_win=long_win, use_rules=False)
        map_a = dict(zip(a["dates"], a["rolling_corr"]))
        map_b = dict(zip(b["dates"], b["rolling_corr"]))
        dates = sorted(set(map_a) & set(map_b))
        series = []
        for d in dates:
            va, vb = map_a[d], map_b[d]
            if va is None or vb is None:
                continue
            series.append({"d": d, "v": va - vb, "pair1": va, "pair2": vb})
        label = f"ρ({symbol1}-{symbol2})−ρ({symbol3}-{symbol4}) @ {long_win}D"

    vals = np.array([p["v"] for p in series], dtype=float)
    if vals.size == 0:
        return {"mode": mode, "label": label, "series": [], "stats": {}}
    mean = float(vals.mean())
    sd = float(vals.std(ddof=1)) if vals.size > 1 else 0.0
    last = float(vals[-1])
    pctl = float((vals <= last).mean() * 100)
    return {
        "mode": mode,
        "label": label,
        "series": series,
        "stats": {
            "mean": mean,
            "std": sd,
            "last": last,
            "pctl": pctl,
            "z": (last - mean) / sd if sd > 1e-12 else 0.0,
            "band_1": [mean - sd, mean + sd],
            "band_2": [mean - 2 * sd, mean + 2 * sd],
            "low": float(vals.min()),
            "high": float(vals.max()),
        },
    }
