"""
Lego-style Universal Exotic Product: Asset / Funding / KO / KI / Coupon legs.
Presets map to CLSA desk templates (Snowball, FCN, Phoenix Memory, …).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

BasketType = Literal["WORSTOF", "BESTOF", "WEIGHTED"]
FundingIndex = Literal["SOFR", "HONIA", "FIXED"]
KoMonType = Literal["EUROPEAN", "AMERICAN"]
KiMonType = Literal["DAILY", "EUROPEAN"]
ShiftType = Literal["TRIANGULAR", "RECTANGULAR"]
ShiftValueType = Literal["PROPORTIONAL", "ABSOLUTE"]
CpnType = Literal["FIXED", "SNOWBALL_ACCRUED", "PHOENIX_CONDITIONAL"]
SettlementType = Literal["CASH", "PHYSICAL"]


class AssetLeg(BaseModel):
    underlying: List[str] = Field(default_factory=lambda: ["SPX"])
    basket_type: BasketType = "WORSTOF"
    correlation_matrix: Optional[List[List[float]]] = None
    repo_rates: Optional[List[float]] = None
    div_yields: Optional[List[float]] = None
    weights: Optional[List[float]] = None

    @field_validator("underlying")
    @classmethod
    def _n_assets(cls, v: List[str]) -> List[str]:
        if not 1 <= len(v) <= 4:
            raise ValueError("underlying must have 1..4 assets")
        return v


class FundingLeg(BaseModel):
    funding_index: FundingIndex = "FIXED"
    funding_spread: float = 0.0
    funding_scale: float = 1.0
    funding_freq: str = "1M"


class KOLeg(BaseModel):
    ko_freq: str = "1M"
    ko_monitoring_type: KoMonType = "EUROPEAN"
    ko_schedule: List[float] = Field(default_factory=lambda: [1.03])
    ko_shift: Optional[List[float]] = None
    ko_shift_type: ShiftType = "RECTANGULAR"
    ko_shift_value_type: ShiftValueType = "PROPORTIONAL"
    memory_ko: bool = False


class KILeg(BaseModel):
    ki_level: float = 0.75
    ki_monitoring_type: KiMonType = "DAILY"
    ki_shift: float = 0.0
    ki_shift_type: ShiftType = "RECTANGULAR"
    put_strike: float = 1.0
    put_participation: float = -1.0
    settlement_type: SettlementType = "CASH"


class CouponLeg(BaseModel):
    cpn_type: CpnType = "SNOWBALL_ACCRUED"
    cpn_rate: float = 0.15
    coupon_barrier: Optional[float] = None
    memory_cpn: bool = False
    guaranteed_periods: int = 0
    ko_rebate: Optional[float] = None  # alias / solve-for target for accrued KO coupon


class UniversalExoticProduct(BaseModel):
    """Composable exotic note assembled from legs."""

    name: str = "Custom Exotic"
    tenor: float = 1.0
    notional: float = 1.0
    asset: AssetLeg = Field(default_factory=AssetLeg)
    funding: FundingLeg = Field(default_factory=FundingLeg)
    ko: KOLeg = Field(default_factory=KOLeg)
    ki: KILeg = Field(default_factory=KILeg)
    coupon: CouponLeg = Field(default_factory=CouponLeg)
    day_count: Literal["act365", "bus252"] = "act365"

    def n_assets(self) -> int:
        return len(self.asset.underlying)

    def effective_corr(self) -> List[List[float]]:
        n = self.n_assets()
        if self.asset.correlation_matrix is not None:
            return self.asset.correlation_matrix
        return [[1.0 if i == j else 0.5 for j in range(n)] for i in range(n)]

    def ko_level0(self) -> float:
        return float(self.ko.ko_schedule[0]) if self.ko.ko_schedule else 1.03

    def step_down(self) -> float:
        sch = self.ko.ko_schedule
        if len(sch) < 2:
            return 0.0
        return float(max(0.0, sch[0] - sch[1]))

    def obs_freq_token(self) -> str:
        f = (self.ko.ko_freq or "1M").upper()
        if f in ("1D", "D", "DAILY"):
            return "daily"
        if f in ("1Y", "Y", "YEARLY", "12M"):
            return "yearly"
        if f in ("E", "EU", "EUROPEAN"):
            return "european"
        return "monthly"

    def inferred_catalog_type(self) -> str:
        """Map legs → legacy registry product_type."""
        ct = self.coupon.cpn_type
        if ct == "FIXED":
            return "fcn"
        if ct == "PHOENIX_CONDITIONAL":
            return "phoenix"
        return "snowball"

    def to_legacy_product_dict(self) -> Dict[str, Any]:
        """Flatten legs into params consumed by registry / vectorized_engine."""
        cpn = self.coupon.cpn_rate
        rebate = self.coupon.ko_rebate if self.coupon.ko_rebate is not None else cpn
        out: Dict[str, Any] = {
            "tenor": self.tenor,
            "notional": self.notional,
            "ko_barrier": self.ko_level0(),
            "ki_barrier": self.ki.ki_level,
            "strike": self.ki.put_strike,
            "obs_freq": self.obs_freq_token(),
            "step_down": self.step_down(),
            "step_down_schedule": list(self.ko.ko_schedule),
            "coupon": rebate,
            "snowball_coupon_pa": rebate,
            "fixed_coupon_pa": cpn,
            "bonus_coupon": rebate,
            "phoenix_coupon": cpn / 12.0 if self.coupon.cpn_type == "PHOENIX_CONDITIONAL" else cpn,
            "coupon_barrier": self.coupon.coupon_barrier
            if self.coupon.coupon_barrier is not None
            else self.ki.ki_level + 0.05,
            "memory": self.coupon.memory_cpn,
            "ki_style": "european"
            if self.ki.ki_monitoring_type == "EUROPEAN"
            else "american",
            "ki_monitoring_type": self.ki.ki_monitoring_type,
            "ko_monitoring_type": self.ko.ko_monitoring_type,
            "settlement": self.ki.settlement_type.lower(),
            "put_participation": self.ki.put_participation,
            "ko_shift": self.ko.ko_shift,
            "ki_shift": self.ki.ki_shift,
            "guaranteed_periods": self.coupon.guaranteed_periods,
            "funding_spread": self.funding.funding_spread,
            "n_assets": self.n_assets(),
            "worst_of": self.asset.basket_type == "WORSTOF",
            "name": self.name,
        }
        return out

    def with_overrides(self, **fields: Any) -> "UniversalExoticProduct":
        data = self.model_dump()
        # allow nested leg field updates via dotted-like flat keys
        flat_map = {
            "cpn_rate": ("coupon", "cpn_rate"),
            "ko_rebate": ("coupon", "ko_rebate"),
            "ki_level": ("ki", "ki_level"),
            "put_strike": ("ki", "put_strike"),
            "coupon_barrier": ("coupon", "coupon_barrier"),
        }
        for k, v in fields.items():
            if k in flat_map:
                leg, key = flat_map[k]
                data[leg][key] = v
            elif k in data:
                data[k] = v
        return UniversalExoticProduct.model_validate(data)


# ---------------------------------------------------------------------------
# Presets Factory
# ---------------------------------------------------------------------------

PRESET_NAMES = (
    "Snowball Classic",
    "Stepdown Snowball",
    "Multi-Asset FCN",
    "Phoenix Memory Autocall",
)


def _identity_corr(n: int, rho: float = 0.5) -> List[List[float]]:
    return [[1.0 if i == j else rho for j in range(n)] for i in range(n)]


def get_preset_product(preset_name: str, **overrides: Any) -> UniversalExoticProduct:
    name = preset_name.strip()
    key = name.lower().replace("_", " ").replace("-", " ")

    if key in ("snowball classic", "snowball"):
        p = UniversalExoticProduct(
            name="Snowball Classic",
            tenor=2.0,
            asset=AssetLeg(underlying=["SPX"], basket_type="WORSTOF"),
            ko=KOLeg(
                ko_freq="1M",
                ko_monitoring_type="EUROPEAN",
                ko_schedule=[1.03] * 24,
            ),
            ki=KILeg(
                ki_level=0.75,
                ki_monitoring_type="DAILY",
                put_strike=1.0,
                put_participation=-1.0,
            ),
            coupon=CouponLeg(
                cpn_type="SNOWBALL_ACCRUED",
                cpn_rate=0.20,
                ko_rebate=0.20,
            ),
        )
    elif key in ("stepdown snowball", "stepdown", "step-down snowball"):
        sch = [1.03 - 0.005 * i for i in range(24)]
        p = UniversalExoticProduct(
            name="Stepdown Snowball",
            tenor=2.0,
            asset=AssetLeg(underlying=["SPX"], basket_type="WORSTOF"),
            ko=KOLeg(
                ko_freq="1M",
                ko_monitoring_type="EUROPEAN",
                ko_schedule=sch,
                ko_shift=[-0.00833 * min(i, 6) for i in range(24)],
                ko_shift_type="TRIANGULAR",
            ),
            ki=KILeg(ki_level=0.70, ki_monitoring_type="DAILY", put_strike=1.0),
            coupon=CouponLeg(cpn_type="SNOWBALL_ACCRUED", cpn_rate=0.18, ko_rebate=0.18),
        )
    elif key in ("multi-asset fcn", "multi asset fcn", "fcn"):
        p = UniversalExoticProduct(
            name="Multi-Asset FCN",
            tenor=1.0,
            asset=AssetLeg(
                underlying=["SPX", "NDX"],
                basket_type="WORSTOF",
                correlation_matrix=_identity_corr(2, 0.6),
                div_yields=[0.015, 0.005],
            ),
            ko=KOLeg(
                ko_freq="1M",
                ko_monitoring_type="EUROPEAN",
                ko_schedule=[1.05] * 12,
            ),
            ki=KILeg(
                ki_level=0.80,
                ki_monitoring_type="EUROPEAN",
                put_strike=0.80,
                put_participation=-1.0,
            ),
            coupon=CouponLeg(cpn_type="FIXED", cpn_rate=0.15, ko_rebate=0.15),
        )
    elif key in ("phoenix memory autocall", "phoenix memory", "phoenix"):
        p = UniversalExoticProduct(
            name="Phoenix Memory Autocall",
            tenor=1.0,
            asset=AssetLeg(
                underlying=["SPX", "HSI"],
                basket_type="WORSTOF",
                correlation_matrix=_identity_corr(2, 0.55),
            ),
            ko=KOLeg(
                ko_freq="1M",
                ko_monitoring_type="EUROPEAN",
                ko_schedule=[1.00] * 12,
            ),
            ki=KILeg(ki_level=0.70, ki_monitoring_type="DAILY", put_strike=1.0),
            coupon=CouponLeg(
                cpn_type="PHOENIX_CONDITIONAL",
                cpn_rate=0.12,
                coupon_barrier=0.80,
                memory_cpn=True,
                ko_rebate=0.12,
            ),
        )
    else:
        p = UniversalExoticProduct(name="Custom Exotic")

    if overrides:
        p = p.with_overrides(**overrides)
    return p


def list_presets() -> List[Dict[str, str]]:
    return [
        {"id": "snowball", "name": "Snowball Classic"},
        {"id": "stepdown", "name": "Stepdown Snowball"},
        {"id": "fcn", "name": "Multi-Asset FCN"},
        {"id": "phoenix", "name": "Phoenix Memory Autocall"},
        {"id": "custom", "name": "Custom Exotic"},
    ]


def price_universal(
    product: UniversalExoticProduct,
    market: Dict[str, Any],
    sim: Optional[Dict[str, Any]] = None,
    greeks: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Price via legacy registry after leg → catalog mapping."""
    from products.registry import price_product

    sim = sim or {"n_paths": 20000, "n_steps": 252, "seed": 42, "bridge": True}
    greeks = greeks or {"enabled": True, "return_ki_profiles": True}
    ptype = product.inferred_catalog_type()
    params = product.to_legacy_product_dict()
    mkt = dict(market)
    n = product.n_assets()
    spots = list(mkt.get("spots") or [100.0])
    while len(spots) < n:
        spots.append(spots[-1])
    mkt["spots"] = spots[:n]
    vols = list(mkt.get("flat_vols") or [0.2])
    while len(vols) < n:
        vols.append(vols[-1])
    mkt["flat_vols"] = vols[:n]
    q = list(mkt.get("q") or product.asset.div_yields or [0.0] * n)
    while len(q) < n:
        q.append(q[-1] if q else 0.0)
    mkt["q"] = q[:n]
    mkt["corr"] = product.effective_corr()
    # funding: bump r by spread * scale
    mkt["r"] = float(mkt.get("r", 0.02)) + product.funding.funding_spread * product.funding.funding_scale
    return price_product(ptype, params, mkt, sim, greeks)
