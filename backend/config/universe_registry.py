"""Multi-asset registry for Realized Correlation UI.

Cascade: AssetClass (EQUITY / COMMODITY / FX)
      → Market (US / HK / CN / …)
      → Instrument (INDEX / SINGLE_STOCK / FX / BASKET)
      → Target
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CANDIDATES = [
    ROOT / "research" / "correlation" / "data" / "corr_mark_universe.csv",
    ROOT / "data" / "corr_mark_universe.csv",
]

# Market buckets (region)
_SUBREGION_MARKET = {
    "US": "US",
    "UP": "US",
    "OC": "US",
    "HK": "HK",
    "CH": "CN",
    "C1": "CN",
    "C2": "CN",
    "EU": "EU_JP_KR",
    "JP": "EU_JP_KR",
    "KS": "EU_JP_KR",
    "LN": "EU_JP_KR",
    "SW": "EU_JP_KR",
    "AU": "EU_JP_KR",
    "SG": "HK",
    "FX": "FX",
}

# Top-level asset-class families (peer of COMMODITY / FX)
ASSET_CLASSES = ("EQUITY", "COMMODITY", "FX")

PRESETS: dict[str, list[str]] = {
    "Global Major Indices": [
        "SPX",
        "NDX",
        "SX5E",
        "NKY",
        "HSI",
        "HSCEI",
        "HSTECH",
        "SHSZ300",
        "CSI1000",
        "SSE50",
        "KOSPI2",
    ],
    "US Tech Giants": [
        "AAPL.US",
        "NVDA.US",
        "META.US",
        "QQQ.US",
        "SOXX.US",
        "MRVL.US",
        "MU.US",
    ],
    "China A+H Core": [
        "700.HK",
        "9988.HK",
        "9618.HK",
        "9888.HK",
        "SHSZ300",
        "CSI1000",
        "HSTECH",
        "HSCEI",
    ],
    "G10 & Asia FX": [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "USD/HKD",
        "USD/CNH",
        "USD/CNY",
        "USD/KRW",
        "AUD/USD",
    ],
}

ROLLING_WINDOWS = [21, 63, 126, 252]


def _universe_path() -> Path:
    for p in UNIVERSE_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("corr_mark_universe.csv not found")


def _subregion(calypso: str, typ: str) -> str:
    if typ == "FX" or "/" in calypso:
        return "FX"
    if "." in calypso:
        return calypso.rsplit(".", 1)[1]
    idx_map = {
        "SPX": "US",
        "NDX": "US",
        "SX5E": "EU",
        "NKY": "JP",
        "KOSPI2": "KS",
        "KOSPI200": "KS",
        "HSI": "HK",
        "HSCEI": "HK",
        "HSTECH": "HK",
        "SHSZ300": "CH",
        "CSI1000": "CH",
        "SH000905": "CH",
        "SSE50": "CH",
    }
    return idx_map.get(calypso, "US")


def _currency(sub: str) -> str:
    return {
        "US": "USD",
        "UP": "USD",
        "OC": "USD",
        "HK": "HKD",
        "CH": "CNY",
        "C1": "CNY",
        "C2": "CNY",
        "JP": "JPY",
        "KS": "KRW",
        "EU": "EUR",
        "LN": "GBP",
        "SW": "CHF",
        "AU": "AUD",
        "SG": "SGD",
        "FX": "FX",
    }.get(sub, "USD")


def _classify(typ: str, calypso: str) -> tuple[str, str]:
    """Return (asset_class family, instrument kind).

    Families: EQUITY | COMMODITY | FX
    Instruments under EQUITY: INDEX | SINGLE_STOCK | BASKET
    Under FX: FX
    """
    if typ == "FX" or "/" in calypso:
        return "FX", "FX"
    if typ == "BASKET":
        return "EQUITY", "BASKET"
    if typ == "CMD" or typ == "COMMODITY":
        return "COMMODITY", "SINGLE_STOCK"
    if typ == "IDX" or ("." not in calypso and "/" not in calypso):
        return "EQUITY", "INDEX"
    # listed equities / ETFs with exchange suffix
    return "EQUITY", "SINGLE_STOCK"


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    path = _universe_path()
    targets: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = (row.get("status") or "").strip()
            if status != "active":
                continue
            cal = (row.get("calypso") or "").strip()
            if not cal or cal == "KOSPI200":
                continue
            typ = (row.get("type") or "").strip() or "EQ"
            sub = _subregion(cal, typ)
            market = _SUBREGION_MARKET.get(sub, "US")
            asset_class, instrument = _classify(typ, cal)
            caps = ["pairwise_corr", "corr_matrix", "corr_spread"]
            if instrument == "BASKET":
                caps = ["corr_matrix"]
            # Basket average implied / CRP (precomputed parquet; NDX densified, SPX/HSI may bootstrap)
            if cal in ("NDX", "SPX", "HSI"):
                caps = list(dict.fromkeys([*caps, "implied_crp"]))

            targets.append(
                {
                    "symbol": cal,
                    "name": cal,
                    "type": typ,
                    "market": market,
                    "asset_class": asset_class,
                    "instrument": instrument,
                    # backward-compatible alias used by older clients
                    "kind": instrument,
                    "subregion": sub,
                    "currency": _currency(sub),
                    "sec_id": row.get("sec_id") or "",
                    "bb_ticker": row.get("bb_ticker") or "",
                    "capabilities": caps,
                }
            )

    # tree: AssetClass → Market → Instrument → targets
    tree: dict[str, Any] = {ac: {} for ac in ASSET_CLASSES}
    for t in targets:
        ac = t["asset_class"]
        m = t["market"]
        inst = t["instrument"]
        tree.setdefault(ac, {}).setdefault(m, {}).setdefault(inst, []).append(
            {
                k: t[k]
                for k in ("symbol", "name", "subregion", "currency", "capabilities", "instrument")
            }
        )
    for ac in tree:
        for m in tree[ac]:
            for inst in tree[ac][m]:
                tree[ac][m][inst] = sorted(tree[ac][m][inst], key=lambda x: x["symbol"])

    # Ensure empty COMMODITY placeholder so UI can show the family
    tree.setdefault("COMMODITY", {})

    presets = []
    sym_set = {t["symbol"] for t in targets}
    for name, syms in PRESETS.items():
        present = [s for s in syms if s in sym_set]
        presets.append({"id": name, "label": name, "symbols": present})

    return {
        "tree": tree,
        "targets": targets,
        "presets": presets,
        "rolling_windows": ROLLING_WINDOWS,
        "asset_classes": list(ASSET_CLASSES),
        "source": str(path),
    }


def resolve_symbols(symbols: list[str]) -> list[str]:
    reg = load_registry()
    known = {t["symbol"] for t in reg["targets"]}
    return [s for s in symbols if s in known]
