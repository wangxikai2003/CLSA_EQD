"""Equity 3-6 Calendar Volatility Long Short — CSI50 / CSI300 / CSI1000."""
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

T_3M = 90.0 / 365.0
T_6M = 180.0 / 365.0
Z_ENTER = 1.5
Z_EXIT = 0.2
DEFAULT_Z_WINDOW = 252  # 1Y trading days; research override via z_window=
MAX_HOLD_REVERSE = 20
VEGA_BUDGET_FRAC = 0.01

# option product (DB) → index / futures multipliers
UNIVERSES: Dict[str, Dict[str, Any]] = {
    "50": {
        "strategy_id": "equity_3_6_calendar_vol_50",
        "strategy_name": "Equity 3-6 Calendar Volatility Long Short Strategy — 50",
        "product": "HO",
        "fut_root": "IH",
        "spot_code": "000016.SH",
        "index_name": "SSE 50",
        "m_opt": 100.0,
        "m_fut": 300.0,
    },
    "300": {
        "strategy_id": "equity_3_6_calendar_vol_300",
        "strategy_name": "Equity 3-6 Calendar Volatility Long Short Strategy — 300",
        "product": "IO",
        "fut_root": "IF",
        "spot_code": "000300.SH",
        "index_name": "CSI 300",
        "m_opt": 100.0,
        "m_fut": 300.0,
    },
    "1000": {
        "strategy_id": "equity_3_6_calendar_vol_1000",
        "strategy_name": "Equity 3-6 Calendar Volatility Long Short Strategy — 1000",
        "product": "MO",
        "fut_root": "IM",
        "spot_code": "000852.SH",
        "index_name": "CSI 1000",
        "m_opt": 100.0,
        "m_fut": 200.0,
    },
}

