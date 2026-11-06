"""Acceptance checks for Equity 3-6 Calendar Vol strategy."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.black76 import black76_greeks
from engine.calendar_vol_strategy import (
    CalendarVolBacktestEngine,
    T_3M,
    UNIVERSES,
    rolling_quantile_bands,
    rolling_zscore,
    run_calendar_vol,
    total_var_interp_iv,
)


def test_total_var_interp_midpoint():
    ttms = np.array([0.2, 0.4])
    iv = 0.2
    watms = iv * iv * ttms
    out = total_var_interp_iv(ttms, watms, 0.3)
    assert abs(out - iv) < 1e-10


def test_rolling_zscore_no_lookahead():
    rng = np.random.default_rng(0)
    s = pd.Series(rng.normal(0, 0.01, size=400))
    z = rolling_zscore(s, window=252)
    t = 300
    z_before = float(z.iloc[t])
    s2 = s.copy()
    s2.iloc[t + 1 :] = 99.0
    z2 = rolling_zscore(s2, window=252)
    assert abs(float(z2.iloc[t]) - z_before) < 1e-12


def test_rolling_quantile_bands_no_lookahead():
    rng = np.random.default_rng(2)
    s = pd.Series(rng.normal(0, 0.01, size=400))
    b = rolling_quantile_bands(s, window=252, enter_q=0.9, exit_lo_q=0.4, exit_hi_q=0.6)
    t = 300
    hi_before = float(b["enter_hi"].iloc[t])
    s2 = s.copy()
    s2.iloc[t:] = 99.0  # corrupt today and future
    b2 = rolling_quantile_bands(s2, window=252, enter_q=0.9, exit_lo_q=0.4, exit_hi_q=0.6)
    # band at t uses only t-1 history → unchanged even if s[t] changed
    assert abs(float(b2["enter_hi"].iloc[t]) - hi_before) < 1e-12
    assert float(b["enter_hi"].iloc[t]) > float(b["enter_lo"].iloc[t])
    assert float(b["exit_hi"].iloc[t]) > float(b["exit_lo"].iloc[t])


def test_leg_weights_sum_to_one():
    eng = CalendarVolBacktestEngine(universe="300")
    rows = []
    for exp, ttm, K in (("2025-03-21", 0.2, 4000.0), ("2025-09-19", 0.5, 4000.0)):
        for cp in ("C", "P"):
            g = black76_greeks(4000.0, K, ttm, 0.2, 0.02, cp)
            rows.append(
                {
                    "trade_date": "2025-01-02",
                    "wind_code": f"IO_{exp}_{cp}",
                    "expiry": exp,
                    "ttm": ttm,
                    "strike": K,
                    "cp": cp,
                    "forward": 4000.0,
                    "mid_price": g["price"],
                    "iv": 0.2,
                    "r": 0.02,
                }
            )
    day = pd.DataFrame(rows)
    pos, _, _ = eng.build_synthetic_leg(day, 4000.0, T_3M, 1.0)
    assert len(pos) == 4
    assert abs(abs(pos[0].n) + abs(pos[2].n) - 1.0) < 1e-9


def test_black76_call_put_vega_equal():
    c = black76_greeks(100, 100, 0.5, 0.2, 0.02, "C")
    p = black76_greeks(100, 100, 0.5, 0.2, 0.02, "P")
    assert abs(c["vega"] - p["vega"]) < 1e-10


@pytest.mark.parametrize("universe", list(UNIVERSES.keys()))
def test_full_backtest_invariants(universe):
    payload = run_calendar_vol(universe, initial_nav=100_000_000.0, z_window=252)
    assert payload["strategy_id"] == UNIVERSES[universe]["strategy_id"]
    assert payload["params"]["enter_q"] == 0.9
    assert payload["params"]["first_open"] < payload["params"]["perf_start"]
    sig = payload["signal"]
    assert "enter_hi" in sig and len(sig["enter_hi"]) == len(sig["dates"])
    # some warm bands exist
    assert any(x is not None for x in sig["enter_hi"])

    c = payload["pnl_contribution"]
    total = c["total_pnl"]
    parts = [
        c.get("vega_pct") or 0,
        c.get("theta_pct") or 0,
        c.get("gamma_pct") or 0,
        c.get("basis_pct") or 0,
        c.get("net_delta_pct") or 0,
        c.get("residual_pct") or 0,
    ]
    if abs(total) > 1:
        assert abs(sum(parts) - 100.0) < 1.0
    assert abs(c.get("net_delta_pct") or 0) < 1.0

    de = payload["pnl_decomposition"]
    s = (
        de["vega_pnl"][-1]
        + de["theta_pnl"][-1]
        + de["gamma_pnl"][-1]
        + de["basis_pnl"][-1]
        + de["net_delta_pnl"][-1]
        + de["residual_pnl"][-1]
    )
    assert abs(s - total) < 1.0
