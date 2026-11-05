"""Acceptance checks for Equity 3-6 Calendar Vol strategy."""
from __future__ import annotations

import math
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
    T_6M,
    rolling_zscore,
    total_var_interp_iv,
)


def test_total_var_interp_midpoint():
    ttms = np.array([0.2, 0.4])
    # constant IV 20% → w = iv^2 * t
    iv = 0.2
    watms = iv * iv * ttms
    mid = 0.3
    out = total_var_interp_iv(ttms, watms, mid)
    assert abs(out - iv) < 1e-10


def test_rolling_zscore_no_lookahead():
    rng = np.random.default_rng(0)
    s = pd.Series(rng.normal(0, 0.01, size=400))
    z = rolling_zscore(s, window=252)
    # mutate future spread — Z at t must be unchanged
    t = 300
    z_before = float(z.iloc[t])
    s2 = s.copy()
    s2.iloc[t + 1 :] = 99.0
    z2 = rolling_zscore(s2, window=252)
    assert abs(float(z2.iloc[t]) - z_before) < 1e-12


def test_leg_weights_sum_to_one():
    eng = CalendarVolBacktestEngine()
    # synthetic day with two expiries
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
    # force bracket around T_3M≈0.246
    pos, nu, _ = eng.build_synthetic_leg(day, 4000.0, T_3M, 1.0)
    assert len(pos) == 4
    # near weight + far weight encoded in |n| for unit N=1 → sum |n|/2 per expiry pair = 1 for calls+puts
    w_near = abs(pos[0].n)  # call near
    w_far = abs(pos[2].n)  # call far
    assert abs(w_near + w_far - 1.0) < 1e-9


def test_black76_call_put_vega_equal():
    c = black76_greeks(100, 100, 0.5, 0.2, 0.02, "C")
    p = black76_greeks(100, 100, 0.5, 0.2, 0.02, "P")
    assert abs(c["vega"] - p["vega"]) < 1e-10


@pytest.mark.slow
def test_full_backtest_invariants():
    eng = CalendarVolBacktestEngine(initial_nav=100_000_000.0)
    # run internal rows by monkeypatching pack — call run and check payload
    payload = eng.run()
    assert payload["strategy_id"] == "equity_3_6_calendar_vol_300"
    assert payload["params"]["max_abs_port_vega"] < 1e-4 or payload["params"]["max_abs_port_vega"] == 0.0

    # rebuild daily rows lightly: identity on last cumulative components
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

    # net delta contribution should be tiny vs total
    nd = abs(c.get("net_delta_pct") or 0)
    assert nd < 1.0  # <1% of total PnL

    de = payload["pnl_decomposition"]
    n = len(de["dates"])
    for i in range(n):
        s = (
            de["vega_pnl"][i]
            + de["theta_pnl"][i]
            + de["gamma_pnl"][i]
            + de["basis_pnl"][i]
            + de["net_delta_pnl"][i]
            + de["residual_pnl"][i]
        )
        # cumulative sum equals NAV-initial approximately at end
        if i == n - 1:
            assert abs(s - (payload["summary"]["final_nav"] - payload["params"]["initial_nav"])) < 1.0
