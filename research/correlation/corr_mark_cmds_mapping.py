"""Calypso / Beidou-style codes → CMDS SECURITYCODE / FXEOD keys.

Does not modify corr_mark.py. Proxy / force_proxy mirrored for Phase C.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_UNIVERSE = ROOT / "data" / "corr_mark_universe.csv"

# Beidou aliases (from corr_mark.py)
BEIDOU_TO_CALYPSO = {
    "FB.US": "META.US",
    "KOSPI2": "KOSPI200",
    "SQ.US": "XYZ.US",
    "GOLD.US": "B.US",
    "YY.US": "JOYY.US",
    "3939.HK": "2979.HK",
}

# Force proxy: calypso → (bb without Equity suffix preference, fxconv, ccy)
FORCE_PROXY = {
    "3750.HK": ("300750 C2", "HKDCNH", "HKD"),
    "CRCL.US": ("COIN US", None, "USD"),
    # Prefer CG (Wind-style); C1 kept as fallback candidate in fetch
    "688795.CH": ("688256 CG", None, "CNY"),
    "SKHY.US": ("MU US", None, "USD"),
    "3308.HK": ("300308 C2", "HKDCNH", "HKD"),
}

# Extra BB_TICKER candidates when primary FORCE_PROXY miss (order = preference)
FORCE_PROXY_BB_FALLBACKS = {
    "688795.CH": ["688256 CG", "688256 C1", "688256 CH"],
}

# ADR ↔ HK proxy (region-dependent in source; store both legs)
PROXY_INFO = {
    "BABA.US": ("9988 HK", "USDHKD"),
    "BIDU.US": ("9888 HK", "USDHKD"),
    "BILI.US": ("9626 HK", "USDHKD"),
    "JD.US": ("9618 HK", "USDHKD"),
    "LI.US": ("2015 HK", "USDHKD"),
    "NIO.US": ("9866 HK", "USDHKD"),
    "NTES.US": ("9999 HK", "USDHKD"),
    "XPEV.US": ("9868 HK", "USDHKD"),
    "9988.HK": ("BABA US", "HKDUSD"),
    "9888.HK": ("BIDU US", "HKDUSD"),
    "9626.HK": ("BILI US", "HKDUSD"),
    "9618.HK": ("JD US", "HKDUSD"),
    "2015.HK": ("LI US", "HKDUSD"),
    "9866.HK": ("NIO US", "HKDUSD"),
    "9999.HK": ("NTES US", "HKDUSD"),
    "9868.HK": ("XPEV US", "HKDUSD"),
}

CURRENCY_MAP = {
    "CH": "CNY",
    "HK": "HKD",
    "JP": "JPY",
    "KS": "KRW",
    "EU": "EUR",
    "LN": "GBP",
    "US": "USD",
    "AU": "AUD",
    "SG": "SGD",
    "SW": "CHF",
    "OC": "USD",
    "UP": "USD",
    "C1": "CNY",
    "C2": "CNY",
}

# Index BB candidates
IDX_BB = {
    "SPX": ["SPX Index", "SPX"],
    "NDX": ["NDX Index", "NDX"],
    "SX5E": ["SX5E", "SX5E Index"],
    "NKY": ["NKY Index", "NKY"],
    "KOSPI2": ["KOSPI2", "KOSPI2 Index"],
    "KOSPI200": ["KOSPI2", "KOSPI200 Index"],
    "HSI": ["HSI Index", "HSI"],
    "HSCEI": ["HSCEI Index", "HSCEI"],
    "HSTECH": ["HSTECH Index", "HSTECH"],
    "SHSZ300": ["SHSZ300 Index", "SHSZ300"],
    "CSI1000": ["CSI1000 Index", "CSI1000"],
    "SH000905": ["SH000905 Index", "SH000905"],
    "SSE50": ["SSE50", "SSE50 Index"],
}

# SubRegion → Region (desk-style coarse map for Phase C)
SUBREGION_TO_REGION = {
    "CH": "APAC",
    "HK": "APAC",
    "JP": "APAC",
    "KS": "APAC",
    "SG": "APAC",
    "AU": "APAC",
    "US": "NAM",
    "OC": "NAM",
    "UP": "NAM",
    "EU": "EMEA",
    "LN": "EMEA",
    "SW": "EMEA",
    "C1": "APAC",
    "C2": "APAC",
}

INDEX_SUBREGION = {
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


def collapse_calypso(t: str) -> str:
    c = BEIDOU_TO_CALYPSO.get(t, t)
    return (
        c.replace("KOSPI200", "KOSPI2")
        .replace(".C1", ".CH")
        .replace(".C2", ".CH")
        .replace(".UP", ".US")
        .replace(".UW", ".US")
        .replace(".UN", ".US")
        .replace(".OC", ".US")
    )


def eq_bb_candidates(cal: str) -> list[str]:
    if "." not in cal:
        return []
    root, suf = cal.rsplit(".", 1)
    cands: list[str] = []
    if suf == "HK":
        if root.isdigit():
            cands += [f"{root} HK", f"{int(root):04d} HK", f"{root.zfill(4)} HK"]
        else:
            cands.append(f"{root} HK")
    elif suf == "US":
        cands += [f"{root} US", f"{root} UW", f"{root} UN", f"{root} UP"]
    elif suf in ("CH", "C1", "C2"):
        cands += [f"{root} C1", f"{root} C2", f"{root} CH", f"{root} CG"]
    elif suf == "SW":
        cands += [f"{root} SE", f"{root} SW"]
    elif suf == "JP":
        cands += [f"{root} JT", f"{root} JP"]
    elif suf == "KS":
        cands += [f"{root} KS", f"{root} KP"]
    else:
        cands.append(f"{root} {suf}")
    return list(OrderedDict.fromkeys(cands))


def eq_wind_candidates(cal: str) -> list[str]:
    if "." not in cal:
        return []
    root, suf = cal.rsplit(".", 1)
    if suf in ("CH", "C1", "C2"):
        return [f"{root}.SH", f"{root}.SZ"]
    if suf == "HK" and root.isdigit():
        return [f"{int(root):04d}.HK"]
    if suf == "US":
        return [f"{root}.O", f"{root}.N", f"{root}.A", f"{root}.K", f"{root}.P"]
    return []


def fx_windcode(pair: str) -> str:
    a, b = pair.split("/")
    return f"{a}{b}.FX"


def invert_fx_pair(pair: str) -> str:
    a, b = pair.split("/")
    return f"{b}/{a}"


def subregion_of(calypso: str) -> str:
    if "/" in calypso:
        return "FX"
    if "." in calypso:
        return calypso.rsplit(".", 1)[1]
    return INDEX_SUBREGION.get(calypso, INDEX_SUBREGION.get(collapse_calypso(calypso), "Unknown"))


def region_of(calypso: str) -> str:
    sr = subregion_of(calypso)
    if sr == "FX":
        return "FX"
    return SUBREGION_TO_REGION.get(sr, "Unknown")


def currency_of(calypso: str) -> str:
    sr = subregion_of(calypso)
    return CURRENCY_MAP.get(sr, "Unknown")


def load_universe(path: Path | str | None = None) -> pd.DataFrame:
    p = Path(path or DEFAULT_UNIVERSE)
    df = pd.read_csv(p)
    for c in ("sec_id", "bb_ticker", "wind_code", "fx_wind", "invert_of", "note"):
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str)
    return df


def active_symbols(universe: pd.DataFrame, types: list[str] | None = None) -> pd.DataFrame:
    out = universe[universe["status"] == "active"].copy()
    if types:
        out = out[out["type"].isin(types)]
    return out.reset_index(drop=True)


def resolve_price_symbol(row: pd.Series) -> str:
    """Panel symbol key = calypso code (unique axis name)."""
    return str(row["calypso"])
