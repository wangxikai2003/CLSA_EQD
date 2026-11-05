"""QIS systematic strategy backtests (cross calendar carry first)."""
from __future__ import annotations

import math
import sqlite3
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from api.v1 import synth_tenors as st

router = APIRouter(tags=["qis"])

_INITIAL_NAV = 100_000_000.0
_SLIPPAGE = 2e-4  # 2bp
_FEE = 0.0023 / 100.0  # 0.0023%
_COST_RATE = _SLIPPAGE + _FEE
_ANN = 252

# Long IM 3M−6M calendar, short IH 3M−6M calendar (eat positive SoS carry).
# Signs on equal *point* lots: PnL = a · ΔSoS with a = NAV / Σ S_legs (gross notional = NAV).
_CROSS_CAL_SIGNS = {
    "IM_3M": 1.0,
    "IM_6M": -1.0,
    "IH_3M": -1.0,
    "IH_6M": 1.0,
}

_cache: Dict[str, Any] = {"key": None, "payload": None}


def _leg_series(levels: Dict, product: str, tenor: str) -> pd.Series:
    pts = levels.get(product, {}).get(tenor, [])
    if not pts:
        return pd.Series(dtype=float)
    return pd.Series({p["d"]: float(p["settle"]) for p in pts}).sort_index()


def _build_cross_cal_panel() -> pd.DataFrame:
    """Equity-only synth panel for IH/IM 3M & 6M (skip commodity for speed)."""
    conn = sqlite3.connect(st._equity_db())
    eq = pd.read_sql(
        "SELECT product, trade_date, wind_code, contract_ym, settle FROM futures_eod",
        conn,
    )
    conn.close()
    levels = st._synth_from_futures(eq, st._EQUITY_DB_KEYS, st._third_friday)
    series = {
        "IM_3M": _leg_series(levels, "IM", "3M"),
        "IM_6M": _leg_series(levels, "IM", "6M"),
        "IH_3M": _leg_series(levels, "IH", "3M"),
        "IH_6M": _leg_series(levels, "IH", "6M"),
    }
    df = pd.DataFrame(series).dropna(how="any").sort_index()
    if df.empty:
        raise HTTPException(503, "Insufficient IH/IM 3M/6M synthetic history for QIS backtest")
    df["sos"] = (df["IM_3M"] - df["IM_6M"]) - (df["IH_3M"] - df["IH_6M"])
    return df


def _max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min()) if len(dd) else float("nan")


def _window_metrics(nav: pd.Series, daily_ret: pd.Series) -> Dict[str, Any]:
    if len(nav) < 2:
        return {
            "n_days": int(len(nav)),
            "ann_return": None,
            "ann_vol": None,
            "sharpe": None,
            "mdd": None,
            "mdd_vol": None,
            "pct_positive_months": None,
        }
    r = daily_ret.dropna()
    n = len(r)
    if n == 0 or nav.iloc[0] <= 0:
        return {
            "n_days": 0,
            "ann_return": None,
            "ann_vol": None,
            "sharpe": None,
            "mdd": None,
            "mdd_vol": None,
            "pct_positive_months": None,
        }
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    years = n / _ANN
    ann_ret = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else float("nan")
    ann_vol = float(r.std(ddof=1) * math.sqrt(_ANN)) if n > 1 else float("nan")
    sharpe = float(ann_ret / ann_vol) if ann_vol and ann_vol > 0 else float("nan")
    mdd = _max_drawdown(nav)
    mdd_vol = float(abs(mdd) / ann_vol) if ann_vol and ann_vol > 0 else float("nan")

    # month-end NAV → monthly returns
    nav_i = nav.copy()
    nav_i.index = pd.to_datetime(nav_i.index)
    try:
        monthly = nav_i.resample("ME").last().dropna()
    except ValueError:
        monthly = nav_i.resample("M").last().dropna()
    mret = monthly.pct_change().dropna()
    pct_pos = float((mret > 0).mean() * 100.0) if len(mret) else float("nan")

    def _pct(x: Optional[float]) -> Optional[float]:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return None
        return round(float(x), 6)

    return {
        "n_days": int(n),
        "ann_return": _pct(ann_ret),
        "ann_vol": _pct(ann_vol),
        "sharpe": _pct(sharpe),
        "mdd": _pct(mdd),
        "mdd_vol": _pct(mdd_vol),
        "pct_positive_months": _pct(pct_pos),
    }


