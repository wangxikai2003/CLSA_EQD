"""POST /api/v1/pricing/exotic"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from api.v1.schemas import ExoticPriceRequest, ExoticPriceResponse
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
