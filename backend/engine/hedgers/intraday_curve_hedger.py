"""Stub for GS Step 4 (intraday short delta) + Step 5 (curve adjustment).

Wired for polymorphism today; fills in once minute/tick bars land.
"""
from __future__ import annotations

from typing import Optional

from engine.hedgers.base_hedger import BaseDeltaHedger, HedgeContext, HedgeDecision
from engine.hedgers.eod_close_hedger import EodCloseDeltaHedger


class IntradayCurveAdjustedHedger(BaseDeltaHedger):
    """Future high-frequency / basis-adjusted hedge.

    Parameters (reserved):
      - ``intraday_freq_minutes``: bar cadence for short-leg rebalances.
      - ``delta_band``: intraday deadband before trading.
      - ``enable_curve_adjustment``: apply sticky-delta / implied-carry beta.
      - ``basis_beta``: sensitivity of effective delta to the IF basis curve.
    """

    def __init__(
        self,
        *,
        long_delta_threshold: float = 0.05,
        m_opt: float = 100.0,
        m_fut: float = 300.0,
        intraday_freq_minutes: int = 30,
        delta_band: float = 0.02,
        enable_curve_adjustment: bool = True,
        basis_beta: float = 0.0,
        short_fut_key: str = "IF_short",
        long_fut_key: str = "IF_long",
    ) -> None:
        self.intraday_freq_minutes = int(intraday_freq_minutes)
        self.delta_band = float(delta_band)
        self.enable_curve_adjustment = bool(enable_curve_adjustment)
        self.basis_beta = float(basis_beta)
        self._eod = EodCloseDeltaHedger(
            long_delta_threshold=long_delta_threshold,
            m_opt=m_opt,
            m_fut=m_fut,
            short_fut_key=short_fut_key,
            long_fut_key=long_fut_key,
        )

    def compute_eod_hedge(self, ctx: HedgeContext) -> HedgeDecision:
        # Until curve logic lands, fall back to the EOD Close rules.
        dec = self._eod.compute_eod_hedge(ctx)
        if self.enable_curve_adjustment and ctx.intraday_bars is None:
            # Keep EOD decision but annotate that curve adj is idle.
            return HedgeDecision(
                target_futures_positions=dec.target_futures_positions,
                delta_before_hedge=dec.delta_before_hedge,
                delta_after_hedge=dec.delta_after_hedge,
                curve_adjustment_delta=0.0,
                hedge_reason=f"{dec.hedge_reason}+curve_idle",
                short_delta=dec.short_delta,
                long_delta=dec.long_delta,
            )
        return dec

    def on_intraday_bar(self, ctx: HedgeContext) -> Optional[HedgeDecision]:
        if ctx.intraday_bars is None:
            # Contract: no intraday dataset → no trade, explicit reason via None.
            # Callers that need a hard failure should call ``require_intraday``.
            return None
        raise NotImplementedError("Awaiting intraday bar dataset")

    def require_intraday(self, ctx: HedgeContext) -> HedgeDecision:
        """Explicit activation path once minute bars exist."""
        if ctx.intraday_bars is None:
            raise NotImplementedError("Awaiting intraday bar dataset")
        raise NotImplementedError("Awaiting intraday bar dataset")

    def compute_curve_adjusted_delta(self, ctx: HedgeContext) -> float:
        base = self._eod.compute_curve_adjusted_delta(ctx)
        if not self.enable_curve_adjustment:
            return base
        if ctx.intraday_bars is None and not ctx.market_state.get("implied_carry_curve"):
            # Graceful idle: return raw delta until carry curve is supplied.
            return base
        raise NotImplementedError("Awaiting intraday bar dataset")
