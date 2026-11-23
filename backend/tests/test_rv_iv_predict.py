"""Unit tests for RV→IV predict engine (Market Data tab)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_VOL_DB = ROOT / "data" / "cffex_vol.sqlite"
needs_vol_db = pytest.mark.skipif(
    not (_VOL_DB.exists() and _VOL_DB.stat().st_size > 0),
    reason="data/cffex_vol.sqlite is local-only",
)

from engine.rv_iv_predict import (
    PRODUCT_META,
    compute_rv_frame,
    predict_from_rv,
    run_predict,
)


def _synthetic_spot(n: int = 800, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, 0.012, size=n)
    px = 4000.0 * np.exp(np.cumsum(rets))
    idx = pd.bdate_range("2020-01-02", periods=n)
    return pd.Series(px, index=idx, name="close")


def test_compute_rv_frame_windows():
    spot = _synthetic_spot(400)
    rv = compute_rv_frame(spot)
    for w in (21, 42, 63, 126, 252):
        assert f"RV_{w}" in rv.columns
        assert rv[f"RV_{w}"].dropna().shape[0] > 0
        assert float(rv[f"RV_{w}"].dropna().iloc[-1]) >= 0.01


def test_predict_from_rv_quotes_and_series():
    spot = _synthetic_spot(900)
    rv = compute_rv_frame(spot)
    out = predict_from_rv(rv)
    assert out["quotes"].get("Mid_3M") is not None
    assert out["quotes"].get("Bid_3M") is not None
    assert out["quotes"].get("Offer_3M") is not None
    assert out["quotes"]["Offer_3M"] >= out["quotes"]["Mid_3M"] >= out["quotes"]["Bid_3M"]
    assert len(out["series"]) > 10
    assert out["current"]["rv_252"] is not None


def test_product_meta_keys():
    assert set(PRODUCT_META) == {"HO", "IO", "MO"}


@needs_vol_db
def test_run_predict_rv_io():
    payload = run_predict("IO", mode="rv", source="futures")
    assert payload["product"] == "IO"
    assert payload["mode"] == "rv"
    assert payload["quotes"].get("Mid_3M") is not None
    assert payload["current"].get("rv_63") is not None
    assert isinstance(payload["series"], list) and len(payload["series"]) > 0


@needs_vol_db
def test_run_predict_bench_mo_vs_io():
    payload = run_predict("MO", mode="bench", source="futures", benchmark="IO")
    assert payload["mode"] == "bench"
    assert payload["benchmark"]["product"] == "IO"
    assert payload["quotes"].get("Mid_3M") is not None or payload["quotes"].get("Mid_63") is not None
