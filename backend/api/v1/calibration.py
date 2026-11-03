"""POST /api/v1/calibration/jwsvi"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.v1.schemas import JwCalibRequest, JwCalibResponse
from models.jwsvi import calibrate_slice

router = APIRouter(prefix="/api/v1/calibration", tags=["calibration"])


@router.post("/jwsvi", response_model=JwCalibResponse)
def calibrate_jwsvi(req: JwCalibRequest) -> JwCalibResponse:
    if len(req.strikes) != len(req.ivs) or len(req.strikes) < 5:
        raise HTTPException(400, "Need >=5 strikes/ivs of equal length")
    try:
        res = calibrate_slice(req.strikes, req.ivs, req.ttm, method=req.method, forward=req.forward)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    d = res.to_dict()
    return JwCalibResponse(
        jw=d["jw"],
        raw=d["raw"],
        rmse=d["rmse"],
        mae=d["mae"],
        arbitrage=d["arbitrage"],
        max_residual=d.get("max_residual", 0.0),
        arbitrage_check=d.get("arbitrage_check", {}),
    )
