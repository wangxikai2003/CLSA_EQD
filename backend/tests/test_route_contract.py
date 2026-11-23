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


def _route_paths(router) -> set[str]:
    found: set[str] = set()
    for route in getattr(router, "routes", []):
        for attr in ("path", "path_format"):
            val = getattr(route, attr, None)
            if isinstance(val, str) and val:
                found.add(val)
    return found


def test_correlation_market_exotic_qis_routes_exist():
    """Assert on each APIRouter, not app.routes, so a FastAPI upgrade cannot hide mounts."""
    corr = _route_paths(corr_routes.router)
    market = _route_paths(market_routes.router)
    exotic = _route_paths(exotic_routes.router)
    qis = _route_paths(qis_routes.router)
    assert any(p.endswith("/universe") for p in corr), corr
    assert any(p.endswith("/yield-curve") for p in market), market
    assert any(p.endswith("/rv-iv-predict") for p in market), market
    assert any(p.endswith("/presets") for p in exotic), exotic
    assert any(p == "/strategies" or p.endswith("/strategies") for p in qis), qis
    assert any("backtest/{strategy_id}" in p for p in qis), qis


def test_strategies_route_is_mounted():
    app = FastAPI()
    app.include_router(qis_routes.router, prefix="/api/v1/qis")
    client = TestClient(app)
    res = client.get("/api/v1/qis/strategies")
    assert res.status_code == 200
    body = res.json()
    ids = {s["id"] for s in body["strategies"]}
    assert "equity_3_6_calendar_vol_300" in ids
