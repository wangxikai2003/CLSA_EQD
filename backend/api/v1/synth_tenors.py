"""Synthetic constant-maturity futures + equity spot for spread / 期现 analysis."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["market"])

ROOT = Path(__file__).resolve().parents[3]  # CLSA_EQD
REPO = ROOT.parent

_MONTH = 365.25 / 12.0
_SYNTH_TENORS = {
    "3M": 3 * _MONTH,
    "6M": 6 * _MONTH,
    "9M": 9 * _MONTH,
    "18M": 18 * _MONTH,
}

_UNIVERSE = [
    {"product": "HO", "fut": "IH", "name": "上证50", "asset_class": "equity", "spot_code": "000016.SH"},
    {"product": "IO", "fut": "IF", "name": "沪深300", "asset_class": "equity", "spot_code": "000300.SH"},
    {"product": "MO", "fut": "IM", "name": "中证1000", "asset_class": "equity", "spot_code": "000852.SH"},
    {"product": "CU", "fut": "CU", "name": "沪铜", "asset_class": "commodity", "spot_code": None},
    {"product": "AU", "fut": "AU", "name": "黄金", "asset_class": "commodity", "spot_code": None},
    {"product": "AG", "fut": "AG", "name": "白银", "asset_class": "commodity", "spot_code": None},
    {"product": "RU", "fut": "RU", "name": "橡胶", "asset_class": "commodity", "spot_code": None},
    {"product": "SC", "fut": "SC", "name": "原油", "asset_class": "commodity", "spot_code": None},
    {"product": "M", "fut": "M", "name": "豆粕", "asset_class": "commodity", "spot_code": None},
    {"product": "I", "fut": "I", "name": "铁矿", "asset_class": "commodity", "spot_code": None},
    {"product": "C", "fut": "C", "name": "玉米", "asset_class": "commodity", "spot_code": None},
    {"product": "SR", "fut": "SR", "name": "白糖", "asset_class": "commodity", "spot_code": None},
    {"product": "CF", "fut": "CF", "name": "棉花", "asset_class": "commodity", "spot_code": None},
    {"product": "MA", "fut": "MA", "name": "甲醇", "asset_class": "commodity", "spot_code": None},
    {"product": "TA", "fut": "TA", "name": "PTA", "asset_class": "commodity", "spot_code": None},
]
_EQUITY = {u["product"] for u in _UNIVERSE if u["asset_class"] == "equity"}
_COMMODITY = {u["product"] for u in _UNIVERSE if u["asset_class"] == "commodity"}
_SPOT_CODE = {u["product"]: u["spot_code"] for u in _UNIVERSE if u.get("spot_code")}

_DB_CANDIDATES = [ROOT / "data" / "cffex_vol.sqlite", Path("/app/data/cffex_vol.sqlite")]
_SPOT_CANDIDATES = [
    ROOT / "data" / "index_spot.parquet",
    Path("/app/data/index_spot.parquet"),
    REPO / "data" / "index_spot.parquet",
]
_COMMODITY_DB_CANDIDATES = [
    ROOT / "data" / "commodity_vol.sqlite",
    Path("/app/data/commodity_vol.sqlite"),
    REPO / "data" / "commodity_vol.sqlite",
    Path("/tmp/commodity_vol.sqlite"),
]

_cache: Dict[str, Any] = {"key": None, "payload": None}


def _equity_db() -> Path:
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    raise HTTPException(503, "cffex_vol.sqlite not found")


def _commodity_db() -> Optional[Path]:
    for p in _COMMODITY_DB_CANDIDATES:
        if not p.exists() or p.stat().st_size <= 0:
            continue
        try:
            if getattr(p.stat(), "st_blocks", 1) == 0:
                continue
        except Exception:
            pass
        return p
    return None


def _third_friday(ym: str) -> pd.Timestamp:
    y = 2000 + int(ym[:2])
    m = int(ym[2:])
    days = pd.date_range(f"{y}-{m:02d}-01", periods=31, freq="D")
    days = days[days.month == m]
    fridays = days[days.weekday == 4]
    return fridays[2] if len(fridays) >= 3 else days[-1]


def _month_end(ym: str) -> pd.Timestamp:
    y = 2000 + int(ym[:2])
    m = int(ym[2:])
    days = pd.date_range(f"{y}-{m:02d}-01", periods=31, freq="D")
    return days[days.month == m][-1]


def _lerp_settle(curve: List[tuple], target: float) -> Optional[Dict[str, Any]]:
    if len(curve) < 2:
        return None

    def _pair(t0, s0, c0, t1, s1, c1, extrapolated: bool):
        if t1 == t0:
            settle, w0 = float(s0), 1.0
        else:
            w0 = (t1 - target) / (t1 - t0)
            settle = w0 * float(s0) + (1.0 - w0) * float(s1)
        return {
            "settle": settle,
            "leg0_code": c0,
            "leg1_code": c1,
            "leg0_dtm": float(t0),
            "leg1_dtm": float(t1),
            "weight0": float(w0),
            "extrapolated": extrapolated,
        }

    for i in range(len(curve) - 1):
        t0, s0, c0 = curve[i]
        t1, s1, c1 = curve[i + 1]
        if t0 <= target <= t1:
            return _pair(t0, s0, c0, t1, s1, c1, False)
    t0, s0, c0 = curve[0]
    t1, s1, c1 = curve[1]
    if target < t0 and (t0 - target) <= 120:
        return _pair(t0, s0, c0, t1, s1, c1, True)
    t0, s0, c0 = curve[-2]
    t1, s1, c1 = curve[-1]
    if target > t1 and (target - t1) <= 120:
        return _pair(t0, s0, c0, t1, s1, c1, True)
    return None


def _synth_from_futures(fut: pd.DataFrame, products: Set[str], expiry_fn) -> Dict[str, Dict[str, List]]:
    tenor_keys = list(_SYNTH_TENORS.keys())
    levels = {p: {t: [] for t in tenor_keys} for p in products}
    fut = fut[fut["product"].isin(products)].copy()
    if fut.empty:
        return levels
    fut["trade_date"] = pd.to_datetime(fut["trade_date"])
    fut["expiry"] = fut["contract_ym"].map(expiry_fn)
    fut["dtm"] = (fut["expiry"] - fut["trade_date"]).dt.days.astype(float)
    fut = fut[fut["dtm"] > 0]
    for product, g in fut.groupby("product"):
        for td, day in g.groupby("trade_date"):
            by_t: Dict[float, tuple] = {}
            for r in day.itertuples():
                by_t[float(r.dtm)] = (float(r.dtm), float(r.settle), str(r.wind_code))
            curve = sorted(by_t.values(), key=lambda x: x[0])
            dstr = pd.Timestamp(td).strftime("%Y-%m-%d")
            for tlabel, tdays in _SYNTH_TENORS.items():
                hit = _lerp_settle(curve, float(tdays))
                if not hit:
                    continue
                levels[product][tlabel].append(
                    {
                        "d": dstr,
                        "settle": round(hit["settle"], 4),
                        "legs": [hit["leg0_code"], hit["leg1_code"]],
                        "leg_dtm": [round(hit["leg0_dtm"], 2), round(hit["leg1_dtm"], 2)],
                        "w0": round(hit["weight0"], 4),
                        "extrapolated": bool(hit.get("extrapolated", False)),
                        "kind": "synth",
                    }
                )
    return levels


def _load_spot_levels() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {p: [] for p in _SPOT_CODE}
    path = next((p for p in _SPOT_CANDIDATES if p.exists()), None)
    if path is None:
        return out
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    code_to_prod = {v: k for k, v in _SPOT_CODE.items()}
    for code, g in df.groupby("wind_code"):
        prod = code_to_prod.get(str(code))
        if not prod:
            continue
        g = g.sort_values("trade_date")
        out[prod] = [
            {
                "d": pd.Timestamp(r.trade_date).strftime("%Y-%m-%d"),
                "settle": round(float(r.close), 4),
                "legs": [str(code)],
                "leg_dtm": [0.0],
                "w0": 1.0,
                "extrapolated": False,
                "kind": "spot",
            }
            for r in g.itertuples()
        ]
    return out


def build_payload() -> Dict[str, Any]:
    tenor_keys = list(_SYNTH_TENORS.keys())
    levels: Dict[str, Dict[str, List]] = {}
    products_out: List[Dict[str, Any]] = []

    conn = sqlite3.connect(_equity_db())
    eq = pd.read_sql(
        "SELECT product, trade_date, wind_code, contract_ym, settle FROM futures_eod",
        conn,
    )
    conn.close()
    for p, tens in _synth_from_futures(eq, _EQUITY, _third_friday).items():
        levels[p] = tens

    for p, pts in _load_spot_levels().items():
        levels.setdefault(p, {t: [] for t in tenor_keys})
        levels[p]["SPOT"] = pts

    for u in _UNIVERSE:
        if u["asset_class"] != "equity" or u["product"] not in levels:
            continue
        products_out.append(
            {
                "product": u["product"],
                "fut": u["fut"],
                "name": u["name"],
                "asset_class": u["asset_class"],
                "spot_code": u["spot_code"],
                "has_spot": bool(levels[u["product"]].get("SPOT")),
            }
        )

    commodity_enabled = False
    cdb = _commodity_db()
    if cdb is not None:
        try:
            conn = sqlite3.connect(f"file:{cdb}?mode=ro", uri=True)
            cm = pd.read_sql(
                "SELECT product, trade_date, wind_code, contract_ym, settle FROM futures_eod",
                conn,
            )
            conn.close()
            for p, tens in _synth_from_futures(cm, _COMMODITY, _month_end).items():
                if not any(tens[t] for t in tenor_keys):
                    continue
                levels[p] = tens
                levels[p]["SPOT"] = []
                meta = next(u for u in _UNIVERSE if u["product"] == p)
                products_out.append(
                    {
                        "product": meta["product"],
                        "fut": meta["fut"],
                        "name": meta["name"],
                        "asset_class": meta["asset_class"],
                        "spot_code": None,
                        "has_spot": False,
                    }
                )
                commodity_enabled = True
        except Exception:
            commodity_enabled = False

    return {
        "tenors": tenor_keys,
        "leg_kinds": tenor_keys + ["SPOT"],
        "tenor_days": {k: round(v, 2) for k, v in _SYNTH_TENORS.items()},
        "products": products_out,
        "levels": levels,
        "commodity_enabled": commodity_enabled,
        "spot_source": "WIND.AINDEXEODPRICES → data/index_spot.parquet (equity); commodity cash TBD",
        "method": (
            "Futures DTM linear combo → 3M/6M/9M/18M. Equity expiry≈3rd Friday; "
            "commodity≈month-end. SPOT=equity index close for 期现. Extrapolate ≤120d."
        ),
    }


@router.get("/synth-tenors")
def synth_tenors(
    refresh: bool = Query(False),
    asset_class: str = Query("all", description="all|equity|commodity"),
):
    db = _equity_db()
    spot = next((p for p in _SPOT_CANDIDATES if p.exists()), None)
    cdb = _commodity_db()
    # Cache full universe; filter by asset_class on read
    key = (
        db.stat().st_mtime,
        spot.stat().st_mtime if spot else None,
        cdb.stat().st_mtime if cdb else None,
    )
    if not refresh and _cache["payload"] is not None and _cache["key"] == key:
        payload = _cache["payload"]
    else:
        payload = build_payload()
        payload["coverage"] = {
            p: {t: len(v) for t, v in tens.items()} for p, tens in payload["levels"].items()
        }
        _cache["key"] = key
        _cache["payload"] = payload

    if asset_class in ("equity", "commodity"):
        keep = {u["product"] for u in payload["products"] if u["asset_class"] == asset_class}
        return {
            **payload,
            "products": [u for u in payload["products"] if u["product"] in keep],
            "levels": {p: payload["levels"][p] for p in keep if p in payload["levels"]},
            "coverage": {p: payload["coverage"].get(p, {}) for p in keep},
        }
    return payload
