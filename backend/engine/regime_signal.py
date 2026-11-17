"""Regime-switching Fade / Momentum signal for Equity Calendar Vol (no look-ahead).

Fade mean-reversion with dual-day shock calm, plus Momentum breakout chase.

Momentum was previously exiting almost immediately because (1) exhaust used a
wide mid-band [q15,q85] / [q30,q70] that sits next to the accel entry zone, and
(2) adverse-shock used q20/q80. Exhaust is now the mean band [q40,q60]; adverse
shock uses extreme q10/q90; max Mom hold is 12d.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

import pandas as pd

# Level (Spread) quantiles — prior W days only.
LEVEL_QS = (0.01, 0.08, 0.10, 0.15, 0.25, 0.30, 0.40, 0.60, 0.70, 0.75, 0.85, 0.90, 0.92, 0.99)
# Shock (garch_z) quantiles — prior W days only.
SHOCK_QS = (0.10, 0.20, 0.25, 0.75, 0.80, 0.90)

DEFAULT_Z_WINDOW = 252
COOLDOWN_DAYS = 3
MAX_HOLD_FADE_REVERSE = 20
MAX_HOLD_MOMENTUM = 12
# Don't exhaust Mom on day-0/1 noise; require this many held days first.
MIN_HOLD_MOMENTUM_EXHAUST = 2

RegimeMode = Optional[str]  # None | "fade" | "momentum"


@dataclass
class RegimeState:
    position: int = 0
    hold_days: int = 0
    regime_mode: RegimeMode = None
    cooldown: int = 0
    entry_spread: Optional[float] = None


@dataclass
class RegimeDecision:
    signal: int
    regime_mode: RegimeMode
    cooldown: int
    reason: str
    entry_spread: Optional[float] = None


def q_key(q: float) -> str:
    pct = int(round(float(q) * 100))
    return f"q{pct:02d}" if pct < 100 else f"q{pct}"


def rolling_quantile_map(
    series: pd.Series,
    window: int = DEFAULT_Z_WINDOW,
    qs: Sequence[float] = LEVEL_QS,
) -> pd.DataFrame:
    """Time-varying quantiles from prior-window values only (shift(1) → no future leak)."""
    window = int(window)
    if window < 2:
        raise ValueError("window must be >= 2")
    q_list = [float(q) for q in qs]
    if not q_list or any(not (0.0 < q < 1.0) for q in q_list):
        raise ValueError("qs must be in (0, 1)")
    # Match calendar_vol_strategy.rolling_min_periods: half-window, floor 60.
    min_periods = max(60, int(math.ceil(window * 0.5)))
    s = series.astype(float)
    hist = s.shift(1)
    roll = hist.rolling(window, min_periods=min_periods)
    cols: Dict[str, pd.Series] = {}
    for q in q_list:
        cols[q_key(q)] = roll.quantile(q)
    return pd.DataFrame(cols, index=s.index)


def _finite(x: Optional[float]) -> bool:
    if x is None:
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _q(band: Mapping[str, float], key: str) -> float:
    return float(band[key])


def decide_regime_signal(
    state: RegimeState,
    S_t: float,
    z_t: float,
    level_q: Mapping[str, float],
    shock_q: Mapping[str, float],
    z_prev: Optional[float] = None,
) -> RegimeDecision:
    """Emit next-day target from post-trade state + today's close levels.

    Priority when flat: Momentum (A) before Fade (B).
    """
    pos = int(state.position)
    hold = int(state.hold_days)
    mode = state.regime_mode
    cd = max(0, int(state.cooldown))
    entry = state.entry_spread

    need_level = (
        "q01",
        "q08",
        "q10",
        "q15",
        "q25",
        "q30",
        "q40",
        "q60",
        "q70",
        "q75",
        "q85",
        "q90",
        "q92",
        "q99",
    )
    need_shock = ("q10", "q20", "q80", "q90")
    bands_ok = (
        _finite(S_t)
        and _finite(z_t)
        and all(k in level_q and _finite(level_q[k]) for k in need_level)
        and all(k in shock_q and _finite(shock_q[k]) for k in need_shock)
    )
    if not bands_ok:
        if pos == 0 and cd > 0:
            return RegimeDecision(0, None, cd - 1, "bands_nan_cooldown", None)
        return RegimeDecision(
            pos, mode if pos != 0 else None, cd, "bands_nan_hold", entry if pos != 0 else None
        )

    q01_l = _q(level_q, "q01")
    q08_l = _q(level_q, "q08")
    q10_l = _q(level_q, "q10")
    q25_l = _q(level_q, "q25")
    q40_l = _q(level_q, "q40")
    q60_l = _q(level_q, "q60")
    q75_l = _q(level_q, "q75")
    q90_l = _q(level_q, "q90")
    q92_l = _q(level_q, "q92")
    q99_l = _q(level_q, "q99")

    q10_s = _q(shock_q, "q10")
    q20_s = _q(shock_q, "q20")
    q80_s = _q(shock_q, "q80")
    q90_s = _q(shock_q, "q90")

    S = float(S_t)
    z = float(z_t)
    zp = float(z_prev) if _finite(z_prev) else float("nan")

    # ---- In position ----
    if pos != 0:
        if mode == "fade":
            if q40_l <= S <= q60_l:
                return RegimeDecision(0, None, COOLDOWN_DAYS, "fade_mean_revert_exit", None)
            if pos == 1 and z > q90_s and _finite(entry) and S > float(entry):
                return RegimeDecision(0, None, COOLDOWN_DAYS, "fade_crisis_stop", None)
            if pos == -1 and z < q10_s and _finite(entry) and S < float(entry):
                return RegimeDecision(0, None, COOLDOWN_DAYS, "fade_crisis_stop", None)
            if pos == -1 and hold >= MAX_HOLD_FADE_REVERSE:
                return RegimeDecision(0, None, COOLDOWN_DAYS, "fade_reverse_max_hold", None)
            return RegimeDecision(pos, "fade", 0, "fade_hold", entry)

        # Momentum exits: mean-band exhaust [q40,q60] after min hold, extreme adverse 10/90,
        # space 1/99, max 12d.
        if hold >= MAX_HOLD_MOMENTUM:
            return RegimeDecision(0, None, COOLDOWN_DAYS, "momentum_max_hold", None)
        if hold >= MIN_HOLD_MOMENTUM_EXHAUST and q40_l <= S <= q60_l:
            return RegimeDecision(0, None, COOLDOWN_DAYS, "momentum_exhaust", None)
        if pos == -1 and S > q99_l:
            return RegimeDecision(0, None, COOLDOWN_DAYS, "momentum_space_tp", None)
        if pos == 1 and S < q01_l:
            return RegimeDecision(0, None, COOLDOWN_DAYS, "momentum_space_tp", None)
        if pos == -1 and z < q10_s:
            return RegimeDecision(0, None, COOLDOWN_DAYS, "momentum_adverse_shock", None)
        if pos == 1 and z > q90_s:
            return RegimeDecision(0, None, COOLDOWN_DAYS, "momentum_adverse_shock", None)
        return RegimeDecision(
            pos, "momentum" if mode == "momentum" else mode, 0, "momentum_hold", entry
        )

    # ---- Flat: cooldown then Momentum then Fade ----
    if cd > 0:
        return RegimeDecision(0, None, cd - 1, "cooldown", None)

    # Mode A: Momentum
    if q75_l < S <= q92_l and z > q90_s:
        return RegimeDecision(-1, "momentum", 0, "momentum_long_breakout", S)
    if q08_l <= S < q25_l and z < q10_s:
        return RegimeDecision(1, "momentum", 0, "momentum_short_breakout", S)

    # Mode B: Fade — level extreme + dual-day shock calm (no fighting breakouts)
    if S > q90_l and z <= q80_s and _finite(zp) and zp <= q80_s:
        return RegimeDecision(1, "fade", 0, "fade_short_resistance", S)
    if S < q10_l and z >= q20_s and _finite(zp) and zp >= q20_s:
        return RegimeDecision(-1, "fade", 0, "fade_long_support", S)

    return RegimeDecision(0, None, 0, "flat", None)
