"""Booster / Capped Booster / Airbag notes."""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import BaseStructuredProduct, PayoffResult


class BoosterAirbagProduct(BaseStructuredProduct):
    def __init__(
        self,
        participation: float = 1.5,
        cap: Optional[float] = None,
        airbag_level: float = 0.80,
        structure: str = "booster",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.participation = float(participation)
        self.cap = float(cap) if cap is not None else None
        self.airbag_level = float(airbag_level)
        self.structure = structure

    def evaluate_payoff(self, price_paths: np.ndarray, df: np.ndarray) -> PayoffResult:
        S0 = price_paths[:, 0, 0]
        ST = price_paths[:, 0, -1]
        perf = ST / np.maximum(S0, 1e-12)
        n = len(perf)
        ko_date = np.full(n, -1, dtype=int)
        ki_flag = perf < self.ki_barrier
        coupons = np.zeros(n)

        if self.structure in ("booster", "capped_booster"):
            upside = np.maximum(perf - self.strike, 0.0) * self.participation
            if self.structure == "capped_boaster" or self.structure == "capped_booster":
                if self.cap is not None:
                    upside = np.minimum(upside, self.cap - 1.0)
            # downside: full participation below strike
            downside = np.minimum(perf - self.strike, 0.0)
            redemption = 1.0 + upside + downside
            if self.cap is not None and self.structure == "capped_booster":
                redemption = np.minimum(redemption, self.cap)
        else:
            # airbag: above airbag_level → 1 + PR*(perf-1); below → perf/airbag_level
            above = perf >= self.airbag_level
            redemption = np.empty(n)
            redemption[above] = 1.0 + self.participation * (perf[above] - 1.0)
            redemption[~above] = perf[~above] / max(self.airbag_level, 1e-12)
            if self.cap is not None:
                redemption = np.minimum(redemption, self.cap)

        pv = redemption * df[-1] * self.notional
        return PayoffResult(
            pv=pv,
            ko_date=ko_date,
            ki_flag=ki_flag,
            coupon_cashflows=coupons,
            tags={"product": self.structure, "participation": self.participation},
        )
