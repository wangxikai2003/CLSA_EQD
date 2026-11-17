"""Equity Calendar Volatility Long Short — CSI50 / CSI300 / CSI1000; tenor 3M-6M | 1M-3M."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine.black76 import black76_greeks

ROOT = Path(__file__).resolve().parents[2]  # CLSA_EQD
REPO = ROOT.parent

_DB_CANDIDATES = [
    ROOT / "data" / "cffex_vol.sqlite",
    Path("/app/data/cffex_vol.sqlite"),
    REPO / "data" / "cffex_vol.sqlite",
]
try:
    from data_paths import cffex_vol as _dp_cffex, index_spot as _dp_spot
except Exception:  # pragma: no cover
    _dp_cffex = _dp_spot = None  # type: ignore
_SPOT_CANDIDATES = [
    ROOT / "data" / "index_spot.parquet",
    Path("/app/data/index_spot.parquet"),
]

T_1M = 30.0 / 365.0
T_3M = 90.0 / 365.0
T_6M = 180.0 / 365.0
DEFAULT_Z_WINDOW = 252  # rolling window for quantiles / Z (trading days)
# Allow bands/z before the full window fills (still causal via shift(1)).
# With window=252 → min_periods=126 ≈ 6M, cutting ~1Y off the blank prefix.
ROLLING_MIN_FLOOR = 60
ROLLING_MIN_FRAC = 0.5


def rolling_min_periods(window: int) -> int:
    w = int(window)
    return max(ROLLING_MIN_FLOOR, int(math.ceil(w * ROLLING_MIN_FRAC)))
# Rolling quantile bands on Spread (prior window only — no look-ahead)
ENTER_Q = 0.90  # enter outside 10/90
EXIT_LO_Q = 0.40
EXIT_HI_Q = 0.60
MAX_HOLD_REVERSE = 20
VEGA_BUDGET_FRAC = 0.01

# Locked tenor pairs (framework): far leg holds vega budget.
# Spread definition depends on spread_mode (iv | iv2); see compute_spread.
TENOR_PAIRS: Dict[str, Dict[str, Any]] = {
    "3M-6M": {
        "near_label": "3M",
        "far_label": "6M",
        "t_near": T_3M,
        "t_far": T_6M,
        "display": "3M−6M",
    },
    "1M-3M": {
        "near_label": "1M",
        "far_label": "3M",
        "t_near": T_1M,
        "t_far": T_3M,
        "display": "1M−3M",
    },
}
DEFAULT_TENOR_PAIR = "3M-6M"

# Position-side filter: both = Normal(+1) & Reverse(−1); long/short_only keep flat+close.
SIDE_MODES = ("both", "long_only", "short_only")
DEFAULT_SIDE_MODE = "both"

# Spread construction after CT-IV interpolation: level diff vs variance diff.
SPREAD_MODES = ("iv", "iv2")
DEFAULT_SPREAD_MODE = "iv"

# Signal operator on the chosen spread series.
# quantile = rolling 10/90 enter & 40/60 exit on raw Spread;
# garch_z = expanding GARCH(1,1)-t on ΔSpread → z, then same rolling quantiles on z;
# regime* = Fade / Momentum regime switch (v2) with cooldown;
#   regime = both books; regime_momentum / regime_fade = single book;
# regime2 = quantile base + dual-day garch_z stress gate (block new opens only).
SIGNAL_MODES = (
    "quantile",
    "garch_z",
    "regime",
    "regime_momentum",
    "regime_fade",
    "regime2",
)
DEFAULT_SIGNAL_MODE = "quantile"
REGIME_SIGNAL_MODES = ("regime", "regime_momentum", "regime_fade")
BUDGET_FADE = 0.01
BUDGET_MOMENTUM = 0.008

# option product (DB) → index / futures multipliers
UNIVERSES: Dict[str, Dict[str, Any]] = {
    "50": {
        "strategy_id": "equity_3_6_calendar_vol_50",
        "strategy_name": "Equity Calendar Volatility Long Short Strategy — 50",
        "product": "HO",
        "fut_root": "IH",
        "spot_code": "000016.SH",
        "index_name": "SSE 50",
        "m_opt": 100.0,
        "m_fut": 300.0,
    },
    "300": {
        "strategy_id": "equity_3_6_calendar_vol_300",
        "strategy_name": "Equity Calendar Volatility Long Short Strategy — 300",
        "product": "IO",
        "fut_root": "IF",
        "spot_code": "000300.SH",
        "index_name": "CSI 300",
        "m_opt": 100.0,
        "m_fut": 300.0,
    },
    "1000": {
        "strategy_id": "equity_3_6_calendar_vol_1000",
        "strategy_name": "Equity Calendar Volatility Long Short Strategy — 1000",
        "product": "MO",
        "fut_root": "IM",
        "spot_code": "000852.SH",
        "index_name": "CSI 1000",
        "m_opt": 100.0,
        "m_fut": 200.0,
    },
}

STRATEGY_BY_ID = {u["strategy_id"]: key for key, u in UNIVERSES.items()}

EXPORTS_DIR = ROOT / "data" / "exports"


def normalize_tenor_pair(tenor_pair: str) -> str:
    key = str(tenor_pair).strip().upper().replace("−", "-").replace("_", "-")
    aliases = {
        "3M6M": "3M-6M",
        "1M3M": "1M-3M",
        "3-6": "3M-6M",
        "1-3": "1M-3M",
    }
    key = aliases.get(key, key)
    if key not in TENOR_PAIRS:
        raise ValueError(f"unknown tenor_pair {tenor_pair!r}; choose {list(TENOR_PAIRS)}")
    return key


def normalize_side_mode(side_mode: str) -> str:
    key = str(side_mode).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "long": "long_only",
        "short": "short_only",
        "longonly": "long_only",
        "shortonly": "short_only",
        "only_long": "long_only",
        "only_short": "short_only",
        "all": "both",
        "ls": "both",
    }
    key = aliases.get(key, key)
    if key not in SIDE_MODES:
        raise ValueError(f"unknown side_mode {side_mode!r}; choose {list(SIDE_MODES)}")
    return key


def normalize_spread_mode(spread_mode: str) -> str:
    key = str(spread_mode).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "level": "iv",
        "diff": "iv",
        "iv_diff": "iv",
        "var": "iv2",
        "variance": "iv2",
        "iv_sq": "iv2",
        "iv_squared": "iv2",
        "square": "iv2",
        "sq": "iv2",
    }
    key = aliases.get(key, key)
    if key not in SPREAD_MODES:
        raise ValueError(f"unknown spread_mode {spread_mode!r}; choose {list(SPREAD_MODES)}")
    return key


def normalize_signal_mode(signal_mode: str) -> str:
    key = str(signal_mode).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "q": "quantile",
        "bands": "quantile",
        "rolling": "quantile",
        "rolling_q": "quantile",
        "garch": "garch_z",
        "gz": "garch_z",
        "g_z": "garch_z",
        "z": "garch_z",
        "fade_momentum": "regime",
        "rs": "regime",
        "regime_switch": "regime",
        "switch": "regime",
        "regime_v2": "regime",
        "v2": "regime",
        "regime_v1": "regime",
        "v1": "regime",
        "regime_both": "regime",
        "mom": "regime_momentum",
        "momentum": "regime_momentum",
        "momentum_only": "regime_momentum",
        "regime_mom": "regime_momentum",
        "fade": "regime_fade",
        "fade_only": "regime_fade",
        "mr": "regime_fade",
        "mean_reversion": "regime_fade",
        "mean_revert": "regime_fade",
        "meanreversion": "regime_fade",
        "regime2": "regime2",
        "r2": "regime2",
        "regime_2": "regime2",
        "hybrid": "regime2",
        "quantile_stress": "regime2",
        "quantile_gate": "regime2",
    }
    key = aliases.get(key, key)
    if key not in SIGNAL_MODES:
        raise ValueError(f"unknown signal_mode {signal_mode!r}; choose {list(SIGNAL_MODES)}")
    return key


def is_regime_signal(signal_mode: str) -> bool:
    return normalize_signal_mode(signal_mode) in REGIME_SIGNAL_MODES


def is_regime2_signal(signal_mode: str) -> bool:
    return normalize_signal_mode(signal_mode) == "regime2"


def uses_garch_z(signal_mode: str) -> bool:
    """True when the engine materializes expanding garch_z (decision or overlay)."""
    mode = normalize_signal_mode(signal_mode)
    return mode == "garch_z" or is_regime_signal(mode) or is_regime2_signal(mode)


def regime_book_for_signal(signal_mode: str) -> str:
    """Map regime* signal_mode → both | momentum | fade."""
    mode = normalize_signal_mode(signal_mode)
    if mode == "regime_momentum":
        return "momentum"
    if mode == "regime_fade":
        return "fade"
    if mode == "regime":
        return "both"
    raise ValueError(f"{signal_mode!r} is not a regime signal")


def compute_spread(iv_near: float, iv_far: float, spread_mode: str = DEFAULT_SPREAD_MODE) -> float:
    """Spread after CT-IV: iv → near−far; iv2 → near²−far²."""
    mode = normalize_spread_mode(spread_mode)
    if mode == "iv2":
        return float(iv_near) * float(iv_near) - float(iv_far) * float(iv_far)
    return float(iv_near) - float(iv_far)


def default_holdings_export_path(
    universe: str,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
    spread_mode: str = DEFAULT_SPREAD_MODE,
    signal_mode: str = DEFAULT_SIGNAL_MODE,
) -> Path:
    cfg = UNIVERSES[universe]
    tp = normalize_tenor_pair(tenor_pair)
    sm = normalize_side_mode(side_mode)
    spm = normalize_spread_mode(spread_mode)
    sig = normalize_signal_mode(signal_mode)
    parts = [cfg["strategy_id"]]
    if tp != DEFAULT_TENOR_PAIR:
        parts.append(tp.lower().replace("-", ""))
    if sm != DEFAULT_SIDE_MODE:
        parts.append(sm)
    if spm != DEFAULT_SPREAD_MODE:
        parts.append(spm)
    if sig != DEFAULT_SIGNAL_MODE:
        parts.append(sig)
    return EXPORTS_DIR / f"{'_'.join(parts)}_holdings.xlsx"


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


def _spot_path() -> Path:
    if _dp_spot is not None:
        p = _dp_spot()
        if p is not None and p.exists() and p.stat().st_size > 0:
            return p
    for p in _SPOT_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError("index_spot.parquet not found")


def total_var_interp_iv(ttms: np.ndarray, watms: np.ndarray, t_tgt: float) -> float:
    """ATM IV via total-variance linear-in-time interpolation to t_tgt."""
    if len(ttms) == 0:
        return float("nan")
    order = np.argsort(ttms)
    ttms = ttms[order]
    watms = watms[order]
    if t_tgt <= ttms[0]:
        w = float(watms[0])
        t_use = float(ttms[0])
    elif t_tgt >= ttms[-1]:
        w = float(watms[-1])
        t_use = float(ttms[-1])
    else:
        i = int(np.searchsorted(ttms, t_tgt))
        t0, t1 = float(ttms[i - 1]), float(ttms[i])
        w0, w1 = float(watms[i - 1]), float(watms[i])
        alpha = (t_tgt - t0) / (t1 - t0) if t1 != t0 else 0.0
        w = w0 + alpha * (w1 - w0)
        t_use = t_tgt
    if t_use <= 1e-12 or w < 0:
        return float("nan")
    return math.sqrt(max(w, 0.0) / t_use)


def rolling_zscore(spread: pd.Series, window: int = DEFAULT_Z_WINDOW) -> pd.Series:
    """Z_t=(Spread_t-μ)/σ with μ,σ from Spread[t-window..t-1] only (no future leak)."""
    window = int(window)
    if window < 2:
        raise ValueError("z_window must be >= 2")
    mp = rolling_min_periods(window)
    s = spread.astype(float)
    hist = s.shift(1)  # exclude today from stats
    mu = hist.rolling(window, min_periods=mp).mean()
    var = hist.rolling(window, min_periods=mp).var(ddof=1)
    sig = np.sqrt(var)
    return (s - mu) / sig.replace(0.0, np.nan)


def rolling_quantile_bands(
    spread: pd.Series,
    window: int = DEFAULT_Z_WINDOW,
    enter_q: float = ENTER_Q,
    exit_lo_q: float = EXIT_LO_Q,
    exit_hi_q: float = EXIT_HI_Q,
) -> pd.DataFrame:
    """Time-varying enter/exit bands from prior-window quantiles of Spread.

    All bands at t use only Spread[t-window .. t-1] (shift(1) → no future leak).
    Bands appear once ``rolling_min_periods(window)`` history exists.
    """
    window = int(window)
    if window < 2:
        raise ValueError("z_window must be >= 2")
    if not (0.5 < enter_q < 1.0):
        raise ValueError("enter_q must be in (0.5, 1)")
    if not (0.0 < exit_lo_q < exit_hi_q < 1.0):
        raise ValueError("need 0 < exit_lo_q < exit_hi_q < 1")
    s = spread.astype(float)
    hist = s.shift(1)
    roll = hist.rolling(window, min_periods=rolling_min_periods(window))
    return pd.DataFrame(
        {
            "enter_hi": roll.quantile(enter_q),
            "enter_lo": roll.quantile(1.0 - enter_q),
            "exit_hi": roll.quantile(exit_hi_q),
            "exit_lo": roll.quantile(exit_lo_q),
        },
        index=s.index,
    )


@dataclass
class OptPos:
    wind_code: str
    expiry: str
    strike: float
    cp: str
    n: float
    ttm: float
    forward: float
    iv: float
    r: float
    mid: float
    delta: float
    gamma: float
    vega: float
    vega_point: float
    theta: float


@dataclass
class DayState:
    position: int = 0
    hold_days: int = 0
    opts: List[OptPos] = field(default_factory=list)
    n_if: float = 0.0
    regime_mode: Optional[str] = None  # None | fade | momentum
    cooldown: int = 0
    entry_spread: Optional[float] = None


class CalendarVolBacktestEngine:
    def __init__(
        self,
        universe: str = "300",
        initial_nav: float = 100_000_000.0,
        z_window: int = DEFAULT_Z_WINDOW,
        enter_q: float = ENTER_Q,
        exit_lo_q: float = EXIT_LO_Q,
        exit_hi_q: float = EXIT_HI_Q,
        tenor_pair: str = DEFAULT_TENOR_PAIR,
        side_mode: str = DEFAULT_SIDE_MODE,
        spread_mode: str = DEFAULT_SPREAD_MODE,
        signal_mode: str = DEFAULT_SIGNAL_MODE,
        budget_fade: float = BUDGET_FADE,
        budget_momentum: float = BUDGET_MOMENTUM,
        budget_fade_vol_scale: bool = False,
        stress_streak_days: int = 2,
        max_hold_reverse: int = MAX_HOLD_REVERSE,
    ):
        if universe not in UNIVERSES:
            raise ValueError(f"unknown universe {universe}; choose {list(UNIVERSES)}")
        self.universe = universe
        self.cfg = UNIVERSES[universe]
        self.tenor_pair = normalize_tenor_pair(tenor_pair)
        self.tenor_cfg = TENOR_PAIRS[self.tenor_pair]
        self.near_label = str(self.tenor_cfg["near_label"])
        self.far_label = str(self.tenor_cfg["far_label"])
        self.t_near = float(self.tenor_cfg["t_near"])
        self.t_far = float(self.tenor_cfg["t_far"])
        self.tenor_display = str(self.tenor_cfg["display"])
        self.side_mode = normalize_side_mode(side_mode)
        self.spread_mode = normalize_spread_mode(spread_mode)
        self.signal_mode = normalize_signal_mode(signal_mode)
        # Chart display: vol pts (*100) for raw spread / regime; unit z (*1) for garch_z.
        self.level_scale = 1.0 if self.signal_mode == "garch_z" else 100.0
        self.initial_nav = float(initial_nav)
        self.z_window = int(z_window)
        self.enter_q = float(enter_q)
        self.exit_lo_q = float(exit_lo_q)
        self.exit_hi_q = float(exit_hi_q)
        self.budget_fade = float(budget_fade)
        self.budget_momentum = float(budget_momentum)
        self.budget_fade_vol_scale = bool(budget_fade_vol_scale)
        self.stress_streak_days = max(1, int(stress_streak_days))
        self.max_hold_reverse = max(1, int(max_hold_reverse))
        self.product = self.cfg["product"]
        self.spot_code = self.cfg["spot_code"]
        self.m_opt = float(self.cfg["m_opt"])
        self.m_fut = float(self.cfg["m_fut"])
        self._vega_checks: List[float] = []
        self._holdings_rows: List[Dict[str, Any]] = []
        self._garch_sigma: Optional[pd.Series] = None
        self._sigma_med252: Optional[pd.Series] = None

    def _load(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.DataFrame]:
        db = _equity_db()
        conn = sqlite3.connect(str(db))
        svi = pd.read_sql(
            """
            SELECT trade_date, ttm, w_atm, jw_v
              FROM svi_slices
             WHERE product=? AND side='otm'
               AND ttm IS NOT NULL AND ttm > 0
             ORDER BY trade_date, ttm
            """,
            conn,
            params=(self.product,),
        )
        # mid_price column is Wind S_DQ_CLOSE (legacy name; never settle).
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

        spot_df = pd.read_parquet(_spot_path())
        spot_df = spot_df[spot_df["wind_code"] == self.spot_code].copy()
        spot_df["trade_date"] = spot_df["trade_date"].astype(str).str[:10]
        spot = spot_df.set_index("trade_date")["close"].astype(float).sort_index()

        # Close-only: never fall back to settle for hedge MTM / sizing.
        fut["trade_date"] = fut["trade_date"].astype(str).str[:10]
        front = (
            fut.sort_values(["trade_date", "contract_ym"])
            .groupby("trade_date", as_index=False)
            .first()
            .set_index("trade_date")[["wind_code", "contract_ym", "close"]]
            .sort_index()
        )
        front["close"] = front["close"].astype(float)

        svi["trade_date"] = svi["trade_date"].astype(str).str[:10]
        opt["trade_date"] = opt["trade_date"].astype(str).str[:10]
        opt["cp"] = opt["cp"].astype(str).str.upper().str[:1]
        return svi, opt, spot, front

    def _daily_ct_iv(
        self, svi: pd.DataFrame, t_near: Optional[float] = None, t_far: Optional[float] = None
    ) -> pd.DataFrame:
        """ATM CT-IV near/far + Spread (iv or iv2). Days missing either bracket omitted."""
        tn = float(self.t_near if t_near is None else t_near)
        tf = float(self.t_far if t_far is None else t_far)
        rows = []
        for td, g in svi.groupby("trade_date"):
            ttms = g["ttm"].to_numpy(float)
            watm = g["w_atm"].to_numpy(float)
            if np.any(~np.isfinite(watm)):
                jw = g["jw_v"].to_numpy(float)
                watm = np.where(np.isfinite(watm), watm, np.maximum(jw, 0.0) * ttms)
            iv_near = total_var_interp_iv(ttms, watm, tn)
            iv_far = total_var_interp_iv(ttms, watm, tf)
            if (
                iv_near == iv_near
                and iv_far == iv_far
                and iv_near > 0
                and iv_far > 0
            ):
                rows.append(
                    {
                        "d": td,
                        "iv_near": iv_near,
                        "iv_far": iv_far,
                        "spread": compute_spread(iv_near, iv_far, self.spread_mode),
                    }
                )
        return pd.DataFrame(rows).set_index("d").sort_index()

    def build_synthetic_leg(
        self,
        day_opts: pd.DataFrame,
        spot: float,
        t_target: float,
        n_leg: float,
    ) -> Tuple[List[OptPos], float, float]:
        if day_opts.empty or not math.isfinite(n_leg) or abs(n_leg) < 1e-16:
            return [], 0.0, 0.0

        by_exp = (
            day_opts.groupby("expiry", as_index=False)
            .agg(ttm=("ttm", "first"))
            .sort_values("ttm")
        )
        ttms = by_exp["ttm"].to_numpy(float)
        exps = by_exp["expiry"].astype(str).tolist()
        if len(ttms) == 0:
            return [], 0.0, 0.0

        if t_target <= ttms[0]:
            near_i, far_i = 0, 0
            w_far, w_near = 0.0, 1.0
        elif t_target >= ttms[-1]:
            near_i = far_i = len(ttms) - 1
            w_far, w_near = 1.0, 0.0
        else:
            i = int(np.searchsorted(ttms, t_target))
            near_i, far_i = i - 1, i
            t0, t1 = float(ttms[near_i]), float(ttms[far_i])
            w_far = (t_target - t0) / (t1 - t0) if t1 != t0 else 0.0
            w_near = 1.0 - w_far

        positions: List[OptPos] = []
        unit_vega = 0.0
        unit_delta = 0.0

        for idx, w in ((near_i, w_near), (far_i, w_far)):
            if w == 0.0 and near_i != far_i:
                continue
            exp = exps[idx]
            slice_ = day_opts[day_opts["expiry"].astype(str) == exp]
            if slice_.empty:
                continue
            strikes = sorted(slice_["strike"].unique())
            K = min(strikes, key=lambda k: abs(float(k) - spot))
            for cp in ("C", "P"):
                row = slice_[(slice_["strike"] == K) & (slice_["cp"] == cp)]
                if row.empty:
                    continue
                r0 = row.iloc[0]
                F = float(r0["forward"]) if pd.notna(r0["forward"]) and r0["forward"] > 0 else spot
                iv = float(r0["iv"])
                ttm = float(r0["ttm"])
                rr = float(r0["r"]) if pd.notna(r0["r"]) else 0.02
                mid = float(r0["mid_price"])
                g = black76_greeks(F, float(K), ttm, iv, rr, cp)
                n = n_leg * w
                positions.append(
                    OptPos(
                        wind_code=str(r0["wind_code"]),
                        expiry=exp,
                        strike=float(K),
                        cp=cp,
                        n=n,
                        ttm=ttm,
                        forward=F,
                        iv=iv,
                        r=rr,
                        mid=mid,
                        delta=g["delta"],
                        gamma=g["gamma"],
                        vega=g["vega"],
                        vega_point=g["vega_point"],
                        theta=g["theta"],
                    )
                )
                unit_vega += w * g["vega"]
                unit_delta += w * g["delta"]

        return positions, unit_vega, unit_delta

    def _size_legs(
        self,
        day_opts: pd.DataFrame,
        spot: float,
        direction: int,
        nav: float,
        budget_frac: Optional[float] = None,
    ) -> Tuple[List[OptPos], float, float]:
        """Far-leg dollar vega = budget_frac·NAV/vol-pt; N_near = −N_far · ν_far / ν_near."""
        _, nu_far, _ = self.build_synthetic_leg(day_opts, spot, self.t_far, 1.0)
        _, nu_near, _ = self.build_synthetic_leg(day_opts, spot, self.t_near, 1.0)
        if nu_near <= 1e-12 or nu_far <= 1e-12:
            return [], 0.0, 0.0

        frac = float(VEGA_BUDGET_FRAC if budget_frac is None else budget_frac)
        unit_vp_far = nu_far / 100.0
        target_cny_vp = frac * nav
        n_far_abs = target_cny_vp / (unit_vp_far * self.m_opt)
        n_far = direction * n_far_abs
        n_near = -n_far * (nu_far / nu_near)

        pos_far, _, _ = self.build_synthetic_leg(day_opts, spot, self.t_far, n_far)
        pos_near, _, _ = self.build_synthetic_leg(day_opts, spot, self.t_near, n_near)
        all_pos = pos_near + pos_far
        if not all_pos:
            return [], 0.0, 0.0
        self._vega_checks.append(abs(sum(p.n * p.vega for p in all_pos)))
        return all_pos, n_near, n_far

    def _budget_for_mode(
        self, regime_mode: Optional[str], trade_date: Optional[str] = None
    ) -> float:
        if not is_regime_signal(self.signal_mode):
            return float(VEGA_BUDGET_FRAC)
        if regime_mode == "momentum":
            return float(self.budget_momentum)
        frac = float(self.budget_fade)
        if (
            self.budget_fade_vol_scale
            and trade_date is not None
            and self._garch_sigma is not None
            and self._sigma_med252 is not None
            and trade_date in self._garch_sigma.index
        ):
            sig = float(self._garch_sigma.loc[trade_date])
            med = float(self._sigma_med252.loc[trade_date])
            if math.isfinite(sig) and math.isfinite(med) and sig > 1e-12:
                # High σ → shrink fade budget; low σ → allow up to 1.5× base.
                scale = max(0.35, min(1.5, med / sig))
                frac *= scale
        return frac

    def _lookup_mtm(self, pos: OptPos, day_opts: pd.DataFrame) -> Optional[Tuple[float, float]]:
        hit = day_opts[day_opts["wind_code"] == pos.wind_code]
        if hit.empty:
            hit = day_opts[
                (day_opts["expiry"].astype(str) == pos.expiry)
                & (day_opts["strike"] == pos.strike)
                & (day_opts["cp"] == pos.cp)
            ]
        if hit.empty:
            return None
        r0 = hit.iloc[0]
        return float(r0["mid_price"]), float(r0["iv"])

    def _apply_side_mode(self, target: int, position: int) -> int:
        """Filter target by side_mode; always allow flat (0) closes.

        long_only: keep +1 and 0; ignore −1 entries (stay flat / force-close stray short).
        short_only: keep −1 and 0; ignore +1 entries.
        both: unchanged.
        """
        sm = self.side_mode
        if sm == "both":
            return target
        if sm == "long_only":
            if target == -1:
                return 0
            if position == -1:
                return 0
            return target
        # short_only
        if target == 1:
            return 0
        if position == 1:
            return 0
        return target

    def _decide_signal(
        self,
        position: int,
        hold_days: int,
        spread: float,
        eh: float,
        el: float,
        xh: float,
        xl: float,
    ) -> int:
        """Emit next-day target from post-trade state + today's close spread."""
        bands_ok = all(math.isfinite(x) for x in (eh, el, xh, xl))
        if not bands_ok:
            return self._apply_side_mode(position, position)
        if position == 0:
            if spread > eh:
                raw = 1  # Normal: short near / long far (spread rich)
            elif spread < el:
                raw = -1  # Reverse
            else:
                raw = 0
            return self._apply_side_mode(raw, position)
        if xl <= spread <= xh:
            return 0
        if position == -1 and hold_days >= self.max_hold_reverse:
            return 0
        return self._apply_side_mode(position, position)

    def _append_holdings(
        self,
        d: str,
        position: int,
        opts: List[OptPos],
        n_if: float,
        fut_code: str,
        fut_ym: str,
        F: float,
        signal_next: int,
        executed: bool,
    ) -> None:
        for p in opts:
            self._holdings_rows.append(
                {
                    "trade_date": d,
                    "instrument": "option",
                    "wind_code": p.wind_code,
                    "contract_ym": str(p.expiry)[:7].replace("-", "")[2:]
                    if "-" in str(p.expiry)
                    else str(p.expiry),
                    "expiry": p.expiry,
                    "strike": p.strike,
                    "cp": p.cp,
                    "direction": "long" if p.n > 0 else "short",
                    "qty": float(p.n),
                    "price": float(p.mid),  # option close (mid_price←S_DQ_CLOSE)
                    "multiplier": self.m_opt,
                    "strategy_position": position,
                    "signal_next": signal_next,
                    "executed": executed,
                    "leg": self.near_label
                    if p.ttm < (self.t_near + self.t_far) / 2
                    else self.far_label,
                }
            )
        if abs(n_if) > 1e-12:
            self._holdings_rows.append(
                {
                    "trade_date": d,
                    "instrument": "future",
                    "wind_code": fut_code,
                    "contract_ym": fut_ym,
                    "expiry": None,
                    "strike": None,
                    "cp": None,
                    "direction": "long" if n_if > 0 else "short",
                    "qty": float(n_if),
                    "price": float(F),  # futures close
                    "multiplier": self.m_fut,
                    "strategy_position": position,
                    "signal_next": signal_next,
                    "executed": executed,
                    "leg": "hedge",
                }
            )

    def holdings_dataframe(self) -> pd.DataFrame:
        if not self._holdings_rows:
            return pd.DataFrame(
                columns=[
                    "trade_date",
                    "instrument",
                    "wind_code",
                    "contract_ym",
                    "expiry",
                    "strike",
                    "cp",
                    "direction",
                    "qty",
                    "price",
                    "multiplier",
                    "strategy_position",
                    "signal_next",
                    "executed",
                    "leg",
                ]
            )
        return pd.DataFrame(self._holdings_rows)

    def write_holdings_excel(self, path: Optional[Path] = None) -> Path:
        """Write concrete holdings ledger to Excel (date/contract/dir/qty/price…)."""
        out = (
            Path(path)
            if path
            else default_holdings_export_path(
                self.universe, self.tenor_pair, self.side_mode, self.spread_mode
            )
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        df = self.holdings_dataframe()
        with pd.ExcelWriter(out, engine="openpyxl") as xl:
            df.to_excel(xl, sheet_name="holdings", index=False)
            meta = pd.DataFrame(
                [
                    {"key": "strategy_id", "value": self.cfg["strategy_id"]},
                    {"key": "universe", "value": self.universe},
                    {"key": "tenor_pair", "value": self.tenor_pair},
                    {"key": "side_mode", "value": self.side_mode},
                    {"key": "spread_mode", "value": self.spread_mode},
                    {"key": "signal_lag", "value": "t-1 signal → t close execution"},
                    {
                        "key": "price",
                        "value": "option close (mid_price←S_DQ_CLOSE) / futures close",
                    },
                    {"key": "n_rows", "value": len(df)},
                    {
                        "key": "n_trade_days",
                        "value": int(df["trade_date"].nunique()) if len(df) else 0,
                    },
                ]
            )
            meta.to_excel(xl, sheet_name="meta", index=False)
        return out

    def run(self) -> Dict[str, Any]:
        svi, opt, spot, front = self._load()
        ct = self._daily_ct_iv(svi)
        # 1M–3M: keep the same nominal calendar as 3M–6M for this index.
        # Days missing the 1M bracket keep NaN spread → no signal (do not shorten start).
        if self.tenor_pair != DEFAULT_TENOR_PAIR:
            ct_ref = self._daily_ct_iv(svi, t_near=T_3M, t_far=T_6M)
            ct = ct.reindex(ct_ref.index)
        z = rolling_zscore(ct["spread"], self.z_window)
        # Always keep CT-IV spread quantile bands for the reference chart (even under garch_z).
        spread_bands = rolling_quantile_bands(
            ct["spread"],
            self.z_window,
            enter_q=self.enter_q,
            exit_lo_q=self.exit_lo_q,
            exit_hi_q=self.exit_hi_q,
        ).rename(
            columns={
                "enter_hi": "iv_enter_hi",
                "enter_lo": "iv_enter_lo",
                "exit_hi": "iv_exit_hi",
                "exit_lo": "iv_exit_lo",
            }
        )
        regime_level_bands = None
        regime_shock_bands = None
        gz_chart_bands = None
        garch_z_series: Optional[pd.Series] = None
        if self.signal_mode == "garch_z":
            from engine.garch_signal import GARCH_WARM, expanding_garch_z

            signal_level, _sigma = expanding_garch_z(ct["spread"])
            garch_z_series = signal_level
            min_hist = GARCH_WARM + rolling_min_periods(self.z_window) + 30
            bands = rolling_quantile_bands(
                signal_level,
                self.z_window,
                enter_q=self.enter_q,
                exit_lo_q=self.exit_lo_q,
                exit_hi_q=self.exit_hi_q,
            )
        elif is_regime_signal(self.signal_mode) or is_regime2_signal(self.signal_mode):
            from engine.garch_signal import GARCH_WARM, expanding_garch_z
            from engine.regime_signal import LEVEL_QS, SHOCK_QS, rolling_quantile_map

            garch_z_series, sigma = expanding_garch_z(ct["spread"])
            self._garch_sigma = sigma
            # Causal median of prior-window σ (shift so today excluded).
            _mp = rolling_min_periods(self.z_window)
            self._sigma_med252 = (
                sigma.shift(1).rolling(self.z_window, min_periods=_mp).median()
            )
            # Decision chart level = raw Spread; shock = garch_z.
            signal_level = ct["spread"].astype(float)
            min_hist = GARCH_WARM + _mp + 30
            # Keep enter/exit columns aligned with fade level bands for markers/charts.
            bands = spread_bands.rename(
                columns={
                    "iv_enter_hi": "enter_hi",
                    "iv_enter_lo": "enter_lo",
                    "iv_exit_hi": "exit_hi",
                    "iv_exit_lo": "exit_lo",
                }
            )
            # Chart + stress gate: same 10/90·40/60 rolling bands on garch_z.
            gz_chart_bands = rolling_quantile_bands(
                garch_z_series,
                self.z_window,
                enter_q=self.enter_q,
                exit_lo_q=self.exit_lo_q,
                exit_hi_q=self.exit_hi_q,
            ).rename(
                columns={
                    "enter_hi": "gz_enter_hi",
                    "enter_lo": "gz_enter_lo",
                    "exit_hi": "gz_exit_hi",
                    "exit_lo": "gz_exit_lo",
                }
            )
            if is_regime_signal(self.signal_mode):
                regime_level_bands = rolling_quantile_map(
                    ct["spread"], self.z_window, LEVEL_QS
                ).add_prefix("lvl_")
                regime_shock_bands = rolling_quantile_map(
                    garch_z_series, self.z_window, SHOCK_QS
                ).add_prefix("shk_")
        else:
            signal_level = ct["spread"].astype(float)
            min_hist = rolling_min_periods(self.z_window) + 30
            # Decision bands == IV-spread bands.
            bands = spread_bands.rename(
                columns={
                    "iv_enter_hi": "enter_hi",
                    "iv_enter_lo": "enter_lo",
                    "iv_exit_hi": "exit_hi",
                    "iv_exit_lo": "exit_lo",
                }
            )
        join_parts = [
            ct,
            z.rename("z"),
            signal_level.rename("signal_level"),
            bands,
            spread_bands,
        ]
        if garch_z_series is not None and (
            is_regime_signal(self.signal_mode) or is_regime2_signal(self.signal_mode)
        ):
            join_parts.append(garch_z_series.rename("garch_z"))
        if regime_level_bands is not None:
            join_parts.append(regime_level_bands)
        if regime_shock_bands is not None:
            join_parts.append(regime_shock_bands)
        if gz_chart_bands is not None:
            join_parts.append(gz_chart_bands)
        ct = join_parts[0]
        for part in join_parts[1:]:
            ct = ct.join(part, how="left")

        dates = sorted(set(ct.index) & set(spot.index) & set(front.index) & set(opt["trade_date"]))
        if len(dates) < min_hist:
            raise RuntimeError(
                f"Insufficient aligned history for {self.universe}/{self.tenor_pair}"
                f"/{self.signal_mode}: {len(dates)} days (need >{min_hist})"
            )

        opt_by_day = {td: g for td, g in opt.groupby("trade_date")}
        nav = self.initial_nav
        state = DayState()
        pending_signal = 0  # day-0: observe only; first trade earliest on day 1
        pending_mode: Optional[str] = None
        pending_cooldown = 0
        pending_entry_spread: Optional[float] = None
        rows: List[Dict[str, Any]] = []
        self._holdings_rows: List[Dict[str, Any]] = []
        cum_vega = cum_theta = cum_gamma = cum_basis = cum_delta = cum_resid = 0.0
        first_open_i: Optional[int] = None
        hedge_scale = self.m_opt / self.m_fut  # IF/IH: 1/3; IM: 1/2

        for i, d in enumerate(dates):
            S = float(spot.loc[d])
            F = float(front.loc[d, "close"])
            fut_code = str(front.loc[d, "wind_code"])
            fut_ym = str(front.loc[d, "contract_ym"])
            day_opts = opt_by_day.get(d, pd.DataFrame())
            sp_raw = ct.loc[d, "spread"]
            spread = float(sp_raw) if pd.notna(sp_raw) else float("nan")
            sl_raw = ct.loc[d, "signal_level"]
            signal_level_f = float(sl_raw) if pd.notna(sl_raw) else float("nan")
            zv = ct.loc[d, "z"]
            zv = float(zv) if pd.notna(zv) else float("nan")
            gz_f = float("nan")
            if (
                is_regime_signal(self.signal_mode) or is_regime2_signal(self.signal_mode)
            ) and "garch_z" in ct.columns:
                gz_raw = ct.loc[d, "garch_z"]
                gz_f = float(gz_raw) if pd.notna(gz_raw) else float("nan")
            elif self.signal_mode == "garch_z":
                gz_f = signal_level_f
            eh = ct.loc[d, "enter_hi"]
            el = ct.loc[d, "enter_lo"]
            xh = ct.loc[d, "exit_hi"]
            xl = ct.loc[d, "exit_lo"]
            eh_f = float(eh) if pd.notna(eh) else float("nan")
            el_f = float(el) if pd.notna(el) else float("nan")
            xh_f = float(xh) if pd.notna(xh) else float("nan")
            xl_f = float(xl) if pd.notna(xl) else float("nan")
            iv_eh = ct.loc[d, "iv_enter_hi"]
            iv_el = ct.loc[d, "iv_enter_lo"]
            iv_xh = ct.loc[d, "iv_exit_hi"]
            iv_xl = ct.loc[d, "iv_exit_lo"]
            iv_eh_f = float(iv_eh) if pd.notna(iv_eh) else float("nan")
            iv_el_f = float(iv_el) if pd.notna(iv_el) else float("nan")
            iv_xh_f = float(iv_xh) if pd.notna(iv_xh) else float("nan")
            iv_xl_f = float(iv_xl) if pd.notna(iv_xl) else float("nan")

            def _opt_band(col: str) -> Optional[float]:
                if col not in ct.columns:
                    return None
                v = ct.loc[d, col]
                if pd.isna(v):
                    return None
                x = float(v)
                return x if math.isfinite(x) else None

            gz_eh_f = _opt_band("gz_enter_hi")
            gz_el_f = _opt_band("gz_enter_lo")
            gz_xh_f = _opt_band("gz_exit_hi")
            gz_xl_f = _opt_band("gz_exit_lo")
            iv_near_raw = ct.loc[d, "iv_near"]
            iv_far_raw = ct.loc[d, "iv_far"]
            iv_near_f = float(iv_near_raw) if pd.notna(iv_near_raw) else float("nan")
            iv_far_f = float(iv_far_raw) if pd.notna(iv_far_raw) else float("nan")

            def _band(col: str) -> Optional[float]:
                if col not in ct.columns:
                    return None
                v = ct.loc[d, col]
                if pd.isna(v):
                    return None
                x = float(v)
                return x if math.isfinite(x) else None

            lvl_q = {
                "q01": _band("lvl_q01"),
                "q10": _band("lvl_q10"),
                "q25": _band("lvl_q25"),
                "q50": _band("lvl_q50"),
                "q75": _band("lvl_q75"),
                "q90": _band("lvl_q90"),
                "q99": _band("lvl_q99"),
            }
            shk_q = {
                "q10": _band("shk_q10"),
                "q20": _band("shk_q20"),
                "q25": _band("shk_q25"),
                "q75": _band("shk_q75"),
                "q80": _band("shk_q80"),
                "q90": _band("shk_q90"),
            }
            sig_f = float("nan")
            if is_regime_signal(self.signal_mode) and self._garch_sigma is not None and d in self._garch_sigma.index:
                _sv = self._garch_sigma.loc[d]
                if pd.notna(_sv):
                    sig_f = float(_sv)

            # 1) Mark overnight holdings (from t−1 execution) to today's close.
            net_delta = basis = gamma = theta = vega_pnl = 0.0
            actual = 0.0
            if i > 0 and state.opts:
                d_prev = dates[i - 1]
                S_prev = float(spot.loc[d_prev])
                F_prev = float(front.loc[d_prev, "close"])
                B_prev = F_prev - S_prev
                B = F - S
                dS = S - S_prev
                dt_years = 1.0 / 365.0

                opt_delta_pts = sum(p.n * p.delta for p in state.opts)
                opt_gamma_pts = sum(p.n * p.gamma for p in state.opts)
                opt_theta_pts = sum(p.n * p.theta for p in state.opts)

                net_delta = (self.m_opt * opt_delta_pts + self.m_fut * state.n_if) * dS
                basis = self.m_fut * state.n_if * (B - B_prev)
                gamma = 0.5 * self.m_opt * opt_gamma_pts * (dS ** 2)
                theta = self.m_opt * opt_theta_pts * dt_years

                for p in state.opts:
                    mtm = self._lookup_mtm(p, day_opts)
                    if mtm is None:
                        g = black76_greeks(F, p.strike, max(p.ttm - dt_years, 1e-6), p.iv, p.r, p.cp)
                        mid_now, iv_now = g["price"], p.iv
                    else:
                        mid_now, iv_now = mtm
                    actual += self.m_opt * p.n * (mid_now - p.mid)
                    vega_pnl += self.m_opt * p.n * p.vega_point * (iv_now - p.iv) * 100.0

                actual += self.m_fut * state.n_if * (F - F_prev)
                residual = actual - (net_delta + basis + gamma + theta + vega_pnl)
                nav = nav + actual
            else:
                residual = 0.0
                actual = 0.0

            cum_vega += vega_pnl
            cum_theta += theta
            cum_gamma += gamma
            cum_basis += basis
            cum_delta += net_delta
            cum_resid += residual

            # 2) Execute t−1 signal at today's close (open / flat / reverse / resize).
            # Missing near/far CT-IV (e.g. no 1M bracket) → cannot open/resize; flatten.
            target = int(pending_signal)
            exec_mode = pending_mode if target != 0 else None
            exec_entry = pending_entry_spread if target != 0 else None
            opts: List[OptPos] = []
            n_if = 0.0
            n_near = n_far = 0.0
            hold = 0
            executed = False
            can_trade = math.isfinite(spread) and math.isfinite(iv_near_f) and math.isfinite(iv_far_f)
            if target != 0 and not day_opts.empty and can_trade:
                budget = self._budget_for_mode(exec_mode, trade_date=d)
                opts, n_near, n_far = self._size_legs(
                    day_opts, S, target, nav, budget_frac=budget
                )
                if opts:
                    opt_delta_pts = sum(p.n * p.delta for p in opts)
                    n_if = -hedge_scale * opt_delta_pts
                    # New open (or flip) → hold=0 then +1? Existing code: hold = prior+1 if same pos else 0
                    # Then decision uses hold; first day in position hold=0. Spec "满 8 日" counts
                    # trading days held; after execute, hold=0 on open day, then increments.
                    # Match prior engine: same-direction continue increments, else 0 on open day.
                    hold = state.hold_days + 1 if state.position == target else 0
                    executed = True
                    # Keep entry_spread from pending on new open; carry if holding same book.
                    if state.position == target and state.entry_spread is not None:
                        exec_entry = state.entry_spread
                else:
                    target = 0
                    exec_mode = None
                    exec_entry = None
                    hold = 0
            elif target == 0:
                executed = state.position != 0  # flat after prior open = close trade
                hold = 0
                exec_mode = None
                exec_entry = None
            else:
                # signalled open but no option book / missing CT bracket → stay flat
                target = 0
                exec_mode = None
                exec_entry = None
                hold = 0

            if first_open_i is None and target != 0:
                first_open_i = i

            # Carry cooldown from pending when we stay/go flat; mode only when in book.
            live_cooldown = int(pending_cooldown) if target == 0 else 0
            state = DayState(
                position=target,
                hold_days=hold,
                opts=opts,
                n_if=n_if,
                regime_mode=exec_mode if target != 0 else None,
                cooldown=live_cooldown,
                entry_spread=exec_entry if target != 0 else None,
            )

            # 3) Emit signal for next day from post-trade state + today's close.
            signal_reason = "quantile"
            if is_regime_signal(self.signal_mode):
                from engine.regime_signal import RegimeState, decide_regime_signal

                need_lvl = (
                    "q01",
                    "q10",
                    "q25",
                    "q50",
                    "q75",
                    "q90",
                    "q99",
                )
                need_shk = ("q10", "q20", "q80", "q90")
                level_ok = all(lvl_q.get(k) is not None for k in need_lvl)
                shock_ok = all(shk_q.get(k) is not None for k in need_shk)
                z_prev = float("nan")
                if i > 0 and "garch_z" in ct.columns:
                    gz_p = ct.loc[dates[i - 1], "garch_z"]
                    if pd.notna(gz_p):
                        z_prev = float(gz_p)
                rs = RegimeState(
                    position=target,
                    hold_days=hold,
                    regime_mode=state.regime_mode,
                    cooldown=live_cooldown,
                    entry_spread=state.entry_spread,
                )
                if level_ok and shock_ok and math.isfinite(spread) and math.isfinite(gz_f):
                    dec = decide_regime_signal(
                        rs,
                        spread,
                        gz_f,
                        {k: float(lvl_q[k]) for k in need_lvl},  # type: ignore[index]
                        {k: float(shk_q[k]) for k in need_shk},  # type: ignore[index]
                        z_prev=z_prev if math.isfinite(z_prev) else None,
                        book=regime_book_for_signal(self.signal_mode),
                    )
                    raw_next = int(dec.signal)
                    signal_next = self._apply_side_mode(raw_next, target)
                    signal_reason = dec.reason
                    if signal_next != 0:
                        pending_mode = dec.regime_mode
                        pending_cooldown = 0
                        if target == 0 or target != signal_next:
                            pending_entry_spread = dec.entry_spread
                        else:
                            pending_entry_spread = state.entry_spread
                    else:
                        pending_mode = None
                        pending_entry_spread = None
                        if raw_next == 0:
                            pending_cooldown = int(dec.cooldown)
                        elif target != 0:
                            pending_cooldown = 3
                            signal_reason = f"side_flat:{dec.reason}"
                        else:
                            pending_cooldown = (
                                max(live_cooldown - 1, 0) if live_cooldown > 0 else 0
                            )
                            signal_reason = f"side_filter:{dec.reason}"
                else:
                    signal_next = self._apply_side_mode(target, target)
                    pending_mode = state.regime_mode if signal_next != 0 else None
                    pending_entry_spread = state.entry_spread if signal_next != 0 else None
                    if signal_next == 0 and live_cooldown > 0:
                        pending_cooldown = live_cooldown - 1
                    else:
                        pending_cooldown = 0 if signal_next != 0 else live_cooldown
                    signal_reason = "bands_nan"
            elif is_regime2_signal(self.signal_mode):
                from engine.regime2_signal import decide_regime2_signal

                z_q10_t = gz_el_f
                z_q90_t = gz_eh_f
                need_hist = max(1, int(self.stress_streak_days))
                z_hist = []
                for k in range(need_hist - 1, 0, -1):
                    if i - k < 0 or "garch_z" not in ct.columns:
                        z_hist.append((None, None, None))
                        continue
                    d_k = dates[i - k]
                    gz_k = ct.loc[d_k, "garch_z"]
                    z_k = float(gz_k) if pd.notna(gz_k) else None
                    q10_k = None
                    q90_k = None
                    if "gz_enter_lo" in ct.columns and pd.notna(ct.loc[d_k, "gz_enter_lo"]):
                        q10_k = float(ct.loc[d_k, "gz_enter_lo"])
                    if "gz_enter_hi" in ct.columns and pd.notna(ct.loc[d_k, "gz_enter_hi"]):
                        q90_k = float(ct.loc[d_k, "gz_enter_hi"])
                    z_hist.append((z_k, q10_k, q90_k))
                z_hist.append(
                    (
                        gz_f if math.isfinite(gz_f) else None,
                        float(z_q10_t) if z_q10_t is not None else None,
                        float(z_q90_t) if z_q90_t is not None else None,
                    )
                )
                dec2 = decide_regime2_signal(
                    target,
                    hold,
                    signal_level_f,
                    eh_f,
                    el_f,
                    xh_f,
                    xl_f,
                    gz_f,
                    float(z_q10_t) if z_q10_t is not None else float("nan"),
                    float(z_q90_t) if z_q90_t is not None else float("nan"),
                    z_hist=z_hist,
                    stress_streak_days=self.stress_streak_days,
                    max_hold_reverse=self.max_hold_reverse,
                )
                raw_next = int(dec2.signal)
                signal_next = self._apply_side_mode(raw_next, target)
                signal_reason = dec2.reason
                pending_mode = None
                pending_cooldown = 0
                pending_entry_spread = None
            else:
                signal_next = self._decide_signal(
                    target, hold, signal_level_f, eh_f, el_f, xh_f, xl_f
                )
                pending_mode = None
                pending_cooldown = 0
                pending_entry_spread = None
            pending_signal = signal_next

            if target != 0 or executed:
                self._append_holdings(
                    d, target, opts, n_if, fut_code, fut_ym, F, signal_next, executed or target != 0
                )

            rows.append(
                {
                    "d": d,
                    "nav": nav,
                    "ret": (actual / (nav - actual)) if i > 0 and (nav - actual) != 0 else 0.0,
                    # CT-IV spread kept for reference; chart/decision use signal_level.
                    "spread_iv": spread if math.isfinite(spread) else float("nan"),
                    "spread": signal_level_f if math.isfinite(signal_level_f) else float("nan"),
                    "garch_z": gz_f if math.isfinite(gz_f) else None,
                    "garch_sigma": sig_f if math.isfinite(sig_f) else None,
                    "z": zv if math.isfinite(zv) else None,
                    "enter_hi": eh_f if math.isfinite(eh_f) else None,
                    "enter_lo": el_f if math.isfinite(el_f) else None,
                    "exit_hi": xh_f if math.isfinite(xh_f) else None,
                    "exit_lo": xl_f if math.isfinite(xl_f) else None,
                    "iv_enter_hi": iv_eh_f if math.isfinite(iv_eh_f) else None,
                    "iv_enter_lo": iv_el_f if math.isfinite(iv_el_f) else None,
                    "iv_exit_hi": iv_xh_f if math.isfinite(iv_xh_f) else None,
                    "iv_exit_lo": iv_xl_f if math.isfinite(iv_xl_f) else None,
                    "gz_enter_hi": gz_eh_f,
                    "gz_enter_lo": gz_el_f,
                    "gz_exit_hi": gz_xh_f,
                    "gz_exit_lo": gz_xl_f,
                    "lvl_q01": lvl_q["q01"],
                    "lvl_q10": lvl_q["q10"],
                    "lvl_q25": lvl_q["q25"],
                    "lvl_q50": lvl_q["q50"],
                    "lvl_q75": lvl_q["q75"],
                    "lvl_q90": lvl_q["q90"],
                    "lvl_q99": lvl_q["q99"],
                    "shk_q10": shk_q["q10"],
                    "shk_q20": shk_q["q20"],
                    "shk_q25": shk_q["q25"],
                    "shk_q75": shk_q["q75"],
                    "shk_q80": shk_q["q80"],
                    "shk_q90": shk_q["q90"],
                    "position": target,  # executed / held after t close
                    "signal_next": signal_next,  # to be traded at t+1 close
                    "regime_mode": state.regime_mode,
                    "cooldown": int(state.cooldown),
                    "entry_spread": state.entry_spread,
                    "signal_reason": signal_reason,
                    "n_near": n_near,
                    "n_far": n_far,
                    "n_if": n_if,
                    "net_delta_pnl": net_delta,
                    "basis_pnl": basis,
                    "gamma_pnl": gamma,
                    "theta_pnl": theta,
                    "vega_pnl": vega_pnl,
                    "residual_pnl": residual,
                    "actual_pnl": actual,
                    "cum_vega": cum_vega,
                    "cum_theta": cum_theta,
                    "cum_gamma": cum_gamma,
                    "cum_basis": cum_basis,
                    "cum_delta": cum_delta,
                    "cum_resid": cum_resid,
                    "iv_near": iv_near_f,
                    "iv_far": iv_far_f,
                }
            )

        return self._pack_payload(rows, first_open_i)

    def _window_metrics(self, nav: pd.Series, daily_ret: pd.Series) -> Dict[str, Any]:
        if len(nav) < 2:
            return {
                "n_days": int(len(nav)),
                "ann_return": None,
                "ann_vol": None,
                "sharpe": None,
                "mdd": None,
                "mdd_vol": None,
                "pct_positive_months": None,
            }
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
        """Calendar-year performance rows (nav rebased to 1.0 at each year start)."""
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

    def _pack_payload(
        self, rows: List[Dict[str, Any]], first_open_i: Optional[int]
    ) -> Dict[str, Any]:
        # Performance starts the first calendar day *after* first open (first overnight PnL).
        if first_open_i is None or first_open_i + 1 >= len(rows):
            raise RuntimeError(f"No trade opened for universe {self.universe}")

        start_i = first_open_i + 1
        open_row = rows[first_open_i]
        base_nav = float(open_row["nav"])
        # rebase cumulative attribution to open-day close (before first overnight)
        base_cum = {
            "cum_vega": float(open_row["cum_vega"]),
            "cum_theta": float(open_row["cum_theta"]),
            "cum_gamma": float(open_row["cum_gamma"]),
            "cum_basis": float(open_row["cum_basis"]),
            "cum_delta": float(open_row["cum_delta"]),
            "cum_resid": float(open_row["cum_resid"]),
        }

        perf: List[Dict[str, Any]] = []
        peak = base_nav
        for r in rows[start_i:]:
            nav = float(r["nav"])
            peak = max(peak, nav)
            perf.append(
                {
                    **r,
                    "nav": nav,
                    "cum_vega": float(r["cum_vega"]) - base_cum["cum_vega"],
                    "cum_theta": float(r["cum_theta"]) - base_cum["cum_theta"],
                    "cum_gamma": float(r["cum_gamma"]) - base_cum["cum_gamma"],
                    "cum_basis": float(r["cum_basis"]) - base_cum["cum_basis"],
                    "cum_delta": float(r["cum_delta"]) - base_cum["cum_delta"],
                    "cum_resid": float(r["cum_resid"]) - base_cum["cum_resid"],
                    "drawdown": nav / peak - 1.0 if peak > 0 else 0.0,
                }
            )

        nav_s = pd.Series({r["d"]: r["nav"] for r in perf})
        ret_s = pd.Series({r["d"]: r["ret"] for r in perf})
        # first performance day ret is the first overnight return — keep it

        metrics = {}
        for name, yrs in (("1y", 1.0), ("3y", 3.0), ("5y", 5.0), ("max", None)):
            nav_w, ret_w = self._slice_from_end(nav_s, ret_s, yrs)
            metrics[name] = self._window_metrics(nav_w, ret_w)
            metrics[name]["start"] = str(nav_w.index[0]) if len(nav_w) else None
            metrics[name]["end"] = str(nav_w.index[-1]) if len(nav_w) else None
        yearly_metrics = self._yearly_metrics(nav_s, ret_s)

        def _sos_pts(sp: Any, scale: Optional[float] = None) -> Optional[float]:
            """Scaled level for overlay charts (vol pts or garch_z units)."""
            sc = self.level_scale if scale is None else float(scale)
            try:
                x = float(sp)
            except (TypeError, ValueError):
                return None
            return None if not math.isfinite(x) else round(x * sc, 4)

        step = max(1, len(perf) // 800)
        nav_idx = list(range(0, len(perf), step))
        if not nav_idx or nav_idx[-1] != len(perf) - 1:
            nav_idx.append(len(perf) - 1)
        # Include executed position-change days so UI markers land on the NAV/sos chart.
        for i in range(1, len(perf)):
            if int(perf[i]["position"]) != int(perf[i - 1]["position"]):
                nav_idx.append(i)
        nav_idx = sorted(set(nav_idx))
        nav_chart = [
            {
                "d": perf[i]["d"],
                "nav": round(perf[i]["nav"] / base_nav, 6),
                "sos": _sos_pts(perf[i]["spread"]),
            }
            for i in nav_idx
        ]

        dec_idx = list(range(0, len(perf), step))
        if dec_idx[-1] != len(perf) - 1:
            dec_idx.append(len(perf) - 1)

        def _col(key: str) -> List[float]:
            return [round(float(perf[i][key]), 4) for i in dec_idx]

        pnl_decomposition = {
            "dates": [perf[i]["d"] for i in dec_idx],
            "vega_pnl": _col("cum_vega"),
            "theta_pnl": _col("cum_theta"),
            "gamma_pnl": _col("cum_gamma"),
            "basis_pnl": _col("cum_basis"),
            "net_delta_pnl": _col("cum_delta"),
            "residual_pnl": _col("cum_resid"),
        }
        # Daily (non-cumulative) greek PnL for bar chart under the cumulative panel.
        pnl_daily_greeks = {
            "dates": [perf[i]["d"] for i in dec_idx],
            "vega_pnl": [round(float(perf[i]["vega_pnl"]), 4) for i in dec_idx],
            "theta_pnl": [round(float(perf[i]["theta_pnl"]), 4) for i in dec_idx],
            "gamma_pnl": [round(float(perf[i]["gamma_pnl"]), 4) for i in dec_idx],
        }

        # signal chart: include warm-up history for Z context, but mark performance start
        sig_step = max(1, len(rows) // 800)
        sig_idx = list(range(0, len(rows), sig_step))
        if sig_idx[-1] != len(rows) - 1:
            sig_idx.append(len(rows) - 1)
        # Keep every executed position change so chart markers are not lost to downsample.
        for i in range(1, len(rows)):
            if int(rows[i]["position"]) != int(rows[i - 1]["position"]):
                sig_idx.append(i)
        sig_idx = sorted(set(sig_idx))

        def _opt(v: Any) -> Optional[float]:
            if v is None:
                return None
            try:
                x = float(v)
            except (TypeError, ValueError):
                return None
            return None if not math.isfinite(x) else round(x, 6)

        # Presentation-only trade markers from executed position transitions (no strategy change).
        trade_markers: List[Dict[str, Any]] = []
        for i in range(1, len(rows)):
            prev_p = int(rows[i - 1]["position"])
            curr_p = int(rows[i]["position"])
            if prev_p == curr_p:
                continue
            if curr_p == 1:
                kind = "long"
            elif curr_p == -1:
                kind = "short"
            else:
                kind = "close"
            trade_markers.append(
                {
                    "d": rows[i]["d"],
                    "kind": kind,
                    "spread": _opt(rows[i]["spread"]),
                    "sos": _sos_pts(rows[i]["spread"]),
                    "spread_iv": _opt(rows[i].get("spread_iv")),
                    "sos_iv": _sos_pts(rows[i].get("spread_iv"), scale=100.0),
                    "garch_z": _opt(rows[i].get("garch_z")),
                    "position": curr_p,
                    "prev_position": prev_p,
                    "regime_mode": rows[i].get("regime_mode"),
                }
            )

        signal = {
            "dates": [rows[i]["d"] for i in sig_idx],
            "spread": [_opt(rows[i]["spread"]) for i in sig_idx],  # decision level
            "spread_iv": [_opt(rows[i].get("spread_iv")) for i in sig_idx],
            "garch_z": [_opt(rows[i].get("garch_z")) for i in sig_idx],
            "zscore": [None if rows[i]["z"] is None else round(float(rows[i]["z"]), 4) for i in sig_idx],
            "enter_hi": [_opt(rows[i]["enter_hi"]) for i in sig_idx],
            "enter_lo": [_opt(rows[i]["enter_lo"]) for i in sig_idx],
            "exit_hi": [_opt(rows[i]["exit_hi"]) for i in sig_idx],
            "exit_lo": [_opt(rows[i]["exit_lo"]) for i in sig_idx],
            # CT-IV spread rolling bands (reference chart; = decision bands when quantile mode)
            "iv_enter_hi": [_opt(rows[i].get("iv_enter_hi")) for i in sig_idx],
            "iv_enter_lo": [_opt(rows[i].get("iv_enter_lo")) for i in sig_idx],
            "iv_exit_hi": [_opt(rows[i].get("iv_exit_hi")) for i in sig_idx],
            "iv_exit_lo": [_opt(rows[i].get("iv_exit_lo")) for i in sig_idx],
            "gz_enter_hi": [_opt(rows[i].get("gz_enter_hi")) for i in sig_idx],
            "gz_enter_lo": [_opt(rows[i].get("gz_enter_lo")) for i in sig_idx],
            "gz_exit_hi": [_opt(rows[i].get("gz_exit_hi")) for i in sig_idx],
            "gz_exit_lo": [_opt(rows[i].get("gz_exit_lo")) for i in sig_idx],
            "lvl_q01": [_opt(rows[i].get("lvl_q01")) for i in sig_idx],
            "lvl_q10": [_opt(rows[i].get("lvl_q10")) for i in sig_idx],
            "lvl_q25": [_opt(rows[i].get("lvl_q25")) for i in sig_idx],
            "lvl_q50": [_opt(rows[i].get("lvl_q50")) for i in sig_idx],
            "lvl_q75": [_opt(rows[i].get("lvl_q75")) for i in sig_idx],
            "lvl_q90": [_opt(rows[i].get("lvl_q90")) for i in sig_idx],
            "lvl_q99": [_opt(rows[i].get("lvl_q99")) for i in sig_idx],
            "shk_q10": [_opt(rows[i].get("shk_q10")) for i in sig_idx],
            "shk_q20": [_opt(rows[i].get("shk_q20")) for i in sig_idx],
            "shk_q25": [_opt(rows[i].get("shk_q25")) for i in sig_idx],
            "shk_q75": [_opt(rows[i].get("shk_q75")) for i in sig_idx],
            "shk_q80": [_opt(rows[i].get("shk_q80")) for i in sig_idx],
            "shk_q90": [_opt(rows[i].get("shk_q90")) for i in sig_idx],
            "position": [rows[i]["position"] for i in sig_idx],  # executed at t close
            "signal_next": [rows[i]["signal_next"] for i in sig_idx],  # for t+1 trade
            "regime_mode": [rows[i].get("regime_mode") for i in sig_idx],
            "cooldown": [int(rows[i].get("cooldown") or 0) for i in sig_idx],
            "entry_spread": [_opt(rows[i].get("entry_spread")) for i in sig_idx],
            "signal_reason": [rows[i].get("signal_reason") for i in sig_idx],
            "trade_markers": trade_markers,
            "enter_q": self.enter_q,
            "exit_lo_q": self.exit_lo_q,
            "exit_hi_q": self.exit_hi_q,
            "window": self.z_window,
            "tenor_pair": self.tenor_pair,
            "side_mode": self.side_mode,
            "spread_mode": self.spread_mode,
            "signal_mode": self.signal_mode,
            "level_scale": self.level_scale,
            "budget_fade": self.budget_fade,
            "budget_momentum": self.budget_momentum,
            "budget_fade_vol_scale": self.budget_fade_vol_scale,
            "perf_start": perf[0]["d"],
            "first_open": open_row["d"],
        }

        total_pnl = perf[-1]["nav"] - base_nav
        parts = {
            "vega": perf[-1]["cum_vega"],
            "theta": perf[-1]["cum_theta"],
            "gamma": perf[-1]["cum_gamma"],
            "basis": perf[-1]["cum_basis"],
            "net_delta": perf[-1]["cum_delta"],
            "residual": perf[-1]["cum_resid"],
        }

        def _share(x: float) -> Optional[float]:
            if abs(total_pnl) < 1e-6:
                return None
            return round(100.0 * x / total_pnl, 2)

        max_vega_resid = max(self._vega_checks) if self._vega_checks else 0.0
        cfg = self.cfg
        opt_p, fut_p = cfg["product"], cfg["fut_root"]
        nl, fl = self.near_label, self.far_label
        side_label = {
            "both": "Long+Short",
            "long_only": "Long only (+ close)",
            "short_only": "Short only (+ close)",
        }[self.side_mode]
        spread_label = {
            "iv": "IV near−far",
            "iv2": "IV² near−far",
        }[self.spread_mode]
        signal_label = {
            "quantile": "rolling quantile",
            "garch_z": "GARCH-z + rolling quantile",
            "regime": "Regime v2 both (Fade+Mom)",
            "regime_momentum": "Regime v2 Momentum only",
            "regime_fade": "Regime v2 Mean-reversion only",
            "regime2": "Regime2 quantile + dual-day z stress gate",
        }[self.signal_mode]
        spread_formula = (
            "IV_near² − IV_far²"
            if self.spread_mode == "iv2"
            else "IV_near − IV_far"
        )
        if self.signal_mode == "garch_z":
            from engine.garch_signal import GARCH_WARM as _garch_warm

            _mp = rolling_min_periods(self.z_window)
            signal_desc = (
                f"expanding GARCH(1,1)-t on ΔSpread (warm {_garch_warm}d, refit every 5d) → garch_z; "
                f"then rolling quantiles of garch_z on prior {self.z_window}d "
                f"(bands from {_mp}d; enter outside {1-self.enter_q:.0%}/{self.enter_q:.0%}; "
                f"exit inside {self.exit_lo_q:.0%}–{self.exit_hi_q:.0%}; no look-ahead)"
            )
        elif is_regime_signal(self.signal_mode):
            _book = regime_book_for_signal(self.signal_mode)
            _book_note = {
                "both": "books=both (Mom then Fade)",
                "momentum": "books=momentum-only (Fade entries off)",
                "fade": "books=fade-only / mean-reversion (Mom entries off)",
            }[_book]
            signal_desc = (
                f"regime ({_book_note}) on prior {self.z_window}d: "
                f"Momentum in accel zone (q75–q90 / q10–q25) with garch_z beyond 90/10; "
                f"mom exits: cross q50 after ≥2d hold, adverse z 10/90, "
                f"space 1/99, max 12d; "
                f"Fade outside 10/90 when z≤q80/≥q20 and z_prev≤q80/≥q20; "
                f"exit at q50 + crisis fuse; cooldown 3d; "
                f"budget fade={self.budget_fade:.1%} momentum={self.budget_momentum:.1%} NAV"
                + ("; fade vol-scaled" if self.budget_fade_vol_scale else "")
            )
        elif is_regime2_signal(self.signal_mode):
            from engine.garch_signal import GARCH_WARM as _garch_warm

            _mp = rolling_min_periods(self.z_window)
            signal_desc = (
                f"regime2: identical Spread rolling quantiles "
                f"(enter outside {1-self.enter_q:.0%}/{self.enter_q:.0%}; "
                f"exit inside {self.exit_lo_q:.0%}–{self.exit_hi_q:.0%}); "
                f"stress when garch_z outside its prior-{self.z_window}d "
                f"{1-self.enter_q:.0%}/{self.enter_q:.0%} for "
                f"{self.stress_streak_days} consecutive closes → block new opens only "
                f"(exits still allowed; Reverse max hold {self.max_hold_reverse}d); "
                f"GARCH warm {_garch_warm}d, bands from {_mp}d"
            )
        else:
            signal_desc = (
                f"rolling quantiles of Spread on prior {self.z_window}d "
                f"(enter outside {1-self.enter_q:.0%}/{self.enter_q:.0%}; "
                f"exit inside {self.exit_lo_q:.0%}–{self.exit_hi_q:.0%}; no look-ahead)"
            )

        return {
            "strategy_id": cfg["strategy_id"],
            "strategy_name": (
                f"{cfg['strategy_name']} ({self.tenor_display}, {side_label}, "
                f"{spread_label}, {signal_label})"
            ),
            "description": (
                f"{cfg['index_name']} ({cfg['spot_code']}) {self.tenor_display} ATM CT-IV "
                f"spread ({spread_formula}) mean-reversion · side_mode={self.side_mode} · "
                f"spread_mode={self.spread_mode} · signal_mode={self.signal_mode}. "
                f"Signal at t−1 close ({signal_desc}); "
                f"execute open/close/resize at t close. "
                f"Dual ATM straddles ({opt_p}) + daily {fut_p} delta hedge. "
                "Reverse max hold 20d (short side). "
                "NAV/metrics start the day after first open. Costs: none (v1)."
            ),
            "legs": [
                {
                    "id": f"{opt_p}_{nl}_straddle",
                    "weight": -1.0,
                    "product": opt_p,
                    "tenor": nl,
                },
                {
                    "id": f"{opt_p}_{fl}_straddle",
                    "weight": 1.0,
                    "product": opt_p,
                    "tenor": fl,
                },
                {"id": f"{fut_p}_hedge", "weight": 0.0, "product": fut_p, "tenor": "front"},
            ],
            "params": {
                "initial_nav": self.initial_nav,
                "slippage_bp": 0.0,
                "fee_pct": 0.0,
                "cost_rate": 0.0,
                "rebalance": "daily close",
                "signal_lag": "t-1 close signal → t close execution (PnL t→t+1 on held book)",
                "price": (
                    f"option close (option_iv.mid_price←S_DQ_CLOSE) / {fut_p} close; "
                    "no settle fallback; CT ATM IV via SVI total-var ACT/365"
                ),
                "sizing": (
                    (
                        f"regime budgets: fade={self.budget_fade*100:.1f}% / "
                        f"momentum={self.budget_momentum*100:.1f}% NAV per vol-pt on {fl}; "
                        f"N_{nl}=-N_{fl}*ν{fl}/ν{nl}"
                    )
                    if is_regime_signal(self.signal_mode)
                    else (
                        f"dollar vega={VEGA_BUDGET_FRAC*100:.0f}% NAV per vol-pt on {fl}; "
                        f"N_{nl}=-N_{fl}*ν{fl}/ν{nl}"
                    )
                ),
                "ttm_targets": {nl: self.t_near, fl: self.t_far},
                "tenor_pair": self.tenor_pair,
                "side_mode": self.side_mode,
                "spread_mode": self.spread_mode,
                "signal_mode": self.signal_mode,
                "regime_book": (
                    regime_book_for_signal(self.signal_mode)
                    if is_regime_signal(self.signal_mode)
                    else None
                ),
                "level_scale": self.level_scale,
                "budget_fade": self.budget_fade,
                "budget_momentum": self.budget_momentum,
                "budget_fade_vol_scale": self.budget_fade_vol_scale,
                "spread_formula": spread_formula,
                "z_window": self.z_window,
                "enter_q": self.enter_q,
                "exit_lo_q": self.exit_lo_q,
                "exit_hi_q": self.exit_hi_q,
                "max_hold_reverse": self.max_hold_reverse,
                "stress_streak_days": self.stress_streak_days,
                "max_abs_port_vega": round(max_vega_resid, 8),
                "universe": self.universe,
                "m_opt": self.m_opt,
                "m_fut": self.m_fut,
                "first_open": open_row["d"],
                "perf_start": perf[0]["d"],
                "holdings_rows": len(self._holdings_rows),
            },
            "nav": nav_chart,
            "metrics": metrics,
            "yearly_metrics": yearly_metrics,
            "summary": {
                "start": perf[0]["d"],
                "end": perf[-1]["d"],
                "n_days": len(perf),
                "final_nav": round(perf[-1]["nav"], 2),
                "final_nav_multiple": round(perf[-1]["nav"] / base_nav, 6),
                "total_cost": 0.0,
                "total_pnl": round(total_pnl, 2),
                "first_open": open_row["d"],
            },
            "pnl_decomposition": pnl_decomposition,
            "pnl_daily_greeks": pnl_daily_greeks,
            "signal": signal,
            "pnl_contribution": {
                "total_pnl": round(total_pnl, 2),
                "vega_pct": _share(parts["vega"]),
                "theta_pct": _share(parts["theta"]),
                "gamma_pct": _share(parts["gamma"]),
                "basis_pct": _share(parts["basis"]),
                "net_delta_pct": _share(parts["net_delta"]),
                "residual_pct": _share(parts["residual"]),
            },
        }


def run_calendar_vol(
    universe: str = "300",
    initial_nav: float = 100_000_000.0,
    z_window: int = DEFAULT_Z_WINDOW,
    export_holdings: bool = False,
    holdings_path: Optional[Path] = None,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
    spread_mode: str = DEFAULT_SPREAD_MODE,
    signal_mode: str = DEFAULT_SIGNAL_MODE,
    enter_q: float = ENTER_Q,
    exit_lo_q: float = EXIT_LO_Q,
    exit_hi_q: float = EXIT_HI_Q,
    stress_streak_days: int = 2,
    max_hold_reverse: int = MAX_HOLD_REVERSE,
) -> Dict[str, Any]:
    eng = CalendarVolBacktestEngine(
        universe=universe,
        initial_nav=initial_nav,
        z_window=z_window,
        tenor_pair=tenor_pair,
        side_mode=side_mode,
        spread_mode=spread_mode,
        signal_mode=signal_mode,
        enter_q=enter_q,
        exit_lo_q=exit_lo_q,
        exit_hi_q=exit_hi_q,
        stress_streak_days=stress_streak_days,
        max_hold_reverse=max_hold_reverse,
    )
    payload = eng.run()
    if export_holdings:
        path = eng.write_holdings_excel(holdings_path)
        payload = {**payload, "holdings_export": str(path)}
    return payload


def run_equity_3_6_calendar_vol_300(
    initial_nav: float = 100_000_000.0, z_window: int = DEFAULT_Z_WINDOW
) -> Dict[str, Any]:
    return run_calendar_vol(
        "300",
        initial_nav=initial_nav,
        z_window=z_window,
        tenor_pair=DEFAULT_TENOR_PAIR,
        side_mode=DEFAULT_SIDE_MODE,
        spread_mode=DEFAULT_SPREAD_MODE,
        signal_mode=DEFAULT_SIGNAL_MODE,
    )


def run_by_strategy_id(
    strategy_id: str,
    initial_nav: float = 100_000_000.0,
    z_window: int = DEFAULT_Z_WINDOW,
    export_holdings: bool = False,
    holdings_path: Optional[Path] = None,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
    spread_mode: str = DEFAULT_SPREAD_MODE,
    signal_mode: str = DEFAULT_SIGNAL_MODE,
) -> Dict[str, Any]:
    if strategy_id not in STRATEGY_BY_ID:
        raise KeyError(strategy_id)
    return run_calendar_vol(
        STRATEGY_BY_ID[strategy_id],
        initial_nav=initial_nav,
        z_window=z_window,
        export_holdings=export_holdings,
        holdings_path=holdings_path,
        tenor_pair=tenor_pair,
        side_mode=side_mode,
        spread_mode=spread_mode,
        signal_mode=signal_mode,
    )


def export_holdings_for_universe(
    universe: str,
    initial_nav: float = 100_000_000.0,
    z_window: int = DEFAULT_Z_WINDOW,
    path: Optional[Path] = None,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
    spread_mode: str = DEFAULT_SPREAD_MODE,
    signal_mode: str = DEFAULT_SIGNAL_MODE,
) -> Path:
    eng = CalendarVolBacktestEngine(
        universe=universe,
        initial_nav=initial_nav,
        z_window=z_window,
        tenor_pair=tenor_pair,
        side_mode=side_mode,
        spread_mode=spread_mode,
        signal_mode=signal_mode,
    )
    eng.run()
    return eng.write_holdings_excel(path)
