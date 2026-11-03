"""Fixed Coupon Note (FCN) — worst-of basket with fixed coupons & KO."""
from __future__ import annotations

import numpy as np

from .base import BaseStructuredProduct, PayoffResult, step_index_for_times


class FcnProduct(BaseStructuredProduct):
    def __init__(self, fixed_coupon_pa: float = 0.12, worst_of: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.fixed_coupon_pa = float(fixed_coupon_pa)
        self.worst_of = bool(worst_of)

    def evaluate_payoff(self, price_paths: np.ndarray, df: np.ndarray) -> PayoffResult:
        S0 = price_paths[:, :, 0]
        rel = price_paths / np.maximum(S0[:, :, None], 1e-12)
        perf = rel.min(axis=1) if self.worst_of else rel.mean(axis=1)

        n_paths, n_steps_p1 = perf.shape
        n_steps = n_steps_p1 - 1
        obs_idx = step_index_for_times(self.obs_dates, n_steps, self.tenor)
        n_obs = len(obs_idx)
        period = self.tenor / max(n_obs, 1)
        cpn_each = self.fixed_coupon_pa * period

        ko_date = np.full(n_paths, -1, dtype=int)
        pv = np.zeros(n_paths)
        coupons = np.zeros(n_paths)
        accrued = np.zeros(n_paths)

        for j, ti in enumerate(obs_idx):
            alive = ko_date < 0
            # pay coupon if alive at obs
            pay = alive
            accrued[pay] += cpn_each
            coupons[pay] += cpn_each
            pv[pay] += cpn_each * df[ti]

            soft = self.soft_above(perf[:, ti], self.effective_ko())
            hit = alive & (soft >= 0.5)
            ko_date[hit] = ti
            # early redeem notional
            pv[hit] += 1.0 * df[ti]

        surv = ko_date < 0
        # maturity: if worst >= strike → 100%; else physical/cash at worst/strike
        good = surv & (perf[:, -1] >= self.strike)
        bad = surv & (perf[:, -1] < self.strike)
        pv[good] += 1.0 * df[-1]
        if self.settlement == "physical":
            pv[bad] += (perf[bad, -1] / max(self.strike, 1e-12)) * df[-1]
        else:
            pv[bad] += (perf[bad, -1] / max(self.strike, 1e-12)) * df[-1]

        ki_flag = surv & (self.soft_below(perf[:, -1], self.effective_ki()) >= 0.5)
        pv *= self.notional
        return PayoffResult(
            pv=pv,
            ko_date=ko_date,
            ki_flag=ki_flag,
            coupon_cashflows=coupons * self.notional,
            tags={"product": "fcn", "n_obs": n_obs},
        )
