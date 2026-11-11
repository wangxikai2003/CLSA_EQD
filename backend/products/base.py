"""Base structured product abstraction."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class PayoffResult:
    pv: np.ndarray  # discounted payoff per path (fraction of notional)
    ko_date: np.ndarray  # step index of KO (-1 if none)
    ki_flag: np.ndarray  # bool
    coupon_cashflows: np.ndarray  # undiscounted coupon sum per path
    tags: Dict[str, Any] = field(default_factory=dict)

    @property
    def mean_pv(self) -> float:
        return float(np.mean(self.pv))

    @property
    def stderr(self) -> float:
        n = max(len(self.pv), 1)
        return float(np.std(self.pv, ddof=1) / np.sqrt(n))


def default_obs_dates(tenor: float, freq: str = "monthly") -> np.ndarray:
    if freq == "daily":
        n = max(int(round(tenor * 252)), 1)
        return np.linspace(tenor / n, tenor, n)
    if freq == "yearly":
        n = max(int(round(tenor)), 1)
        return np.linspace(tenor / n, tenor, n)
    if freq == "european":
        return np.array([tenor], dtype=float)
    # monthly
    n = max(int(round(tenor * 12)), 1)
    return np.linspace(tenor / n, tenor, n)


def step_index_for_times(obs_times: np.ndarray, n_steps: int, tenor: float) -> np.ndarray:
    """Map observation times (years) to path step indices."""
    tenor = max(float(tenor), 1e-12)
    idx = np.round(obs_times / tenor * n_steps).astype(int)
    return np.clip(idx, 1, n_steps)


class BaseStructuredProduct(ABC):
    def __init__(
        self,
        tenor: float = 1.0,
        obs_dates: Optional[List[float]] = None,
        strike: float = 1.0,
        ko_barrier: float = 1.03,
        ki_barrier: float = 0.75,
        day_count: str = "act365",
        settlement: str = "cash",
        notional: float = 1.0,
        obs_freq: str = "monthly",
        barrier_shift_bps: float = 0.0,
        spread_smoothing_width: float = 0.0,
        apply_desk_shift: bool = False,
        force_ki: Optional[bool] = None,
        **kwargs: Any,
    ):
        self.tenor = float(tenor)
        self.strike = float(strike)
        self.ko_barrier = float(ko_barrier)
        self.ki_barrier = float(ki_barrier)
        self.day_count = day_count
        self.settlement = settlement
        self.notional = float(notional)
        self.obs_freq = obs_freq
        self.barrier_shift_bps = float(barrier_shift_bps)
        self.spread_smoothing_width = float(spread_smoothing_width)
        self.apply_desk_shift = bool(apply_desk_shift)
        # None = path-natural KI; False = pre-KI regime; True = post-KI regime
        self.force_ki = force_ki
        self.extra = kwargs
        if obs_dates is None:
            self.obs_dates = default_obs_dates(self.tenor, obs_freq)
        else:
            self.obs_dates = np.asarray(obs_dates, dtype=float)

    def resolve_ki_flag(self, natural_ki: np.ndarray) -> np.ndarray:
        """Apply force_ki regime override for Greeks / scenario pricing."""
        if self.force_ki is None:
            return np.asarray(natural_ki, dtype=bool)
        if self.force_ki:
            return np.ones(len(natural_ki), dtype=bool)
        return np.zeros(len(natural_ki), dtype=bool)

    def _shift_frac(self) -> float:
        return abs(self.barrier_shift_bps) / 10000.0

    def effective_ko(self, level: Optional[float] = None) -> float:
        """Desk shift: lower KO (triggers earlier) when apply_desk_shift."""
        H = float(self.ko_barrier if level is None else level)
        if not self.apply_desk_shift or self.barrier_shift_bps == 0:
            return H
        return H * (1.0 - self._shift_frac())

    def effective_ki(self, level: Optional[float] = None) -> float:
        """Desk shift: higher KI (triggers earlier) when apply_desk_shift."""
        H = float(self.ki_barrier if level is None else level)
        if not self.apply_desk_shift or self.barrier_shift_bps == 0:
            return H
        return H * (1.0 + self._shift_frac())

    def soft_above(self, S: np.ndarray, barrier: float) -> np.ndarray:
        from engine.greeks import call_spread_smooth

        w = self.spread_smoothing_width
        if w <= 0:
            return (np.asarray(S, dtype=float) >= barrier).astype(float)
        return call_spread_smooth(S, barrier, w, above=True)

    def soft_below(self, S: np.ndarray, barrier: float) -> np.ndarray:
        from engine.greeks import call_spread_smooth

        w = self.spread_smoothing_width
        if w <= 0:
            return (np.asarray(S, dtype=float) < barrier).astype(float)
        return call_spread_smooth(S, barrier, w, above=False)

    @abstractmethod
    def evaluate_payoff(self, price_paths: np.ndarray, df: np.ndarray) -> PayoffResult:
        """
        Parameters
        ----------
        price_paths : (n_paths, n_assets, n_steps+1) absolute prices
        df : (n_steps+1,) discount factors from t0 to each step (df[0]=1)
        """
