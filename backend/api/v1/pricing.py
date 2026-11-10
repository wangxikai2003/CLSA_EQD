"""POST /api/v1/pricing/exotic & /greeks-term-structure"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException

from api.v1.schemas import (
    ExoticPriceRequest,
    ExoticPriceResponse,
    GreeksTermStructureRequest,
    GreeksTermStructureResponse,
)
from engine.vectorized_engine import compute_greeks_term_structure
from products.registry import price_product

router = APIRouter(prefix="/api/v1/pricing", tags=["pricing"])


@router.post("/exotic", response_model=ExoticPriceResponse)
def price_exotic(req: ExoticPriceRequest) -> ExoticPriceResponse:
    t0 = time.perf_counter()
    market = req.market.model_dump()
    if market.get("corr") is None:
        n = len(market["spots"])
        market["corr"] = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    try:
        out = price_product(
            req.product_type,
            req.product,
            market,
            req.sim.model_dump(),
            req.greeks.model_dump(),
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    out["diagnostics"]["elapsed_ms"] = (time.perf_counter() - t0) * 1000
    return ExoticPriceResponse(**out)


@router.post("/greeks-term-structure", response_model=GreeksTermStructureResponse)
def greeks_term_structure(req: GreeksTermStructureRequest) -> GreeksTermStructureResponse:
    t0 = time.perf_counter()
    market = req.market.model_dump()
    spot_range: Optional[Tuple[float, float]] = None
    if req.spot_range and len(req.spot_range) >= 2:
        spot_range = (float(req.spot_range[0]), float(req.spot_range[1]))
    try:
        out = compute_greeks_term_structure(
            product_type=req.product_type,
            product=req.product,
            market=market,
            time_slices=req.time_slices,
            spot_range=spot_range,
            spot_steps=req.spot_steps,
            ki_monitoring_type=req.ki_monitoring_type,
            ko_monitoring_type=req.ko_monitoring_type,
            include_greeks=req.include_greeks,
            n_paths=req.n_paths,
            seed=req.seed,
            barrier_shift_bps=req.barrier_shift_bps,
            spread_smoothing_width=req.spread_smoothing_width,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
    return GreeksTermStructureResponse(**out)
