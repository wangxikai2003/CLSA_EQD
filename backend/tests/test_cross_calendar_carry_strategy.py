"""Unit tests for listed rolling Cross Calendar Carry helpers."""
from __future__ import annotations

import pandas as pd

from engine.cross_calendar_carry_strategy import (
    _pick_short_long_ym,
    third_friday,
    trading_days_to_expiry,
)


def test_third_friday_known():
    # 2024-03 third Friday = 2024-03-15
    assert str(third_friday("2403").date()) == "2024-03-15"


def test_trading_days_to_expiry():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    assert trading_days_to_expiry(dates, "2024-01-02", "2024-01-08") == 4


def test_pick_short_long_prefers_quarter():
    rows = [
        {"contract_ym": "2401", "close": 100.0},
        {"contract_ym": "2402", "close": 99.0},
        {"contract_ym": "2403", "close": 98.0},
        {"contract_ym": "2406", "close": 96.0},
    ]
    day = pd.DataFrame(rows)
    # 2024-01-02: Jan still listed with DTE to third Friday Jan 19
    pair = _pick_short_long_ym(
        day,
        day,
        "2024-01-02",
        short_min_dte=5,
        long_min_gap_days=40,
        quarter_months=(3, 6, 9, 12),
    )
    assert pair is not None
    short_ym, long_ym = pair
    assert short_ym == "2401"
    assert long_ym == "2403"
