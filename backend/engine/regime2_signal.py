"""Regime2: quantile base + multi-day garch_z stress gate (no look-ahead).

Calm: identical to rolling-quantile on Spread (enter outside enter_q,
exit inside [exit_lo, exit_hi], Reverse max-hold). No crisis stop.

Stress: garch_z outside its rolling enter band for ``stress_streak_days``
consecutive closes → force-flat any open book and block new opens.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

# Defaults (percent-style knobs live on the engine / sweep script).
MAX_HOLD_REVERSE = 20
STRESS_STREAK_DAYS = 2

# (z, z_q10, z_q90) for one close; None/NaN ⇒ not extreme that day.
ZDay = Tuple[Optional[float], Optional[float], Optional[float]]


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


def day_is_extreme(z: Optional[float], q10: Optional[float], q90: Optional[float]) -> bool:
    if not (_finite(z) and _finite(q10) and _finite(q90)):
        return False
    return is_z_extreme(float(z), float(q10), float(q90))


def stress_from_z_hist(
    z_hist: Sequence[ZDay],
    stress_streak_days: int = STRESS_STREAK_DAYS,
) -> bool:
    """True if the last ``stress_streak_days`` closes (incl. today) are all extreme."""
    need = max(1, int(stress_streak_days))
    if len(z_hist) < need:
        return False
    window = z_hist[-need:]
    return all(day_is_extreme(z, q10, q90) for z, q10, q90 in window)


def quantile_raw_signal(
    position: int,
    hold_days: int,
    spread: float,
    enter_hi: float,
    enter_lo: float,
    exit_hi: float,
    exit_lo: float,
    max_hold_reverse: int = MAX_HOLD_REVERSE,
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
    if pos == -1 and int(hold_days) >= int(max_hold_reverse):
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
    z_prev: Optional[float] = None,
    z_q10_prev: Optional[float] = None,
    z_q90_prev: Optional[float] = None,
    z_hist: Optional[Sequence[ZDay]] = None,
    stress_streak_days: int = STRESS_STREAK_DAYS,
    max_hold_reverse: int = MAX_HOLD_REVERSE,
) -> Regime2Decision:
    """Emit next-day target: quantile rules + N-day stress force-flat / block entry.

    Prefer ``z_hist`` (oldest→newest, last = today). If omitted, falls back to
    today + optional yesterday (legacy dual-day API).
    """
    if z_hist is None:
        hist: list[ZDay] = []
        if _finite(z_prev) or _finite(z_q10_prev) or _finite(z_q90_prev):
            hist.append((z_prev, z_q10_prev, z_q90_prev))
        hist.append((z_t, z_q10_t, z_q90_t))
    else:
        hist = list(z_hist)

    stress = stress_from_z_hist(hist, stress_streak_days=stress_streak_days)
    raw = quantile_raw_signal(
        position,
        hold_days,
        spread,
        enter_hi,
        enter_lo,
        exit_hi,
        exit_lo,
        max_hold_reverse=max_hold_reverse,
    )
    pos = int(position)

    if not all(math.isfinite(x) for x in (enter_hi, enter_lo, exit_hi, exit_lo, spread)):
        return Regime2Decision(pos, stress, "bands_nan_hold" if pos != 0 else "bands_nan")

    # Stress Gate: flatten open books first, then keep flat (no new opens).
    if stress and pos != 0:
        return Regime2Decision(0, True, "stress_force_flat")

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
        if pos == -1 and int(hold_days) >= int(max_hold_reverse):
            if not (exit_lo <= spread <= exit_hi):
                reason = "quantile_max_hold_reverse"
        return Regime2Decision(0, stress, reason)

    return Regime2Decision(pos, stress, "quantile_hold")
