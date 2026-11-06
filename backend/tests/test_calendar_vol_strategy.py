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


def test_z_window_parameter_changes_series():
    rng = np.random.default_rng(1)
    s = pd.Series(rng.normal(0, 0.01, size=400))
    z252 = rolling_zscore(s, 252)
    z126 = rolling_zscore(s, 126)
    # after both windows warm, values generally differ
    assert abs(float(z252.iloc[300]) - float(z126.iloc[300])) > 1e-8


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
    assert payload["params"]["z_window"] == 252
    assert payload["summary"]["start"] == payload["params"]["perf_start"]
    assert payload["summary"]["first_open"] < payload["summary"]["start"] or (
        payload["summary"]["first_open"] <= payload["summary"]["start"]
    )
    # first open is strictly before perf start (day after open)
    assert payload["params"]["first_open"] < payload["params"]["perf_start"]

    assert payload["params"]["max_abs_port_vega"] < 1e-4 or payload["params"]["max_abs_port_vega"] == 0.0

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

    nd = abs(c.get("net_delta_pct") or 0)
    assert nd < 1.0

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
