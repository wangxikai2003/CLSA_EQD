"""HTTP-level smoke: QIS backtest durable cache hit / refresh / fingerprint miss."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1 import qis_backtest as qb
from api.v1 import qis_results_store as qrs


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(qrs, "_DB_CANDIDATES", [tmp_path / "qis_results.sqlite"])
    equity = tmp_path / "cffex_vol.sqlite"
    equity.write_bytes(b"db")
    spot = tmp_path / "index_spot.parquet"
    spot.write_bytes(b"sp")
    monkeypatch.setattr(qb.st, "_equity_db", lambda: equity)
    monkeypatch.setattr(qb.st, "_SPOT_CANDIDATES", [spot])

    fake = {
        "strategy_id": "cross_calendar_carry",
        "strategy_name": "Cross Calendar Carry",
        "description": "test",
        "legs": [],
        "params": {
            "initial_nav": qb._INITIAL_NAV,
            "slippage_bp": 2,
            "fee_pct": 0.0023,
            "cost_rate": 0,
            "rebalance": "daily",
            "signal_lag": "x",
            "price": "y",
        },
        "nav": [{"d": "2020-01-01", "nav": 1.0, "sos": 0.0}],
        "metrics": {},
        "summary": {
            "start": "2020-01-01",
            "end": "2020-01-02",
            "n_days": 2,
            "final_nav": 1e8,
            "final_nav_multiple": 1.0,
            "total_cost": 0,
        },
    }
    calls = {"n": 0}

    def fake_run(*_a, **_k):
        calls["n"] += 1
        return dict(fake)

    monkeypatch.setattr(qb, "run_cross_calendar_carry", fake_run)
    qb._cache.clear()
    qb._cache.update({"key": None, "payload": None})

    app = FastAPI()
    app.include_router(qb.router, prefix="/api/v1/qis")
    return TestClient(app), calls, equity


def test_cross_calendar_db_reuse_and_refresh(client):
    cli, calls, equity = client
    r1 = cli.get("/api/v1/qis/backtest/cross_calendar_carry").json()
    r2 = cli.get("/api/v1/qis/backtest/cross_calendar_carry").json()
    r3 = cli.get("/api/v1/qis/backtest/cross_calendar_carry?refresh=true").json()
    assert r1["_cache"]["source"] == "miss" and r1["_cache"]["hit"] is False
    assert r2["_cache"]["source"] == "db" and r2["_cache"]["hit"] is True
    assert r3["_cache"]["source"] == "miss" and r3["_cache"]["hit"] is False
    assert calls["n"] == 2

    qb._cache.clear()
    qb._cache.update({"key": None, "payload": None})
    r4 = cli.get("/api/v1/qis/backtest/cross_calendar_carry").json()
    assert r4["_cache"]["source"] == "db"
    assert calls["n"] == 2

    equity.write_bytes(b"changed")
    r5 = cli.get("/api/v1/qis/backtest/cross_calendar_carry").json()
    assert r5["_cache"]["source"] == "miss"
    assert calls["n"] == 3
