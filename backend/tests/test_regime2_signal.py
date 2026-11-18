"""Unit tests for Regime2 quantile + dual-day garch_z stress gate."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.regime2_signal import decide_regime2_signal, quantile_raw_signal


def test_quantile_raw_matches_enter_exit():
    assert quantile_raw_signal(0, 0, 1.5, 1.0, -1.0, 0.0) == 1
    assert quantile_raw_signal(0, 0, -1.5, 1.0, -1.0, 0.0) == -1
    assert quantile_raw_signal(0, 0, 0.0, 1.0, -1.0, 0.0) == 0
    # Long exits at or through q50, including an overshoot below it.
    assert quantile_raw_signal(1, 3, 0.0, 1.0, -1.0, 0.0) == 0
    assert quantile_raw_signal(1, 3, -0.4, 1.0, -1.0, 0.0) == 0
    # Still above the median: keep the long.
    assert quantile_raw_signal(1, 3, 0.8, 1.0, -1.0, 0.0) == 1
    # Short exits once spread rises to q50; max-hold still flats a short that has not.
    assert quantile_raw_signal(-1, 3, 0.0, 1.0, -1.0, 0.0) == 0
    assert quantile_raw_signal(-1, 3, -0.4, 1.0, -1.0, 0.0) == -1
    assert quantile_raw_signal(-1, 20, -0.4, 1.0, -1.0, 0.0) == 0


def test_calm_identical_to_quantile_entry():
    dec = decide_regime2_signal(
        position=0,
        hold_days=0,
        spread=1.5,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=0.0,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_prev=0.1,
        z_q10_prev=-1.2,
        z_q90_prev=1.2,
    )
    assert dec.signal == 1 and not dec.stress
    assert dec.reason == "quantile_enter_hi"


def test_single_day_extreme_does_not_block():
    """Only one extreme close → still allow quantile entry."""
    dec = decide_regime2_signal(
        position=0,
        hold_days=0,
        spread=1.5,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=2.0,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_prev=0.0,
        z_q10_prev=-1.2,
        z_q90_prev=1.2,
    )
    assert dec.signal == 1 and not dec.stress
    assert dec.reason == "quantile_enter_hi"


def test_dual_day_stress_blocks_new_open():
    dec = decide_regime2_signal(
        position=0,
        hold_days=0,
        spread=1.5,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=2.0,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_prev=2.5,
        z_q10_prev=-1.1,
        z_q90_prev=1.1,
    )
    assert dec.signal == 0 and dec.stress
    assert dec.reason == "stress_block_entry"


def test_stress_still_allows_quantile_exit():
    dec = decide_regime2_signal(
        position=1,
        hold_days=5,
        spread=0.0,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=2.0,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_prev=2.5,
        z_q10_prev=-1.1,
        z_q90_prev=1.1,
    )
    assert dec.signal == 0 and dec.stress
    assert dec.reason == "quantile_exit_q50"


def test_stress_allows_hold_outside_exit_band():
    dec = decide_regime2_signal(
        position=1,
        hold_days=5,
        spread=0.8,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=2.0,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_prev=2.5,
        z_q10_prev=-1.1,
        z_q90_prev=1.1,
    )
    assert dec.signal == 1 and dec.stress
    assert dec.reason == "quantile_hold"


def test_stress_streak_three_requires_three_extremes():
    hist2 = [
        (2.0, -1.2, 1.2),
        (2.1, -1.2, 1.2),
    ]
    dec2 = decide_regime2_signal(
        position=0,
        hold_days=0,
        spread=1.5,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=2.1,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_hist=hist2,
        stress_streak_days=3,
    )
    assert dec2.signal == 1 and not dec2.stress
    hist3 = [
        (2.0, -1.2, 1.2),
        (2.2, -1.2, 1.2),
        (2.1, -1.2, 1.2),
    ]
    dec3 = decide_regime2_signal(
        position=0,
        hold_days=0,
        spread=1.5,
        enter_hi=1.0,
        enter_lo=-1.0,
        q50=0.0,
        z_t=2.1,
        z_q10_t=-1.2,
        z_q90_t=1.2,
        z_hist=hist3,
        stress_streak_days=3,
    )
    assert dec3.signal == 0 and dec3.stress and dec3.reason == "stress_block_entry"
