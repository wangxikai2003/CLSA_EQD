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
_SPOT_CANDIDATES = [
    ROOT / "data" / "index_spot.parquet",
    Path("/app/data/index_spot.parquet"),
]

T_1M = 30.0 / 365.0
T_3M = 90.0 / 365.0
T_6M = 180.0 / 365.0
DEFAULT_Z_WINDOW = 252  # rolling window for quantiles / Z (trading days)
# Rolling quantile bands on Spread (prior window only — no look-ahead)
ENTER_Q = 0.90  # enter outside 10/90
EXIT_LO_Q = 0.40
EXIT_HI_Q = 0.60
MAX_HOLD_REVERSE = 20
VEGA_BUDGET_FRAC = 0.01

# Locked tenor pairs (framework): Spread = IV_near − IV_far; far leg holds vega budget.
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


def default_holdings_export_path(
    universe: str,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
) -> Path:
    cfg = UNIVERSES[universe]
    tp = normalize_tenor_pair(tenor_pair)
    sm = normalize_side_mode(side_mode)
    parts = [cfg["strategy_id"]]
    if tp != DEFAULT_TENOR_PAIR:
        parts.append(tp.lower().replace("-", ""))
    if sm != DEFAULT_SIDE_MODE:
        parts.append(sm)
    return EXPORTS_DIR / f"{'_'.join(parts)}_holdings.xlsx"


def _equity_db() -> Path:
    for p in _DB_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError("cffex_vol.sqlite not found")


