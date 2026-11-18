"""Universe cascade: EQUITY/COMMODITY/FX → market → INDEX/SINGLE_STOCK."""
from __future__ import annotations


def test_registry_asset_class_cascade():
    from config.universe_registry import load_registry

    load_registry.cache_clear()
    reg = load_registry()
    assert "EQUITY" in reg["tree"]
    assert "FX" in reg["tree"]
    assert "COMMODITY" in reg["asset_classes"]

    eq = reg["tree"]["EQUITY"]
    assert "US" in eq
    assert "INDEX" in eq["US"]
    assert "SINGLE_STOCK" in eq["US"]
    assert any(t["symbol"] == "SPX" for t in eq["US"]["INDEX"])
    assert any(t["symbol"].endswith(".US") for t in eq["US"]["SINGLE_STOCK"])

    # No legacy EQUITY bucket under markets
    for market in eq.values():
        assert "EQUITY" not in market

    fx = reg["tree"]["FX"]
    assert fx
    # FX leaves live under instrument kind FX
    flat = [t for m in fx.values() for kinds in m.values() for t in kinds]
    assert any("/" in t["symbol"] for t in flat)

    for t in reg["targets"]:
        if t["asset_class"] == "EQUITY" and t["instrument"] == "INDEX":
            assert "." not in t["symbol"] and "/" not in t["symbol"]
        if t["instrument"] == "SINGLE_STOCK":
            assert t["asset_class"] in ("EQUITY", "COMMODITY")
