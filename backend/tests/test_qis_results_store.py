"""Unit tests for durable QIS backtest result cache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.v1 import qis_results_store as qrs


@pytest.fixture()
def tmp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "qis_results.sqlite"
    monkeypatch.setattr(qrs, "_DB_CANDIDATES", [db])
    return db


def test_put_get_roundtrip_and_source_fingerprint(tmp_store: Path, tmp_path: Path):
    equity = tmp_path / "eq.sqlite"
    equity.write_bytes(b"x")
    spot = tmp_path / "spot.parquet"
    spot.write_bytes(b"y")
    src = qrs.source_key(equity, spot)

    payload = {
        "strategy_id": "equity_3_6_calendar_vol_300",
        "summary": {"final_nav_multiple": 1.25},
    }
    qrs.put_cached(
        "equity_3_6_calendar_vol_300",
        252,
        1e8,
        src,
        payload,
        tenor_pair="3M-6M",
        side_mode="long_only",
        spread_mode="iv",
        cost_sig_value=qrs.cost_sig(0.0, 0.0),
    )

    hit = qrs.get_cached(
        "equity_3_6_calendar_vol_300",
        252,
        1e8,
        src,
        tenor_pair="3M-6M",
        side_mode="long_only",
        spread_mode="iv",
        cost_sig_value=qrs.cost_sig(0.0, 0.0),
    )
    assert hit is not None
    assert hit["summary"]["final_nav_multiple"] == 1.25
    assert hit["_cache"]["hit"] is True
    assert hit["_cache"]["source"] == "db"

    # fingerprint change → miss
    equity.write_bytes(b"xx")
    src2 = qrs.source_key(equity, spot)
    assert src2 != src
    assert (
        qrs.get_cached(
            "equity_3_6_calendar_vol_300",
            252,
            1e8,
            src2,
            tenor_pair="3M-6M",
            side_mode="long_only",
            spread_mode="iv",
        )
        is None
    )


def test_cross_calendar_cost_sig_is_separate_key(tmp_store: Path, tmp_path: Path):
    equity = tmp_path / "eq.sqlite"
    equity.write_bytes(b"a")
    src = qrs.source_key(equity, None)
    cs_a = qrs.cost_sig(2e-4, 0.0023 / 100.0)
    cs_b = qrs.cost_sig(3e-4, 0.0023 / 100.0)

    qrs.put_cached(
        "cross_calendar_carry",
        qrs.CROSS_Z_WINDOW,
        1e8,
        src,
        {"strategy_id": "cross_calendar_carry", "summary": {"final_nav_multiple": 1.01}},
        tenor_pair=qrs.CROSS_TENOR,
        side_mode=qrs.CROSS_SIDE,
        spread_mode=qrs.CROSS_SPREAD,
        cost_sig_value=cs_a,
    )
    assert (
        qrs.get_cached(
            "cross_calendar_carry",
            qrs.CROSS_Z_WINDOW,
            1e8,
            src,
            tenor_pair=qrs.CROSS_TENOR,
            side_mode=qrs.CROSS_SIDE,
            spread_mode=qrs.CROSS_SPREAD,
            cost_sig_value=cs_a,
        )
        is not None
    )
    assert (
        qrs.get_cached(
            "cross_calendar_carry",
            qrs.CROSS_Z_WINDOW,
            1e8,
            src,
            tenor_pair=qrs.CROSS_TENOR,
            side_mode=qrs.CROSS_SIDE,
            spread_mode=qrs.CROSS_SPREAD,
            cost_sig_value=cs_b,
        )
        is None
    )


def test_migrate_adds_cost_sig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sqlite3

    db = tmp_path / "legacy.sqlite"
    monkeypatch.setattr(qrs, "_DB_CANDIDATES", [db])
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE qis_backtest_cache (
          strategy_id TEXT NOT NULL,
          z_window INTEGER NOT NULL,
          initial_nav REAL NOT NULL,
          tenor_pair TEXT NOT NULL,
          side_mode TEXT NOT NULL,
          spread_mode TEXT NOT NULL,
          source_key TEXT NOT NULL,
          payload TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode)
        );
        """
    )
    conn.execute(
        """
        INSERT INTO qis_backtest_cache VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            "equity_3_6_calendar_vol_300",
            252,
            1e8,
            "3M-6M",
            "both",
            "iv",
            "db:1",
            json.dumps({"ok": True}),
            "t0",
        ),
    )
    conn.commit()
    conn.close()

    # connect() migrates
    hit = qrs.get_cached(
        "equity_3_6_calendar_vol_300",
        252,
        1e8,
        "db:1",
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        cost_sig_value="0|0",
    )
    assert hit is not None
    assert hit["ok"] is True
    rows = qrs.list_cached()
    assert rows[0]["cost_sig"] == "0|0"
