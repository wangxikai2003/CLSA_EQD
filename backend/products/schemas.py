"""Pydantic schemas for structured products."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ProductBaseParams(BaseModel):
    tenor: float = Field(1.0, description="maturity in years")
    obs_dates: Optional[List[float]] = Field(None, description="observation times in years from t0")
    strike: float = 1.0
    ko_barrier: float = 1.03
    ki_barrier: float = 0.75
    day_count: Literal["act365", "bus252"] = "act365"
    settlement: Literal["cash", "physical"] = "cash"
    notional: float = 1.0
    coupon: float = 0.2
    bonus_coupon: float = 0.0
    obs_freq: Literal["daily", "monthly", "yearly", "european"] = "monthly"


class SnowballParams(ProductBaseParams):
    step_down: float = 0.0
    step_down_schedule: Optional[List[float]] = None
    ki_style: Literal["american", "european", "axki"] = "american"
    snowball_coupon_pa: Optional[float] = None


class FcnParams(ProductBaseParams):
    fixed_coupon_pa: float = 0.12
    n_assets: int = 2
    worst_of: bool = True


class PhoenixParams(ProductBaseParams):
    coupon_barrier: float = 0.8
    memory: bool = True
    phoenix_coupon: float = 0.01


class BoosterAirbagParams(ProductBaseParams):
    participation: float = 1.5
    cap: Optional[float] = None
    airbag_level: float = 0.8
    structure: Literal["booster", "capped_booster", "airbag"] = "booster"


class PayoffResultModel(BaseModel):
    mean_pv: float
    stderr: float
    ko_prob: float
    ki_prob: float
    tags: Dict[str, Any] = {}


# Re-export Lego legs for API / docs consumers
from products.universal_exotic import (  # noqa: E402
    AssetLeg,
    CouponLeg,
    FundingLeg,
    KILeg,
    KOLeg,
    UniversalExoticProduct,
)