def _spot_path() -> Path:
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
    s = spread.astype(float)
    hist = s.shift(1)  # exclude today from stats
    mu = hist.rolling(window, min_periods=window).mean()
    var = hist.rolling(window, min_periods=window).var(ddof=1)
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
    roll = hist.rolling(window, min_periods=window)
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
        self.initial_nav = float(initial_nav)
        self.z_window = int(z_window)
        self.enter_q = float(enter_q)
        self.exit_lo_q = float(exit_lo_q)
        self.exit_hi_q = float(exit_hi_q)
        self.product = self.cfg["product"]
        self.spot_code = self.cfg["spot_code"]
        self.m_opt = float(self.cfg["m_opt"])
        self.m_fut = float(self.cfg["m_fut"])
        self._vega_checks: List[float] = []
        self._holdings_rows: List[Dict[str, Any]] = []

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
        """ATM CT-IV near/far and Spread=near−far. Days missing either bracket are omitted."""
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
                        "spread": iv_near - iv_far,
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
    ) -> Tuple[List[OptPos], float, float]:
        """Far-leg dollar vega = 1% NAV/vol-pt; N_near = −N_far · ν_far / ν_near (daily)."""
        _, nu_far, _ = self.build_synthetic_leg(day_opts, spot, self.t_far, 1.0)
        _, nu_near, _ = self.build_synthetic_leg(day_opts, spot, self.t_near, 1.0)
        if nu_near <= 1e-12 or nu_far <= 1e-12:
            return [], 0.0, 0.0

        unit_vp_far = nu_far / 100.0
        target_cny_vp = VEGA_BUDGET_FRAC * nav
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
        if position == -1 and hold_days >= MAX_HOLD_REVERSE:
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
                self.universe, self.tenor_pair, self.side_mode
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
        bands = rolling_quantile_bands(
            ct["spread"],
            self.z_window,
            enter_q=self.enter_q,
            exit_lo_q=self.exit_lo_q,
            exit_hi_q=self.exit_hi_q,
        )
        ct = ct.join(z.rename("z"), how="left").join(bands, how="left")

        dates = sorted(set(ct.index) & set(spot.index) & set(front.index) & set(opt["trade_date"]))
        if len(dates) < self.z_window + 30:
            raise RuntimeError(
                f"Insufficient aligned history for {self.universe}/{self.tenor_pair}: "
                f"{len(dates)} days (need >{self.z_window + 30})"
            )

        opt_by_day = {td: g for td, g in opt.groupby("trade_date")}
        nav = self.initial_nav
        state = DayState()
        pending_signal = 0  # day-0: observe only; first trade earliest on day 1
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
            zv = ct.loc[d, "z"]
            zv = float(zv) if pd.notna(zv) else float("nan")
            eh = ct.loc[d, "enter_hi"]
            el = ct.loc[d, "enter_lo"]
            xh = ct.loc[d, "exit_hi"]
            xl = ct.loc[d, "exit_lo"]
            eh_f = float(eh) if pd.notna(eh) else float("nan")
            el_f = float(el) if pd.notna(el) else float("nan")
            xh_f = float(xh) if pd.notna(xh) else float("nan")
            xl_f = float(xl) if pd.notna(xl) else float("nan")
            iv_near_raw = ct.loc[d, "iv_near"]
            iv_far_raw = ct.loc[d, "iv_far"]
            iv_near_f = float(iv_near_raw) if pd.notna(iv_near_raw) else float("nan")
            iv_far_f = float(iv_far_raw) if pd.notna(iv_far_raw) else float("nan")

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
            opts: List[OptPos] = []
            n_if = 0.0
            n_near = n_far = 0.0
            hold = 0
            executed = False
            can_trade = math.isfinite(spread) and math.isfinite(iv_near_f) and math.isfinite(iv_far_f)
            if target != 0 and not day_opts.empty and can_trade:
                opts, n_near, n_far = self._size_legs(day_opts, S, target, nav)
                if opts:
                    opt_delta_pts = sum(p.n * p.delta for p in opts)
                    n_if = -hedge_scale * opt_delta_pts
                    hold = state.hold_days + 1 if state.position == target else 0
                    executed = True
                else:
                    target = 0
                    hold = 0
            elif target == 0:
                executed = state.position != 0  # flat after prior open = close trade
                hold = 0
            else:
                # signalled open but no option book / missing CT bracket → stay flat
                target = 0
                hold = 0

            if first_open_i is None and target != 0:
                first_open_i = i

            state = DayState(position=target, hold_days=hold, opts=opts, n_if=n_if)

            # 3) Emit signal for next day from post-trade state + today's close.
            # NaN spread (missing bracket) → keep position / no new entry via bands_ok=False.
            signal_next = self._decide_signal(
                target, hold, spread, eh_f, el_f, xh_f, xl_f
            )
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
                    "spread": spread if math.isfinite(spread) else float("nan"),
                    "z": zv if math.isfinite(zv) else None,
                    "enter_hi": eh_f if math.isfinite(eh_f) else None,
                    "enter_lo": el_f if math.isfinite(el_f) else None,
                    "exit_hi": xh_f if math.isfinite(xh_f) else None,
                    "exit_lo": xl_f if math.isfinite(xl_f) else None,
                    "position": target,  # executed / held after t close
                    "signal_next": signal_next,  # to be traded at t+1 close
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

        def _sos_pts(sp: Any) -> Optional[float]:
            try:
                x = float(sp)
            except (TypeError, ValueError):
                return None
            return None if not math.isfinite(x) else round(x * 100.0, 4)

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
                    "position": curr_p,
                    "prev_position": prev_p,
                }
            )

        signal = {
            "dates": [rows[i]["d"] for i in sig_idx],
            "spread": [_opt(rows[i]["spread"]) for i in sig_idx],
            "zscore": [None if rows[i]["z"] is None else round(float(rows[i]["z"]), 4) for i in sig_idx],
            "enter_hi": [_opt(rows[i]["enter_hi"]) for i in sig_idx],
            "enter_lo": [_opt(rows[i]["enter_lo"]) for i in sig_idx],
            "exit_hi": [_opt(rows[i]["exit_hi"]) for i in sig_idx],
            "exit_lo": [_opt(rows[i]["exit_lo"]) for i in sig_idx],
            "position": [rows[i]["position"] for i in sig_idx],  # executed at t close
            "signal_next": [rows[i]["signal_next"] for i in sig_idx],  # for t+1 trade
            "trade_markers": trade_markers,
            "enter_q": self.enter_q,
            "exit_lo_q": self.exit_lo_q,
            "exit_hi_q": self.exit_hi_q,
            "window": self.z_window,
            "tenor_pair": self.tenor_pair,
            "side_mode": self.side_mode,
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

        return {
            "strategy_id": cfg["strategy_id"],
            "strategy_name": (
                f"{cfg['strategy_name']} ({self.tenor_display}, {side_label})"
            ),
            "description": (
                f"{cfg['index_name']} ({cfg['spot_code']}) {self.tenor_display} ATM CT-IV "
                f"spread mean-reversion · side_mode={self.side_mode}. "
                f"Signal at t−1 close (rolling quantiles of Spread on prior {self.z_window}d; "
                f"enter outside {1-self.enter_q:.0%}/{self.enter_q:.0%}; "
                f"exit inside {self.exit_lo_q:.0%}–{self.exit_hi_q:.0%}; no look-ahead); "
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
                    f"dollar vega={VEGA_BUDGET_FRAC*100:.0f}% NAV per vol-pt on {fl}; "
                    f"N_{nl}=-N_{fl}*ν{fl}/ν{nl}"
                ),
                "ttm_targets": {nl: self.t_near, fl: self.t_far},
                "tenor_pair": self.tenor_pair,
                "side_mode": self.side_mode,
                "z_window": self.z_window,
                "enter_q": self.enter_q,
                "exit_lo_q": self.exit_lo_q,
                "exit_hi_q": self.exit_hi_q,
                "max_hold_reverse": MAX_HOLD_REVERSE,
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
) -> Dict[str, Any]:
    eng = CalendarVolBacktestEngine(
        universe=universe,
        initial_nav=initial_nav,
        z_window=z_window,
        tenor_pair=tenor_pair,
        side_mode=side_mode,
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
    )


def run_by_strategy_id(
    strategy_id: str,
    initial_nav: float = 100_000_000.0,
    z_window: int = DEFAULT_Z_WINDOW,
    export_holdings: bool = False,
    holdings_path: Optional[Path] = None,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
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
    )


def export_holdings_for_universe(
    universe: str,
    initial_nav: float = 100_000_000.0,
    z_window: int = DEFAULT_Z_WINDOW,
    path: Optional[Path] = None,
    tenor_pair: str = DEFAULT_TENOR_PAIR,
    side_mode: str = DEFAULT_SIDE_MODE,
) -> Path:
    eng = CalendarVolBacktestEngine(
        universe=universe,
        initial_nav=initial_nav,
        z_window=z_window,
        tenor_pair=tenor_pair,
        side_mode=side_mode,
    )
    eng.run()
    return eng.write_holdings_excel(path)
