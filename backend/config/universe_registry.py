"""Multi-market / multi-asset registry for Realized Correlation UI."""

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

# Market buckets
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
    # indices
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


def _asset_class(typ: str, calypso: str) -> str:
    if typ == "BASKET":
        return "BASKET"
    if typ == "FX" or "/" in calypso:
        return "FX"
    if typ == "IDX" or ("." not in calypso and "/" not in calypso):
        return "INDEX"
    return "EQUITY"


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
            ac = _asset_class(typ, cal)
            caps = ["pairwise_corr", "corr_matrix", "corr_spread"]
            if ac == "BASKET":
                caps = ["corr_matrix"]
            targets.append(
                {
                    "symbol": cal,
                    "name": cal,
                    "type": typ,
                    "market": market,
                    "asset_class": ac,
                    "subregion": sub,
                    "currency": _currency(sub),
                    "sec_id": row.get("sec_id") or "",
                    "bb_ticker": row.get("bb_ticker") or "",
                    "capabilities": caps,
                }
            )

    # tree: Market → AssetClass → targets
    tree: dict[str, Any] = {}
    for t in targets:
        m = t["market"]
        ac = t["asset_class"]
        tree.setdefault(m, {}).setdefault(ac, []).append(
            {k: t[k] for k in ("symbol", "name", "subregion", "currency", "capabilities")}
        )
    for m in tree:
        for ac in tree[m]:
            tree[m][ac] = sorted(tree[m][ac], key=lambda x: x["symbol"])

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
        "source": str(path),
    }


def resolve_symbols(symbols: list[str]) -> list[str]:
    reg = load_registry()
    known = {t["symbol"] for t in reg["targets"]}
    return [s for s in symbols if s in known]
