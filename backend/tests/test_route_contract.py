"""Lock the route set that parallel agents have dropped before.

Importing routers (not the full app) avoids opening cffex_vol.sqlite, which
is not in git and is absent on CI.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1 import correlation as corr_routes
from api.v1 import exotic_workbench as exotic_routes
from api.v1 import market as market_routes
from api.v1 import qis_backtest as qis_routes


def _paths(app: FastAPI) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


def test_correlation_market_exotic_qis_routes_exist():
    app = FastAPI()
    app.include_router(corr_routes.router, prefix="/api/v1/correlation")
    app.include_router(market_routes.router)
    app.include_router(exotic_routes.router)
    app.include_router(qis_routes.router, prefix="/api/v1/qis")
    paths = _paths(app)
    required = {
        "/api/v1/correlation/universe",
        "/api/v1/market/yield-curve",
        "/api/v1/exotic/presets",
        "/api/v1/qis/strategies",
        "/api/v1/qis/backtest/{strategy_id}",
    }
    missing = required - paths
    assert not missing, missing


def test_strategies_route_is_mounted():
    app = FastAPI()
    app.include_router(qis_routes.router, prefix="/api/v1/qis")
    client = TestClient(app)
    res = client.get("/api/v1/qis/strategies")
    assert res.status_code == 200
    body = res.json()
    ids = {s["id"] for s in body["strategies"]}
    assert "equity_3_6_calendar_vol_300" in ids
