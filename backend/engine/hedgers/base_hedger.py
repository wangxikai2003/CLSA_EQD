"""Pluggable delta-hedge contracts for CSI 300 Vol RV (and future Step 4/5)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OptionLot:
    """One option line in an active vintage book."""

    wind_code: str
    expiry: str
    strike: float
    cp: str
    n: float
    delta: float
    gamma: float
    vega: float
    theta: float
    mid: float
    iv: float
    ttm: float
    forward: float
    leg: str  # "short" | "long"
    vintage_id: str


@dataclass
class HedgeContext:
    timestamp: str
    active_lots: List[OptionLot] = field(default_factory=list)
    current_futures_positions: Dict[str, float] = field(default_factory=dict)
    market_state: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    # Optional minute/tick frame for future intraday hedgers.
    intraday_bars: Optional[Any] = None


@dataclass
class HedgeDecision:
    target_futures_positions: Dict[str, float]
    delta_before_hedge: float
    delta_after_hedge: float
    curve_adjustment_delta: float = 0.0
    hedge_reason: str = ""
    short_delta: float = 0.0
    long_delta: float = 0.0


class BaseDeltaHedger(ABC):
    """Strategy-pattern hedger: EOD today, intraday/curve tomorrow."""

    @abstractmethod
    def compute_eod_hedge(self, ctx: HedgeContext) -> HedgeDecision:
        """Close-of-day futures target after applying short/long rules."""

    @abstractmethod
    def on_intraday_bar(self, ctx: HedgeContext) -> Optional[HedgeDecision]:
        """Optional intraday rebalance. None = no trade this bar."""

    @abstractmethod
    def compute_curve_adjusted_delta(self, ctx: HedgeContext) -> float:
        """Effective delta after implied-carry / sticky-delta adjustments."""
