"""Universal Exotic Workbench API: price / solve / greeks-term-structure."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.greeks_term_structure import (
    compute_exotic_risk_summary,
    compute_greeks_term_structure_universal,
)
from engine.solver import solve_exotic_parameter
from products.universal_exotic import (
    UniversalExoticProduct,
    get_preset_product,
    list_presets,
    price_universal,
)

router = APIRouter(prefix="/api/v1/exotic", tags=["exotic-workbench"])


class MarketEnv(BaseModel):
    spots: List[float] = Field(default_factory=lambda: [100.0])
    r: float = 0.02
    q: List[float] = Field(default_factory=lambda: [0.0])
    flat_vols: List[float] = Field(default_factory=lambda: [0.2])
    corr: Optional[List[List[float]]] = None
    vol_mode: str = "flat"


class SimCfg(BaseModel):
    n_paths: int = 20000
    n_steps: int = 252
    seed: int = 42
    bridge: bool = True


class ExoticPriceBody(BaseModel):
    product: Optional[Dict[str, Any]] = None
    preset: Optional[str] = None
    market: MarketEnv = Field(default_factory=MarketEnv)
    sim: SimCfg = Field(default_factory=SimCfg)
    include_risk_summary: bool = True


class ExoticSolveBody(BaseModel):
    product: Optional[Dict[str, Any]] = None
    preset: Optional[str] = None
    market: MarketEnv = Field(default_factory=MarketEnv)
    solve_for: Literal["cpn_rate", "ko_rebate", "ki_level", "put_strike"] = "cpn_rate"
    target_price: float = 0.99
    bounds: Optional[List[float]] = None
    n_paths: int = 8000
    seed: int = 42


class ExoticGtsBody(BaseModel):
    product: Optional[Dict[str, Any]] = None
    preset: Optional[str] = None
    market: MarketEnv = Field(default_factory=MarketEnv)
    time_slices: Optional[List[float]] = None
    spot_range: Optional[List[float]] = None
    spot_steps: int = 41
    include_greeks: List[str] = Field(default_factory=lambda: ["pv", "delta", "gamma", "vanna"])
    n_paths: int = 4000
    seed: int = 42
    barrier_shift_bps: float = 0.0
    spread_smoothing_width: float = 0.02
    include_risk_summary: bool = False


def _resolve_product(body_product: Optional[Dict], preset: Optional[str]) -> UniversalExoticProduct:
    if body_product:
        return UniversalExoticProduct.model_validate(body_product)
    if preset:
        return get_preset_product(preset)
    return get_preset_product("Snowball Classic")


@router.get("/presets")
def exotic_presets() -> Dict[str, Any]:
    return {"presets": list_presets()}


@router.get("/presets/{name}")
def exotic_preset_detail(name: str) -> Dict[str, Any]:
    try:
        p = get_preset_product(name)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return p.model_dump()


@router.post("/price")
def exotic_price(body: ExoticPriceBody) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        prod = _resolve_product(body.product, body.preset)
        market = body.market.model_dump()
        if body.include_risk_summary:
            out = compute_exotic_risk_summary(
                prod, market, n_paths=body.sim.n_paths, seed=body.sim.seed
            )
        else:
            priced = price_universal(prod, market, body.sim.model_dump(), {"enabled": True})
            out = {
                "price": priced["price"],
                "stderr": priced.get("stderr"),
                "greeks": priced.get("greeks") or {},
                "diagnostics": priced.get("diagnostics") or {},
                "meta": {"product_name": prod.name, "catalog_type": prod.inferred_catalog_type()},
            }
        out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
        out["product"] = prod.model_dump()
        return out
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.post("/solve")
def exotic_solve(body: ExoticSolveBody) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        prod = _resolve_product(body.product, body.preset)
        bounds = tuple(body.bounds) if body.bounds and len(body.bounds) >= 2 else None
        out = solve_exotic_parameter(
            prod,
            body.market.model_dump(),
            solve_for=body.solve_for,
            target_price=body.target_price,
            bounds=bounds,
            n_paths=body.n_paths,
            seed=body.seed,
        )
        out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
        out["product"] = prod.model_dump()
        return out
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.post("/greeks-term-structure")
def exotic_greeks_term_structure(body: ExoticGtsBody) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        prod = _resolve_product(body.product, body.preset)
        spot_range: Optional[Tuple[float, float]] = None
        if body.spot_range and len(body.spot_range) >= 2:
            spot_range = (float(body.spot_range[0]), float(body.spot_range[1]))
        out = compute_greeks_term_structure_universal(
            prod,
            body.market.model_dump(),
            time_slices=body.time_slices,
            spot_range=spot_range,
            spot_steps=body.spot_steps,
            include_greeks=body.include_greeks,
            n_paths=body.n_paths,
            seed=body.seed,
            barrier_shift_bps=body.barrier_shift_bps,
            spread_smoothing_width=body.spread_smoothing_width,
            include_risk_summary=body.include_risk_summary,
        )
        out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
        return out
    except Exception as e:
        raise HTTPException(400, str(e)) from e
