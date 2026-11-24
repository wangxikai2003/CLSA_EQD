"""Cross Calendar Carry — listed overlapping vintages (Vol-RV style).

Holds equal *index-point* lots on four CFFEX equity-index futures:

  +IM_short −IM_long −IH_short +IH_long

so overnight PnL ≈ a · ΔSoS with
  SoS = (IM_s − IM_l) − (IH_s − IH_l).

Each trading day may open a new vintage sized to ``daily_budget_frac`` of NAV.
Vintages exit ``n_roll`` sessions before the short-month expiry (third Friday).
No constant-maturity interpolation — only listed ``contract_ym`` closes.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

STRATEGY_ID = "cross_calendar_carry"
ANN = 252
DEFAULT_INITIAL_NAV = 100_000_000.0
DEFAULT_SLIPPAGE = 2e-4
DEFAULT_FEE = 0.0023 / 100.0
SHORT_MIN_DTE = 5
N_ROLL = 2
# Futures notional turnover is far costlier than option-premium budgets in Vol RV;
# open every N sessions so vintages still overlap but costs do not dominate.
OPEN_EVERY_N = 5
DAILY_BUDGET_FRAC = 1.0 / 10.0
LONG_MIN_GAP_DAYS = 40
QUARTER_MONTHS = (3, 6, 9, 12)
# DB product → public Wind root
_DB_PRODUCT = {"IH": "HO", "IM": "MO"}
_ENGINE_VERSION = "listed-roll-v1"

ROOT = Path(__file__).resolve().parents[2]
_DB_CANDIDATES = [
    ROOT / "data" / "cffex_vol.sqlite",
    Path("/app/data/cffex_vol.sqlite"),
]


def _equity_db() -> Path:
    for p in _DB_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError("cffex_vol.sqlite not found")


def third_friday(ym: str) -> pd.Timestamp:
    y = 2000 + int(str(ym)[:2])
    m = int(str(ym)[2:])
    days = pd.date_range(f"{y}-{m:02d}-01", periods=31, freq="D")
    days = days[days.month == m]
    fridays = days[days.weekday == 4]
    return fridays[2] if len(fridays) >= 3 else days[-1]


def trading_days_to_expiry(dates: Sequence[str], today: str, expiry: str) -> int:
    return sum(1 for d in dates if today < d <= expiry)


@dataclass
class FutLeg:
    product: str
    wind_code: str
    contract_ym: str
    sign: float
    lots: float
    close: float


@dataclass
class Vintage:
    vintage_id: str
    open_date: str
    exit_date: str
    short_ym: str
    long_ym: str
    legs: List[FutLeg] = field(default_factory=list)


def _load_futures() -> Dict[str, pd.DataFrame]:
    """Return {IH|IM: DataFrame with trade_date, wind_code, contract_ym, close, expiry}."""
    db = _equity_db()
    con = sqlite3.connect(str(db))
    out: Dict[str, pd.DataFrame] = {}
    for pub, db_prod in _DB_PRODUCT.items():
        df = pd.read_sql(
            """
            SELECT trade_date, wind_code, contract_ym, close
              FROM futures_eod
             WHERE product=? AND close IS NOT NULL AND close > 0
             ORDER BY trade_date, contract_ym
            """,
            con,
            params=(db_prod,),
        )
        if df.empty:
            con.close()
            raise RuntimeError(f"No futures_eod rows for {db_prod} ({pub})")
        df["trade_date"] = df["trade_date"].astype(str).str[:10]
        df["contract_ym"] = df["contract_ym"].astype(str).str[:4]
        df["expiry"] = df["contract_ym"].map(lambda ym: third_friday(ym).strftime("%Y-%m-%d"))
        out[pub] = df
    con.close()
    return out


def _day_map(fut: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, pd.DataFrame]]:
    return {pub: {td: g.copy() for td, g in df.groupby("trade_date")} for pub, df in fut.items()}


def _common_dates(fut: Dict[str, pd.DataFrame]) -> List[str]:
    sets = [set(df["trade_date"].unique()) for df in fut.values()]
    return sorted(set.intersection(*sets)) if sets else []


def _pick_short_long_ym(
    im_day: pd.DataFrame,
    ih_day: pd.DataFrame,
    today: str,
    *,
    short_min_dte: int,
    long_min_gap_days: int,
    quarter_months: Sequence[int],
) -> Optional[Tuple[str, str]]:
    """Pick shared short/long contract months present on both IM and IH curves."""
    im_yms = set(im_day["contract_ym"].astype(str))
    ih_yms = set(ih_day["contract_ym"].astype(str))
    common = sorted(im_yms & ih_yms, key=lambda ym: third_friday(ym))
    if len(common) < 2:
        return None
    candidates: List[Tuple[str, str, int]] = []  # ym, expiry, cal_dte
    for ym in common:
        exp = third_friday(ym).strftime("%Y-%m-%d")
        if exp <= today:
            continue
        dte = (pd.Timestamp(exp) - pd.Timestamp(today)).days
        if dte < int(short_min_dte):
            continue
        candidates.append((ym, exp, int(dte)))
    if len(candidates) < 2:
        return None
    short_ym, short_exp, short_dte = candidates[0]
    # Prefer a quarterly long with enough gap past the short.
    long_pick: Optional[str] = None
    for ym, exp, dte in candidates[1:]:
        if dte < short_dte + int(long_min_gap_days):
            continue
        month = int(str(ym)[2:])
        if month in set(int(x) for x in quarter_months):
            long_pick = ym
            break
    if long_pick is None:
        for ym, exp, dte in candidates[1:]:
            if dte >= short_dte + int(long_min_gap_days):
                long_pick = ym
                break
    if long_pick is None:
        long_pick = candidates[1][0]
    if long_pick == short_ym:
        return None
    return short_ym, long_pick


def _row_for_ym(day: pd.DataFrame, ym: str) -> Optional[pd.Series]:
    hit = day[day["contract_ym"].astype(str) == str(ym)]
    if hit.empty:
        return None
    return hit.iloc[0]


def _exit_date(dates: Sequence[str], today_i: int, short_expiry: str, n_roll: int) -> str:
    candidates = [d for d in dates[today_i:] if d < short_expiry]
    if not candidates:
        return dates[min(today_i, len(dates) - 1)]
    for d in candidates:
        if trading_days_to_expiry(dates, d, short_expiry) <= int(n_roll):
            return d
    return candidates[-1]


def _lookup_close(
    day_maps: Dict[str, Dict[str, pd.DataFrame]], d: str, product: str, ym: str
) -> Optional[float]:
    day = day_maps.get(product, {}).get(d)
    if day is None or day.empty:
        return None
    r = _row_for_ym(day, ym)
    if r is None:
        return None
    px = float(r["close"])
    return px if math.isfinite(px) and px > 0 else None


def _package_sos(legs: Sequence[FutLeg]) -> float:
    try:
        im_s = next(leg.close for leg in legs if leg.product == "IM" and leg.sign > 0)
        im_l = next(leg.close for leg in legs if leg.product == "IM" and leg.sign < 0)
        ih_s = next(leg.close for leg in legs if leg.product == "IH" and leg.sign < 0)
        ih_l = next(leg.close for leg in legs if leg.product == "IH" and leg.sign > 0)
    except StopIteration:
        return float("nan")
    return (im_s - im_l) - (ih_s - ih_l)


class CrossCalendarCarryEngine:
    def __init__(
        self,
        *,
        initial_nav: float = DEFAULT_INITIAL_NAV,
        slippage: float = DEFAULT_SLIPPAGE,
        fee: float = DEFAULT_FEE,
        short_min_dte: int = SHORT_MIN_DTE,
        n_roll: int = N_ROLL,
        open_every_n: int = OPEN_EVERY_N,
        daily_budget_frac: float = DAILY_BUDGET_FRAC,
        long_min_gap_days: int = LONG_MIN_GAP_DAYS,
        quarter_months: Sequence[int] = QUARTER_MONTHS,
    ) -> None:
        self.initial_nav = float(initial_nav)
        self.slippage = float(slippage)
        self.fee = float(fee)
        self.cost_rate = self.slippage + self.fee
        self.short_min_dte = max(1, int(short_min_dte))
        self.n_roll = max(0, int(n_roll))
        self.open_every_n = max(1, int(open_every_n))
        self.daily_budget_frac = float(daily_budget_frac)
        self.long_min_gap_days = max(1, int(long_min_gap_days))
        self.quarter_months = tuple(int(x) for x in quarter_months)

    def _open_vintage(
        self,
        d: str,
        dates: Sequence[str],
        today_i: int,
        im_day: pd.DataFrame,
        ih_day: pd.DataFrame,
        nav: float,
        vintage_id: str,
    ) -> Optional[Tuple[Vintage, float]]:
        pair = _pick_short_long_ym(
            im_day,
            ih_day,
            d,
            short_min_dte=self.short_min_dte,
            long_min_gap_days=self.long_min_gap_days,
            quarter_months=self.quarter_months,
        )
        if pair is None:
            return None
        short_ym, long_ym = pair
        specs = [
            ("IM", short_ym, +1.0, im_day),
            ("IM", long_ym, -1.0, im_day),
            ("IH", short_ym, -1.0, ih_day),
            ("IH", long_ym, +1.0, ih_day),
        ]
        rows: List[Tuple[str, str, float, str, float]] = []
        gross = 0.0
        for prod, ym, sign, day in specs:
            r = _row_for_ym(day, ym)
            if r is None:
                return None
            px = float(r["close"])
            if not (math.isfinite(px) and px > 0):
                return None
            gross += px
            rows.append((prod, ym, sign, str(r["wind_code"]), px))
        budget = max(self.daily_budget_frac * nav, 0.0)
        if gross <= 0 or budget <= 0:
            return None
        a = budget / gross
        legs = [
            FutLeg(product=prod, wind_code=code, contract_ym=ym, sign=sign, lots=a, close=px)
            for prod, ym, sign, code, px in rows
        ]
        short_exp = third_friday(short_ym).strftime("%Y-%m-%d")
        exit_d = _exit_date(dates, today_i, short_exp, self.n_roll)
        vin = Vintage(
            vintage_id=vintage_id,
            open_date=d,
            exit_date=exit_d,
            short_ym=short_ym,
            long_ym=long_ym,
            legs=legs,
        )
        open_notional = sum(leg.lots * leg.close for leg in legs)
        open_cost = open_notional * self.cost_rate
        return vin, open_cost

    def run(self) -> Dict[str, Any]:
        fut = _load_futures()
        day_maps = _day_map(fut)
        dates = _common_dates(fut)
        if len(dates) < 60:
            raise RuntimeError(f"Insufficient IH/IM overlap: {len(dates)} days")

        nav = float(self.initial_nav)
        book: List[Vintage] = []
        rows: List[Dict[str, Any]] = []
        first_open_i: Optional[int] = None
        last_open_i = -10**9
        n_opened = 0
        n_closed = 0
        sos_open_sum = 0.0
        sos_close_sum = 0.0
        sos_pair_n = 0

        # Seed day 0 flat.
        rows.append(
            {
                "d": dates[0],
                "nav": nav,
                "ret": 0.0,
                "turnover": 0.0,
                "cost": 0.0,
                "sos": float("nan"),
                "n_vintages": 0,
                "executed": False,
            }
        )

        for i in range(1, len(dates)):
            d = dates[i]
            cost = 0.0
            turnover = 0.0
            pnl = 0.0

            # 1) Mark open vintages overnight on listed closes.
            alive: List[Vintage] = []
            for v in book:
                marked: List[FutLeg] = []
                ok = True
                for leg in v.legs:
                    px = _lookup_close(day_maps, d, leg.product, leg.contract_ym)
                    if px is None:
                        # Contract missing (rare) — flatten at previous close, no overnight pnl.
                        ok = False
                        break
                    pnl += leg.lots * leg.sign * (px - leg.close)
                    marked.append(
                        FutLeg(
                            product=leg.product,
                            wind_code=leg.wind_code,
                            contract_ym=leg.contract_ym,
                            sign=leg.sign,
                            lots=leg.lots,
                            close=px,
                        )
                    )
                if not ok:
                    # Force close at last marks.
                    close_notional = sum(leg.lots * leg.close for leg in v.legs)
                    turnover += close_notional
                    cost += close_notional * self.cost_rate
                    n_closed += 1
                    continue
                v.legs = marked
                if d >= v.exit_date:
                    close_notional = sum(leg.lots * leg.close for leg in v.legs)
                    turnover += close_notional
                    cost += close_notional * self.cost_rate
                    sos_c = _package_sos(v.legs)
                    if math.isfinite(sos_c):
                        # Pair with open SoS if we stored it on legs at open — recompute from open_date.
                        sos_o = float("nan")
                        # Approximate: SoS at open from first marks stored? Use vintage open snapshot via re-lookup.
                        im_s = _lookup_close(day_maps, v.open_date, "IM", v.short_ym)
                        im_l = _lookup_close(day_maps, v.open_date, "IM", v.long_ym)
                        ih_s = _lookup_close(day_maps, v.open_date, "IH", v.short_ym)
                        ih_l = _lookup_close(day_maps, v.open_date, "IH", v.long_ym)
                        if None not in (im_s, im_l, ih_s, ih_l):
                            sos_o = (im_s - im_l) - (ih_s - ih_l)  # type: ignore[operator]
                        if math.isfinite(sos_o):
                            sos_open_sum += float(sos_o)
                            sos_close_sum += float(sos_c)
                            sos_pair_n += 1
                    n_closed += 1
                    continue
                alive.append(v)
            book = alive
            nav_pre = nav + pnl

            # 2) Open overlapping vintage every open_every_n sessions (Vol-RV style book).
            im_day = day_maps["IM"].get(d)
            ih_day = day_maps["IH"].get(d)
            opened = False
            sos_today = float("nan")
            can_open = (i - last_open_i) >= self.open_every_n
            if (
                can_open
                and im_day is not None
                and ih_day is not None
                and not im_day.empty
                and not ih_day.empty
            ):
                opened_pack = self._open_vintage(
                    d, dates, i, im_day, ih_day, nav_pre, vintage_id=f"xcal-{d}"
                )
                if opened_pack is not None:
                    vin, open_cost = opened_pack
                    open_turn = sum(leg.lots * leg.close for leg in vin.legs)
                    turnover += open_turn
                    cost += open_cost
                    book.append(vin)
                    n_opened += 1
                    opened = True
                    last_open_i = i
                    if first_open_i is None:
                        first_open_i = i
                    sos_today = _package_sos(vin.legs)

            if not math.isfinite(sos_today) and book:
                sos_today = _package_sos(book[-1].legs)

            nav = nav_pre - cost
            prev_nav = float(rows[-1]["nav"])
            ret = (nav / prev_nav - 1.0) if prev_nav else 0.0
            rows.append(
                {
                    "d": d,
                    "nav": nav,
                    "ret": ret,
                    "turnover": turnover,
                    "cost": cost,
                    "sos": sos_today if math.isfinite(sos_today) else None,
                    "n_vintages": len(book),
                    "executed": opened or cost > 0,
                }
            )

        return self._pack(
            rows,
            first_open_i=first_open_i,
            n_opened=n_opened,
            n_closed=n_closed,
            sos_open_sum=sos_open_sum,
            sos_close_sum=sos_close_sum,
            sos_pair_n=sos_pair_n,
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
            }
        total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
        years = n / float(ANN)
        ann_ret = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else float("nan")
        ann_vol = float(r.std(ddof=1) * math.sqrt(ANN)) if n > 1 else float("nan")
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

    def _pack(
        self,
        rows: List[Dict[str, Any]],
        *,
        first_open_i: Optional[int],
        n_opened: int,
        n_closed: int,
        sos_open_sum: float,
        sos_close_sum: float,
        sos_pair_n: int,
    ) -> Dict[str, Any]:
        if first_open_i is None or first_open_i + 1 >= len(rows):
            raise RuntimeError("No Cross Calendar Carry vintage opened")
        start_i = first_open_i
        base_nav = float(rows[start_i]["nav"])
        if base_nav <= 0:
            base_nav = float(self.initial_nav)
        perf = rows[start_i:]
        nav_s = pd.Series({r["d"]: float(r["nav"]) for r in perf})
        ret_s = pd.Series({r["d"]: float(r["ret"]) for r in perf})
        # Rebase returns at performance start.
        ret_s.iloc[0] = float("nan")
        nav_mult = nav_s / base_nav

        metrics: Dict[str, Any] = {}
        for name, yrs in (("1y", 1.0), ("3y", 3.0), ("5y", 5.0), ("max", None)):
            nav_w, ret_w = self._slice_from_end(nav_mult, ret_s, yrs)
            metrics[name] = self._window_metrics(nav_w, ret_w)
            metrics[name]["start"] = str(nav_w.index[0]) if len(nav_w) else None
            metrics[name]["end"] = str(nav_w.index[-1]) if len(nav_w) else None

        yearly: List[Dict[str, Any]] = []
        idx_y = pd.to_datetime(nav_mult.index)
        for year in sorted({int(y) for y in idx_y.year}):
            mask = idx_y.year == year
            nav_y = nav_mult.loc[mask]
            ret_y = ret_s.loc[mask]
            if len(nav_y) < 2:
                continue
            base_y = float(nav_y.iloc[0])
            if base_y <= 0:
                continue
            ym = self._window_metrics(nav_y / base_y, ret_y)
            ym["year"] = int(year)
            ym["start"] = str(pd.Timestamp(nav_y.index[0]).date())
            ym["end"] = str(pd.Timestamp(nav_y.index[-1]).date())
            yearly.append(ym)

        step = max(1, len(perf) // 800)
        nav_idx = list(range(0, len(perf), step))
        if not nav_idx or nav_idx[-1] != len(perf) - 1:
            nav_idx.append(len(perf) - 1)
        nav_chart = [
            {
                "d": perf[j]["d"],
                "nav": round(float(perf[j]["nav"]) / base_nav, 6),
                "sos": None
                if perf[j]["sos"] is None or not math.isfinite(float(perf[j]["sos"]))
                else round(float(perf[j]["sos"]), 4),
            }
            for j in nav_idx
        ]
        nav_daily = [
            {"d": r["d"], "nav": round(float(r["nav"]) / base_nav, 6)} for r in perf
        ]

        mean_sos_capture = None
        if sos_pair_n > 0:
            # Positive ⇒ listed SoS rose from open→exit on average (carry harvested in level terms).
            mean_sos_capture = round((sos_close_sum - sos_open_sum) / sos_pair_n, 6)

        return {
            "strategy_id": STRATEGY_ID,
            "strategy_name": "Cross Calendar Carry",
            "description": (
                "Listed overlapping vintages: +IM_short −IM_long −IH_short +IH_long "
                "(SoS=(IM_s−IM_l)−(IH_s−IH_l)). Equal index-point lots; open daily; "
                f"exit {self.n_roll} sessions before short expiry; "
                f"open every {self.open_every_n} sessions. No constant-maturity lerp."
            ),
            "legs": [
                {"id": "IM_short", "weight": 1.0, "product": "IM", "tenor": "near"},
                {"id": "IM_long", "weight": -1.0, "product": "IM", "tenor": "deferred"},
                {"id": "IH_short", "weight": -1.0, "product": "IH", "tenor": "near"},
                {"id": "IH_long", "weight": 1.0, "product": "IH", "tenor": "deferred"},
            ],
            "params": {
                "initial_nav": self.initial_nav,
                "slippage_bp": self.slippage * 1e4,
                "fee_pct": self.fee * 100.0,
                "cost_rate": self.cost_rate,
                "rebalance": (
                    f"overlapping vintages every {self.open_every_n} sessions; "
                    "exit n_roll before short expiry"
                ),
                "signal_lag": "open at t close; mark next days",
                "price": "listed futures close (no settle / no CM lerp)",
                "sizing": f"point lots a=budget/ΣS; budget={self.daily_budget_frac:.4g}·NAV per vintage",
                "short_min_dte": self.short_min_dte,
                "n_roll": self.n_roll,
                "open_every_n": self.open_every_n,
                "daily_budget_frac": self.daily_budget_frac,
                "long_min_gap_days": self.long_min_gap_days,
                "engine_version": _ENGINE_VERSION,
                "tenor_pair": _ENGINE_VERSION,
                "side_mode": "n/a",
                "spread_mode": "n/a",
                "signal_mode": f"{self.slippage:.8g}|{self.fee:.8g}|{_ENGINE_VERSION}",
                "n_opened": n_opened,
                "n_closed": n_closed,
                "mean_sos_capture": mean_sos_capture,
                "sos_pairs": sos_pair_n,
            },
            "nav": nav_chart,
            "nav_daily": nav_daily,
            "metrics": metrics,
            "yearly_metrics": yearly,
            "summary": {
                "start": perf[0]["d"],
                "end": perf[-1]["d"],
                "n_days": len(perf),
                "final_nav": round(float(perf[-1]["nav"]), 2),
                "final_nav_multiple": round(float(perf[-1]["nav"]) / base_nav, 6),
                "total_cost": round(sum(float(r["cost"]) for r in perf), 2),
                "first_open": rows[first_open_i]["d"] if first_open_i is not None else None,
                "mean_sos_capture": mean_sos_capture,
                "n_opened": n_opened,
            },
        }


def run_cross_calendar_carry(
    initial_nav: float = DEFAULT_INITIAL_NAV,
    slippage: float = DEFAULT_SLIPPAGE,
    fee: float = DEFAULT_FEE,
    **kwargs: Any,
) -> Dict[str, Any]:
    eng = CrossCalendarCarryEngine(
        initial_nav=initial_nav,
        slippage=slippage,
        fee=fee,
        **{k: v for k, v in kwargs.items() if k in {
            "short_min_dte",
            "n_roll",
            "open_every_n",
            "daily_budget_frac",
            "long_min_gap_days",
            "quarter_months",
        }},
    )
    return eng.run()
