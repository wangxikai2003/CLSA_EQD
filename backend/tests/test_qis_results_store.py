"""QIS result-store: put → get hit; source_key mismatch → miss; carry sentinel PK."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def store(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    import importlib

    import data_paths as dp
    import api.v1.qis_results_store as qrs

    importlib.reload(dp)
    importlib.reload(qrs)
    # Force writes into the temp live dir even if a bundled sqlite exists.
    monkeypatch.setattr(qrs, "_dp_qis", lambda: tmp_path / "qis_results.sqlite")
    return qrs


def test_put_get_hit_and_fingerprint_miss(store):
    qrs = store
    payload = {"strategy_id": "equity_3_6_calendar_vol_300", "summary": {"final_nav_multiple": 1.1}}
    qrs.put_cached(
        "equity_3_6_calendar_vol_300",
        252,
        1e8,
        "src-v1",
        payload,
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        signal_mode="regime",
    )
    hit = qrs.get_cached(
        "equity_3_6_calendar_vol_300",
        252,
        1e8,
        "src-v1",
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        signal_mode="regime",
    )
    assert hit is not None
    assert hit["_cache"]["hit"] is True
    assert hit["_cache"]["source"] == "db"
    assert hit["summary"]["final_nav_multiple"] == 1.1

    miss = qrs.get_cached(
        "equity_3_6_calendar_vol_300",
        252,
        1e8,
        "src-v2",
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        signal_mode="regime",
    )
    assert miss is None


def test_carry_sentinel_row(store):
    qrs = store
    cs = qrs.cost_sig(2e-4, 0.0023 / 100.0)
    payload = {"strategy_id": "cross_calendar_carry", "summary": {"final_nav_multiple": 1.5}}
    qrs.put_cached(
        "cross_calendar_carry",
        qrs.CROSS_Z_WINDOW,
        1e8,
        "srcA",
        payload,
        tenor_pair=qrs.CROSS_TENOR,
        side_mode=qrs.CROSS_SIDE,
        spread_mode=qrs.CROSS_SPREAD,
        signal_mode=cs,
    )
    hit = qrs.get_cached(
        "cross_calendar_carry",
        qrs.CROSS_Z_WINDOW,
        1e8,
        "srcA",
        tenor_pair=qrs.CROSS_TENOR,
        side_mode=qrs.CROSS_SIDE,
        spread_mode=qrs.CROSS_SPREAD,
        signal_mode=cs,
    )
    assert hit is not None
    assert hit["_cache"]["signal_mode"] == cs
    assert hit["_cache"]["source"] == "db"
