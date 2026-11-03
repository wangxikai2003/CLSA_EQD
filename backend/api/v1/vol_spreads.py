"""Vol spread levels: equity / commodity RV & IV (ATM/ITM/OTM) histories."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["market"])

ROOT = Path(__file__).resolve().parents[3]
_PARQUET_CANDIDATES = [
    ROOT / "data" / "vol_spread_levels.parquet",
    Path("/app/data/vol_spread_levels.parquet"),
]

_TENORS = ["20D", "40D", "120D", "6M", "1Y"]
_MNY = ["ATM", "OTM", "ITM"]

_cache: Dict[str, Any] = {"mtime": None, "df": None}


def _parquet() -> Path:
    for p in _PARQUET_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise HTTPException(
        503,
        "vol_spread_levels.parquet missing — run scripts/build_vol_spread_levels.py",
    )


def _df() -> pd.DataFrame:
    path = _parquet()
    mtime = path.stat().st_mtime
    if _cache["df"] is not None and _cache["mtime"] == mtime:
        return _cache["df"]
    df = pd.read_parquet(path)
    _cache["df"] = df
    _cache["mtime"] = mtime
    return df


def _series_payload(sub: pd.DataFrame, max_pts: int = 600) -> List[Dict[str, Any]]:
    if sub.empty:
        return []
    sub = sub.sort_values("d")
    if len(sub) > max_pts:
        step = max(1, len(sub) // max_pts)
        keep = sub.iloc[::step]
        if keep.iloc[-1]["d"] != sub.iloc[-1]["d"]:
            keep = pd.concat([keep, sub.iloc[[-1]]])
        sub = keep
    return [{"d": str(r.d), "v": round(float(r.v), 6)} for r in sub.itertuples()]


@router.get("/vol-spreads")
def vol_spreads(
    asset_class: str = Query("equity", description="equity|commodity|all"),
    family: Optional[str] = Query(None, description="RV|IV|omit for both"),
    refresh: bool = Query(False),
):
    if refresh:
        _cache["df"] = None
        _cache["mtime"] = None

    df = _df()
    if asset_class in ("equity", "commodity"):
        df = df[df["asset_class"] == asset_class]
    if family in ("RV", "IV"):
        df = df[df["family"] == family]

    products = (
        df[["product", "db_key", "asset_class"]]
        .drop_duplicates()
        .sort_values(["asset_class", "product"])
        .to_dict(orient="records")
    )

    # levels[product][family][tenor][moneyness or ""] = [{d,v},...]
    levels: Dict[str, Any] = {}
    coverage: Dict[str, Any] = {}
    for prod, gprod in df.groupby("product"):
        levels[prod] = {"RV": {}, "IV": {}}
        coverage[prod] = {"RV": {}, "IV": {}}
        for fam, gfam in gprod.groupby("family"):
            if fam == "RV":
                for ten, gten in gfam.groupby("tenor"):
                    pts = _series_payload(gten)
                    levels[prod]["RV"][ten] = pts
                    coverage[prod]["RV"][ten] = len(gten)
            else:
                for ten, gten in gfam.groupby("tenor"):
                    levels[prod]["IV"].setdefault(ten, {})
                    coverage[prod]["IV"].setdefault(ten, {})
                    for mny, gm in gten.groupby("moneyness"):
                        key = str(mny or "ATM")
                        pts = _series_payload(gm)
                        levels[prod]["IV"][ten][key] = pts
                        coverage[prod]["IV"][ten][key] = len(gm)

    return {
        "tenors": _TENORS,
        "moneyness": _MNY,
        "families": ["RV", "IV"],
        "products": products,
        "levels": levels,
        "coverage": coverage,
        "unit": "decimal vol (e.g. 0.20 = 20%)",
        "method": (
            "RV: front-month futures log-return rolling std × √252 "
            "(20/40/120D, 6M=126D, 1Y=252D). "
            "IV: SVI otm-side constant-maturity interp to TTM=D/252; "
            "ATM k=0, OTM K/F=0.95 (put wing), ITM K/F=1.05 (call wing). "
            "Ids = Wind futures roots (IH/IF/IM); IV from option surfaces HO/IO/MO."
        ),
        "source": str(_parquet()),
    }
