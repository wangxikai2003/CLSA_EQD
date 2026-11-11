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
    SIDE_MODES,
    SPREAD_MODES,
    T_3M,
    TENOR_PAIRS,
    UNIVERSES,
    compute_spread,
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
@pytest.mark.parametrize("tenor_pair", list(TENOR_PAIRS.keys()))
def test_full_backtest_invariants(universe, tenor_pair):
    payload = run_calendar_vol(
        universe, initial_nav=100_000_000.0, z_window=252, tenor_pair=tenor_pair
    )
    assert payload["strategy_id"] == UNIVERSES[universe]["strategy_id"]
    assert payload["params"]["tenor_pair"] == tenor_pair
    assert payload["params"]["enter_q"] == 0.9
    assert payload["params"]["first_open"] < payload["params"]["perf_start"]
    assert "t-1" in payload["params"]["signal_lag"]
    assert "close" in payload["params"]["price"].lower()
    assert "settle" in payload["params"]["price"].lower()  # "no settle fallback"
    sig = payload["signal"]
    assert "enter_hi" in sig and len(sig["enter_hi"]) == len(sig["dates"])
    assert "signal_next" in sig and len(sig["signal_next"]) == len(sig["dates"])
    # some warm bands exist
    assert any(x is not None for x in sig["enter_hi"])
    # t−1 signal → t execution: position[t] == signal_next[t−1] on the downsampled series
    # (exact check on full engine rows via a short re-run below)

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


def test_compute_spread_iv_vs_iv2():
    assert abs(compute_spread(0.20, 0.18, "iv") - 0.02) < 1e-12
    assert abs(compute_spread(0.20, 0.18, "iv2") - (0.04 - 0.0324)) < 1e-12
    assert set(SPREAD_MODES) == {"iv", "iv2"}


def test_300_36_nav_regression():
    """Locked 3M–6M CSI300 final NAV multiple (~1.198563) under default spread_mode=iv."""
    payload = run_calendar_vol(
        "300",
        initial_nav=100_000_000.0,
        z_window=252,
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
    )
    mult = float(payload["summary"]["final_nav_multiple"])
    assert abs(mult - 1.198563) < 5e-4, mult
    assert payload["params"]["spread_mode"] == "iv"


def test_spread_mode_iv2_differs_from_iv():
    """iv2 rebuilds Spread from CT-IV squares; NAV path differs from iv."""
    iv = run_calendar_vol(
        "300", tenor_pair="3M-6M", side_mode="both", spread_mode="iv"
    )
    iv2 = run_calendar_vol(
        "300", tenor_pair="3M-6M", side_mode="both", spread_mode="iv2"
    )
    assert iv2["params"]["spread_mode"] == "iv2"
    assert iv2["params"]["spread_formula"] == "IV_near² − IV_far²"
    assert float(iv2["summary"]["final_nav_multiple"]) != float(
        iv["summary"]["final_nav_multiple"]
    )
    # Same CT-IV days → spread series length equal; values differ.
    assert len(iv["signal"]["spread"]) == len(iv2["signal"]["spread"])
    diffs = [
        (a, b)
        for a, b in zip(iv["signal"]["spread"], iv2["signal"]["spread"])
        if a is not None and b is not None and abs(a - b) > 1e-12
    ]
    assert len(diffs) > 0


def test_side_mode_filters_positions():
    """long_only never holds −1; short_only never holds +1; both has both sides."""
    both = run_calendar_vol("300", tenor_pair="3M-6M", side_mode="both")
    long_o = run_calendar_vol("300", tenor_pair="3M-6M", side_mode="long_only")
    short_o = run_calendar_vol("300", tenor_pair="3M-6M", side_mode="short_only")
    assert both["params"]["side_mode"] == "both"
    assert set(SIDE_MODES) == {"both", "long_only", "short_only"}
    assert any(p == 1 for p in both["signal"]["position"])
    assert any(p == -1 for p in both["signal"]["position"])
    assert any(p == 1 for p in long_o["signal"]["position"])
    assert all(p != -1 for p in long_o["signal"]["position"])
    assert any(p == -1 for p in short_o["signal"]["position"])
    assert all(p != 1 for p in short_o["signal"]["position"])
    kinds_l = {m["kind"] for m in long_o["signal"].get("trade_markers") or []}
    kinds_s = {m["kind"] for m in short_o["signal"].get("trade_markers") or []}
    assert "short" not in kinds_l
    assert "long" not in kinds_s
    assert "close" in kinds_l or 0 in long_o["signal"]["position"]
    assert "close" in kinds_s or 0 in short_o["signal"]["position"]
    assert float(long_o["summary"]["final_nav_multiple"]) != float(
        both["summary"]["final_nav_multiple"]
    ) or float(short_o["summary"]["final_nav_multiple"]) != float(
        both["summary"]["final_nav_multiple"]
    )


def test_signal_lag_t_minus_1_execution():
    """Signal emitted on day t-1 must equal position executed on day t."""
    eng = CalendarVolBacktestEngine(universe="300", z_window=252)
    # Monkey-patch pack to capture raw rows via a thin wrapper
    rows_box: list = []
    orig_pack = eng._pack_payload

    def _capture(rows, first_open_i):
        rows_box.append(rows)
        return orig_pack(rows, first_open_i)

    eng._pack_payload = _capture  # type: ignore[method-assign]
    eng.run()
    rows = rows_box[0]
    assert rows[0]["position"] == 0  # day-0 observe only
    for i in range(1, len(rows)):
        assert rows[i]["position"] == rows[i - 1]["signal_next"]


def test_holdings_excel_export(tmp_path):
    from engine.calendar_vol_strategy import export_holdings_for_universe

    path = export_holdings_for_universe("300", path=tmp_path / "holdings_300.xlsx")
    assert path.exists() and path.stat().st_size > 1000
    df = pd.read_excel(path, sheet_name="holdings")
    assert {"trade_date", "wind_code", "direction", "qty", "price"}.issubset(df.columns)
    assert len(df) > 0
