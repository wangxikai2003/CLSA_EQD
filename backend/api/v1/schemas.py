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
    return_ki_profiles: bool = True


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
    pre_ki_profile: Optional[Dict[str, Any]] = None
    post_ki_profile: Optional[Dict[str, Any]] = None


class GreeksTermStructureRequest(BaseModel):
    product_type: Literal["snowball", "fcn", "phoenix"] = "snowball"
    product: Dict[str, Any] = Field(default_factory=dict)
    market: MarketSnapshot = Field(default_factory=MarketSnapshot)
    time_slices: Optional[List[float]] = None
    spot_range: Optional[List[float]] = None  # [lo, hi]
    spot_steps: int = 41
    ki_monitoring_type: Literal["DAILY", "EUROPEAN", "AMERICAN", "AXKI"] = "DAILY"
    ko_monitoring_type: Literal["EUROPEAN", "DAILY", "CONTINUOUS_AMERICAN"] = "EUROPEAN"
    include_greeks: List[str] = Field(default_factory=lambda: ["delta", "gamma", "pv"])
    n_paths: int = 6000
    seed: int = 42
    barrier_shift_bps: float = 0.0
    spread_smoothing_width: float = 0.0


class KiRegimeCurve(BaseModel):
    valid_spots: List[float]
    pv: Optional[List[float]] = None
    delta: Optional[List[float]] = None
    gamma: Optional[List[float]] = None


class GreeksTermSlice(BaseModel):
    t: float
    label: str
    before_ki: KiRegimeCurve
    after_ki: KiRegimeCurve


class GreeksTermStructureResponse(BaseModel):
    spot_grid: List[float]
    S0: float
    T: float
    ki_level: float
    ko_level: float
    ki_monitoring_type: str
    ko_monitoring_type: str
    slices: List[GreeksTermSlice]
    elapsed_ms: float = 0.0


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


# ---- QIS ----
QISStrategyId = Literal[
    "cross_calendar_carry",
    "equity_3_6_calendar_vol_50",
    "equity_3_6_calendar_vol_300",
    "equity_3_6_calendar_vol_1000",
    "equity_3_6_calendar_vol_zscore_50",
    "equity_3_6_calendar_vol_zscore_300",
    "equity_3_6_calendar_vol_zscore_1000",
]


class PnLDecomposition(BaseModel):
    dates: List[str]
    vega_pnl: List[float]
    theta_pnl: List[float]
    gamma_pnl: List[float]
    basis_pnl: List[float]
    net_delta_pnl: List[float]
    residual_pnl: List[float]


class QISSignalSeries(BaseModel):
    dates: List[str]
    spread: List[float]
    zscore: List[Optional[float]]
    position: List[int]
    enter_band: float = 1.5
    exit_band: float = 0.2


class QISBacktestRequest(BaseModel):
    strategy_type: QISStrategyId = "cross_calendar_carry"
    initial_nav: float = 100_000_000.0
    refresh: bool = False


class QISBacktestResponse(BaseModel):
    strategy_id: str
    strategy_name: str
    description: str
    legs: List[Dict[str, Any]] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)
    nav: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    pnl_decomposition: Optional[PnLDecomposition] = None
    signal: Optional[QISSignalSeries] = None
    pnl_contribution: Optional[Dict[str, Any]] = None
