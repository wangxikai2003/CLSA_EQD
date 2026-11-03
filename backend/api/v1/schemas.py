"""Pydantic request/response for /api/v1."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class SimConfig(BaseModel):
    n_paths: int = 20000
    n_steps: int = 252
    seed: int = 42
    bridge: bool = True
    bgk: bool = False


class GreeksConfig(BaseModel):
    enabled: bool = False
    bump_spot: float = 0.01
    bump_vol: float = 0.01
    barrier_shift: float = 0.0  # legacy alias
    digital_width: float = 0.0  # legacy alias
    barrier_shift_bps: float = 0.0
    spread_smoothing_width: float = 0.0
    return_profiles: bool = False


class MarketSnapshot(BaseModel):
    spots: List[float] = Field(default_factory=lambda: [100.0])
    r: float = 0.02
    q: List[float] = Field(default_factory=lambda: [0.0])
    corr: Optional[List[List[float]]] = None
    vol_mode: Literal["flat", "jwsvi", "local_vol"] = "flat"
    flat_vols: List[float] = Field(default_factory=lambda: [0.2])
    jwsvi_slices: Optional[List[Dict[str, Any]]] = None
    local_vol_grid: Optional[Dict[str, Any]] = None


class ExoticPriceRequest(BaseModel):
    product_type: Literal["snowball", "fcn", "phoenix", "booster", "airbag", "capped_booster"]
    product: Dict[str, Any] = Field(default_factory=dict)
    market: MarketSnapshot = Field(default_factory=MarketSnapshot)
    sim: SimConfig = Field(default_factory=SimConfig)
    greeks: GreeksConfig = Field(default_factory=GreeksConfig)


class ExoticPriceResponse(BaseModel):
    price: float
    price_ci_95: List[float]
    stderr: float
    greeks: Dict[str, Any] = {}
    diagnostics: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}
    raw_profile: Optional[Dict[str, Any]] = None
    shifted_profile: Optional[Dict[str, Any]] = None
    reserve_metrics: Optional[Dict[str, Any]] = None


class JwCalibRequest(BaseModel):
    ttm: float
    strikes: List[float]
    ivs: List[float]
    forward: float = 100.0
    method: Literal["SLSQP", "BFGS", "LS"] = "SLSQP"


class JwCalibResponse(BaseModel):
    jw: Dict[str, float]
    raw: Dict[str, float]
    rmse: float
    mae: float
    arbitrage: Dict[str, Any]
    max_residual: float = 0.0
    arbitrage_check: Dict[str, Any] = {}
