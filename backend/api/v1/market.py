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


# --- Synthetic futures tenors (3M/6M/9M/18M) from listed contracts' actual DTM ---

_MONTH = 365.25 / 12.0
_SYNTH_TENORS = {
    "3M": 3 * _MONTH,
    "6M": 6 * _MONTH,
    "9M": 9 * _MONTH,
    "18M": 18 * _MONTH,
}
_FUT_PREFIX = {"HO": "IH", "IO": "IF", "MO": "IM"}

_synth_cache: Dict[str, Any] = {"mtime": None, "payload": None}


def _third_friday(ym: str) -> pd.Timestamp:
    y = 2000 + int(ym[:2])
    m = int(ym[2:])
    days = pd.date_range(f"{y}-{m:02d}-01", periods=31, freq="D")
    days = days[days.month == m]
    fridays = days[days.weekday == 4]
    return fridays[2] if len(fridays) >= 3 else days[-1]


def _lerp_settle(curve: List[tuple], target: float) -> Optional[Dict[str, Any]]:
    """curve: sorted (dtm, settle, wind_code). Prefer interpolate; else extrapolate with outer pair.

    Extrapolation only if |target - nearest_end| <= 120 calendar days (CFFEX strip is short;
    otherwise 9M would almost always be empty).
    """
    if len(curve) < 2:
        return None

    def _pair(t0: float, s0: float, c0: str, t1: float, s1: float, c1: str, extrapolated: bool):
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

    # interior
    for i in range(len(curve) - 1):
        t0, s0, c0 = curve[i]
        t1, s1, c1 = curve[i + 1]
        if t0 <= target <= t1:
            return _pair(t0, s0, c0, t1, s1, c1, False)

    # short extrapolate
    t0, s0, c0 = curve[0]
    t1, s1, c1 = curve[1]
    if target < t0 and (t0 - target) <= 120:
        return _pair(t0, s0, c0, t1, s1, c1, True)

    # long extrapolate
    t0, s0, c0 = curve[-2]
    t1, s1, c1 = curve[-1]
    if target > t1 and (target - t1) <= 120:
        return _pair(t0, s0, c0, t1, s1, c1, True)

    return None


def _build_synth_payload() -> Dict[str, Any]:
    db = _db()
    conn = sqlite3.connect(db)
    fut = pd.read_sql(
        "SELECT product, trade_date, wind_code, contract_ym, settle FROM futures_eod",
        conn,
    )
    conn.close()
    fut["trade_date"] = pd.to_datetime(fut["trade_date"])
    fut["expiry"] = fut["contract_ym"].map(_third_friday)
    fut["dtm"] = (fut["expiry"] - fut["trade_date"]).dt.days.astype(float)
    fut = fut[fut["dtm"] > 0]

    tenor_keys = list(_SYNTH_TENORS.keys())
    levels: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        p: {t: [] for t in tenor_keys} for p in _FUT_PREFIX
    }

    for product, g in fut.groupby("product"):
        if product not in _FUT_PREFIX:
            continue
        for td, day in g.groupby("trade_date"):
            day = day.sort_values("dtm")
            # unique dtm ascending; if duplicate dtm keep higher oi would need oi — keep first
            curve = [
                (float(r.dtm), float(r.settle), str(r.wind_code))
                for r in day.itertuples()
            ]
            # dedupe by dtm keeping last
            by_t: Dict[float, tuple] = {}
            for item in curve:
                by_t[item[0]] = item
            curve = sorted(by_t.values(), key=lambda x: x[0])
            dstr = pd.Timestamp(td).strftime("%Y-%m-%d")
            for tlabel, tdays in _SYNTH_TENORS.items():
                hit = _lerp_settle(curve, float(tdays))
                if hit is None:
                    continue
                levels[product][tlabel].append(
                    {
                        "d": dstr,
                        "settle": round(hit["settle"], 4),
                        "legs": [hit["leg0_code"], hit["leg1_code"]],
                        "leg_dtm": [round(hit["leg0_dtm"], 2), round(hit["leg1_dtm"], 2)],
                        "w0": round(hit["weight0"], 4),
                        "extrapolated": bool(hit.get("extrapolated", False)),
                    }
                )

    return {
        "tenors": tenor_keys,
        "tenor_days": {k: round(v, 2) for k, v in _SYNTH_TENORS.items()},
        "products": [
            {"product": p, "fut": _FUT_PREFIX[p], "name": n}
            for p, n in (("HO", "上证50"), ("IO", "沪深300"), ("MO", "中证1000"))
        ],
        "levels": levels,
        "method": (
            "Listed futures settles vs actual DTM (expiry≈3rd Friday of contract month). "
            "Linear combo of two surrounding contracts to hit exact 3M/6M/9M/18M "
            "(= months×365.25/12 days). If target is outside the strip by ≤120 days, "
            "use the outer pair to extrapolate and flag extrapolated=true. "
            "18M usually unavailable under CFFEX 4-contract listing."
        ),
    }


@router.get("/synth-tenors")
def synth_tenors(refresh: bool = Query(False, description="bypass cache")):
    """Synthetic constant-maturity futures from listed contract DTM interpolation."""
    db = _db()
    mtime = db.stat().st_mtime
    if (
        not refresh
        and _synth_cache["payload"] is not None
        and _synth_cache["mtime"] == mtime
    ):
        return _synth_cache["payload"]
    payload = _build_synth_payload()
    # coverage stats
    cov = {}
    for p, tens in payload["levels"].items():
        cov[p] = {t: len(v) for t, v in tens.items()}
    payload["coverage"] = cov
    _synth_cache["mtime"] = mtime
    _synth_cache["payload"] = payload
    return payload
