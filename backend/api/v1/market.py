"""Market intelligence endpoints: yield curve, vol cone, T-quote."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/market", tags=["market"])

ROOT = Path(__file__).resolve().parents[3]  # CLSA_EQD
REPO = ROOT.parent  # vol_trading
_DB_CANDIDATES = [
    ROOT / "data" / "cffex_vol.sqlite",
    Path("/app/data/cffex_vol.sqlite"),
]
_RATES_CANDIDATES = [
    ROOT / "data" / "rates_eod.parquet",
    Path("/app/data/rates_eod.parquet"),
    REPO / "data" / "rates_eod.parquet",
]


def _db() -> Path:
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    raise HTTPException(503, "cffex_vol.sqlite not found")


def _rates_path() -> Path:
    for p in _RATES_CANDIDATES:
        if p.exists():
            return p
    raise HTTPException(503, "rates_eod.parquet not found")


@router.get("/yield-curve")
def yield_curve(
    codes: str = Query("SHIBORON.IR,SHIBOR1M.IR,SHIBOR3M.IR,SHIBOR6M.IR,SHIBOR1Y.IR,TB10Y.WI"),
    asof: Optional[str] = None,
    history: str = Query("1W,1M,1Y", description="comma offsets to overlay"),
):
    df = pd.read_parquet(_rates_path())
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    df = df[df["wind_code"].isin(code_list)].copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if asof:
        asof_ts = pd.Timestamp(asof)
    else:
        asof_ts = df["trade_date"].max()

    TENOR_MAP = {
        "SHIBORON.IR": 1 / 365,
        "SHIBOR1W.IR": 7 / 365,
        "SHIBOR1M.IR": 1 / 12,
        "SHIBOR3M.IR": 0.25,
        "SHIBOR6M.IR": 0.5,
        "SHIBOR1Y.IR": 1.0,
        "TB10Y.WI": 10.0,
        "CGB10Y.WI": 10.0,
        "SOFR.IR": 1 / 365,
    }

    def curve_at(ts: pd.Timestamp) -> List[Dict[str, Any]]:
        pts = []
        for code in code_list:
            g = df[df["wind_code"] == code]
            sub = g[g["trade_date"] <= ts]
            if sub.empty:
                continue
            row = sub.loc[sub["trade_date"].idxmax()]
            pts.append(
                {
                    "code": code,
                    "tenor_years": TENOR_MAP.get(code, 1.0),
                    "rate": float(row["rate"]),
                    "rate_pct": float(row["rate"]) * 100,
                    "trade_date": pd.Timestamp(row["trade_date"]).strftime("%Y-%m-%d"),
                }
            )
        pts.sort(key=lambda x: x["tenor_years"])
        return pts

    offsets = {
        "Current": asof_ts,
        "1W": asof_ts - pd.Timedelta(days=7),
        "1M": asof_ts - pd.Timedelta(days=30),
        "1Y": asof_ts - pd.Timedelta(days=365),
    }
    want = {"Current"} | {h.strip() for h in history.split(",") if h.strip()}
    series = {k: curve_at(v) for k, v in offsets.items() if k in want or k == "Current"}

    # spreads
    cur = {p["code"]: p["rate"] for p in series.get("Current", [])}
    spreads = {}
    if "TB10Y.WI" in cur and "SHIBOR1Y.IR" in cur:
        spreads["10Y_1Y_bp"] = (cur["TB10Y.WI"] - cur["SHIBOR1Y.IR"]) * 1e4
    if "SHIBOR1Y.IR" in cur and "SHIBOR3M.IR" in cur:
        spreads["1Y_3M_bp"] = (cur["SHIBOR1Y.IR"] - cur["SHIBOR3M.IR"]) * 1e4

    return {"asof": asof_ts.strftime("%Y-%m-%d"), "curves": series, "spreads": spreads}


_TENOR_LABELS = {
    10: "10D",
    20: "1M",
    30: "1M",
    60: "2M",
    90: "3M",
    180: "6M",
    252: "1Y",
    270: "9M",
}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _black76_greeks(F: float, K: float, t: float, sigma: float, cp: str) -> Dict[str, Optional[float]]:
    """Forward Black-76 delta / gamma (undiscounted)."""
    t = max(t, 1e-8)
    sigma = max(sigma, 1e-8)
    F = max(F, 1e-12)
    K = max(K, 1e-12)
    vol_sqrt = sigma * math.sqrt(t)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * t) / vol_sqrt
    d2 = d1 - vol_sqrt
    gamma = _norm_pdf(d1) / (F * vol_sqrt)
    if cp == "C":
        delta = _norm_cdf(d1)
    else:
        delta = _norm_cdf(d1) - 1.0
    return {"delta": delta, "gamma": gamma, "d1": d1, "d2": d2}


@router.get("/vol-cone")
def vol_cone(
    product: str = Query("IO"),
    windows: str = Query("10,20,30,60,90,180,252"),
):
    """Realized-vol cone + IV term + recent RV for Buy/Sell Vega overlays."""
    wins = [int(x) for x in windows.split(",") if x.strip()]
    conn = sqlite3.connect(_db())
    conn.row_factory = sqlite3.Row
    fut = pd.read_sql(
        """
        SELECT trade_date, settle FROM futures_eod
         WHERE product=? ORDER BY trade_date, contract_ym
        """,
        conn,
        params=(product.upper(),),
    )
    if fut.empty:
        conn.close()
        raise HTTPException(404, f"No futures for {product}")
    daily = fut.groupby("trade_date", as_index=False)["settle"].mean()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily = daily.sort_values("trade_date")
    rets = np.log(daily["settle"]).diff().dropna()

    cone = []
    for w in wins:
        if len(rets) < w + 5:
            continue
        rv = rets.rolling(w).std() * math.sqrt(252)
        rv = rv.dropna()
        # near-20 trading day mean of this window's RV series
        recent = float(rv.iloc[-20:].mean()) if len(rv) >= 5 else float(rv.iloc[-1])
        days = int(w)
        tenor = _TENOR_LABELS.get(days, f"{days}D")
        cone.append(
            {
                "window": days,
                "days": days,
                "tenor": tenor,
                "label": tenor,
                "min": float(rv.min()),
                "p25": float(rv.quantile(0.25)),
                "median": float(rv.median()),
                "p75": float(rv.quantile(0.75)),
                "max": float(rv.max()),
                "current_rv": float(rv.iloc[-1]),
                "recent_rv": recent,
                # band helpers for stacked areas (frontend)
                "band_core": float(rv.quantile(0.75) - rv.quantile(0.25)),
                "band_low": float(rv.quantile(0.25) - rv.min()),
                "band_high": float(rv.max() - rv.quantile(0.75)),
            }
        )

    date = conn.execute(
        "SELECT MAX(trade_date) FROM surface_meta WHERE product=? AND side='otm'",
        (product.upper(),),
    ).fetchone()[0]
    iv_term = []
    if date:
        rows = conn.execute(
            """
            SELECT ttm, jw_v FROM svi_slices
             WHERE product=? AND trade_date=? AND side='otm' ORDER BY ttm
            """,
            (product.upper(), date),
        ).fetchall()
        for r in rows:
            ttm = float(r["ttm"])
            atm = math.sqrt(max(float(r["jw_v"]), 0.0))
            days = int(round(ttm * 365))
            iv_term.append(
                {
                    "ttm": ttm,
                    "days": days,
                    "tenor": _TENOR_LABELS.get(days, f"{days}D"),
                    "atm_iv": atm,
                }
            )
    # Map current IV onto cone windows (nearest days)
    for c in cone:
        if iv_term:
            nearest = min(iv_term, key=lambda x: abs(x["days"] - c["days"]))
            c["current_iv"] = nearest["atm_iv"]
            # trading hint vs quantile bands
            if nearest["atm_iv"] > c["p75"]:
                c["signal"] = "Sell Vega"
            elif nearest["atm_iv"] < c["p25"]:
                c["signal"] = "Buy Vega"
            else:
                c["signal"] = None
        else:
            c["current_iv"] = None
            c["signal"] = None

    conn.close()
    return {
        "product": product.upper(),
        "asof": date,
        "cone": cone,
        "current_iv_term": iv_term,
        "axis_labels": ["days", "tenor"],
    }


@router.get("/tquote")
def tquote(
    product: str = Query("IO"),
    date: Optional[str] = None,
    expiry: Optional[str] = None,
):
    conn = sqlite3.connect(_db())
    conn.row_factory = sqlite3.Row
    if not date:
        date = conn.execute(
            "SELECT MAX(trade_date) FROM option_iv WHERE product=?",
            (product.upper(),),
        ).fetchone()[0]
    if not date:
        conn.close()
        raise HTTPException(404, "no option_iv")
    q = """
        SELECT strike, expiry, ttm, cp, iv, forward, mid_price
          FROM option_iv WHERE product=? AND trade_date=?
    """
    params: list = [product.upper(), date]
    if expiry:
        q += " AND expiry=?"
        params.append(expiry)
    q += " ORDER BY expiry, strike"
    rows = conn.execute(q, params).fetchall()
    expiries = sorted({r["expiry"] for r in rows})
    if not expiry and expiries:
        expiry = expiries[0]
        rows = [r for r in rows if r["expiry"] == expiry]
    spot = float(rows[0]["forward"]) if rows else 0.0
    ttm = float(rows[0]["ttm"]) if rows else 0.25
    by_k: Dict[float, Dict[str, Any]] = {}
    for r in rows:
        k = float(r["strike"])
        slot = by_k.setdefault(k, {"strike": k, "call": None, "put": None})
        mid = float(r["mid_price"]) if r["mid_price"] is not None else None
        iv = float(r["iv"]) if r["iv"] is not None else None
        greeks = (
            _black76_greeks(spot, k, float(r["ttm"] or ttm), iv, r["cp"])
            if iv is not None
            else {"delta": None, "gamma": None}
        )
        # settle book: no true bid/ask — synthetic 1-tick wide spread around mid
        tick = max(abs(mid or 0) * 0.005, 0.2) if mid is not None else None
        bid = (mid - tick) if mid is not None and tick is not None else mid
        ask = (mid + tick) if mid is not None and tick is not None else mid
        spread = (ask - bid) if bid is not None and ask is not None else None
        cell = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "iv": iv,
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "oi": None,
            "volume": None,
            "source": "settle",
        }
        if r["cp"] == "C":
            slot["call"] = cell
        else:
            slot["put"] = cell
    board = sorted(by_k.values(), key=lambda x: x["strike"])
    atm = min(board, key=lambda x: abs(x["strike"] - spot))["strike"] if board else None
    conn.close()
    return {
        "product": product.upper(),
        "trade_date": date,
        "expiry": expiry,
        "expiries": expiries,
        "spot": spot,
        "atm_strike": atm,
        "ttm": ttm,
        "board": board,
        "source": "settle",
    }
