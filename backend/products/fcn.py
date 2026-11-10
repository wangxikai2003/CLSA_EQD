"""
Fixed Coupon Note (FCN) — Yield Enhancement (EQD Product Catalog).

Structure (catalog):
  - Pays fixed coupon on each observation until KO or maturity.
  - KO: if (worst-of) performance >= KO Barrier on an obs date
        → redeem 100% notional + that period's coupon (prior coupons kept).
  - No KO to maturity:
        → Min(100%, Final/Strike) × Notional + all coupons received.
  Typical illustrative: monthly obs, KO ~105%, Strike ~72–76%, coupon ~15% p.a.
"""
from __future__ import annotations

import numpy as np

from .base import BaseStructuredProduct, PayoffResult, step_index_for_times


class FcnProduct(BaseStructuredProduct):
    def __init__(self, fixed_coupon_pa: float = 0.15, worst_of: bool = True, **kwargs):
        # Catalog FCN: put-like strike often 70–80%; default KO 105%
        kwargs.setdefault("ko_barrier", 1.05)
        kwargs.setdefault("strike", 0.75)
        kwargs.setdefault("ki_barrier", kwargs.get("strike", 0.75))
        kwargs.setdefault("obs_freq", "monthly")
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

        for j, ti in enumerate(obs_idx):
            alive = ko_date < 0
            # Fixed coupon if still alive at observation
            pay = alive
            coupons[pay] += cpn_each
            pv[pay] += cpn_each * df[ti]

            soft = self.soft_above(perf[:, ti], self.effective_ko())
            hit = alive & (soft >= 0.5)
            ko_date[hit] = ti
            # Early redemption: 100% notional (coupon for this period already paid)
            pv[hit] += 1.0 * df[ti]

        surv = ko_date < 0
        # Natural "KI" for FCN = finishing below strike (put conversion at maturity)
        natural_ki = surv & (self.soft_below(perf[:, -1], self.strike) >= 0.5)
        ki_flag = self.resolve_ki_flag(natural_ki)

        # Maturity redemption for survivors
        # pre-KI (force_ki=False): always 100%; post-KI (True): always Final/Strike;
        # blended (None): catalog Min(100%, Final/Strike)
        if self.force_ki is False:
            # Pre-KI regime: capital protected at maturity if no KO
            pv[surv] += 1.0 * df[-1]
        elif self.force_ki is True:
            # Post-KI regime: catalog put conversion Min(100%, Final/Strike)
            redemption = np.minimum(perf[surv, -1] / max(self.strike, 1e-12), 1.0)
            pv[surv] += redemption * df[-1]
        else:
            # Blended (catalog): Min(100%, Final/Strike)
            redemption = np.minimum(perf[surv, -1] / max(self.strike, 1e-12), 1.0)
            pv[surv] += redemption * df[-1]

        pv *= self.notional
        return PayoffResult(
            pv=pv,
            ko_date=ko_date,
            ki_flag=ki_flag,
            coupon_cashflows=coupons * self.notional,
            tags={
                "product": "fcn",
                "n_obs": n_obs,
                "fixed_coupon_pa": self.fixed_coupon_pa,
                "worst_of": self.worst_of,
            },
        )
