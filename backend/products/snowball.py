"""Snowball & step-down snowball autocallable."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .base import BaseStructuredProduct, PayoffResult, step_index_for_times


class SnowballProduct(BaseStructuredProduct):
    def __init__(
        self,
        coupon: float = 0.20,
        bonus_coupon: float = 0.0,
        step_down: float = 0.0,
        step_down_schedule: Optional[List[float]] = None,
        ki_style: str = "american",
        snowball_coupon_pa: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.coupon = float(snowball_coupon_pa if snowball_coupon_pa is not None else coupon)
        self.bonus_coupon = float(bonus_coupon)
        self.step_down = float(step_down)
        self.step_down_schedule = step_down_schedule
        self.ki_style = ki_style

    def _ko_levels(self, n_obs: int) -> np.ndarray:
        if self.step_down_schedule is not None:
            sched = np.asarray(self.step_down_schedule, dtype=float)
            if len(sched) >= n_obs:
                return sched[:n_obs]
            extra = np.full(n_obs - len(sched), sched[-1])
            return np.concatenate([sched, extra])
        # linear step-down from ko_barrier
        if self.step_down == 0:
            return np.full(n_obs, self.ko_barrier)
        return self.ko_barrier - self.step_down * np.arange(n_obs)

    def evaluate_payoff(self, price_paths: np.ndarray, df: np.ndarray) -> PayoffResult:
        # use worst-of performance vs spot0
        S0 = price_paths[:, :, 0]  # (n, a)
        # relative: min_a S/S0
        rel = price_paths / np.maximum(S0[:, :, None], 1e-12)
        perf = rel.min(axis=1)  # (n_paths, n_steps+1)

        n_paths, n_steps_p1 = perf.shape
        n_steps = n_steps_p1 - 1
        obs_idx = step_index_for_times(self.obs_dates, n_steps, self.tenor)
        n_obs = len(obs_idx)
        ko_lvls = self._ko_levels(n_obs)

        ki_h = self.effective_ki()
        # KI american: any day below ki; european/axki: only at maturity obs
        if self.ki_style == "american":
            soft_ki = self.soft_below(perf[:, 1:], ki_h)
            ki_flag = soft_ki.max(axis=1) >= 0.5
        else:
            ki_flag = self.soft_below(perf[:, -1], ki_h) >= 0.5

        ko_date = np.full(n_paths, -1, dtype=int)
        pv = np.zeros(n_paths)
        coupons = np.zeros(n_paths)

        # accrued time fractions at obs
        obs_t = self.obs_dates

        for j, (ti, ko_h0) in enumerate(zip(obs_idx, ko_lvls)):
            alive = ko_date < 0
            ko_h = self.effective_ko(ko_h0)
            soft = self.soft_above(perf[:, ti], ko_h)
            hit = alive & (soft >= 0.5)
            ko_date[hit] = ti
            # KO payoff: 1 + coupon * time
            cpn = self.coupon * float(obs_t[j])
            coupons[hit] = cpn
            pv[hit] = (1.0 + cpn) * df[ti]

        # survivors to maturity
        surv = ko_date < 0
        # no KI: 1 + bonus (or coupon)
        safe = surv & (~ki_flag)
        cpn_safe = self.bonus_coupon if self.bonus_coupon > 0 else self.coupon * self.tenor
        coupons[safe] = cpn_safe
        pv[safe] = (1.0 + cpn_safe) * df[-1]
        # KI: put-like loss min(1, S_T/S0)/strike style — cash settlement at strike
        hit_ki = surv & ki_flag
        st = perf[hit_ki, -1]
        # loss: ST/strike if ST < strike else 1 (capital return) — classic snowball KI
        redemption = np.minimum(st / max(self.strike, 1e-12), 1.0)
        coupons[hit_ki] = 0.0
        pv[hit_ki] = redemption * df[-1]

        pv *= self.notional
        return PayoffResult(
            pv=pv,
            ko_date=ko_date,
            ki_flag=ki_flag,
            coupon_cashflows=coupons * self.notional,
            tags={"product": "snowball", "n_obs": n_obs},
        )