STRATEGY_BY_ID = {u["strategy_id"]: key for key, u in UNIVERSES.items()}


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
    ):
        if universe not in UNIVERSES:
            raise ValueError(f"unknown universe {universe}; choose {list(UNIVERSES)}")
        self.universe = universe
        self.cfg = UNIVERSES[universe]
        self.initial_nav = float(initial_nav)
        self.z_window = int(z_window)
        self.product = self.cfg["product"]
        self.spot_code = self.cfg["spot_code"]
        self.m_opt = float(self.cfg["m_opt"])
        self.m_fut = float(self.cfg["m_fut"])
        self._vega_checks: List[float] = []

    def _load(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
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
            SELECT trade_date, contract_ym, close, settle
              FROM futures_eod
             WHERE product=? AND (close > 0 OR settle > 0)
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

        fut["trade_date"] = fut["trade_date"].astype(str).str[:10]
        fut["px"] = fut["close"].where(fut["close"].notna() & (fut["close"] > 0), fut["settle"])
        front = (
            fut.sort_values(["trade_date", "contract_ym"])
            .groupby("trade_date", as_index=True)
            .first()["px"]
            .astype(float)
            .sort_index()
        )

        svi["trade_date"] = svi["trade_date"].astype(str).str[:10]
        opt["trade_date"] = opt["trade_date"].astype(str).str[:10]
        opt["cp"] = opt["cp"].astype(str).str.upper().str[:1]
        return svi, opt, spot, front

    def _daily_ct_iv(self, svi: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for td, g in svi.groupby("trade_date"):
            ttms = g["ttm"].to_numpy(float)
            watm = g["w_atm"].to_numpy(float)
            if np.any(~np.isfinite(watm)):
                jw = g["jw_v"].to_numpy(float)
                watm = np.where(np.isfinite(watm), watm, np.maximum(jw, 0.0) * ttms)
            iv3 = total_var_interp_iv(ttms, watm, T_3M)
            iv6 = total_var_interp_iv(ttms, watm, T_6M)
            if iv3 == iv3 and iv6 == iv6 and iv3 > 0 and iv6 > 0:
                rows.append({"d": td, "iv_3m": iv3, "iv_6m": iv6, "spread": iv3 - iv6})
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
        _, nu6, _ = self.build_synthetic_leg(day_opts, spot, T_6M, 1.0)
        _, nu3, _ = self.build_synthetic_leg(day_opts, spot, T_3M, 1.0)
        if nu3 <= 1e-12 or nu6 <= 1e-12:
            return [], 0.0, 0.0

        unit_vp6 = nu6 / 100.0
        target_cny_vp = VEGA_BUDGET_FRAC * nav
        n6_abs = target_cny_vp / (unit_vp6 * self.m_opt)
        n6 = direction * n6_abs
        n3 = -n6 * (nu6 / nu3)

        pos6, _, _ = self.build_synthetic_leg(day_opts, spot, T_6M, n6)
        pos3, _, _ = self.build_synthetic_leg(day_opts, spot, T_3M, n3)
        all_pos = pos3 + pos6
        if not all_pos:
            return [], 0.0, 0.0
        self._vega_checks.append(abs(sum(p.n * p.vega for p in all_pos)))
        return all_pos, n3, n6

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

    def run(self) -> Dict[str, Any]:
        svi, opt, spot, front = self._load()
        ct = self._daily_ct_iv(svi)
        z = rolling_zscore(ct["spread"], self.z_window)
        ct = ct.join(z.rename("z"), how="left")

        dates = sorted(set(ct.index) & set(spot.index) & set(front.index) & set(opt["trade_date"]))
        if len(dates) < self.z_window + 30:
            raise RuntimeError(
                f"Insufficient aligned history for {self.universe}: {len(dates)} days "
                f"(need >{self.z_window + 30})"
            )

        opt_by_day = {td: g for td, g in opt.groupby("trade_date")}
        nav = self.initial_nav
        state = DayState()
        rows: List[Dict[str, Any]] = []
        cum_vega = cum_theta = cum_gamma = cum_basis = cum_delta = cum_resid = 0.0
        first_open_i: Optional[int] = None
        hedge_scale = self.m_opt / self.m_fut  # IF/IH: 1/3; IM: 1/2

        for i, d in enumerate(dates):
            S = float(spot.loc[d])
            F = float(front.loc[d])
            day_opts = opt_by_day.get(d, pd.DataFrame())
            spread = float(ct.loc[d, "spread"])
            zv = ct.loc[d, "z"]
            zv = float(zv) if pd.notna(zv) else float("nan")

            net_delta = basis = gamma = theta = vega_pnl = 0.0
            actual = 0.0
            if i > 0 and state.opts:
                d_prev = dates[i - 1]
                S_prev = float(spot.loc[d_prev])
                F_prev = float(front.loc[d_prev])
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

            new_pos = state.position
            hold = state.hold_days
            if math.isfinite(zv):
                if state.position == 0:
                    if zv > Z_ENTER:
                        new_pos = 1
                        hold = 0
                    elif zv < -Z_ENTER:
                        new_pos = -1
                        hold = 0
                else:
                    hold += 1
                    if abs(zv) <= Z_EXIT:
                        new_pos = 0
                        hold = 0
                    elif state.position == -1 and hold >= MAX_HOLD_REVERSE:
                        new_pos = 0
                        hold = 0

            opts: List[OptPos] = []
            n_if = 0.0
            n3 = n6 = 0.0
            if new_pos != 0 and not day_opts.empty:
                opts, n3, n6 = self._size_legs(day_opts, S, new_pos, nav)
                if opts:
                    opt_delta_pts = sum(p.n * p.delta for p in opts)
                    n_if = -hedge_scale * opt_delta_pts
                else:
                    new_pos = 0
                    hold = 0

            if first_open_i is None and new_pos != 0:
                first_open_i = i

            state = DayState(position=new_pos, hold_days=hold, opts=opts, n_if=n_if)

            rows.append(
                {
                    "d": d,
                    "nav": nav,
                    "ret": (actual / (nav - actual)) if i > 0 and (nav - actual) != 0 else 0.0,
                    "spread": spread,
                    "z": zv if math.isfinite(zv) else None,
                    "position": new_pos,
                    "n3": n3,
                    "n6": n6,
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
                    "iv_3m": float(ct.loc[d, "iv_3m"]),
                    "iv_6m": float(ct.loc[d, "iv_6m"]),
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

        step = max(1, len(perf) // 800)
        nav_chart = [
            {
                "d": perf[i]["d"],
                "nav": round(perf[i]["nav"] / base_nav, 6),
                "sos": round(perf[i]["spread"] * 100.0, 4),
            }
            for i in range(0, len(perf), step)
        ]
        if nav_chart and nav_chart[-1]["d"] != perf[-1]["d"]:
            nav_chart.append(
                {
                    "d": perf[-1]["d"],
                    "nav": round(perf[-1]["nav"] / base_nav, 6),
                    "sos": round(perf[-1]["spread"] * 100.0, 4),
                }
            )

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
        signal = {
            "dates": [rows[i]["d"] for i in sig_idx],
            "spread": [round(rows[i]["spread"], 6) for i in sig_idx],
            "zscore": [None if rows[i]["z"] is None else round(float(rows[i]["z"]), 4) for i in sig_idx],
            "position": [rows[i]["position"] for i in sig_idx],
            "enter_band": Z_ENTER,
            "exit_band": Z_EXIT,
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

        return {
            "strategy_id": cfg["strategy_id"],
            "strategy_name": cfg["strategy_name"],
            "description": (
                f"{cfg['index_name']} ({cfg['spot_code']}) 3M−6M ATM CT-IV spread mean-reversion. "
                f"Z-score uses prior {self.z_window}d window only (no look-ahead). "
                f"Dual ATM straddles ({opt_p}) + daily {fut_p} delta hedge. "
                "Enter |Z|>1.5; exit |Z|≤0.2; reverse max hold 20d. "
                "NAV/metrics start the day after first open. Costs: none (v1)."
            ),
            "legs": [
                {"id": f"{opt_p}_3M_straddle", "weight": -1.0, "product": opt_p, "tenor": "3M"},
                {"id": f"{opt_p}_6M_straddle", "weight": 1.0, "product": opt_p, "tenor": "6M"},
                {"id": f"{fut_p}_hedge", "weight": 0.0, "product": fut_p, "tenor": "front"},
            ],
            "params": {
                "initial_nav": self.initial_nav,
                "slippage_bp": 0.0,
                "fee_pct": 0.0,
                "cost_rate": 0.0,
                "rebalance": "daily close",
                "signal_lag": "Z at t close → position from t close (PnL t→t+1)",
                "price": f"option mid / {fut_p} close; CT ATM IV via SVI total-var ACT/365",
                "sizing": f"dollar vega={VEGA_BUDGET_FRAC*100:.0f}% NAV per vol-pt on 6M; N_3M=-N_6M*ν6/ν3",
                "ttm_targets": {"3M": T_3M, "6M": T_6M},
                "z_window": self.z_window,
                "z_enter": Z_ENTER,
                "z_exit": Z_EXIT,
                "max_hold_reverse": MAX_HOLD_REVERSE,
                "max_abs_port_vega": round(max_vega_resid, 8),
                "universe": self.universe,
                "m_opt": self.m_opt,
                "m_fut": self.m_fut,
                "first_open": open_row["d"],
                "perf_start": perf[0]["d"],
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
) -> Dict[str, Any]:
    return CalendarVolBacktestEngine(
        universe=universe, initial_nav=initial_nav, z_window=z_window
    ).run()


def run_equity_3_6_calendar_vol_300(
    initial_nav: float = 100_000_000.0, z_window: int = DEFAULT_Z_WINDOW
) -> Dict[str, Any]:
    return run_calendar_vol("300", initial_nav=initial_nav, z_window=z_window)


def run_by_strategy_id(
    strategy_id: str,
    initial_nav: float = 100_000_000.0,
    z_window: int = DEFAULT_Z_WINDOW,
) -> Dict[str, Any]:
    if strategy_id not in STRATEGY_BY_ID:
        raise KeyError(strategy_id)
    return run_calendar_vol(STRATEGY_BY_ID[strategy_id], initial_nav=initial_nav, z_window=z_window)
