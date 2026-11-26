"""Implied CRP service — densified NDX + multi-basket + surface."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "data" / "corr_ndx_implied_crp.parquet"
SPX = ROOT / "data" / "corr_spx_implied_crp.parquet"
HSI = ROOT / "data" / "corr_hsi_implied_crp.parquet"


@pytest.fixture(scope="module")
def panel_exists() -> bool:
    return PANEL.is_file()


def test_implied_crp_parquet_schema(panel_exists: bool):
    if not panel_exists:
        pytest.skip("corr_ndx_implied_crp.parquet not built")
    df = pd.read_parquet(PANEL)
    for col in ("trade_date", "tenor_days", "mny", "variant", "rho_imp", "rho_real", "crp"):
        assert col in df.columns
    assert set(df["variant"].astype(str).str.lower().unique()) <= {"clean", "dirty"}
    assert df["tenor_days"].nunique() >= 1
    assert df["trade_date"].nunique() >= 100


def test_implied_corr_service_meta_and_series(panel_exists: bool):
    if not panel_exists:
        pytest.skip("corr_ndx_implied_crp.parquet not built")
    from engine import implied_corr_service as svc

    svc.load_panel(force=True)
    m = svc.meta("NDX")
    assert m["basket"] == "NDX"
    assert 30 in m["tenors"]
    assert m["n_rows"] > 0
    assert m["n_dates"] >= 100
    assert "implied_crp_surface" in m["capabilities"]
    assert any(b["basket"] == "NDX" and b["available"] for b in m["baskets"])

    s = svc.series(tenor_days=30, mny=1.0, variant="clean", basket="NDX")
    assert s["mode"] == "implied_crp"
    assert s["n_obs"] >= 1
    assert s["series"]
    for row in s["series"]:
        if row.get("rho_imp") is not None and row.get("rho_real") is not None and row.get("crp") is not None:
            assert abs(row["crp"] - (row["rho_imp"] - row["rho_real"])) < 1e-9


def test_implied_corr_levels_skew_term_surface(panel_exists: bool):
    if not panel_exists:
        pytest.skip("corr_ndx_implied_crp.parquet not built")
    from engine import implied_corr_service as svc

    svc.load_panel(force=True)
    m = svc.meta("NDX")
    lv = svc.levels(
        variant="clean",
        metrics="rho_imp,rho_real,crp",
        tenors="30,60",
        mnys="1",
        basket="NDX",
    )
    assert lv["basket"] == "NDX"
    assert "1" in lv["levels"]["rho_imp"]["30"]
    assert len(lv["levels"]["rho_imp"]["30"]["1"]) >= 50

    asof = m["history_end"]
    sk = svc.skew_slice(trade_date=asof, tenor_days=30, variant="clean", basket="NDX")
    assert sk["mode"] == "skew"
    assert len(sk["points"]) >= 1

    tm = svc.term_structure(trade_date=asof, mny=1.0, variant="clean", basket="NDX")
    assert tm["mode"] == "term"
    assert len(tm["points"]) >= 1

    sf = svc.surface_slice(trade_date=asof, variant="clean", metric="rho_imp", basket="NDX")
    assert sf["mode"] == "surface"
    assert sf["tenors"]
    assert sf["mnys"]
    assert len(sf["z"]) == len(sf["tenors"])
    assert all(len(row) == len(sf["mnys"]) for row in sf["z"])


def test_multi_basket_spx_hsi():
    from engine import implied_corr_service as svc

    if not SPX.is_file() or not HSI.is_file():
        pytest.skip("SPX/HSI CRP panels not seeded")
    for b in ("SPX", "HSI"):
        svc.load_panel(b, force=True)
        m = svc.meta(b)
        assert m["basket"] == b
        assert m["n_dates"] >= 1
        sf = svc.surface_slice(variant="clean", metric="crp", basket=b)
        assert sf["basket"] == b
        assert sf["z"]


def test_ndx_spx_hsi_have_implied_crp_capability():
    from config.universe_registry import load_registry

    load_registry.cache_clear()
    reg = load_registry()
    for sym in ("NDX", "SPX", "HSI"):
        t = next(x for x in reg["targets"] if x["symbol"] == sym)
        assert "implied_crp" in t["capabilities"]
