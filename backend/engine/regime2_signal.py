"""Regime2: quantile base + dual-day garch_z stress gate (no look-ahead).

Calm: z inside prior-window [q10, q90] → identical to rolling-quantile on Spread
(enter outside 10/90, exit inside 40/60, Reverse max-hold). No crisis stop.

Stress: z > q90 or z < q10 on *two consecutive* closes → block new opens only;
existing books still exit via the same quantile rules.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Match calendar_vol_strategy.MAX_HOLD_REVERSE
MAX_HOLD_REVERSE = 20
STRESS_STREAK_DAYS = 2


@dataclass
class Regime2Decision:
    signal: int
    stress: bool
    reason: str


def _finite(x: Optional[float]) -> bool:
    if x is None:
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def is_z_extreme(z: float, q10: float, q90: float) -> bool:
    """True when z is strictly outside prior-window [q10, q90]."""
    return float(z) > float(q90) or float(z) < float(q10)


def quantile_raw_signal(
    position: int,
    hold_days: int,
    spread: float,
    enter_hi: float,
    enter_lo: float,
    exit_hi: float,
    exit_lo: float,
) -> int:
    """Exact copy of CalendarVolEngine._decide_signal before side_mode filter."""
    bands_ok = all(
        math.isfinite(x) for x in (enter_hi, enter_lo, exit_hi, exit_lo, spread)
    )
    if not bands_ok:
        return int(position)
    pos = int(position)
    if pos == 0:
        if spread > enter_hi:
            return 1
        if spread < enter_lo:
            return -1
        return 0
    if exit_lo <= spread <= exit_hi:
        return 0
    if pos == -1 and int(hold_days) >= MAX_HOLD_REVERSE:
        return 0
    return pos


def decide_regime2_signal(
    position: int,
    hold_days: int,
    spread: float,
    enter_hi: float,
    enter_lo: float,
    exit_hi: float,
    exit_lo: float,
    z_t: float,
    z_q10_t: float,
    z_q90_t: float,
    z_prev: Optional[float],
    z_q10_prev: Optional[float],
    z_q90_prev: Optional[float],
) -> Regime2Decision:
    """Emit next-day target: quantile rules + dual-day stress blocks flat→open."""
    extreme_t = (
        _finite(z_t)
        and _finite(z_q10_t)
        and _finite(z_q90_t)
        and is_z_extreme(float(z_t), float(z_q10_t), float(z_q90_t))
    )
    extreme_prev = (
        _finite(z_prev)
        and _finite(z_q10_prev)
        and _finite(z_q90_prev)
        and is_z_extreme(float(z_prev), float(z_q10_prev), float(z_q90_prev))
    )
    stress = bool(extreme_t and extreme_prev)

    raw = quantile_raw_signal(
        position, hold_days, spread, enter_hi, enter_lo, exit_hi, exit_lo
    )
    pos = int(position)

    if not all(math.isfinite(x) for x in (enter_hi, enter_lo, exit_hi, exit_lo, spread)):
        return Regime2Decision(pos, stress, "bands_nan_hold" if pos != 0 else "bands_nan")

    if pos == 0:
        if stress and raw != 0:
            return Regime2Decision(0, True, "stress_block_entry")
        if raw == 1:
            return Regime2Decision(1, stress, "quantile_enter_hi")
        if raw == -1:
            return Regime2Decision(-1, stress, "quantile_enter_lo")
        return Regime2Decision(0, stress, "stress_flat" if stress else "quantile_flat")

    if raw == 0:
        reason = "quantile_exit_band"
        if pos == -1 and int(hold_days) >= MAX_HOLD_REVERSE:
            # Distinguish max-hold only when still outside exit band would matter;
            # quantile_raw already collapsed both to 0 — re-check for reason label.
            if not (exit_lo <= spread <= exit_hi):
                reason = "quantile_max_hold_reverse"
        return Regime2Decision(0, stress, reason)

    return Regime2Decision(pos, stress, "quantile_hold")
