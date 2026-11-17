"""Realized Correlation REST API."""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(tags=["correlation"])


class MatrixRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=2)
    rolling_win: int = 63
    metric: str = "realized"  # realized | desk_mark | diff
    use_rules: bool = True
    lookback_months: int = 24


@router.get("/universe")
def get_universe() -> dict[str, Any]:
    try:
        from config.universe_registry import load_registry

        return load_registry()
    except FileNotFoundError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex


@router.get("/pairwise")
def get_pairwise(
    symbol1: str = Query(...),
    symbol2: str = Query(...),
    rolling_win: int = Query(63),
    lookback_months: int = Query(24),
    use_rules: bool = Query(True),
    pad_override: Optional[int] = Query(None),
) -> dict[str, Any]:
    try:
        from engine import realized_corr_service as svc

        return svc.pairwise(
            symbol1=symbol1,
            symbol2=symbol2,
            rolling_win=rolling_win,
            lookback_months=lookback_months,
            use_rules=use_rules,
            pad_override=pad_override,
        )
    except FileNotFoundError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    except KeyError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    except Exception as ex:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/matrix")
def post_matrix(body: MatrixRequest) -> dict[str, Any]:
    try:
        from engine import realized_corr_service as svc

        return svc.matrix(
            symbols=body.symbols,
            rolling_win=body.rolling_win,
            metric=body.metric,
            use_rules=body.use_rules,
            lookback_months=body.lookback_months,
        )
    except FileNotFoundError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    except Exception as ex:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/spreads")
def get_spreads(
    mode: str = Query("term", pattern="^(term|cross)$"),
    symbol1: str = Query(...),
    symbol2: str = Query(...),
    symbol3: Optional[str] = Query(None),
    symbol4: Optional[str] = Query(None),
    short_win: int = Query(21),
    long_win: int = Query(126),
) -> dict[str, Any]:
    try:
        from engine import realized_corr_service as svc

        return svc.spreads(
            mode=mode,
            symbol1=symbol1,
            symbol2=symbol2,
            symbol3=symbol3,
            symbol4=symbol4,
            short_win=short_win,
            long_win=long_win,
        )
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except FileNotFoundError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    except Exception as ex:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/precomputed-matrix")
def get_precomputed_matrix(
    mode: str = Query("IDX_IDX_simple_last"),
) -> dict[str, Any]:
    """Serve build_web_realized_data.py snapshot when available."""
    try:
        from engine import realized_corr_service as svc

        mats = svc.load_matrices()
        if not mats or mode not in mats.get("modes", {}):
            raise HTTPException(status_code=404, detail=f"mode {mode} not in corr_web_matrices.json")
        return {"asof": mats.get("asof"), "mode": mode, **mats["modes"][mode]}
    except HTTPException:
        raise
    except Exception as ex:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(ex)) from ex
