"""NDX implied CRP service — reads corr_ndx_implied_crp.parquet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "data" / "corr_ndx_implied_crp.parquet"


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


def test_implied_corr_service_meta_and_series(panel_exists: bool):
    if not panel_exists:
        pytest.skip("corr_ndx_implied_crp.parquet not built")
    from engine import implied_corr_service as svc

    svc.load_panel(force=True)
    m = svc.meta()
    assert m["basket"] == "NDX"
    assert 30 in m["tenors"]
    assert m["n_rows"] > 0

    s = svc.series(tenor_days=30, mny=1.0, variant="clean")
    assert s["mode"] == "implied_crp"
    assert s["n_obs"] >= 1
    assert s["series"]
    # CRP identity when both legs present
    for row in s["series"]:
        if row.get("rho_imp") is not None and row.get("rho_real") is not None and row.get("crp") is not None:
            assert abs(row["crp"] - (row["rho_imp"] - row["rho_real"])) < 1e-9


def test_ndx_has_implied_crp_capability():
    from config.universe_registry import load_registry

    load_registry.cache_clear()
    reg = load_registry()
    ndx = next(t for t in reg["targets"] if t["symbol"] == "NDX")
    assert "implied_crp" in ndx["capabilities"]
