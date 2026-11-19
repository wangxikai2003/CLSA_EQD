"""Hedger strategy-pattern contracts for CSI 300 Vol RV."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.hedgers import (
    BaseDeltaHedger,
    EodCloseDeltaHedger,
    HedgeContext,
    IntradayCurveAdjustedHedger,
    OptionLot,
    get_hedger,
)


def _lot(leg: str, n: float, delta: float, vintage: str = "v0") -> OptionLot:
    return OptionLot(
        wind_code=f"{leg}_{n}",
        expiry="2025-03-21",
        strike=4000.0,
        cp="C",
        n=n,
        delta=delta,
        gamma=0.0,
        vega=0.0,
        theta=0.0,
        mid=10.0,
        iv=0.2,
        ttm=0.2,
        forward=4000.0,
        leg=leg,
        vintage_id=vintage,
    )


def test_eod_threshold_hold_vs_breach():
    hedger = EodCloseDeltaHedger(long_delta_threshold=0.05, m_opt=100.0, m_fut=300.0)
    # Long net delta inside band → keep prior long futures.
    ctx = HedgeContext(
        timestamp="2024-01-02",
        active_lots=[
            _lot("short", -1.0, 0.50),
            _lot("short", -1.0, -0.50),
            _lot("long", 1.0, 0.03),
        ],
        current_futures_positions={"IF_short": 0.0, "IF_long": 1.25},
        params={"long_delta_threshold": 0.05},
    )
    hold = hedger.compute_eod_hedge(ctx)
    assert "long_hold_band" in hold.hedge_reason
    assert hold.target_futures_positions["IF_long"] == pytest.approx(1.25)
    # Short flattened: short delta ~0 → futures ~0
    assert hold.target_futures_positions["IF_short"] == pytest.approx(0.0)

    # Breach: long delta 0.10 > 0.05 → rebalance
    ctx.active_lots = [
        _lot("short", -1.0, 0.50),
        _lot("short", -1.0, -0.50),
        _lot("long", 1.0, 0.10),
    ]
    breach = hedger.compute_eod_hedge(ctx)
    assert "long_threshold_breach" in breach.hedge_reason
    # n*δ = 0.10 → IF = -0.10 * 100/300
    assert breach.target_futures_positions["IF_long"] == pytest.approx(-0.10 * 100.0 / 300.0)


def test_factory_polymorphism():
    eod = get_hedger("eod_close")
    intra = get_hedger("intraday_curve")
    assert isinstance(eod, BaseDeltaHedger)
    assert isinstance(eod, EodCloseDeltaHedger)
    assert isinstance(intra, BaseDeltaHedger)
    assert isinstance(intra, IntradayCurveAdjustedHedger)
    with pytest.raises(ValueError):
        get_hedger("nope")


def test_intraday_stub_contract():
    stub = IntradayCurveAdjustedHedger(enable_curve_adjustment=True)
    ctx = HedgeContext(timestamp="2024-01-02", active_lots=[], intraday_bars=None)
    assert stub.on_intraday_bar(ctx) is None
    with pytest.raises(NotImplementedError, match="Awaiting intraday bar dataset"):
        stub.require_intraday(ctx)
    # Without bars / carry curve, curve-adjusted delta idles to raw.
    assert stub.compute_curve_adjusted_delta(ctx) == pytest.approx(0.0)
    dec = stub.compute_eod_hedge(ctx)
    assert "curve_idle" in dec.hedge_reason
