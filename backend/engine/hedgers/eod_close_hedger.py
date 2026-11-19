"""EOD Close IF hedge: short book flat delta; long book threshold band."""
from __future__ import annotations

from typing import Dict, Optional

from engine.hedgers.base_hedger import (
    BaseDeltaHedger,
    HedgeContext,
    HedgeDecision,
    OptionLot,
)


def _sum_delta(lots: list[OptionLot], leg: Optional[str] = None) -> float:
    total = 0.0
    for lot in lots:
        if leg is not None and lot.leg != leg:
            continue
        total += float(lot.n) * float(lot.delta)
    return total


class EodCloseDeltaHedger(BaseDeltaHedger):
    """GS Step 1/2 EOD hedge on CFFEX IF.

    Short ATM strangle: flatten net short delta every close.
    Long OTM strangle: rebalance only when |net_long_delta| exceeds
    ``long_delta_threshold`` (absolute option-delta units after n·δ).
    """

    def __init__(
        self,
        *,
        long_delta_threshold: float = 0.05,
        m_opt: float = 100.0,
        m_fut: float = 300.0,
        short_fut_key: str = "IF_short",
        long_fut_key: str = "IF_long",
        flatten_short: bool = True,
    ) -> None:
        self.long_delta_threshold = float(long_delta_threshold)
        self.m_opt = float(m_opt)
        self.m_fut = float(m_fut)
        self.short_fut_key = short_fut_key
        self.long_fut_key = long_fut_key
        self.flatten_short = bool(flatten_short)

    def _opt_to_fut(self, opt_delta_pts: float) -> float:
        """Map option-point delta sum to IF contracts (signed)."""
        if self.m_fut <= 0:
            return 0.0
        return -opt_delta_pts * (self.m_opt / self.m_fut)

    def compute_curve_adjusted_delta(self, ctx: HedgeContext) -> float:
        # EOD path: no curve adjustment.
        return _sum_delta(ctx.active_lots)

    def on_intraday_bar(self, ctx: HedgeContext) -> Optional[HedgeDecision]:
        return None

    def compute_eod_hedge(self, ctx: HedgeContext) -> HedgeDecision:
        thr = float(ctx.params.get("long_delta_threshold", self.long_delta_threshold))
        short_d = _sum_delta(ctx.active_lots, "short")
        long_d = _sum_delta(ctx.active_lots, "long")
        before = short_d + long_d

        targets: Dict[str, float] = dict(ctx.current_futures_positions)
        reasons: list[str] = []

        if self.flatten_short:
            targets[self.short_fut_key] = self._opt_to_fut(short_d)
            reasons.append("short_flat")
        else:
            targets.setdefault(self.short_fut_key, float(ctx.current_futures_positions.get(self.short_fut_key, 0.0)))

        prev_long = float(ctx.current_futures_positions.get(self.long_fut_key, 0.0))
        if abs(long_d) > thr + 1e-12:
            targets[self.long_fut_key] = self._opt_to_fut(long_d)
            reasons.append("long_threshold_breach")
        else:
            targets[self.long_fut_key] = prev_long
            reasons.append("long_hold_band")

        # Residual option delta after intended futures hedge (in option points).
        fut_short = float(targets.get(self.short_fut_key, 0.0))
        fut_long = float(targets.get(self.long_fut_key, 0.0))
        fut_opt_equiv = (fut_short + fut_long) * (self.m_fut / self.m_opt) if self.m_opt else 0.0
        after = before + fut_opt_equiv

        return HedgeDecision(
            target_futures_positions=targets,
            delta_before_hedge=before,
            delta_after_hedge=after,
            curve_adjustment_delta=0.0,
            hedge_reason="+".join(reasons),
            short_delta=short_d,
            long_delta=long_d,
        )
