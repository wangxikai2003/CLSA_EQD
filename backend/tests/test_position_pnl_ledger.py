"""Position & P&L ledger identities, expiry settlement, and margin estimator."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.ledger_aggregator import build_position_ledger, portfolio_pnl_by_day
from engine.margin_estimator import estimate_margin

_VOL_DB = ROOT / "data" / "cffex_vol.sqlite"
needs_vol_db = pytest.mark.skipif(
    not (_VOL_DB.exists() and _VOL_DB.stat().st_size > 0),
    reason="data/cffex_vol.sqlite is local-only",
)

M = 100.0


def _opt(ticker, qty, close, role="SHORT_LEG", strike=4000.0, cp="C", expiry="2024-01-19"):
    return {
        "ticker": ticker,
        "asset_type": "OPTION",
        "leg_role": role,
        "multiplier": M,
        "qty": qty,
        "close": close,
        "strike": strike,
        "cp": cp,
        "expiry": expiry,
    }


def test_unique_key_and_cross_day_continuity():
    ticker = "IO2401-C-4000.CFE"
    days = [
        {
            "trade_date": "2024-01-02",
            "spot": 4000.0,
            "prev": [],
            "trades": [
                {
                    "ticker": ticker,
                    "asset_type": "OPTION",
                    "leg_role": "SHORT_LEG",
                    "action_type": "OPEN_NEW_VINTAGE",
                    "vintage_id": "v1",
                    "qty": -2.0,
                    "trade_price": 80.0,
                    "multiplier": M,
                    "strike": 4000.0,
                    "cp": "C",
                    "expiry": "2024-01-19",
                }
            ],
            "marks": {ticker: {"close": 80.0, "delta": -0.5, "gamma": 0.01, "vega": 2.0, "theta": -1.0}},
            "gross_pnl": 0.0,
        },
        {
            "trade_date": "2024-01-03",
            "spot": 4010.0,
            "prev": [_opt(ticker, -2.0, 80.0)],
            "trades": [],
            "marks": {ticker: {"close": 70.0, "delta": -0.45, "gamma": 0.01, "vega": 2.0, "theta": -1.0}},
            "gross_pnl": -2.0 * (70.0 - 80.0) * M,
        },
        {
            "trade_date": "2024-01-04",
            "spot": 3990.0,
            "prev": [_opt(ticker, -2.0, 70.0)],
            "trades": [
                {
                    "ticker": ticker,
                    "asset_type": "OPTION",
                    "leg_role": "SHORT_LEG",
                    "action_type": "CLOSE_ROLL",
                    "vintage_id": "v1",
                    "qty": 2.0,
                    "trade_price": 60.0,
                    "multiplier": M,
                    "strike": 4000.0,
                    "cp": "C",
                },
                {
                    "ticker": ticker,
                    "asset_type": "OPTION",
                    "leg_role": "SHORT_LEG",
                    "action_type": "OPEN_NEW_VINTAGE",
                    "vintage_id": "v2",
                    "qty": -1.0,
                    "trade_price": 60.0,
                    "multiplier": M,
                    "strike": 4000.0,
                    "cp": "C",
                },
            ],
            "marks": {ticker: {"close": 60.0, "delta": -0.4}},
            "gross_pnl": -2.0 * (60.0 - 70.0) * M,
        },
    ]
    ledger, blotter = build_position_ledger(days)
    assert ledger.duplicated(["trade_date", "ticker"]).sum() == 0
    by_day = {d: g.set_index("ticker") for d, g in ledger.groupby("trade_date")}
    assert by_day["2024-01-03"].loc[ticker, "prev_pos"] == by_day["2024-01-02"].loc[ticker, "curr_pos"]
    assert by_day["2024-01-04"].loc[ticker, "prev_pos"] == by_day["2024-01-03"].loc[ticker, "curr_pos"]
    assert float(by_day["2024-01-04"].loc[ticker, "curr_pos"]) == -1.0
    assert float(by_day["2024-01-04"].loc[ticker, "trade_qty"]) == 1.0


def test_accounting_identity_and_blotter_qty():
    ticker = "IF2401.CFE"
    days = [
        {
            "trade_date": "2024-01-02",
            "spot": 4000.0,
            "mark_futures": True,
            "prev": [],
            "trades": [
                {
                    "ticker": ticker,
                    "asset_type": "FUTURE",
                    "leg_role": "HEDGE_FUT",
                    "action_type": "EOD_DELTA_HEDGE",
                    "vintage_id": "PORTFOLIO_HEDGE",
                    "qty": 2.0,
                    "trade_price": 10.0,
                    "multiplier": 300.0,
                },
                {
                    "ticker": ticker,
                    "asset_type": "FUTURE",
                    "leg_role": "HEDGE_FUT",
                    "action_type": "EOD_DELTA_HEDGE",
                    "vintage_id": "PORTFOLIO_HEDGE",
                    "qty": -1.0,
                    "trade_price": 12.0,
                    "multiplier": 300.0,
                },
            ],
            "marks": {ticker: {"close": 11.0, "asset_type": "FUTURE"}},
            "gross_pnl": None,
        }
    ]
    # Net +1 at vwap (2*10 + 1*12) / 3, close 11.
    ledger, blotter = build_position_ledger(days)
    row = ledger.iloc[0]
    vwap = (2 * 10.0 + 1 * 12.0) / 3.0
    assert row["trade_qty"] == pytest.approx(1.0)
    assert row["avg_trade_price"] == pytest.approx(vwap)
    assert row["trading_pnl"] == pytest.approx(1.0 * (11.0 - vwap) * 300.0)
    assert row["holding_pnl"] == pytest.approx(0.0)
    lhs = float(row["holding_pnl"] + row["trading_pnl"])
    rhs = (1.0 * 11.0 - 0.0) * 300.0 - 1.0 * vwap * 300.0
    assert lhs == pytest.approx(rhs)
    assert abs(lhs - float(row["total_pnl"])) < 1e-6
    assert float(blotter["qty"].sum()) == pytest.approx(float(row["trade_qty"]))
    # Flat book the next day keeps continuity and drops the dead name afterwards.
    nxt, blot2 = build_position_ledger(
        [
            days[0],
            {
                "trade_date": "2024-01-03",
                "spot": 4010.0,
                "prev": [
                    {
                        "ticker": ticker,
                        "asset_type": "FUTURE",
                        "leg_role": "HEDGE_FUT",
                        "multiplier": 300.0,
                        "qty": 1.0,
                        "close": 11.0,
                    }
                ],
                "trades": [],
                "marks": {ticker: {"close": 13.0, "asset_type": "FUTURE"}},
            },
        ]
    )
    assert float(nxt.loc[nxt["trade_date"] == "2024-01-03", "prev_pos"].iloc[0]) == 1.0
    assert float(nxt.loc[nxt["trade_date"] == "2024-01-03", "holding_pnl"].iloc[0]) == pytest.approx(2.0 * 300.0)
    assert blot2.loc[blot2["trade_date"] == "2024-01-03"].empty


def test_expiry_settlement_clears_next_day():
    ticker = "IO2401-C-3900.CFE"
    days = [
        {
            "trade_date": "2024-01-18",
            "spot": 4000.0,
            "prev": [_opt(ticker, -3.0, 20.0, strike=3900.0, cp="C", expiry="2024-01-19")],
            "trades": [],
            "marks": {ticker: {"close": 30.0, "delta": -0.8}},
        },
        {
            "trade_date": "2024-01-19",
            "spot": 4050.0,
            "prev": [_opt(ticker, -3.0, 30.0, strike=3900.0, cp="C", expiry="2024-01-19")],
            "trades": [
                {
                    "ticker": ticker,
                    "asset_type": "OPTION",
                    "leg_role": "SHORT_LEG",
                    "action_type": "EXPIRY_SETTLE",
                    "vintage_id": "v1",
                    "qty": 3.0,
                    "trade_price": 999.0,
                    "multiplier": M,
                    "strike": 3900.0,
                    "cp": "C",
                    "expiry": "2024-01-19",
                }
            ],
            "marks": {},
        },
        {
            "trade_date": "2024-01-22",
            "spot": 4060.0,
            "prev": [],
            "trades": [],
            "marks": {},
        },
    ]
    ledger, blotter = build_position_ledger(days)
    exp = ledger[ledger["trade_date"] == "2024-01-19"].iloc[0]
    settle = max(4050.0 - 3900.0, 0.0)
    assert exp["curr_pos"] == pytest.approx(0.0)
    assert exp["close_price"] == pytest.approx(settle)
    assert exp["trading_pnl"] == pytest.approx(0.0)
    assert exp["holding_pnl"] == pytest.approx(-3.0 * (settle - 30.0) * M)
    assert exp["total_pnl"] == pytest.approx(exp["holding_pnl"])
    assert "2024-01-22" not in set(ledger["trade_date"])
    assert float(blotter.loc[blotter["action_type"] == "EXPIRY_SETTLE", "trade_price"].iloc[0]) == pytest.approx(settle)
    got = portfolio_pnl_by_day(ledger)
    assert abs(got["2024-01-19"] - float(exp["total_pnl"])) < 1e-6


def test_margin_estimator_does_not_mutate_ledger():
    ledger = pd.DataFrame(
        [
            {
                "trade_date": "2024-01-02",
                "ticker": "IO2401-C-4200.CFE",
                "asset_type": "OPTION",
                "leg_role": "SHORT_LEG",
                "multiplier": 100.0,
                "close_price": 80.0,
                "curr_pos": -2.0,
                "strike": 4200.0,
                "cp": "C",
                "spot": 4000.0,
            },
            {
                "trade_date": "2024-01-02",
                "ticker": "IO2401-C-3800.CFE",
                "asset_type": "OPTION",
                "leg_role": "SHORT_LEG",
                "multiplier": 100.0,
                "close_price": 80.0,
                "curr_pos": -2.0,
                "strike": 3800.0,
                "cp": "C",
                "spot": 4000.0,
            },
            {
                "trade_date": "2024-01-02",
                "ticker": "IO2401-P-3800.CFE",
                "asset_type": "OPTION",
                "leg_role": "LONG_LEG",
                "multiplier": 100.0,
                "close_price": 10.0,
                "curr_pos": 4.0,
                "strike": 3800.0,
                "cp": "P",
                "spot": 4000.0,
            },
            {
                "trade_date": "2024-01-02",
                "ticker": "IF2401.CFE",
                "asset_type": "FUTURE",
                "leg_role": "HEDGE_FUT",
                "multiplier": 300.0,
                "close_price": 4000.0,
                "curr_pos": 3.0,
                "strike": None,
                "cp": None,
                "spot": 4000.0,
            },
            {
                "trade_date": "2024-01-03",
                "ticker": "AU2402C500.SHF",
                "asset_type": "OPTION",
                "leg_role": "SHORT_LEG",
                "multiplier": 1000.0,
                "close_price": 8.0,
                "curr_pos": -1.0,
                "strike": 500.0,
                "cp": "C",
                "spot": 480.0,
            },
        ]
    )
    before = ledger.copy(deep=True)
    nav = pd.Series({"2024-01-02": 1_000_000.0, "2024-01-03": 1_000_000.0})
    out = estimate_margin(ledger, nav=nav)
    pd.testing.assert_frame_equal(ledger, before)
    day = out[out["trade_date"] == "2024-01-02"].iloc[0]
    # OTM call K=4200: extra = max(0.12*4000 - 200, 0.07*4000) = 280; per lot 80+280
    otm = 2 * 100 * (80.0 + 280.0)
    # ITM call K=3800: extra = max(480 - 0, 280) = 480; per lot 80+480
    itm = 2 * 100 * (80.0 + 480.0)
    fut = 3 * 300 * 4000.0 * 0.12
    assert day["option_short_margin"] == pytest.approx(otm + itm)
    assert day["futures_margin"] == pytest.approx(fut)
    assert day["total_margin_required"] == pytest.approx(otm + itm + fut)
    assert day["margin_to_nav_ratio"] == pytest.approx((otm + itm + fut) / 1_000_000.0)
    assert day["margin_note"] is None or (isinstance(day["margin_note"], float) and pd.isna(day["margin_note"]))
    blocked = out[out["trade_date"] == "2024-01-03"].iloc[0]
    assert pd.isna(blocked["option_short_margin"])
    assert blocked["margin_note"] == "non-CFFEX option margin not estimated"
    assert blocked["futures_margin"] == pytest.approx(0.0)


@needs_vol_db
def test_engine_ledger_matches_gross_pnl_on_short_window():
    from engine.csi300_vol_rv_strategy import Csi300VolRvEngine
    from engine.ledger_aggregator import build_position_ledger, portfolio_pnl_by_day

    eng = Csi300VolRvEngine(
        universe="300",
        tilt="balanced",
        run_ablation=False,
        start_date="2024-01-02",
        end_date="2024-06-28",
        option_fee_pct=0.0,
        option_slippage_pct=0.0,
        futures_fee_pct=0.0,
        futures_slippage_pct=0.0,
    )
    payload = eng.run()
    assert payload["ledger"]["available"] is True
    assert payload["ledger"]["max_abs_gap"] < 1e-4
    ledger, blotter = build_position_ledger(eng._ledger_days)
    by_day = portfolio_pnl_by_day(ledger)
    for day in eng._ledger_days:
        gross = float(day["gross_pnl"])
        got = by_day.get(str(day["trade_date"])[:10], 0.0)
        assert abs(got - gross) < 1e-4
        # Fees are zero, so gross equals the booked net.
    assert "position_pnl_ledger" in payload
    assert payload["ledger"]["n_rows"] == len(payload["position_pnl_ledger"])
    # Blotter qty matches ledger trade qty per day/ticker.
    if not blotter.empty:
        bsum = blotter.groupby(["trade_date", "ticker"])["qty"].sum()
        for rec in ledger.itertuples(index=False):
            key = (rec.trade_date, rec.ticker)
            bq = float(bsum.get(key, 0.0))
            assert abs(bq - float(rec.trade_qty)) < 1e-6


def test_put_cached_strips_ledger_into_side_tables(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    import importlib

    import data_paths as dp
    import api.v1.qis_results_store as qrs

    importlib.reload(dp)
    importlib.reload(qrs)
    monkeypatch.setattr(qrs, "_dp_qis", lambda: tmp_path / "qis_results.sqlite")
    payload = {
        "summary": {"final_nav_multiple": 1.0},
        "ledger": {"available": True, "start": "2024-01-02", "end": "2024-01-02", "n_rows": 1},
        "position_pnl_ledger": [
            {
                "trade_date": "2024-01-02",
                "ticker": "IF2401.CFE",
                "asset_type": "FUTURE",
                "leg_role": "HEDGE_FUT",
                "multiplier": 300.0,
                "prev_close": 4000.0,
                "close_price": 4010.0,
                "prev_pos": 1.0,
                "trade_qty": 0.0,
                "avg_trade_price": None,
                "curr_pos": 1.0,
                "holding_pnl": 3000.0,
                "trading_pnl": 0.0,
                "total_pnl": 3000.0,
                "eod_notional": 1_203_000.0,
                "eod_delta": 300.0,
                "eod_gamma": 0.0,
                "eod_vega": 0.0,
                "eod_theta": 0.0,
                "strike": None,
                "cp": None,
                "expiry": None,
                "spot": 4000.0,
            }
        ],
        "trade_blotter": [],
    }
    qrs.put_cached(
        "csi300_vol_rv",
        252,
        1e8,
        "src",
        payload,
        tenor_pair="vrv300",
        side_mode="both",
        spread_mode="iv",
        signal_mode="led-test",
    )
    assert "position_pnl_ledger" not in payload
    assert payload["ledger"]["n_rows"] == 1
    hit = qrs.get_cached(
        "csi300_vol_rv", 252, 1e8, "src",
        tenor_pair="vrv300", side_mode="both", spread_mode="iv", signal_mode="led-test",
    )
    assert hit is not None
    assert "position_pnl_ledger" not in hit
    day = qrs.ledger_day("csi300_vol_rv", 252, 1e8, "vrv300", "both", "iv", "led-test", "2024-01-02")
    assert len(day["rows"]) == 1
    assert day["rows"][0]["total_pnl"] == pytest.approx(3000.0)
    agg = qrs.ledger_aggregate("csi300_vol_rv", 252, 1e8, "vrv300", "both", "iv", "led-test")
    assert agg[0]["ticker"] == "IF2401.CFE"
