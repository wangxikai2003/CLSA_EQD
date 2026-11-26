"""In-memory Implied Correlation / CRP reader (precomputed parquet panels).

Supports baskets: NDX (primary densified), SPX / HSI (bootstrap or CMDS-built).
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

BASKETS = ("NDX", "SPX", "HSI")
DEFAULT_BASKET = "NDX"

METRIC_COLS = {
    "rho_imp": "rho_imp",
    "rho_real": "rho_real",
    "crp": "crp",
}

_lock = threading.Lock()
_cache: dict[str, pd.DataFrame] = {}


def _panel_candidates(basket: str) -> list[Path]:
    key = basket.strip().lower()
    names = [f"corr_{key}_implied_crp.parquet"]
    if key == "ndx":
        names.append("corr_ndx_implied_crp.parquet")
    out: list[Path] = []
    for name in names:
        out.extend(
            [
                ROOT / "data" / name,
                ROOT / "research" / "correlation" / "data" / name,
            ]
        )
    return out


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _norm_basket(basket: str | None) -> str:
    b = (basket or DEFAULT_BASKET).strip().upper()
    if b not in BASKETS:
        raise KeyError(f"unsupported basket={basket}; expected one of {BASKETS}")
    return b


def available_baskets() -> list[dict[str, Any]]:
    rows = []
    for b in BASKETS:
        path = _first_existing(_panel_candidates(b))
        if path is None:
            rows.append({"basket": b, "available": False, "n_dates": 0, "path": None})
            continue
        try:
            df = pd.read_parquet(path, columns=["trade_date"])
            n = int(pd.to_datetime(df["trade_date"].astype(str)).dt.strftime("%Y-%m-%d").nunique())
        except Exception:
            n = 0
        rows.append({"basket": b, "available": n > 0, "n_dates": n, "path": str(path)})
    return rows


def load_panel(basket: str | None = None, force: bool = False) -> pd.DataFrame:
    b = _norm_basket(basket)
    with _lock:
        if b in _cache and not force:
            return _cache[b]
        path = _first_existing(_panel_candidates(b))
        if path is None:
            raise FileNotFoundError(
                f"corr_{b.lower()}_implied_crp.parquet missing — run densify/build_basket_implied_crp"
            )
        df = pd.read_parquet(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str)).dt.strftime("%Y-%m-%d")
        for c in ("tenor_days", "mny", "rho_imp", "rho_real", "crp", "n_members"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "variant" in df.columns:
            df["variant"] = df["variant"].astype(str).str.lower()
        if "status" in df.columns:
            df = df[df["status"].fillna("ok") == "ok"]
        df = df.sort_values(["trade_date", "tenor_days", "mny", "variant"]).reset_index(drop=True)
        _cache[b] = df
        return df


def _manifest_note(basket: str) -> str | None:
    key = basket.lower()
    for base in (ROOT / "data", ROOT / "research" / "correlation" / "data"):
        p = base / f"corr_{key}_implied_crp_manifest.json"
        if p.exists():
            try:
                import json

                return json.loads(p.read_text()).get("note")
            except Exception:
                return None
    return None


def meta(basket: str | None = None) -> dict[str, Any]:
    b = _norm_basket(basket)
    df = load_panel(b)
    atm = df[np.isclose(df["mny"].astype(float), 1.0)]
    dates = sorted(df["trade_date"].unique().tolist())
    note = _manifest_note(b) or (
        "Basket average implied corr (variance decomposition); CRP = ρ_imp − ρ_real(forward)."
    )
    return {
        "basket": b,
        "baskets": available_baskets(),
        "top_n": 50 if b == "NDX" else None,
        "root_pref": "NDXP" if b == "NDX" else None,
        "tenors": sorted(int(x) for x in df["tenor_days"].dropna().unique()),
        "mnys": sorted(float(x) for x in df["mny"].dropna().unique()),
        "variants": sorted(df["variant"].dropna().unique().tolist()) if "variant" in df.columns else ["clean"],
        "metrics": list(METRIC_COLS.keys()),
        "dates": dates,
        "history_start": dates[0] if dates else None,
        "history_end": dates[-1] if dates else None,
        "n_rows": int(len(df)),
        "n_dates": int(len(dates)),
        "atm_crp_points": int(atm["crp"].notna().sum()) if "crp" in atm.columns else 0,
        "capabilities": [
            "implied_crp",
            "implied_crp_levels",
            "implied_crp_skew",
            "implied_crp_term",
            "implied_crp_surface",
        ],
        "note": note,
    }


def _stats(vals: np.ndarray) -> dict[str, Any]:
    if vals.size == 0:
        return {}
    mean = float(vals.mean())
    sd = float(vals.std(ddof=1)) if vals.size > 1 else 0.0
    last = float(vals[-1])
    pctl = float((vals <= last).mean() * 100)
    return {
        "mean": mean,
        "std": sd,
        "last": last,
        "pctl": pctl,
        "z": (last - mean) / sd if sd > 1e-12 else 0.0,
        "band_1": [mean - sd, mean + sd],
        "band_2": [mean - 2 * sd, mean + 2 * sd],
        "low": float(vals.min()),
        "high": float(vals.max()),
    }


def _parse_list_floats(raw: str | None, default: list[float]) -> list[float]:
    if not raw:
        return default
    out: list[float] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    return out or default


def _parse_list_ints(raw: str | None, default: list[int]) -> list[int]:
    if not raw:
        return default
    out: list[int] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(float(part)))
    return out or default


def _parse_metrics(raw: str | None) -> list[str]:
    default = ["rho_imp", "rho_real", "crp"]
    if not raw:
        return default
    out = []
    for part in str(raw).split(","):
        p = part.strip().lower()
        if p in METRIC_COLS:
            out.append(p)
    return out or default


def series(
    tenor_days: int = 30,
    mny: float = 1.0,
    variant: str = "clean",
    basket: str | None = None,
) -> dict[str, Any]:
    b = _norm_basket(basket)
    df = load_panel(b)
    variant = (variant or "clean").lower()
    sub = df[
        (df["tenor_days"].astype(int) == int(tenor_days))
        & (np.isclose(df["mny"].astype(float), float(mny)))
        & (df["variant"].astype(str).str.lower() == variant)
    ].sort_values("trade_date")
    if sub.empty:
        raise KeyError(f"no implied/CRP rows for basket={b} tenor={tenor_days} mny={mny} variant={variant}")

    series_rows: list[dict[str, Any]] = []
    for _, r in sub.iterrows():
        series_rows.append(
            {
                "d": str(r["trade_date"]),
                "v": None if pd.isna(r.get("crp")) else float(r["crp"]),
                "rho_imp": None if pd.isna(r.get("rho_imp")) else float(r["rho_imp"]),
                "rho_real": None if pd.isna(r.get("rho_real")) else float(r["rho_real"]),
                "crp": None if pd.isna(r.get("crp")) else float(r["crp"]),
            }
        )

    crp_vals = np.array([p["crp"] for p in series_rows if p["crp"] is not None], dtype=float)
    imp_vals = np.array([p["rho_imp"] for p in series_rows if p["rho_imp"] is not None], dtype=float)
    real_vals = np.array([p["rho_real"] for p in series_rows if p["rho_real"] is not None], dtype=float)
    last = series_rows[-1] if series_rows else {}
    label = (
        f"{b} CRP clean ATM@{tenor_days}D"
        if variant == "clean" and abs(mny - 1.0) < 1e-9
        else f"{b} CRP {variant} mny={mny:g} @{tenor_days}D"
    )
    return {
        "mode": "implied_crp",
        "label": label,
        "basket": b,
        "tenor_days": int(tenor_days),
        "mny": float(mny),
        "variant": variant,
        "history_start": str(sub["trade_date"].min()),
        "history_end": str(sub["trade_date"].max()),
        "n_obs": len(series_rows),
        "series": series_rows,
        "stats": _stats(crp_vals),
        "stats_imp": _stats(imp_vals),
        "stats_real": _stats(real_vals),
        "realized_last": last.get("rho_real"),
        "implied_last": last.get("rho_imp"),
        "crp_last": last.get("crp"),
        "note": _manifest_note(b),
    }


def levels(
    variant: str = "clean",
    metrics: str | None = None,
    tenors: str | None = None,
    mnys: str | None = None,
    basket: str | None = None,
) -> dict[str, Any]:
    b = _norm_basket(basket)
    df = load_panel(b)
    variant = (variant or "clean").lower()
    metric_list = _parse_metrics(metrics)
    tenor_list = _parse_list_ints(tenors, sorted(int(x) for x in df["tenor_days"].unique()))
    mny_list = _parse_list_floats(mnys, sorted(float(x) for x in df["mny"].unique()))

    sub = df[df["variant"].astype(str).str.lower() == variant]
    out: dict[str, Any] = {}
    for metric in metric_list:
        col = METRIC_COLS[metric]
        out[metric] = {}
        for t in tenor_list:
            out[metric][str(t)] = {}
            for m in mny_list:
                slice_df = sub[
                    (sub["tenor_days"].astype(int) == int(t)) & (np.isclose(sub["mny"].astype(float), float(m)))
                ].sort_values("trade_date")
                pts = []
                for _, r in slice_df.iterrows():
                    val = r.get(col)
                    if pd.isna(val):
                        continue
                    pts.append({"d": str(r["trade_date"]), "v": float(val)})
                out[metric][str(t)][f"{m:g}"] = pts

    return {
        "basket": b,
        "variant": variant,
        "metrics": metric_list,
        "tenors": tenor_list,
        "mnys": mny_list,
        "levels": out,
        "note": "Vol-spreads-style bulk panel for ρ_imp / ρ_real / CRP overlays",
    }


def _resolve_asof(df: pd.DataFrame, trade_date: str | None) -> str:
    dates = sorted(df["trade_date"].unique().tolist())
    if not dates:
        raise KeyError("no dates in implied CRP panel")
    if not trade_date:
        return dates[-1]
    td = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
    if td in dates:
        return td
    earlier = [d for d in dates if d <= td]
    if earlier:
        return earlier[-1]
    return dates[0]


def skew_slice(
    trade_date: str | None = None,
    tenor_days: int = 30,
    variant: str = "clean",
    basket: str | None = None,
) -> dict[str, Any]:
    b = _norm_basket(basket)
    df = load_panel(b)
    variant = (variant or "clean").lower()
    asof = _resolve_asof(df, trade_date)
    sub = df[
        (df["trade_date"] == asof)
        & (df["tenor_days"].astype(int) == int(tenor_days))
        & (df["variant"].astype(str).str.lower() == variant)
    ].sort_values("mny")
    if sub.empty:
        raise KeyError(f"no skew slice for {b} {asof} tenor={tenor_days} variant={variant}")

    points = []
    for _, r in sub.iterrows():
        points.append(
            {
                "mny": float(r["mny"]),
                "rho_imp": None if pd.isna(r.get("rho_imp")) else float(r["rho_imp"]),
                "rho_real": None if pd.isna(r.get("rho_real")) else float(r["rho_real"]),
                "crp": None if pd.isna(r.get("crp")) else float(r["crp"]),
            }
        )
    return {
        "mode": "skew",
        "basket": b,
        "trade_date": asof,
        "tenor_days": int(tenor_days),
        "variant": variant,
        "points": points,
        "label": f"{b} ρ skew @{asof} {tenor_days}D ({variant})",
    }


def term_structure(
    trade_date: str | None = None,
    mny: float = 1.0,
    variant: str = "clean",
    basket: str | None = None,
) -> dict[str, Any]:
    b = _norm_basket(basket)
    df = load_panel(b)
    variant = (variant or "clean").lower()
    asof = _resolve_asof(df, trade_date)
    sub = df[
        (df["trade_date"] == asof)
        & (np.isclose(df["mny"].astype(float), float(mny)))
        & (df["variant"].astype(str).str.lower() == variant)
    ].sort_values("tenor_days")
    if sub.empty:
        raise KeyError(f"no term structure for {b} {asof} mny={mny} variant={variant}")

    points = []
    for _, r in sub.iterrows():
        points.append(
            {
                "tenor_days": int(r["tenor_days"]),
                "rho_imp": None if pd.isna(r.get("rho_imp")) else float(r["rho_imp"]),
                "rho_real": None if pd.isna(r.get("rho_real")) else float(r["rho_real"]),
                "crp": None if pd.isna(r.get("crp")) else float(r["crp"]),
            }
        )
    return {
        "mode": "term",
        "basket": b,
        "trade_date": asof,
        "mny": float(mny),
        "variant": variant,
        "points": points,
        "label": f"{b} ρ term @{asof} mny={mny:g} ({variant})",
    }


def surface_slice(
    trade_date: str | None = None,
    variant: str = "clean",
    metric: str = "rho_imp",
    basket: str | None = None,
) -> dict[str, Any]:
    """Fixed-date tenor × moneyness matrix for Plotly surface / heatmap."""
    b = _norm_basket(basket)
    df = load_panel(b)
    variant = (variant or "clean").lower()
    metric = (metric or "rho_imp").lower()
    if metric not in METRIC_COLS:
        raise KeyError(f"metric must be one of {list(METRIC_COLS)}")
    asof = _resolve_asof(df, trade_date)
    sub = df[(df["trade_date"] == asof) & (df["variant"].astype(str).str.lower() == variant)]
    if sub.empty:
        raise KeyError(f"no surface for {b} {asof} variant={variant}")

    tenors = sorted(int(x) for x in sub["tenor_days"].dropna().unique())
    mnys = sorted(float(x) for x in sub["mny"].dropna().unique())
    col = METRIC_COLS[metric]
    z: list[list[float | None]] = []
    z_real: list[list[float | None]] = []
    for t in tenors:
        row_z: list[float | None] = []
        row_r: list[float | None] = []
        for m in mnys:
            hit = sub[(sub["tenor_days"].astype(int) == t) & np.isclose(sub["mny"].astype(float), m)]
            if hit.empty or pd.isna(hit.iloc[0].get(col)):
                row_z.append(None)
            else:
                row_z.append(float(hit.iloc[0][col]))
            if hit.empty or pd.isna(hit.iloc[0].get("rho_real")):
                row_r.append(None)
            else:
                row_r.append(float(hit.iloc[0]["rho_real"]))
        z.append(row_z)
        z_real.append(row_r)

    return {
        "mode": "surface",
        "basket": b,
        "trade_date": asof,
        "variant": variant,
        "metric": metric,
        "tenors": tenors,
        "mnys": mnys,
        "z": z,
        "rho_real": z_real,
        "label": f"{b} {metric} surface @{asof} ({variant})",
        "note": _manifest_note(b),
    }


def nan_to_none(x: float) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return float(x)