def _slice_from_end(nav: pd.Series, ret: pd.Series, years: Optional[float]) -> Tuple[pd.Series, pd.Series]:
    if years is None:
        # rebase max window to 1.0 at start for chart-comparable metrics on absolute NAV
        base = float(nav.iloc[0])
        return (nav / base if base else nav), ret
    end = pd.Timestamp(nav.index[-1])
    start = end - timedelta(days=int(years * 365.25))
    idx = pd.to_datetime(nav.index)
    ge = [i for i, t in enumerate(idx) if t >= start]
    if not ge:
        return nav.iloc[0:0], ret.iloc[0:0]
    i0 = ge[0]
    base = float(nav.iloc[i0])
    nav_w = nav.iloc[i0:] / base if base else nav.iloc[i0:]
    ret_w = ret.iloc[i0:]
    return nav_w, ret_w


def _point_lot_size(nav: float, row: pd.Series, legs: List[str]) -> float:
    """a such that gross notional Σ |q_i| S_i = NAV with |q_i|=a for all legs."""
    gross_px = sum(float(row[k]) for k in legs)
    if gross_px <= 0:
        return 0.0
    return nav / gross_px


def run_cross_calendar_carry(
    initial_nav: float = _INITIAL_NAV,
    slippage: float = _SLIPPAGE,
    fee: float = _FEE,
) -> Dict[str, Any]:
    """
    Always long SoS = (IM3−IM6) − (IH3−IH6).

    Position: equal absolute *index-point* lots a on four legs with signs
    (+IM3, −IM6, −IH3, +IH6), sized so gross notional a·ΣS = NAV.
    Then overnight PnL = a · ΔSoS (matches Spread-tab point carry).

    Timing: lot size signalled from t−1 NAV/prices; executed / resized at t settle.
    Costs: (|Δq|·S)·(slippage+fee) on rebalance notional.
    """
    px = _build_cross_cal_panel()
    legs = list(_CROSS_CAL_SIGNS.keys())
    signs = _CROSS_CAL_SIGNS
    cost_rate = slippage + fee

    dates = list(px.index)
    nav = initial_nav
    q = {k: 0.0 for k in legs}  # flat until first execution day
    rows: List[Dict[str, Any]] = []

    # Day 0: observe prices, emit signal (lot size from day-0 state), no trade yet
    rows.append(
        {
            "d": dates[0],
            "nav": nav,
            "ret": 0.0,
            "turnover": 0.0,
            "cost": 0.0,
            "sos": float(px.iloc[0]["sos"]),
            "lot_a": _point_lot_size(nav, px.iloc[0], legs),
            "executed": False,
        }
    )

    for i in range(1, len(dates)):
        d = dates[i]
        s_prev = px.iloc[i - 1]
        s_now = px.iloc[i]

        # 1) Mark overnight point lots to today's settle → PnL = a_prev · ΔSoS
        pnl = 0.0
        for k in legs:
            pnl += q[k] * (float(s_now[k]) - float(s_prev[k]))
        nav_pre = nav + pnl

        # 2) Execute t−1 signal at t settle: resize to target lot from nav_pre & t prices
        #    (constant long-carry signs; a recomputed daily = weight signal)
        a_tgt = _point_lot_size(nav_pre, s_now, legs)
        turnover = 0.0
        for k in legs:
            s = float(s_now[k])
            q_tgt = signs[k] * a_tgt
            turnover += abs(q_tgt - q[k]) * s

        cost = turnover * cost_rate
        nav = nav_pre - cost
        a = _point_lot_size(nav, s_now, legs)
        for k in legs:
            q[k] = signs[k] * a

        ret = (nav / rows[-1]["nav"] - 1.0) if rows[-1]["nav"] else 0.0
        rows.append(
            {
                "d": d,
                "nav": nav,
                "ret": ret,
                "turnover": turnover,
                "cost": cost,
                "sos": float(s_now["sos"]),
                "lot_a": a,
                "executed": True,
            }
        )

    nav_s = pd.Series({r["d"]: r["nav"] for r in rows})
    ret_s = pd.Series({r["d"]: r["ret"] for r in rows})
    # first day ret is 0 by construction; metrics use from first execution
    ret_s.iloc[0] = float("nan")

    windows = {
        "1y": 1.0,
        "3y": 3.0,
        "5y": 5.0,
        "max": None,
    }
    metrics = {}
    for name, yrs in windows.items():
        nav_w, ret_w = _slice_from_end(nav_s, ret_s, yrs)
        metrics[name] = _window_metrics(nav_w, ret_w)
        metrics[name]["start"] = str(nav_w.index[0]) if len(nav_w) else None
        metrics[name]["end"] = str(nav_w.index[-1]) if len(nav_w) else None

    # downsample NAV for chart
    step = max(1, len(rows) // 800)
    nav_chart = [
        {"d": rows[i]["d"], "nav": round(rows[i]["nav"] / initial_nav, 6), "sos": round(rows[i]["sos"], 4)}
        for i in range(0, len(rows), step)
    ]
    if nav_chart[-1]["d"] != rows[-1]["d"]:
        nav_chart.append(
            {
                "d": rows[-1]["d"],
                "nav": round(rows[-1]["nav"] / initial_nav, 6),
                "sos": round(rows[-1]["sos"], 4),
            }
        )

    return {
        "strategy_id": "cross_calendar_carry",
        "strategy_name": "Cross Calendar Carry",
        "description": (
            "Long IM 3M−6M calendar vs short IH 3M−6M calendar "
            "(SoS = (IM3−IM6)−(IH3−IH6)). Always long to harvest observed positive carry. "
            "Signal weights on t−1; execute at t settlement."
        ),
        "legs": [
            {
                "id": k,
                "weight": signs[k],  # point-lot sign; |q|=a, a=NAV/ΣS
                "product": k.split("_")[0],
                "tenor": k.split("_")[1],
            }
            for k in legs
        ],
        "params": {
            "initial_nav": initial_nav,
            "slippage_bp": slippage * 1e4,
            "fee_pct": fee * 100.0,
            "cost_rate": cost_rate,
            "rebalance": "daily",
            "signal_lag": "t-1 lot signal → t settle execution",
            "price": "synthetic constant-maturity settle (Wind S_DQ_SETTLE lerp)",
            "sizing": "equal point lots a=NAV/ΣS_legs so overnight PnL=a·ΔSoS; gross notional=NAV",
        },
        "nav": nav_chart,
        "metrics": metrics,
        "summary": {
            "start": dates[0],
            "end": dates[-1],
            "n_days": len(rows),
            "final_nav": round(nav, 2),
            "final_nav_multiple": round(nav / initial_nav, 6),
            "total_cost": round(sum(r["cost"] for r in rows), 2),
        },
    }


@router.get("/strategies")
def list_strategies():
    return {
        "strategies": [
            {
                "id": "cross_calendar_carry",
                "name": "Cross Calendar Carry",
                "status": "live",
                "blurb": "IM 3M−6M minus IH 3M−6M calendar carry",
            },
            {
                "id": "equity_3_6_calendar_vol_300",
                "name": "Equity 3-6 Calendar Volatility Long Short Strategy — 300",
                "status": "live",
                "blurb": "CSI300 3M−6M ATM CT-IV spread Z-score; IO straddles + IF hedge",
            },
        ]
    }


@router.get("/backtest/{strategy_id}")
def backtest(
    strategy_id: str,
    refresh: bool = Query(False),
):
    db = st._equity_db()

    if strategy_id == "cross_calendar_carry":
        key = (strategy_id, db.stat().st_mtime, _INITIAL_NAV, _SLIPPAGE, _FEE)
        if not refresh and _cache["payload"] is not None and _cache["key"] == key:
            return _cache["payload"]
        payload = run_cross_calendar_carry()
        _cache["key"] = key
        _cache["payload"] = payload
        return payload

    if strategy_id == "equity_3_6_calendar_vol_300":
        from engine.calendar_vol_strategy import run_equity_3_6_calendar_vol_300

        spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
        key = (
            strategy_id,
            db.stat().st_mtime,
            spot.stat().st_mtime if spot else None,
            _INITIAL_NAV,
        )
        cached = _cache.get("cal_vol")
        if (
            not refresh
            and isinstance(cached, dict)
            and cached.get("key") == key
            and cached.get("payload") is not None
        ):
            return cached["payload"]
        payload = run_equity_3_6_calendar_vol_300(_INITIAL_NAV)
        _cache["cal_vol"] = {"key": key, "payload": payload}
        return payload

    raise HTTPException(404, f"Unknown strategy: {strategy_id}")
