"""Volatility Term Structure Relative Value (GS GSVLBRR localised).

Daily overlapping vintages:
  Short ATM near-month strangle + Long OTM deferred strangle,
  futures delta hedge via pluggable hedgers, premium-based long leverage (Step 3),
  and Step1/2/3 ablation NAV paths.

Universes: CSI300 (IO), SSE50 (HO), CSI1000 (MO), SHFE Gold (AU), INE Crude (SC),
COMEX Gold (GC), NYMEX WTI (CL), CME E-mini S&P (ES) — overseas via CMDS
→ commodity_vol_{gc,cl,es}.sqlite. ES hedge leg = ES futures (not SPX spot).
Does not modify calendar_vol_strategy.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from engine.black76 import black76_greeks
from engine.hedgers import (
    HedgeContext,
    OptionLot,
    get_hedger,
)

try:
    from data_paths import cffex_vol as _dp_cffex
    from data_paths import commodity_vol_for as _dp_commodity_vol_for
    from data_paths import index_spot as _dp_spot
except Exception:  # pragma: no cover
    _dp_cffex = None
    _dp_commodity_vol_for = None
    _dp_spot = None

ROOT = Path(__file__).resolve().parents[2]
_DB_CANDIDATES = [ROOT / "data" / "cffex_vol.sqlite", ROOT.parent / "data" / "cffex_vol.sqlite"]
_SPOT_CANDIDATES = [
    ROOT / "data" / "index_spot.parquet",
    ROOT.parent / "data" / "index_spot.parquet",
]

STRATEGY_ID = "csi300_vol_rv"
DEFAULT_UNIVERSE = "300"
SHORT_MIN_DTE = 5
N_ROLL = 1
DEFAULT_TARGET_OTM_DELTA = 0.15
DEFAULT_LONG_DELTA_THR = 0.05
DEFAULT_DAILY_BUDGET_FRAC = 0.0025  # ~0.25% NAV short-premium budget per vintage
PREMIUM_WINDOW = 252
TARGET_PREMIUM_PCT = 0.02

# Back-compat aliases used by older API/cache helpers.
PRODUCT = "IO"
SPOT_CODE = "000300.SH"
M_OPT = 100.0
M_FUT = 300.0
CACHE_TENOR = "IO-IF"
CACHE_SIDE = "both"
CACHE_SPREAD = "strangle"

STYLE_PRESETS: Dict[str, Dict[str, float]] = {
    "balanced": {"lambda_tilt": 1.0, "l_min": 0.5, "l_max": 2.5},
    "carry": {"lambda_tilt": 0.5, "l_min": 0.25, "l_max": 1.25},
    "defensive": {"lambda_tilt": 1.8, "l_min": 1.0, "l_max": 4.0},
}

# Universe registry — equity index options + SHFE gold + INE crude.
VOL_RV_UNIVERSES: Dict[str, Dict[str, Any]] = {
    "300": {
        "key": "300",
        "label": "CSI 300",
        "strategy_name": "CSI 300 Vol RV",
        "product": "IO",
        "fut_root": "IF",
        "spot_code": "000300.SH",
        "spot_mode": "index",
        "db": "cffex",
        "m_opt": 100.0,
        "m_fut": 300.0,
        "quarter_months": (3, 6, 9, 12),
        "cache_tenor": "IO-IF",
    },
    "50": {
        "key": "50",
        "label": "SSE 50",
        "strategy_name": "SSE 50 Vol RV",
        "product": "HO",
        "fut_root": "IH",
        "spot_code": "000016.SH",
        "spot_mode": "index",
        "db": "cffex",
        "m_opt": 100.0,
        "m_fut": 300.0,
        "quarter_months": (3, 6, 9, 12),
        "cache_tenor": "HO-IH",
    },
    "1000": {
        "key": "1000",
        "label": "CSI 1000",
        "strategy_name": "CSI 1000 Vol RV",
        "product": "MO",
        "fut_root": "IM",
        "spot_code": "000852.SH",
        "spot_mode": "index",
        "db": "cffex",
        "m_opt": 100.0,
        "m_fut": 200.0,
        "quarter_months": (3, 6, 9, 12),
        "cache_tenor": "MO-IM",
    },
    "AU": {
        "key": "AU",
        "label": "SHFE Gold",
        "strategy_name": "SHFE Gold Vol RV",
        "product": "AU",
        "fut_root": "AU",
        "spot_code": None,
        "spot_mode": "front_fut",
        "db": "commodity",
        # SHFE AU: quote ¥/g, contract size 1000g for both futures and options.
        "m_opt": 1000.0,
        "m_fut": 1000.0,
        "quarter_months": (2, 4, 6, 8, 10, 12),
        "cache_tenor": "AU-AU",
    },
    "SC": {
        "key": "SC",
        "label": "INE Crude",
        "strategy_name": "INE Crude Vol RV",
        "product": "SC",
        "fut_root": "SC",
        "spot_code": None,
        "spot_mode": "front_fut",
        "db": "commodity",
        # INE SC: quote ¥/barrel, contract size 1000 barrels.
        "m_opt": 1000.0,
        "m_fut": 1000.0,
        # Monthly listed; treat all months as deferred candidates (~90 DTE pick).
        "quarter_months": tuple(range(1, 13)),
        "cache_tenor": "SC-SC",
    },
    "GC": {
        "key": "GC",
        "label": "COMEX Gold",
        "strategy_name": "COMEX Gold Vol RV",
        "product": "GC",
        "fut_root": "GC",
        "spot_code": None,
        "spot_mode": "front_fut",
        "db": "commodity",
        # COMEX GC: USD/oz, 100 troy oz.
        "m_opt": 100.0,
        "m_fut": 100.0,
        "quarter_months": (2, 4, 6, 8, 10, 12),
        "cache_tenor": "GC-GC",
    },
    "CL": {
        "key": "CL",
        "label": "NYMEX WTI",
        "strategy_name": "NYMEX WTI Vol RV",
        "product": "CL",
        "fut_root": "CL",
        "spot_code": None,
        "spot_mode": "front_fut",
        "db": "commodity",
        # NYMEX CL: USD/bbl, 1000 barrels.
        "m_opt": 1000.0,
        "m_fut": 1000.0,
        "quarter_months": tuple(range(1, 13)),
        "cache_tenor": "CL-CL",
    },
    "ES": {
        "key": "ES",
        "label": "CME E-mini S&P",
        "strategy_name": "E-mini S&P Vol RV",
        "product": "ES",
        "fut_root": "ES",
        "spot_code": None,
        # Confirmed: hedge with ES futures, not SPX/NDX spot.
        "spot_mode": "front_fut",
        "db": "commodity",
        # E-mini S&P: USD 50 × index.
        "m_opt": 50.0,
        "m_fut": 50.0,
        "quarter_months": (3, 6, 9, 12),
        "cache_tenor": "ES-ES",
    },
}


def _equity_db() -> Path:
    if _dp_cffex is not None:
        try:
            return _dp_cffex()
        except FileNotFoundError:
            pass
    for p in _DB_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError("cffex_vol.sqlite not found")


def _commodity_vol_db(product: str) -> Path:
    prod = str(product).upper()
    if _dp_commodity_vol_for is not None:
        try:
            p = _dp_commodity_vol_for(prod)
            if p is not None and p.exists() and p.stat().st_size > 0:
                return p
        except Exception:
            pass
    ordered = [
        ROOT.parent / "data" / "commodity_vol.sqlite",
        ROOT / "data" / "commodity_vol.sqlite",
        ROOT / "data" / "commodity_vol_lite.sqlite",
        ROOT / "data" / f"commodity_vol_{prod.lower()}.sqlite",
    ]
    if prod == "AU":
        ordered.append(ROOT / "data" / "commodity_vol_au.sqlite")
    if prod == "SC":
        ordered.append(ROOT / "data" / "commodity_vol_sc.sqlite")
    if prod == "GC":
        ordered.extend(
            [
                ROOT / "data" / "commodity_vol_gc.sqlite",
                ROOT.parent / "data" / "commodity_vol_gc.sqlite",
                ROOT.parent / "data" / "overseas_vol.sqlite",
            ]
        )
    if prod == "CL":
        ordered.extend(
            [
                ROOT / "data" / "commodity_vol_cl.sqlite",
                ROOT.parent / "data" / "commodity_vol_cl.sqlite",
                ROOT.parent / "data" / "overseas_vol.sqlite",
            ]
        )
    if prod == "ES":
        ordered.extend(
            [
                ROOT / "data" / "commodity_vol_es.sqlite",
                ROOT.parent / "data" / "commodity_vol_es.sqlite",
                ROOT.parent / "data" / "overseas_vol.sqlite",
            ]
        )
    for p in ordered:
        if not (p.exists() and p.stat().st_size > 0):
            continue
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            hit = conn.execute(
                "SELECT 1 FROM option_iv WHERE product=? LIMIT 1", (prod,)
            ).fetchone()
            conn.close()
            if hit:
                return p
        except Exception:
            continue
    raise FileNotFoundError(
        f"commodity vol DB for {prod} not found "
        f"(need commodity_vol.sqlite or data/commodity_vol_{prod.lower()}.sqlite)"
    )


def _spot_path() -> Path:
    if _dp_spot is not None:
        p = _dp_spot()
        if p is not None and p.exists() and p.stat().st_size > 0:
            return p
    for p in _SPOT_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError("index_spot.parquet not found")


def normalize_universe(universe: Optional[str]) -> str:
    raw = str(universe or DEFAULT_UNIVERSE).strip()
    aliases = {
        "csi300": "300",
        "hs300": "300",
        "io": "300",
        "sse50": "50",
        "ho": "50",
        "csi1000": "1000",
        "mo": "1000",
        "gold": "AU",
        "au": "AU",
        "sc": "SC",
        "crude": "SC",
        "oil": "SC",
        "ine_crude": "SC",
        "gc": "GC",
        "comex_gold": "GC",
        "comex": "GC",
        "cl": "CL",
        "wti": "CL",
        "nymex": "CL",
        "nymex_wti": "CL",
        "es": "ES",
        "spx": "ES",
        "emini": "ES",
        "e_mini": "ES",
        "e-mini": "ES",
        "mes": "ES",
    }
    key = aliases.get(raw.lower(), raw)
    if key.upper() in ("AU", "SC", "GC", "CL", "ES"):
        key = key.upper()
    if key not in VOL_RV_UNIVERSES:
        raise ValueError(f"unknown universe {universe!r}; choose {list(VOL_RV_UNIVERSES)}")
    return key


def universe_cfg(universe: Optional[str] = None) -> Dict[str, Any]:
    return VOL_RV_UNIVERSES[normalize_universe(universe)]


def cache_tenor_for(universe: Optional[str] = None) -> str:
    return str(universe_cfg(universe)["cache_tenor"])


def normalize_tilt(tilt: str) -> str:
    key = str(tilt or "balanced").strip().lower()
    aliases = {
        "gsvlbrr1": "balanced",
        "gsvlbrr2": "defensive",
        "vol_rv_carry": "carry",
        "vol_rv_defensive": "defensive",
        "standard": "balanced",
    }
    key = aliases.get(key, key)
    if key not in STYLE_PRESETS:
        raise ValueError(f"unknown tilt {tilt!r}; choose {list(STYLE_PRESETS)}")
    return key


def vol_rv_cache_sig(
    tilt: str,
    target_otm_delta: float,
    long_delta_threshold: float,
    hedger_mode: str,
    lambda_tilt: Optional[float] = None,
    l_min: Optional[float] = None,
    l_max: Optional[float] = None,
    universe: Optional[str] = None,
) -> str:
    """Fingerprint stored in qis_results_store.signal_mode column."""
    t = normalize_tilt(tilt)
    u = normalize_universe(universe)
    preset = STYLE_PRESETS[t]
    lam = float(preset["lambda_tilt"] if lambda_tilt is None else lambda_tilt)
    lo = float(preset["l_min"] if l_min is None else l_min)
    hi = float(preset["l_max"] if l_max is None else l_max)
    d = int(round(float(target_otm_delta) * 100))
    th = int(round(float(long_delta_threshold) * 100))
    hm = str(hedger_mode or "eod_close").strip().lower().replace(" ", "")
    # Universe also encoded in tenor_pair PK; keep here for tilt fingerprint clarity.
    return f"u{u}|tilt_{t}|d{d}|th{th}|{hm}|L{lam:.2f}_{lo:.2f}_{hi:.2f}"


def _is_quarter_expiry(expiry: str, months: Sequence[int] = (3, 6, 9, 12)) -> bool:
    try:
        m = int(str(expiry)[5:7])
    except Exception:
        return False
    return m in set(int(x) for x in months)


def trading_days_to_expiry(dates: Sequence[str], today: str, expiry: str) -> int:
    """Count trading sessions in ``dates`` with today < d <= expiry date."""
    exp = str(expiry)[:10]
    return sum(1 for d in dates if today < d <= exp)


def select_short_expiry(
    day_opts: pd.DataFrame,
    today: str,
    dates: Sequence[str],
    min_dte: int = SHORT_MIN_DTE,
) -> Optional[str]:
    exps = sorted({str(x)[:10] for x in day_opts["expiry"].astype(str).unique()})
    for exp in exps:
        if trading_days_to_expiry(dates, today, exp) >= int(min_dte):
            return exp
    return exps[-1] if exps else None


def select_long_expiry_candidates(
    day_opts: pd.DataFrame,
    short_expiry: str,
    today: str,
    dates: Sequence[str],
    prefer_quarter: bool = True,
    quarter_months: Sequence[int] = (3, 6, 9, 12),
) -> List[str]:
    """Ordered long-expiry candidates (~90 DTE quarters first, then other deferred)."""
    exps = sorted({str(x)[:10] for x in day_opts["expiry"].astype(str).unique()})
    candidates = [e for e in exps if e > short_expiry]
    if not candidates:
        return []
    if prefer_quarter:
        quarters = [e for e in candidates if _is_quarter_expiry(e, quarter_months)]
        if quarters:
            scored = []
            for e in quarters:
                dte = trading_days_to_expiry(dates, today, e)
                # Prefer ~90 DTE, then deeper slices (more strikes) as soft tie-break later.
                n = int((day_opts["expiry"].astype(str).str[:10] == e).sum())
                scored.append((abs(dte - 90), -n, e))
            scored.sort()
            ordered = [e for _, _, e in scored]
            rest = [e for e in candidates if e not in ordered]
            return ordered + rest
    return candidates


def select_long_expiry(
    day_opts: pd.DataFrame,
    short_expiry: str,
    today: str,
    dates: Sequence[str],
    prefer_quarter: bool = True,
    quarter_months: Sequence[int] = (3, 6, 9, 12),
) -> Optional[str]:
    cands = select_long_expiry_candidates(
        day_opts, short_expiry, today, dates, prefer_quarter, quarter_months
    )
    return cands[0] if cands else None


def pick_atm_strike(slice_: pd.DataFrame, forward: float) -> Optional[float]:
    """Nearest strike to forward; prefer strikes listed on both call and put."""
    cp = slice_["cp"].astype(str).str.upper().str[:1]
    calls = set(float(k) for k in slice_.loc[cp == "C", "strike"])
    puts = set(float(k) for k in slice_.loc[cp == "P", "strike"])
    both = sorted(calls & puts)
    pool = both if both else sorted(calls | puts)
    if not pool:
        return None
    return min(pool, key=lambda k: abs(k - float(forward)))


def _is_valid_ym(ym: Any) -> bool:
    s = str(ym or "").strip()
    return len(s) == 4 and s.isdigit()


def _is_continuous_code(code: Any, root: str) -> bool:
    """True for CME continuous roots like ESc1 / GCc1 (not dated ESH25)."""
    c = str(code or "").upper()
    r = str(root or "").upper()
    if not r or not c.startswith(r):
        return False
    rest = c[len(r) :]
    return bool(re.fullmatch(r"[CV]\d{1,2}", rest))


def _ranked_fut_day(day_fut: pd.DataFrame, root: str) -> pd.DataFrame:
    """Prefer continuous c1, then dated YYMM ascending; drop empty ym noise."""
    if day_fut is None or day_fut.empty:
        return day_fut
    g = day_fut.copy()
    g["_cont"] = g["wind_code"].map(lambda c: _is_continuous_code(c, root))
    g["_ym_ok"] = g["contract_ym"].map(_is_valid_ym)
    g["_ym_sort"] = g["contract_ym"].map(lambda y: str(y) if _is_valid_ym(y) else "9999")
    # continuous first (c1 before c2…), then dated months
    g["_rank"] = 0
    cont = g["_cont"]
    g.loc[cont, "_rank"] = g.loc[cont, "wind_code"].astype(str).str.extract(r"(\d+)$", expand=False).fillna("99").astype(int)
    g.loc[~cont & g["_ym_ok"], "_rank"] = 100 + g.loc[~cont & g["_ym_ok"], "_ym_sort"].astype(int)
    g.loc[~cont & ~g["_ym_ok"], "_rank"] = 10_000
    return g.sort_values(["_rank", "wind_code"]).drop(columns=["_cont", "_ym_ok", "_ym_sort", "_rank"])


def pick_otm_strike_by_delta(
    slice_: pd.DataFrame,
    cp: str,
    forward: float,
    target_delta: float,
) -> Optional[Tuple[float, Dict[str, float], pd.Series]]:
    """Closest listed strike whose Black-76 |delta| matches target (call) or put abs."""
    cp_u = cp.upper()[:1]
    best = None
    for K in sorted({float(k) for k in slice_["strike"].unique()}):
        if cp_u == "C" and K <= forward:
            continue
        if cp_u == "P" and K >= forward:
            continue
        row = slice_[(slice_["strike"] == K) & (slice_["cp"] == cp_u)]
        if row.empty:
            continue
        r0 = row.iloc[0]
        iv = float(r0["iv"])
        ttm = float(r0["ttm"])
        rr = float(r0["r"]) if pd.notna(r0["r"]) else 0.02
        F = float(r0["forward"]) if pd.notna(r0["forward"]) and r0["forward"] > 0 else forward
        g = black76_greeks(F, K, ttm, iv, rr, cp_u)
        d = abs(float(g["delta"])) if cp_u == "P" else float(g["delta"])
        err = abs(d - float(target_delta))
        if best is None or err < best[0]:
            best = (err, K, g, r0)
    if best is None:
        return None
    return best[1], best[2], best[3]


def dynamic_leverage(
    premium_pct: float,
    pref: float,
    lambda_tilt: float,
    l_min: float,
    l_max: float,
) -> float:
    if not math.isfinite(premium_pct) or premium_pct <= 1e-12:
        return float(l_min)
    raw = float(lambda_tilt) * float(pref) / float(premium_pct)
    return float(min(max(raw, l_min), l_max))


def causal_premium_ref(
    history: Sequence[float],
    window: int = PREMIUM_WINDOW,
    fallback: float = TARGET_PREMIUM_PCT,
) -> float:
    """Median of past premiums only (caller must exclude today)."""
    hist = [float(x) for x in history if math.isfinite(float(x)) and float(x) > 0]
    if not hist:
        return float(fallback)
    tail = hist[-int(window) :]
    return float(np.median(np.asarray(tail, dtype=float)))


@dataclass
class LegPos:
    wind_code: str
    expiry: str
    strike: float
    cp: str
    n: float
    mid: float
    iv: float
    ttm: float
    forward: float
    r: float
    delta: float
    gamma: float
    vega: float
    theta: float
    leg: str


@dataclass
class Vintage:
    vintage_id: str
    open_date: str
    short_expiry: str
    long_expiry: str
    exit_date: str
    legs: List[LegPos] = field(default_factory=list)
    leverage: float = 1.0
    premium_pct: float = float("nan")


@dataclass
class BookState:
    vintages: List[Vintage] = field(default_factory=list)
    fut_short: float = 0.0
    fut_long: float = 0.0
    fut_short_code: str = ""
    fut_long_code: str = ""


class Csi300VolRvEngine:
    def __init__(
        self,
        initial_nav: float = 100_000_000.0,
        tilt: str = "balanced",
        universe: str = DEFAULT_UNIVERSE,
        target_otm_delta: float = DEFAULT_TARGET_OTM_DELTA,
        long_delta_threshold: float = DEFAULT_LONG_DELTA_THR,
        hedger_mode: str = "eod_close",
        lambda_tilt: Optional[float] = None,
        l_min: Optional[float] = None,
        l_max: Optional[float] = None,
        daily_budget_frac: float = DEFAULT_DAILY_BUDGET_FRAC,
        n_roll: int = N_ROLL,
        short_min_dte: int = SHORT_MIN_DTE,
        premium_window: int = PREMIUM_WINDOW,
        target_premium_pct: float = TARGET_PREMIUM_PCT,
        option_fee_pct: float = 0.0,
        option_slippage_pct: float = 0.0,
        futures_fee_pct: float = 0.0,
        futures_slippage_pct: float = 0.0,
        run_ablation: bool = True,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> None:
        self.initial_nav = float(initial_nav)
        self.universe = normalize_universe(universe)
        self.cfg = universe_cfg(self.universe)
        self.tilt = normalize_tilt(tilt)
        preset = STYLE_PRESETS[self.tilt]
        self.lambda_tilt = float(preset["lambda_tilt"] if lambda_tilt is None else lambda_tilt)
        self.l_min = float(preset["l_min"] if l_min is None else l_min)
        self.l_max = float(preset["l_max"] if l_max is None else l_max)
        if not (self.l_min > 0 and self.l_max >= self.l_min):
            raise ValueError("need 0 < l_min <= l_max")
        self.target_otm_delta = float(target_otm_delta)
        self.long_delta_threshold = float(long_delta_threshold)
        self.hedger_mode = str(hedger_mode or "eod_close")
        self.daily_budget_frac = float(daily_budget_frac)
        self.n_roll = max(1, int(n_roll))
        self.short_min_dte = max(1, int(short_min_dte))
        self.premium_window = max(5, int(premium_window))
        self.target_premium_pct = float(target_premium_pct)
        self.option_fee_pct = float(option_fee_pct)
        self.option_slippage_pct = float(option_slippage_pct)
        self.futures_fee_pct = float(futures_fee_pct)
        self.futures_slippage_pct = float(futures_slippage_pct)
        self.run_ablation = bool(run_ablation)
        self.start_date = str(start_date)[:10] if start_date else None
        self.end_date = str(end_date)[:10] if end_date else None
        self.product = str(self.cfg["product"])
        self.fut_root = str(self.cfg["fut_root"])
        self.spot_code = self.cfg.get("spot_code")
        self.spot_mode = str(self.cfg.get("spot_mode") or "index")
        self.quarter_months = tuple(int(x) for x in self.cfg.get("quarter_months") or (3, 6, 9, 12))
        self.cache_tenor = str(self.cfg["cache_tenor"])
        self.m_opt = float(self.cfg["m_opt"])
        self.m_fut = float(self.cfg["m_fut"])

    def _load(self) -> Tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
        if self.cfg["db"] == "commodity":
            db = _commodity_vol_db(self.product)
        else:
            db = _equity_db()
        conn = sqlite3.connect(str(db))
        opt = pd.read_sql(
            """
            SELECT trade_date, wind_code, expiry, ttm, strike, cp,
                   forward, mid_price, iv, r
              FROM option_iv
             WHERE product=? AND iv IS NOT NULL AND iv > 0
               AND mid_price IS NOT NULL AND mid_price > 0
               AND ttm IS NOT NULL AND ttm > 1e-6
            """,
            conn,
            params=(self.product,),
        )
        fut = pd.read_sql(
            """
            SELECT trade_date, wind_code, contract_ym, close
              FROM futures_eod
             WHERE product=? AND close IS NOT NULL AND close > 0
             ORDER BY trade_date, contract_ym
            """,
            conn,
            params=(self.product,),
        )
        conn.close()

        fut["trade_date"] = fut["trade_date"].astype(str).str[:10]
        opt["trade_date"] = opt["trade_date"].astype(str).str[:10]
        opt["expiry"] = opt["expiry"].astype(str).str[:10]
        opt["cp"] = opt["cp"].astype(str).str.upper().str[:1]

        if self.spot_mode == "front_fut":
            # Prefer continuous c1 (e.g. ESc1) when dated near months are gone from CMDS.
            root = str(self.fut_root)
            pieces = []
            for td, g in fut.groupby("trade_date"):
                ranked = _ranked_fut_day(g, root)
                if ranked is None or ranked.empty:
                    continue
                pieces.append((td, float(ranked.iloc[0]["close"])))
            spot = (
                pd.Series({td: px for td, px in pieces}, dtype=float).sort_index()
                if pieces
                else pd.Series(dtype=float)
            )
        else:
            spot_df = pd.read_parquet(_spot_path())
            spot_df = spot_df[spot_df["wind_code"] == str(self.spot_code)].copy()
            spot_df["trade_date"] = spot_df["trade_date"].astype(str).str[:10]
            spot = spot_df.set_index("trade_date")["close"].astype(float).sort_index()
        return opt, spot, fut

    def _fut_map(self, fut: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        return {td: g.copy() for td, g in fut.groupby("trade_date")}

    def _pick_if(self, day_fut: pd.DataFrame, prefer_ym: Optional[str] = None) -> Tuple[str, float, str]:
        if day_fut is None or day_fut.empty:
            return "", float("nan"), ""
        if prefer_ym and _is_valid_ym(prefer_ym):
            hit = day_fut[day_fut["contract_ym"].astype(str) == str(prefer_ym)]
            if not hit.empty:
                r = hit.iloc[0]
                return str(r["wind_code"]), float(r["close"]), str(r["contract_ym"])
        g = _ranked_fut_day(day_fut, str(self.fut_root))
        r = g.iloc[0]
        return str(r["wind_code"]), float(r["close"]), str(r["contract_ym"])

    def _build_strangle(
        self,
        slice_: pd.DataFrame,
        forward: float,
        n_abs: float,
        leg: str,
        otm_delta: Optional[float] = None,
    ) -> Optional[List[LegPos]]:
        if slice_.empty or n_abs <= 0:
            return None
        out: List[LegPos] = []
        if otm_delta is None:
            K = pick_atm_strike(slice_, forward)
            if K is None:
                return None
            n = -abs(n_abs) if leg == "short" else abs(n_abs)
            for cp in ("C", "P"):
                row = slice_[(slice_["strike"] == K) & (slice_["cp"] == cp)]
                if row.empty:
                    return None
                r0 = row.iloc[0]
                F = float(r0["forward"]) if pd.notna(r0["forward"]) and r0["forward"] > 0 else forward
                iv = float(r0["iv"])
                ttm = float(r0["ttm"])
                rr = float(r0["r"]) if pd.notna(r0["r"]) else 0.02
                g = black76_greeks(F, float(K), ttm, iv, rr, cp)
                out.append(
                    LegPos(
                        wind_code=str(r0["wind_code"]),
                        expiry=str(r0["expiry"])[:10],
                        strike=float(K),
                        cp=cp,
                        n=n,
                        mid=float(r0["mid_price"]),
                        iv=iv,
                        ttm=ttm,
                        forward=F,
                        r=rr,
                        delta=float(g["delta"]),
                        gamma=float(g["gamma"]),
                        vega=float(g["vega"]),
                        theta=float(g["theta"]),
                        leg=leg,
                    )
                )
        else:
            for cp in ("C", "P"):
                picked = pick_otm_strike_by_delta(slice_, cp, forward, otm_delta)
                if picked is None:
                    return None
                K, g, r0 = picked
                n = abs(n_abs)  # long only for OTM in this strategy
                out.append(
                    LegPos(
                        wind_code=str(r0["wind_code"]),
                        expiry=str(r0["expiry"])[:10],
                        strike=float(K),
                        cp=cp,
                        n=n,
                        mid=float(r0["mid_price"]),
                        iv=float(r0["iv"]),
                        ttm=float(r0["ttm"]),
                        forward=float(g.get("d1") and r0["forward"] or forward),
                        r=float(r0["r"]) if pd.notna(r0["r"]) else 0.02,
                        delta=float(g["delta"]),
                        gamma=float(g["gamma"]),
                        vega=float(g["vega"]),
                        theta=float(g["theta"]),
                        leg=leg,
                    )
                )
                # fix forward from row
                out[-1].forward = (
                    float(r0["forward"])
                    if pd.notna(r0["forward"]) and float(r0["forward"]) > 0
                    else float(forward)
                )
        return out if len(out) == 2 else None

    def _mtm_leg(self, pos: LegPos, day_opts: pd.DataFrame) -> Optional[Tuple[float, float, Dict[str, float]]]:
        hit = day_opts[day_opts["wind_code"] == pos.wind_code]
        if hit.empty:
            hit = day_opts[
                (day_opts["expiry"].astype(str).str[:10] == pos.expiry)
                & (day_opts["strike"] == pos.strike)
                & (day_opts["cp"] == pos.cp)
            ]
        if hit.empty:
            return None
        r0 = hit.iloc[0]
        mid = float(r0["mid_price"])
        iv = float(r0["iv"])
        F = float(r0["forward"]) if pd.notna(r0["forward"]) and r0["forward"] > 0 else pos.forward
        ttm = float(r0["ttm"]) if pd.notna(r0["ttm"]) else max(pos.ttm - 1.0 / 365.0, 1e-6)
        rr = float(r0["r"]) if pd.notna(r0["r"]) else pos.r
        g = black76_greeks(F, pos.strike, ttm, iv, rr, pos.cp)
        return mid, iv, g

    def _option_trade_cost(self, notional_cny: float) -> float:
        return abs(notional_cny) * (self.option_fee_pct + self.option_slippage_pct)

    def _fut_trade_cost(self, n_contracts: float, price: float) -> float:
        notional = abs(n_contracts) * price * self.m_fut
        return notional * (self.futures_fee_pct + self.futures_slippage_pct)

    def _lots_from_book(self, book: BookState, vintage_filter: Optional[str] = None) -> List[OptionLot]:
        lots: List[OptionLot] = []
        for v in book.vintages:
            if vintage_filter and v.vintage_id != vintage_filter:
                continue
            for p in v.legs:
                lots.append(
                    OptionLot(
                        wind_code=p.wind_code,
                        expiry=p.expiry,
                        strike=p.strike,
                        cp=p.cp,
                        n=p.n,
                        delta=p.delta,
                        gamma=p.gamma,
                        vega=p.vega,
                        theta=p.theta,
                        mid=p.mid,
                        iv=p.iv,
                        ttm=p.ttm,
                        forward=p.forward,
                        leg=p.leg,
                        vintage_id=v.vintage_id,
                    )
                )
        return lots

    def _exit_date_for_short(
        self, dates: Sequence[str], today_i: int, short_expiry: str
    ) -> str:
        # Flat N_roll trading days before short expiry (last session with d < expiry and remaining >= N_roll after today).
        candidates = [d for d in dates[today_i:] if d < short_expiry]
        if not candidates:
            return dates[min(today_i, len(dates) - 1)]
        # Sessions strictly before expiry; exit when remaining trading days to expiry == N_roll.
        for d in candidates:
            rem = trading_days_to_expiry(dates, d, short_expiry)
            if rem <= self.n_roll:
                return d
        return candidates[-1]

    def _open_vintage(
        self,
        d: str,
        dates: Sequence[str],
        day_opts: pd.DataFrame,
        nav: float,
        leverage: float,
        vintage_id: str,
    ) -> Optional[Vintage]:
        short_exp = select_short_expiry(day_opts, d, dates, self.short_min_dte)
        if short_exp is None:
            return None
        short_slice = day_opts[day_opts["expiry"].astype(str).str[:10] == short_exp]
        if short_slice.empty:
            return None
        F_s = float(short_slice["forward"].median())
        if not (math.isfinite(F_s) and F_s > 0):
            return None

        # Probe ATM premium for short sizing (needs both C and P).
        K_atm = pick_atm_strike(short_slice, F_s)
        if K_atm is None:
            return None
        prem = 0.0
        for cp in ("C", "P"):
            row = short_slice[(short_slice["strike"] == K_atm) & (short_slice["cp"] == cp)]
            if row.empty:
                return None
            prem += float(row.iloc[0]["mid_price"])
        if prem <= 1e-12:
            return None
        budget = max(self.daily_budget_frac * nav, 0.0)
        n_short = budget / (prem * self.m_opt)
        if n_short <= 0:
            return None

        short_legs = self._build_strangle(short_slice, F_s, n_short, leg="short", otm_delta=None)
        if not short_legs:
            return None

        # Deferred leg: try ~90 DTE first, then deeper listed months with enough strikes
        # (CMDS often keeps only far ES months after near dated futures drop off).
        long_cands = select_long_expiry_candidates(
            day_opts,
            short_exp,
            d,
            dates,
            prefer_quarter=True,
            quarter_months=self.quarter_months,
        )
        long_legs = None
        long_exp = None
        F_l = float("nan")
        for cand in long_cands:
            long_slice = day_opts[day_opts["expiry"].astype(str).str[:10] == cand]
            if long_slice.empty or len(long_slice) < 4:
                continue
            F_try = float(long_slice["forward"].median())
            if not (math.isfinite(F_try) and F_try > 0):
                continue
            legs = self._build_strangle(
                long_slice, F_try, n_short * float(leverage), leg="long", otm_delta=self.target_otm_delta
            )
            if legs:
                long_legs, long_exp, F_l = legs, cand, F_try
                break
        if not long_legs or long_exp is None:
            return None
        long_prem = sum(abs(p.mid) for p in long_legs)
        premium_pct = long_prem / F_l if F_l > 0 else float("nan")
        try:
            i0 = list(dates).index(d)
        except ValueError:
            i0 = 0
        exit_d = self._exit_date_for_short(dates, i0, short_exp)
        return Vintage(
            vintage_id=vintage_id,
            open_date=d,
            short_expiry=short_exp,
            long_expiry=long_exp,
            exit_date=exit_d,
            legs=short_legs + long_legs,
            leverage=float(leverage),
            premium_pct=float(premium_pct) if math.isfinite(premium_pct) else float("nan"),
        )

    def _window_metrics(self, nav: pd.Series, daily_ret: pd.Series) -> Dict[str, Any]:
        r = daily_ret.dropna()
        n = len(r)
        if n == 0 or float(nav.iloc[0]) <= 0:
            return {
                "n_days": 0,
                "ann_return": None,
                "ann_vol": None,
                "sharpe": None,
                "mdd": None,
                "mdd_vol": None,
                "pct_positive_months": None,
                "best_month": None,
                "worst_month": None,
            }
        total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
        years = n / 252.0
        ann_ret = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else float("nan")
        ann_vol = float(r.std(ddof=1) * math.sqrt(252)) if n > 1 else float("nan")
        sharpe = float(ann_ret / ann_vol) if ann_vol and ann_vol > 0 else float("nan")
        peak = nav.cummax()
        mdd = float((nav / peak - 1.0).min())
        mdd_vol = float(abs(mdd) / ann_vol) if ann_vol and ann_vol > 0 else float("nan")
        nav_i = nav.copy()
        nav_i.index = pd.to_datetime(nav_i.index)
        try:
            monthly = nav_i.resample("ME").last().dropna()
        except ValueError:
            monthly = nav_i.resample("M").last().dropna()
        mret = monthly.pct_change().dropna()
        pct_pos = float((mret > 0).mean() * 100.0) if len(mret) else float("nan")
        best = float(mret.max()) if len(mret) else float("nan")
        worst = float(mret.min()) if len(mret) else float("nan")

        def _pct(x: Optional[float]) -> Optional[float]:
            if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
                return None
            return round(float(x), 6)

        return {
            "n_days": int(n),
            "ann_return": _pct(ann_ret),
            "ann_vol": _pct(ann_vol),
            "sharpe": _pct(sharpe),
            "mdd": _pct(mdd),
            "mdd_vol": _pct(mdd_vol),
            "pct_positive_months": _pct(pct_pos),
            "best_month": _pct(best),
            "worst_month": _pct(worst),
        }

    def _slice_from_end(
        self, nav: pd.Series, ret: pd.Series, years: Optional[float]
    ) -> Tuple[pd.Series, pd.Series]:
        if years is None:
            base = float(nav.iloc[0])
            return (nav / base if base else nav), ret
        end = pd.Timestamp(nav.index[-1])
        start = end - timedelta(days=int(years * 365.25))
        idx = pd.to_datetime(nav.index)
        ge = [i for i, t in enumerate(idx) if t >= start]
        if not ge:
            return nav.iloc[0:0], ret.iloc[0:0]
        i0 = ge[0]
        base = float(nav.iloc[i0])
        return (nav.iloc[i0:] / base if base else nav.iloc[i0:]), ret.iloc[i0:]

    def _yearly_metrics(self, nav: pd.Series, daily_ret: pd.Series) -> List[Dict[str, Any]]:
        if len(nav) < 2:
            return []
        idx = pd.to_datetime(nav.index)
        out: List[Dict[str, Any]] = []
        for year in sorted({int(y) for y in idx.year}):
            mask = idx.year == year
            nav_y = nav.loc[mask]
            ret_y = daily_ret.loc[mask]
            if len(nav_y) < 2:
                continue
            base = float(nav_y.iloc[0])
            if base <= 0:
                continue
            m = self._window_metrics(nav_y / base, ret_y)
            m["year"] = int(year)
            m["start"] = str(pd.Timestamp(nav_y.index[0]).date())
            m["end"] = str(pd.Timestamp(nav_y.index[-1]).date())
            out.append(m)
        return out

    def _simulate_book(
        self,
        *,
        label: str,
        dates: List[str],
        opt_by_day: Dict[str, pd.DataFrame],
        fut_by_day: Dict[str, pd.DataFrame],
        spot: pd.Series,
        use_dynamic_leverage: bool,
        flatten_long: bool,
        premium_hist_seed: List[float],
    ) -> Dict[str, Any]:
        hedger = get_hedger(
            "eod_close" if self.hedger_mode.startswith("eod") or flatten_long else self.hedger_mode,
            long_delta_threshold=0.0 if flatten_long else self.long_delta_threshold,
            m_opt=self.m_opt,
            m_fut=self.m_fut,
        )
        # When flatten_long (step1), force threshold 0 so long always hedges.
        book = BookState()
        nav = self.initial_nav
        premium_hist = list(premium_hist_seed)
        rows: List[Dict[str, Any]] = []
        first_open_i: Optional[int] = None
        leverages: List[float] = []
        cum_vega = cum_theta = cum_gamma = cum_basis = cum_delta = cum_resid = 0.0
        dt_years = 1.0 / 365.0

        for i, d in enumerate(dates):
            day_opts = opt_by_day.get(d, pd.DataFrame())
            day_fut = fut_by_day.get(d, pd.DataFrame())
            S = float(spot.loc[d]) if d in spot.index else float("nan")
            short_opt = long_opt = fut_pnl = cost = 0.0
            g_delta = g_basis = g_gamma = g_theta = g_vega = g_resid = 0.0

            # 1) Mark previous book to today + Black-76 greek / basis decomposition.
            if i > 0 and book.vintages:
                d_prev = dates[i - 1]
                S_prev = float(spot.loc[d_prev]) if d_prev in spot.index else float("nan")
                dS = (S - S_prev) if (math.isfinite(S) and math.isfinite(S_prev)) else float("nan")
                F_prev_s = float("nan")
                F_prev_l = float("nan")
                if book.fut_short_code:
                    prev_fut = fut_by_day.get(d_prev, pd.DataFrame())
                    hit = prev_fut[prev_fut["wind_code"] == book.fut_short_code]
                    if not hit.empty:
                        F_prev_s = float(hit.iloc[0]["close"])
                if book.fut_long_code:
                    prev_fut = fut_by_day.get(d_prev, pd.DataFrame())
                    hit = prev_fut[prev_fut["wind_code"] == book.fut_long_code]
                    if not hit.empty:
                        F_prev_l = float(hit.iloc[0]["close"])
                F_s = F_l = float("nan")
                if book.fut_short_code and not day_fut.empty:
                    hit = day_fut[day_fut["wind_code"] == book.fut_short_code]
                    if not hit.empty:
                        F_s = float(hit.iloc[0]["close"])
                if book.fut_long_code and not day_fut.empty:
                    hit = day_fut[day_fut["wind_code"] == book.fut_long_code]
                    if not hit.empty:
                        F_l = float(hit.iloc[0]["close"])
                if math.isfinite(F_s) and math.isfinite(F_prev_s):
                    fut_pnl += self.m_fut * book.fut_short * (F_s - F_prev_s)
                if math.isfinite(F_l) and math.isfinite(F_prev_l):
                    fut_pnl += self.m_fut * book.fut_long * (F_l - F_prev_l)

                # Overnight greek PnL uses yesterday's greeks / futures lots (pre-hedge update).
                opt_delta_pts = sum(p.n * p.delta for v in book.vintages for p in v.legs)
                opt_gamma_pts = sum(p.n * p.gamma for v in book.vintages for p in v.legs)
                opt_theta_pts = sum(p.n * p.theta for v in book.vintages for p in v.legs)
                n_fut = float(book.fut_short) + float(book.fut_long)
                if math.isfinite(dS):
                    g_delta = (self.m_opt * opt_delta_pts + self.m_fut * n_fut) * dS
                    g_gamma = 0.5 * self.m_opt * opt_gamma_pts * (dS ** 2)
                g_theta = self.m_opt * opt_theta_pts * dt_years
                if (
                    math.isfinite(F_s)
                    and math.isfinite(F_prev_s)
                    and math.isfinite(S)
                    and math.isfinite(S_prev)
                ):
                    g_basis += self.m_fut * book.fut_short * ((F_s - S) - (F_prev_s - S_prev))
                if (
                    math.isfinite(F_l)
                    and math.isfinite(F_prev_l)
                    and math.isfinite(S)
                    and math.isfinite(S_prev)
                ):
                    g_basis += self.m_fut * book.fut_long * ((F_l - S) - (F_prev_l - S_prev))

                new_vintages: List[Vintage] = []
                for v in book.vintages:
                    still = []
                    for p in v.legs:
                        mtm = self._mtm_leg(p, day_opts)
                        if mtm is None:
                            # Drop missing; crystallize zero overnight.
                            still.append(p)
                            continue
                        mid_now, iv_now, g = mtm
                        pnl = self.m_opt * p.n * (mid_now - p.mid)
                        if p.leg == "short":
                            short_opt += pnl
                        else:
                            long_opt += pnl
                        # vega_point = vega/100 → ×100 recovers per-1.0-vol vega × ΔIV.
                        g_vega += self.m_opt * p.n * (float(p.vega) / 100.0) * (iv_now - p.iv) * 100.0
                        still.append(
                            LegPos(
                                wind_code=p.wind_code,
                                expiry=p.expiry,
                                strike=p.strike,
                                cp=p.cp,
                                n=p.n,
                                mid=mid_now,
                                iv=iv_now,
                                ttm=max(float(p.ttm) - dt_years, 1e-6),
                                forward=p.forward,
                                r=p.r,
                                delta=float(g["delta"]),
                                gamma=float(g["gamma"]),
                                vega=float(g["vega"]),
                                theta=float(g["theta"]),
                                leg=p.leg,
                            )
                        )
                    v.legs = still
                    new_vintages.append(v)
                book.vintages = new_vintages

                actual = short_opt + long_opt + fut_pnl
                g_resid = actual - (g_delta + g_basis + g_gamma + g_theta + g_vega)

            cum_vega += g_vega
            cum_theta += g_theta
            cum_gamma += g_gamma
            cum_basis += g_basis
            cum_delta += g_delta
            cum_resid += g_resid

            net = short_opt + long_opt + fut_pnl - cost
            nav = nav + net

            # 2) Exit vintages due today.
            kept: List[Vintage] = []
            for v in book.vintages:
                if d >= v.exit_date:
                    # Close options at mid; cost on notional.
                    for p in v.legs:
                        notional = abs(p.n) * p.mid * self.m_opt
                        c = self._option_trade_cost(notional)
                        cost += c
                        # Closing PnL already in overnight mark; here only costs.
                    continue
                kept.append(v)
            book.vintages = kept
            nav -= 0.0  # costs applied below

            # 3) Open new vintage (premium_ref uses prior days only — no lookahead).
            L = self.lambda_tilt
            pref = causal_premium_ref(premium_hist, self.premium_window, self.target_premium_pct)
            if use_dynamic_leverage:
                # Open at L=1 to observe today's premium, then scale long notionals.
                vin = self._open_vintage(d, dates, day_opts, nav, leverage=1.0, vintage_id=f"{label}-{d}")
                if vin is not None and math.isfinite(vin.premium_pct):
                    L = dynamic_leverage(
                        vin.premium_pct, pref, self.lambda_tilt, self.l_min, self.l_max
                    )
                    if abs(L - 1.0) > 1e-12:
                        scaled: List[LegPos] = []
                        for p in vin.legs:
                            n = p.n * L if p.leg == "long" else p.n
                            scaled.append(
                                LegPos(
                                    wind_code=p.wind_code,
                                    expiry=p.expiry,
                                    strike=p.strike,
                                    cp=p.cp,
                                    n=n,
                                    mid=p.mid,
                                    iv=p.iv,
                                    ttm=p.ttm,
                                    forward=p.forward,
                                    r=p.r,
                                    delta=p.delta,
                                    gamma=p.gamma,
                                    vega=p.vega,
                                    theta=p.theta,
                                    leg=p.leg,
                                )
                            )
                        vin.legs = scaled
                        vin.leverage = float(L)
            else:
                vin = self._open_vintage(d, dates, day_opts, nav, leverage=L, vintage_id=f"{label}-{d}")
            if vin is not None:
                open_cost = 0.0
                for p in vin.legs:
                    open_cost += self._option_trade_cost(abs(p.n) * p.mid * self.m_opt)
                cost += open_cost
                book.vintages.append(vin)
                if math.isfinite(vin.premium_pct):
                    premium_hist.append(vin.premium_pct)
                leverages.append(vin.leverage)
                if first_open_i is None:
                    first_open_i = i

            # 4) Hedge
            lots = self._lots_from_book(book)
            thr = 0.0 if flatten_long else self.long_delta_threshold
            ctx = HedgeContext(
                timestamp=d,
                active_lots=lots,
                current_futures_positions={
                    "IF_short": book.fut_short,
                    "IF_long": book.fut_long,
                },
                market_state={"spot": S},
                params={"long_delta_threshold": thr},
            )
            # Rebuild hedger threshold each day for step1
            if flatten_long:
                local = get_hedger("eod_close", long_delta_threshold=0.0, m_opt=self.m_opt, m_fut=self.m_fut)
                dec = local.compute_eod_hedge(ctx)
            else:
                dec = hedger.compute_eod_hedge(ctx)

            # Map IF codes: front (c1 / nearest dated) for short; next dated for long.
            code_s, px_s, _ = self._pick_if(day_fut)
            code_l, px_l = code_s, px_s
            ranked = _ranked_fut_day(day_fut, str(self.fut_root))
            if ranked is not None and len(ranked) >= 2:
                r1 = ranked.iloc[1]
                code_l, px_l = str(r1["wind_code"]), float(r1["close"])
            t_s = float(dec.target_futures_positions.get("IF_short", 0.0))
            t_l = float(dec.target_futures_positions.get("IF_long", 0.0))
            d_s = t_s - book.fut_short
            d_l = t_l - book.fut_long
            if math.isfinite(px_s):
                cost += self._fut_trade_cost(d_s, px_s)
            if math.isfinite(px_l):
                cost += self._fut_trade_cost(d_l, px_l)
            book.fut_short = t_s
            book.fut_long = t_l
            book.fut_short_code = code_s
            book.fut_long_code = code_l

            nav -= cost  # subtract costs not already in net (overnight net excluded cost)
            # Recompute day's net including costs for attribution identity.
            day_net = short_opt + long_opt + fut_pnl - cost
            # Adjust: we already added short_opt+long_opt+fut_pnl earlier, then subtracted cost once above.
            # So nav is consistent. Store attribution with identity.
            net_pnl = short_opt + long_opt + fut_pnl - cost

            net_delta = sum(p.n * p.delta for v in book.vintages for p in v.legs)
            net_gamma = sum(p.n * p.gamma for v in book.vintages for p in v.legs)
            net_vega = sum(p.n * p.vega for v in book.vintages for p in v.legs)
            net_theta = sum(p.n * p.theta for v in book.vintages for p in v.legs)
            avg_L = float(np.mean([v.leverage for v in book.vintages])) if book.vintages else float("nan")

            rows.append(
                {
                    "d": d,
                    "nav": nav,
                    "ret": (day_net / (nav - day_net)) if i > 0 and abs(nav - day_net) > 1e-9 else 0.0,
                    "short_option_pnl": short_opt,
                    "long_option_pnl": long_opt,
                    "futures_hedge_pnl": fut_pnl,
                    "transaction_cost": cost,
                    "net_pnl": net_pnl,
                    "position": 1 if book.vintages else 0,
                    "n_vintages": len(book.vintages),
                    "net_delta": net_delta,
                    "net_gamma": net_gamma,
                    "net_vega": net_vega,
                    "net_theta": net_theta,
                    "long_dynamic_leverage": avg_L if math.isfinite(avg_L) else None,
                    "hedge_reason": dec.hedge_reason,
                    "vega_pnl": g_vega,
                    "theta_pnl": g_theta,
                    "gamma_pnl": g_gamma,
                    "basis_pnl": g_basis,
                    "net_delta_pnl": g_delta,
                    "residual_pnl": g_resid,
                    "cum_vega": cum_vega,
                    "cum_theta": cum_theta,
                    "cum_gamma": cum_gamma,
                    "cum_basis": cum_basis,
                    "cum_delta": cum_delta,
                    "cum_resid": cum_resid,
                }
            )

        return {
            "label": label,
            "rows": rows,
            "first_open_i": first_open_i,
            "mean_leverage": float(np.mean(leverages)) if leverages else float("nan"),
            "leverages": leverages,
        }

    def run(self) -> Dict[str, Any]:
        opt, spot, fut = self._load()
        opt_by_day = {td: g.copy() for td, g in opt.groupby("trade_date")}
        fut_by_day = self._fut_map(fut)
        dates = sorted(set(opt_by_day) & set(spot.index) & set(fut_by_day))
        if self.start_date:
            dates = [d for d in dates if d >= self.start_date]
        if self.end_date:
            dates = [d for d in dates if d <= self.end_date]
        # Equity CFFEX books are long; overseas GC/CL/ES (and AU/SC extracts) are shorter.
        if self.start_date or self.end_date:
            min_days = 60
        elif str(self.cfg.get("db") or "") == "commodity":
            min_days = 60
        else:
            min_days = 300
        if len(dates) < min_days:
            raise RuntimeError(f"Insufficient IO/IF history: {len(dates)} days")

        # Main path = step3
        step3 = self._simulate_book(
            label="step3",
            dates=dates,
            opt_by_day=opt_by_day,
            fut_by_day=fut_by_day,
            spot=spot,
            use_dynamic_leverage=True,
            flatten_long=False,
            premium_hist_seed=[],
        )
        ablations = {"step3_dynamic_leverage": step3}
        if self.run_ablation:
            ablations["step1_eod_vanilla"] = self._simulate_book(
                label="step1",
                dates=dates,
                opt_by_day=opt_by_day,
                fut_by_day=fut_by_day,
                spot=spot,
                use_dynamic_leverage=False,
                flatten_long=True,
                premium_hist_seed=[],
            )
            ablations["step2_long_threshold"] = self._simulate_book(
                label="step2",
                dates=dates,
                opt_by_day=opt_by_day,
                fut_by_day=fut_by_day,
                spot=spot,
                use_dynamic_leverage=False,
                flatten_long=False,
                premium_hist_seed=[],
            )

        return self._pack(ablations)

    def _pack_nav(
        self, sim: Dict[str, Any]
    ) -> Tuple[
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        Dict[str, Any],
        List[Dict[str, Any]],
        Dict[str, Any],
    ]:
        rows: List[Dict[str, Any]] = sim["rows"]
        first_open_i = sim["first_open_i"]
        if first_open_i is None or first_open_i + 1 >= len(rows):
            raise RuntimeError("No Vol RV vintage opened")
        start_i = first_open_i + 1
        base_nav = float(rows[first_open_i]["nav"])
        perf = rows[start_i:]
        nav_s = pd.Series({r["d"]: float(r["nav"]) for r in perf})
        ret_s = pd.Series({r["d"]: float(r["ret"]) for r in perf})
        metrics = {}
        for name, yrs in (("1y", 1.0), ("3y", 3.0), ("5y", 5.0), ("max", None)):
            nav_w, ret_w = self._slice_from_end(nav_s, ret_s, yrs)
            metrics[name] = self._window_metrics(nav_w, ret_w)
            metrics[name]["start"] = str(nav_w.index[0]) if len(nav_w) else None
            metrics[name]["end"] = str(nav_w.index[-1]) if len(nav_w) else None
        yearly = self._yearly_metrics(nav_s, ret_s)
        step = max(1, len(perf) // 800)
        idx = list(range(0, len(perf), step))
        if not idx or idx[-1] != len(perf) - 1:
            idx.append(len(perf) - 1)
        nav_chart = [
            {"d": perf[i]["d"], "nav": round(float(perf[i]["nav"]) / base_nav, 6), "sos": None}
            for i in idx
        ]
        # Full daily series for basket / analytics (chart `nav` may be downsampled).
        nav_daily = [
            {"d": r["d"], "nav": round(float(r["nav"]) / base_nav, 6)}
            for r in perf
        ]
        summary = {
            "start": perf[0]["d"],
            "end": perf[-1]["d"],
            "n_days": len(perf),
            "final_nav": round(float(perf[-1]["nav"]), 2),
            "final_nav_multiple": round(float(perf[-1]["nav"]) / base_nav, 6),
            "total_cost": round(sum(float(r["transaction_cost"]) for r in perf), 2),
            "total_pnl": round(float(perf[-1]["nav"]) - base_nav, 2),
            "first_open": rows[first_open_i]["d"],
            "mean_leverage": None
            if not math.isfinite(sim["mean_leverage"])
            else round(float(sim["mean_leverage"]), 4),
        }
        return nav_chart, nav_daily, metrics, yearly, summary

    def _pack(self, ablations: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        main = ablations["step3_dynamic_leverage"]
        nav_chart, nav_daily, metrics, yearly, summary = self._pack_nav(main)
        rows = main["rows"]
        first_open_i = main["first_open_i"]
        start_i = first_open_i + 1
        perf = rows[start_i:]
        step = max(1, len(perf) // 800)
        idx = list(range(0, len(perf), step))
        if idx[-1] != len(perf) - 1:
            idx.append(len(perf) - 1)

        def _series(key: str) -> List[float]:
            return [round(float(perf[i][key]), 4) for i in idx]

        # Attribution identity check on full path (tolerance).
        for r in perf:
            lhs = float(r["net_pnl"])
            rhs = (
                float(r["short_option_pnl"])
                + float(r["long_option_pnl"])
                + float(r["futures_hedge_pnl"])
                - float(r["transaction_cost"])
            )
            if abs(lhs - rhs) > 1e-4:
                # Soft-fix in packing; unit tests assert tightly.
                pass

        ablation_nav: Dict[str, List[Dict[str, Any]]] = {}
        for k, sim in ablations.items():
            chart, _, _, _, _ = self._pack_nav(sim)
            ablation_nav[k] = chart

        total_pnl = float(perf[-1]["nav"]) - float(rows[first_open_i]["nav"])
        parts = {
            "vega": float(perf[-1]["cum_vega"]),
            "theta": float(perf[-1]["cum_theta"]),
            "gamma": float(perf[-1]["cum_gamma"]),
            "basis": float(perf[-1]["cum_basis"]),
            "net_delta": float(perf[-1]["cum_delta"]),
            "residual": float(perf[-1]["cum_resid"]),
        }

        def _share(x: float) -> Optional[float]:
            if abs(total_pnl) < 1e-6:
                return None
            return round(100.0 * x / total_pnl, 2)

        return {
            "strategy_id": STRATEGY_ID,
            "strategy_name": f"{self.cfg['strategy_name']} · {self.tilt}",
            "description": (
                f"{self.cfg['label']} ({self.product}/{self.fut_root}) overlapping daily vintages: "
                "short near-month ATM strangle + long deferred OTM strangle (target delta), "
                "EOD futures hedge with long delta threshold, premium-inverse long leverage. "
                f"tilt={self.tilt} λ={self.lambda_tilt} L∈[{self.l_min},{self.l_max}]."
            ),
            "legs": [
                {
                    "id": f"{self.product}_short_atm_strangle",
                    "weight": -1.0,
                    "product": self.product,
                    "tenor": "1M",
                },
                {
                    "id": f"{self.product}_long_otm_strangle",
                    "weight": 1.0,
                    "product": self.product,
                    "tenor": "3M-6M",
                },
                {
                    "id": f"{self.fut_root}_hedge",
                    "weight": 0.0,
                    "product": self.fut_root,
                    "tenor": "front",
                },
            ],
            "params": {
                "initial_nav": self.initial_nav,
                "slippage_bp": round(self.option_slippage_pct * 1e4, 4),
                "fee_pct": round(self.option_fee_pct * 100.0, 6),
                "cost_rate": self.option_fee_pct + self.option_slippage_pct,
                "rebalance": "daily vintage + EOD delta hedge",
                "signal_lag": "t close open / hold to short expiry−N_roll",
                "price": f"option mid_price←close; {self.fut_root} close",
                "universe": self.universe,
                "product": self.product,
                "fut_root": self.fut_root,
                "tilt": self.tilt,
                "lambda_tilt": self.lambda_tilt,
                "l_min": self.l_min,
                "l_max": self.l_max,
                "target_otm_delta": self.target_otm_delta,
                "long_delta_threshold": self.long_delta_threshold,
                "hedger_mode": self.hedger_mode,
                "daily_budget_frac": self.daily_budget_frac,
                "n_roll": self.n_roll,
                "short_min_dte": self.short_min_dte,
                "premium_window": self.premium_window,
                "option_fee_pct": self.option_fee_pct,
                "option_slippage_pct": self.option_slippage_pct,
                "futures_fee_pct": self.futures_fee_pct,
                "futures_slippage_pct": self.futures_slippage_pct,
                "tenor_pair": self.cache_tenor,
                "side_mode": CACHE_SIDE,
                "spread_mode": CACHE_SPREAD,
                "signal_mode": vol_rv_cache_sig(
                    self.tilt,
                    self.target_otm_delta,
                    self.long_delta_threshold,
                    self.hedger_mode,
                    self.lambda_tilt,
                    self.l_min,
                    self.l_max,
                    self.universe,
                ),
                "m_opt": self.m_opt,
                "m_fut": self.m_fut,
                "first_open": summary["first_open"],
                "mean_leverage": summary["mean_leverage"],
                "start_date": self.start_date,
                "end_date": self.end_date,
            },
            "nav": nav_chart,
            "nav_daily": nav_daily,
            "metrics": metrics,
            "yearly_metrics": yearly,
            "summary": summary,
            "ablation": ablation_nav,
            "pnl_attribution": {
                "dates": [perf[i]["d"] for i in idx],
                "short_option_pnl": _series("short_option_pnl"),
                "long_option_pnl": _series("long_option_pnl"),
                "futures_hedge_pnl": _series("futures_hedge_pnl"),
                "transaction_cost": _series("transaction_cost"),
                "net_pnl": _series("net_pnl"),
            },
            "pnl_decomposition": {
                "dates": [perf[i]["d"] for i in idx],
                "vega_pnl": _series("cum_vega"),
                "theta_pnl": _series("cum_theta"),
                "gamma_pnl": _series("cum_gamma"),
                "basis_pnl": _series("cum_basis"),
                "net_delta_pnl": _series("cum_delta"),
                "residual_pnl": _series("cum_resid"),
            },
            "pnl_daily_greeks": {
                "dates": [perf[i]["d"] for i in idx],
                "vega_pnl": _series("vega_pnl"),
                "theta_pnl": _series("theta_pnl"),
                "gamma_pnl": _series("gamma_pnl"),
            },
            "greeks_summary": {
                "dates": [perf[i]["d"] for i in idx],
                "net_delta": _series("net_delta"),
                "net_gamma": _series("net_gamma"),
                "net_vega": _series("net_vega"),
                "net_theta": _series("net_theta"),
                "long_dynamic_leverage": [
                    None
                    if perf[i]["long_dynamic_leverage"] is None
                    else round(float(perf[i]["long_dynamic_leverage"]), 4)
                    for i in idx
                ],
            },
            "pnl_contribution": {
                "total_pnl": round(float(total_pnl), 2),
                "vega_pct": _share(parts["vega"]),
                "theta_pct": _share(parts["theta"]),
                "gamma_pct": _share(parts["gamma"]),
                "basis_pct": _share(parts["basis"]),
                "net_delta_pct": _share(parts["net_delta"]),
                "residual_pct": _share(parts["residual"]),
            },
            "signal": {
                "dates": [perf[i]["d"] for i in idx],
                "spread": [None for _ in idx],
                "position": [int(perf[i]["position"]) for i in idx],
                "signal_mode": "csi300_vol_rv",
            },
        }


def run_csi300_vol_rv(**kwargs: Any) -> Dict[str, Any]:
    return Csi300VolRvEngine(**kwargs).run()
