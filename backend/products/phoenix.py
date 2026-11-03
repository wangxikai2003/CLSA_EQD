"""Phoenix autocallable with coupon barrier and memory effect."""
from __future__ import annotations

import numpy as np

from .base import BaseStructuredProduct, PayoffResult, step_index_for_times


class PhoenixProduct(BaseStructuredProduct):
    def __init__(
        self,
        coupon_barrier: float = 0.80,
        memory: bool = True,
        phoenix_coupon: float = 0.01,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.coupon_barrier = float(coupon_barrier)
        self.memory = bool(memory)
        self.phoenix_coupon = float(phoenix_coupon)

    def evaluate_payoff(self, price_paths: np.ndarray, df: np.ndarray) -> PayoffResult:
        S0 = price_paths[:, :, 0]
        rel = price_paths / np.maximum(S0[:, :, None], 1e-12)
        perf = rel.min(axis=1)

        n_paths, n_steps_p1 = perf.shape
        n_steps = n_steps_p1 - 1
        obs_idx = step_index_for_times(self.obs_dates, n_steps, self.tenor)

        ko_date = np.full(n_paths, -1, dtype=int)
        pv = np.zeros(n_paths)
        coupons = np.zeros(n_paths)
        memory_count = np.zeros(n_paths, dtype=int)
        ki_ever = np.zeros(n_paths, dtype=bool)

        for j, ti in enumerate(obs_idx):
            alive = ko_date < 0
            # KI track (desk shift + optional smoothing)
            ki_ever |= alive & (self.soft_below(perf[:, ti], self.effective_ki()) >= 0.5)

            # coupon with memory
            above_cpn = alive & (perf[:, ti] >= self.coupon_barrier)
            if self.memory:
                # miss accumulates
                miss = alive & (perf[:, ti] < self.coupon_barrier)
                memory_count[miss] += 1
                # pay all missed + current when above
                n_pay = memory_count[above_cpn] + 1
                memory_count[above_cpn] = 0
            else:
                n_pay = np.ones(n_paths, dtype=int)
                n_pay = n_pay[above_cpn] if False else np.where(above_cpn, 1, 0)

            if self.memory:
                pay_amt = np.zeros(n_paths)
                pay_amt[above_cpn] = n_pay * self.phoenix_coupon
            else:
                pay_amt = np.where(above_cpn, self.phoenix_coupon, 0.0)

            coupons += pay_amt
            pv += pay_amt * df[ti]

            soft = self.soft_above(perf[:, ti], self.effective_ko())
            hit = alive & (soft >= 0.5)
            ko_date[hit] = ti
            pv[hit] += 1.0 * df[ti]

        surv = ko_date < 0
        # maturity redemption
        safe = surv & (~ki_ever | (perf[:, -1] >= self.strike))
        # simplify: if never KI or final >= strike → 100%; else put
        final_ok = surv & (perf[:, -1] >= self.strike)
        final_bad = surv & (perf[:, -1] < self.strike) & ki_ever
        final_mid = surv & ~(final_ok | final_bad)
        pv[final_ok] += 1.0 * df[-1]
        pv[final_mid] += 1.0 * df[-1]
        pv[final_bad] += (perf[final_bad, -1] / max(self.strike, 1e-12)) * df[-1]

        pv *= self.notional
        return PayoffResult(
            pv=pv,
            ko_date=ko_date,
            ki_flag=ki_ever,
            coupon_cashflows=coupons * self.notional,
            tags={"product": "phoenix", "memory": self.memory},
        )
