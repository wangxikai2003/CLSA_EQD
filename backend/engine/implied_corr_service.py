"""In-memory NDX Implied Correlation / CRP reader (precomputed parquet only)."""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PANEL_CANDIDATES = [
    ROOT / "data" / "corr_ndx_implied_crp.parquet",
    ROOT / "research" / "correlation" / "data" / "corr_ndx_implied_crp.parquet",
]

_lock = threading.Lock()
_df: pd.DataFrame | None = None

METRIC_COLS = {
    "rho_imp": "rho_imp",
    "rho_real": "rho_real",
    "crp": "crp",
}


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def load_panel(force: bool = False) -> pd.DataFrame:
    global _df
    with _lock:
        if _df is not None and not force:
            return _df
        path = _first_existing(PANEL_CANDIDATES)
        if path is None:
            raise FileNotFoundError(
                "corr_ndx_implied_crp.parquet missing — run research/correlation/build_web_implied_data.py"
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
        _df = df.sort_values(["trade_date", "tenor_days", "mny", "variant"]).reset_index(drop=True)
        return _df


def meta() -> dict[str, Any]:
    df = load_panel()
    atm = df[np.isclose(df["mny"].astype(float), 1.0)]
    dates = sorted(df["trade_date"].unique().tolist())
    return {
        "basket": "NDX",
        "top_n": 50,
        "root_pref": "NDXP",
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
        "capabilities": ["implied_crp", "implied_crp_levels", "implied_crp_skew", "implied_crp_term"],
        "note": "Basket average implied corr (variance decomposition); CRP = ρ_imp − ρ_real(forward).",
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
) -> dict[str, Any]:
    df = load_panel()
    variant = (variant or "clean").lower()
    sub = df[
        (df["tenor_days"].astype(int) == int(tenor_days))
        & (np.isclose(df["mny"].astype(float), float(mny)))
        & (df["variant"].astype(str).str.lower() == variant)
    ].sort_values("trade_date")
    if sub.empty:
        raise KeyError(f"no implied/CRP rows for tenor={tenor_days} mny={mny} variant={variant}")

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

    label = (
        f"NDX CRP clean ATM@{tenor_days}D"
        if variant == "clean" and abs(mny - 1.0) < 1e-9
        else f"NDX CRP {variant} mny={mny:g} @{tenor_days}D"
    )

    last = series_rows[-1] if series_rows else {}
    return {
        "mode": "implied_crp",
        "label": label,
        "basket": "NDX",
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
        "note": "Top50 | NDXP | forward realized aligned to tenor",
    }


def levels(
    variant: str = "clean",
    metrics: str | None = None,
    tenors: str | None = None,
    mnys: str | None = None,
) -> dict[str, Any]:
    """Bulk levels nested like vol-spreads: levels[metric][tenor][mny] -> [{d,v}].

    rho_real is duplicated across mny (same forward realized per tenor).
    """
    df = load_panel()
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
        "basket": "NDX",
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
    # nearest earlier
    earlier = [d for d in dates if d <= td]
    if earlier:
        return earlier[-1]
    return dates[0]


def skew_slice(
    trade_date: str | None = None,
    tenor_days: int = 30,
    variant: str = "clean",
) -> dict[str, Any]:
    """Fixed-date moneyness skew: ρ_imp / CRP vs mny (ρ_real flat across mny)."""
    df = load_panel()
    variant = (variant or "clean").lower()
    asof = _resolve_asof(df, trade_date)
    sub = df[
        (df["trade_date"] == asof)
        & (df["tenor_days"].astype(int) == int(tenor_days))
        & (df["variant"].astype(str).str.lower() == variant)
    ].sort_values("mny")
    if sub.empty:
        raise KeyError(f"no skew slice for {asof} tenor={tenor_days} variant={variant}")

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
        "basket": "NDX",
        "trade_date": asof,
        "tenor_days": int(tenor_days),
        "variant": variant,
        "points": points,
        "label": f"NDX ρ skew @{asof} {tenor_days}D ({variant})",
    }


def term_structure(
    trade_date: str | None = None,
    mny: float = 1.0,
    variant: str = "clean",
) -> dict[str, Any]:
    """Fixed-date term structure: ρ_imp / ρ_real / CRP vs tenor."""
    df = load_panel()
    variant = (variant or "clean").lower()
    asof = _resolve_asof(df, trade_date)
    sub = df[
        (df["trade_date"] == asof)
        & (np.isclose(df["mny"].astype(float), float(mny)))
        & (df["variant"].astype(str).str.lower() == variant)
    ].sort_values("tenor_days")
    if sub.empty:
        raise KeyError(f"no term structure for {asof} mny={mny} variant={variant}")

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
        "basket": "NDX",
        "trade_date": asof,
        "mny": float(mny),
        "variant": variant,
        "points": points,
        "label": f"NDX ρ term @{asof} mny={mny:g} ({variant})",
    }


def nan_to_none(x: float) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return float(x)
