"""(trade_date, ticker) position and P&L ledger. Pure function, no database.

Gross mark-to-market only: fees and slippage stay outside the ledger.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

LEDGER_COLUMNS = [
    "trade_date",
    "ticker",
    "asset_type",
    "leg_role",
    "multiplier",
    "prev_close",
    "close_price",
    "prev_pos",
    "trade_qty",
    "avg_trade_price",
    "curr_pos",
    "holding_pnl",
    "trading_pnl",
    "total_pnl",
    "eod_notional",
    "eod_delta",
    "eod_gamma",
    "eod_vega",
    "eod_theta",
    "strike",
    "cp",
    "expiry",
    "spot",
]

BLOTTER_COLUMNS = [
    "trade_date",
    "ticker",
    "asset_type",
    "leg_role",
    "action_type",
    "vintage_id",
    "qty",
    "trade_price",
    "notional",
    "multiplier",
]

_EPS = 1e-12


def option_intrinsic(spot: float, strike: float, cp: str) -> float:
    """Cash settlement value. Call max(S-K, 0), put max(K-S, 0)."""
    if not (math.isfinite(spot) and math.isfinite(strike)):
        return float("nan")
    side = str(cp or "").upper()[:1]
    if side == "P":
        return float(max(strike - spot, 0.0))
    return float(max(spot - strike, 0.0))


def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _vwap(trades: Sequence[Mapping[str, Any]]) -> Optional[float]:
    weight = 0.0
    acc = 0.0
    for t in trades:
        q = _finite(t.get("qty"))
        p = _finite(t.get("trade_price"))
        if q is None or p is None or abs(q) <= _EPS:
            continue
        weight += abs(q)
        acc += abs(q) * p
    if weight <= _EPS:
        return None
    return acc / weight


def _dominant_role(prev_roles: Mapping[str, float], trades: Sequence[Mapping[str, Any]]) -> str:
    eod: Dict[str, float] = {str(k): float(v) for k, v in prev_roles.items()}
    for t in trades:
        role = str(t.get("leg_role") or "SHORT_LEG")
        eod[role] = eod.get(role, 0.0) + float(t.get("qty") or 0.0)
    live = {k: v for k, v in eod.items() if abs(v) > _EPS}
    if live:
        return max(live, key=lambda r: (abs(live[r]), r))
    traded: Dict[str, float] = {}
    for t in trades:
        role = str(t.get("leg_role") or "SHORT_LEG")
        traded[role] = traded.get(role, 0.0) + abs(float(t.get("qty") or 0.0))
    if traded:
        return max(traded, key=lambda r: (traded[r], r))
    if prev_roles:
        return max(prev_roles, key=lambda r: (abs(float(prev_roles[r])), r))
    return "SHORT_LEG"


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def _empty_blotter() -> pd.DataFrame:
    return pd.DataFrame(columns=BLOTTER_COLUMNS)


def build_position_ledger(
    days: Sequence[Mapping[str, Any]],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate one dict per session into ledger rows and a trade blotter.

    Each day mapping:
      trade_date, spot, prev (start-of-day positions), trades, marks, mark_futures.
    Positions that are flat before and after the session, with no trade, are dropped.
    """
    ledger_rows: List[Dict[str, Any]] = []
    blotter_rows: List[Dict[str, Any]] = []

    for day in days:
        d = str(day["trade_date"])[:10]
        spot = _finite(day.get("spot"))
        mark_futures = bool(day.get("mark_futures", True))
        prev_map: Dict[str, Dict[str, Any]] = {}
        for raw in day.get("prev") or []:
            ticker = str(raw["ticker"])
            slot = prev_map.setdefault(
                ticker,
                {
                    "qty": 0.0,
                    "roles": {},
                    "close": _finite(raw.get("close")),
                    "asset_type": str(raw.get("asset_type") or "OPTION"),
                    "multiplier": _finite(raw.get("multiplier")) or 1.0,
                    "strike": _finite(raw.get("strike")),
                    "cp": raw.get("cp"),
                    "expiry": raw.get("expiry"),
                },
            )
            q = float(raw.get("qty") or 0.0)
            slot["qty"] += q
            role = str(raw.get("leg_role") or "SHORT_LEG")
            slot["roles"][role] = slot["roles"].get(role, 0.0) + q
            if slot["close"] is None:
                slot["close"] = _finite(raw.get("close"))

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for raw in day.get("trades") or []:
            ticker = str(raw["ticker"])
            grouped.setdefault(ticker, []).append(dict(raw))

        marks = day.get("marks") or {}
        tickers = set(prev_map) | set(grouped) | {str(k) for k in marks.keys()}

        for ticker in sorted(tickers):
            prev = prev_map.get(ticker)
            trades = grouped.get(ticker, [])
            mark = marks.get(ticker) or {}
            asset = str(
                (prev or {}).get("asset_type")
                or (trades[0].get("asset_type") if trades else None)
                or mark.get("asset_type")
                or "OPTION"
            )
            mult = _finite((prev or {}).get("multiplier"))
            if mult is None and trades:
                mult = _finite(trades[0].get("multiplier"))
            if mult is None:
                mult = _finite(mark.get("multiplier")) or 1.0
            strike = _finite((prev or {}).get("strike"))
            if strike is None and trades:
                strike = _finite(trades[0].get("strike"))
            cp = (prev or {}).get("cp") if prev else None
            if cp is None and trades:
                cp = trades[0].get("cp")
            expiry = (prev or {}).get("expiry") if prev else None
            if expiry is None and trades:
                expiry = trades[0].get("expiry")

            expiring = any(str(t.get("action_type")) == "EXPIRY_SETTLE" for t in trades)
            if expiring:
                close = option_intrinsic(float(spot or float("nan")), float(strike or float("nan")), str(cp or ""))
                if not math.isfinite(close):
                    close = _finite(mark.get("close"))
                for t in trades:
                    if str(t.get("action_type")) == "EXPIRY_SETTLE" and close is not None:
                        t["trade_price"] = close
            elif asset == "FUTURE" and not mark_futures and prev is not None and prev.get("close") is not None:
                close = float(prev["close"])
                for t in trades:
                    t["trade_price"] = close
            else:
                close = _finite(mark.get("close"))
                if close is None and trades:
                    close = _finite(trades[-1].get("trade_price"))
                if close is None and prev is not None:
                    close = prev.get("close")

            if close is None:
                continue

            prev_pos = float(prev["qty"]) if prev else 0.0
            trade_qty = float(sum(float(t.get("qty") or 0.0) for t in trades))
            curr_pos = prev_pos + trade_qty
            if abs(prev_pos) <= _EPS and abs(trade_qty) <= _EPS and abs(curr_pos) <= _EPS:
                continue

            avg = _vwap(trades) if trades else None
            prev_close = prev.get("close") if prev else None
            if prev_close is None:
                prev_close = avg if avg is not None else close

            holding = prev_pos * (float(close) - float(prev_close)) * float(mult)
            trading = 0.0
            if trades and avg is not None:
                trading = trade_qty * (float(close) - float(avg)) * float(mult)

            unit_delta = _finite(mark.get("delta")) or 0.0
            unit_gamma = _finite(mark.get("gamma")) or 0.0
            unit_vega = _finite(mark.get("vega")) or 0.0
            unit_theta = _finite(mark.get("theta")) or 0.0
            if asset == "FUTURE":
                unit_delta = 1.0
                unit_gamma = unit_vega = unit_theta = 0.0
            if abs(curr_pos) <= _EPS:
                unit_delta = unit_gamma = unit_vega = unit_theta = 0.0

            role = _dominant_role((prev or {}).get("roles") or {}, trades)
            ledger_rows.append(
                {
                    "trade_date": d,
                    "ticker": ticker,
                    "asset_type": asset,
                    "leg_role": role,
                    "multiplier": float(mult),
                    "prev_close": float(prev_close),
                    "close_price": float(close),
                    "prev_pos": prev_pos,
                    "trade_qty": trade_qty,
                    "avg_trade_price": avg,
                    "curr_pos": curr_pos,
                    "holding_pnl": holding,
                    "trading_pnl": trading,
                    "total_pnl": holding + trading,
                    "eod_notional": curr_pos * float(close) * float(mult),
                    "eod_delta": curr_pos * unit_delta * float(mult),
                    "eod_gamma": curr_pos * unit_gamma * float(mult),
                    "eod_vega": curr_pos * unit_vega * float(mult),
                    "eod_theta": curr_pos * unit_theta * float(mult),
                    "strike": strike,
                    "cp": None if cp is None else str(cp).upper()[:1],
                    "expiry": None if expiry is None else str(expiry)[:10],
                    "spot": spot,
                }
            )
            for t in trades:
                px = _finite(t.get("trade_price"))
                q = float(t.get("qty") or 0.0)
                blotter_rows.append(
                    {
                        "trade_date": d,
                        "ticker": ticker,
                        "asset_type": asset,
                        "leg_role": str(t.get("leg_role") or role),
                        "action_type": str(t.get("action_type") or "EOD_DELTA_HEDGE"),
                        "vintage_id": t.get("vintage_id") or None,
                        "qty": q,
                        "trade_price": px,
                        "notional": None if px is None else q * px * float(mult),
                        "multiplier": float(mult),
                    }
                )

    ledger = pd.DataFrame(ledger_rows, columns=LEDGER_COLUMNS) if ledger_rows else _empty_ledger()
    blotter = pd.DataFrame(blotter_rows, columns=BLOTTER_COLUMNS) if blotter_rows else _empty_blotter()
    if not ledger.empty:
        ledger = ledger.sort_values(["trade_date", "ticker"]).reset_index(drop=True)
    if not blotter.empty:
        blotter = blotter.sort_values(["trade_date", "ticker", "action_type"]).reset_index(drop=True)
    return ledger, blotter


def portfolio_pnl_by_day(ledger: pd.DataFrame) -> Dict[str, float]:
    if ledger is None or ledger.empty:
        return {}
    out: Dict[str, float] = {}
    for d, g in ledger.groupby("trade_date"):
        out[str(d)] = float(g["total_pnl"].sum())
    return out
