"""
Standard SPX ATM IV pipeline (refactored).

Fixes vs compute_spx_iv_from_prices.py:
  1) Separate SPX (AM) vs SPXW (PM); never mix same-calendar-day AM/PM
  2) Prefer mid=(bid+ask)/2 when available; else flag close-only (current dump)
  3) Implied forward F from put-call parity near ATM; Black-76 thereafter
  4) T = calendar ACT/365 (optional AM/PM hour fraction)
  5) Strike interp to K_target (spot or forward moneyness) in IV space, OTM wing
  6) Constant-maturity via linear total-variance; no sigma-linear; no silent 0.0 IV

Data note: archive/spx_options.parquet has close only (no bid/ask) and
days_to_strike <= 182 → 1Y tenor cannot be filled without a longer dump.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
DEFAULT_PARQUET = Path(
    os.environ.get(
        "SPX_OPTIONS_PARQUET",
        str(ROOT.parent / "vol_trading" / "archive" / "spx_options.parquet"),
    )
)
_pipe = Path(os.environ.get("VOL_TRADING_PIPELINE", str(ROOT.parent / "vol_trading" / "pipeline")))
if _pipe.exists():
    sys.path.insert(0, str(_pipe))
from vol_lib import implied_vol  # noqa: E402

RootPref = Literal["SPXW", "SPX", "auto"]
AtmMode = Literal["spot", "forward"]  # BBG 100%MNY_DF ≈ spot; clean Black uses forward


# ---------------------------------------------------------------------------
# 1. Filtering
# ---------------------------------------------------------------------------

def parse_root(ticker: str) -> Optional[str]:
    s = str(ticker)
    if "SPXW" in s:
        return "SPXW"
    if "SPX" in s:
        return "SPX"
    return None


def settlement_hours_et(root: str) -> float:
    """Hours from midnight ET to settlement print used for T fraction."""
    # AM SPX: ~09:30 open settlement; PM SPXW: ~16:00 close
    return 9.5 if root == "SPX" else 16.0


def mid_price(row) -> tuple[Optional[float], str]:
    """Return (price, source). Prefer mid; never invent bid/ask."""
    bid = getattr(row, "bid", None) if not isinstance(row, dict) else row.get("bid")
    ask = getattr(row, "ask", None) if not isinstance(row, dict) else row.get("ask")
    close = getattr(row, "close", None) if not isinstance(row, dict) else row.get("close")

    def _f(x):
        try:
            v = float(x)
            return v if np.isfinite(v) else None
        except Exception:
            return None

    b, a, c = _f(bid), _f(ask), _f(close)
    if b is not None and a is not None and b > 0 and a >= b:
        mid = 0.5 * (b + a)
        rel_spread = (a - b) / mid if mid > 0 else np.inf
        if rel_spread > 0.30:
            return None, "wide_spread"
        return mid, "mid"
    if c is not None and c > 0:
        return c, "close"
    return None, "missing"


def passes_microstructure(
    price: float,
    F: float,
    K: float,
    T: float,
    r: float,
    cp: str,
    *,
    min_dte_days: float = 5.0,
    dte_days: float,
) -> bool:
    if dte_days < min_dte_days or T <= 0 or price <= 0 or F <= 0 or K <= 0:
        return False
    df = math.exp(-r * T)
    intrinsic = df * (max(F - K, 0.0) if cp == "C" else max(K - F, 0.0))
    # soft lower bound (noise / close non-synch can slightly breach)
    if price < intrinsic * 0.999:
        return False
    return True


# ---------------------------------------------------------------------------
# 2. Forward + rate
# ---------------------------------------------------------------------------

@dataclass
class ForwardResult:
    F: float
    K_star: float
    r: float
    method: str
    n_pcp_pairs: int = 0


def rate_at_T(T: float, curve: Optional[pd.DataFrame], flat_r: float) -> float:
    """
    curve columns: tenor_years, rate (continuously compounded).
    Linear interp in maturity; flat extrapolation.
    """
    if curve is None or curve.empty:
        return float(flat_r)
    c = curve.sort_values("tenor_years")
    xs = c["tenor_years"].to_numpy(dtype=float)
    ys = c["rate"].to_numpy(dtype=float)
    if T <= xs[0]:
        return float(ys[0])
    if T >= xs[-1]:
        return float(ys[-1])
    return float(np.interp(T, xs, ys))


def implied_forward_pcp(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    T: float,
    r: float,
    spot: float,
    top_n: int = 5,
) -> ForwardResult:
    """
    Robust PCP: median of F_k over Top-N near-ATM strikes with smallest |C-P|.
    Prefer volume>0 pairs when a volume column is present.
    """
    if calls.empty or puts.empty:
        return ForwardResult(
            F=float(spot), K_star=float(spot), r=r, method="spot_fallback", n_pcp_pairs=0
        )

    m = calls.merge(puts, on="strike_price", suffixes=("_c", "_p"))
    if m.empty:
        return ForwardResult(
            F=float(spot), K_star=float(spot), r=r, method="spot_fallback", n_pcp_pairs=0
        )

    band = 0.05 * spot
    m = m[(m["strike_price"] - spot).abs() <= band].copy()
    if m.empty:
        return ForwardResult(
            F=float(spot), K_star=float(spot), r=r, method="spot_fallback", n_pcp_pairs=0
        )

    # optional volume filter (close-only dumps may lack it)
    vol_cols = [c for c in m.columns if "volume" in c.lower()]
    if vol_cols:
        v = pd.to_numeric(m[vol_cols[0]], errors="coerce").fillna(0)
        traded = m[v > 0]
        if len(traded) >= 3:
            m = traded

    m["diff"] = (m["price_c"] - m["price_p"]).abs()
    m = m.sort_values("diff").head(int(top_n))
    Fs, Ks = [], []
    for row in m.itertuples(index=False):
        K = float(row.strike_price)
        Fk = K + math.exp(r * T) * (float(row.price_c) - float(row.price_p))
        if np.isfinite(Fk) and Fk > 0:
            Fs.append(Fk)
            Ks.append(K)
    if not Fs:
        return ForwardResult(
            F=float(spot), K_star=float(spot), r=r, method="spot_fallback", n_pcp_pairs=0
        )
    F = float(np.median(Fs))
    K_star = float(np.median(Ks))
    return ForwardResult(F=F, K_star=K_star, r=r, method="pcp_median", n_pcp_pairs=int(len(Fs)))


# ---------------------------------------------------------------------------
# 3. Time to expiry
# ---------------------------------------------------------------------------

def year_fraction_calendar(
    as_of: pd.Timestamp,
    expiry: pd.Timestamp,
    root: str,
    *,
    use_settlement_hour: bool = True,
) -> float:
    """ACT/365 with optional AM/PM hour fraction (never bus/252)."""
    as_of = pd.Timestamp(as_of).normalize()
    expiry = pd.Timestamp(expiry).normalize()
    days = (expiry - as_of).days
    if days < 0:
        return 0.0
    if not use_settlement_hour:
        return days / 365.0
    # assume quote ~ 16:00 ET close; settle at root-specific hour on expiry day
    hours_left_on_expiry = settlement_hours_et(root)
    # from close as_of to midnight expiry: days calendar, then + settle hours
    # approx: (days - 1) full days after as_of close + (24 - 16) to midnight
    #        + hours_left_on_expiry on expiry, if days >= 1
    if days == 0:
        # same-day: remaining hours to settle from 16:00 — usually none / tiny
        return max(hours_left_on_expiry - 16.0, 0.0) / (365.0 * 24.0)
    hours = (days - 1) * 24.0 + (24.0 - 16.0) + hours_left_on_expiry
    return hours / (365.0 * 24.0)


# ---------------------------------------------------------------------------
# 4. Single-expiry ATM
# ---------------------------------------------------------------------------

def _cp(contract_type) -> Optional[str]:
    t = str(contract_type).lower()
    if t.startswith("c"):
        return "C"
    if t.startswith("p"):
        return "P"
    return None


def invert_slice_ivs(
    g: pd.DataFrame,
    F: float,
    T: float,
    r: float,
) -> pd.DataFrame:
    rows = []
    for row in g.itertuples(index=False):
        cp = _cp(row.contract_type)
        if cp is None:
            continue
        px, src = mid_price(row)
        if px is None:
            continue
        dte = float(row.days_to_strike) if hasattr(row, "days_to_strike") else T * 365.0
        if not passes_microstructure(px, F, float(row.strike_price), T, r, cp, dte_days=dte):
            continue
        sig = implied_vol(px, F, float(row.strike_price), T, r, cp)
        if sig is None or not np.isfinite(sig) or not (1e-4 < sig < 5.0):
            continue  # NaN / skip — never coerce to 0.0
        rows.append(
            {
                "K": float(row.strike_price),
                "cp": cp,
                "iv": float(sig),
                "price": px,
                "price_src": src,
                "otm": (cp == "C" and row.strike_price >= F) or (cp == "P" and row.strike_price <= F),
            }
        )
    return pd.DataFrame(rows)


def atm_iv_from_slice(
    ivs: pd.DataFrame,
    F: float,
    spot: float,
    mode: AtmMode = "spot",
) -> Optional[float]:
    """
    Interpolate IV to K_target in IV-space using OTM quotes when possible.
    mode='spot' → K=S0 (BBG 100%MNY_DF style); 'forward' → K=F.
    """
    if ivs.empty:
        return None
    K_tgt = float(spot) if mode == "spot" else float(F)
    use = ivs[ivs["otm"]] if ivs["otm"].any() else ivs
    # one IV per strike: prefer OTM already filtered
    by_k = use.groupby("K", as_index=False)["iv"].median().sort_values("K")
    ks = by_k["K"].to_numpy(dtype=float)
    sigs = by_k["iv"].to_numpy(dtype=float)
    if len(ks) == 0:
        return None
    lo, hi = float(ks[0]), float(ks[-1])
    tol = 0.005
    if K_tgt < lo * (1.0 - tol) or K_tgt > hi * (1.0 + tol):
        return None  # no flat extrapolation
    if len(ks) == 1:
        return float(sigs[0])
    K_clip = min(max(K_tgt, lo), hi)
    return float(np.interp(K_clip, ks, sigs))


# ---------------------------------------------------------------------------
# 5. Constant maturity (total variance)
# ---------------------------------------------------------------------------

def total_var_interp(tenors: np.ndarray, ivs: np.ndarray, t_tgt: float) -> Optional[float]:
    """
    σ(T*) from linear interp of w=σ²T; returns σ not w.
    No extrapolation beyond listed tenors (returns None) — caller may allow mild.
    """
    mask = np.isfinite(tenors) & np.isfinite(ivs) & (tenors > 0) & (ivs > 0)
    tenors, ivs = tenors[mask], ivs[mask]
    if len(tenors) == 0:
        return None
    order = np.argsort(tenors)
    tenors, ivs = tenors[order], ivs[order]
    w = ivs * ivs * tenors
    if t_tgt < tenors[0] or t_tgt > tenors[-1]:
        return None  # refuse hard extrapolate (explains empty 1Y if max DTE~182)
    i = int(np.searchsorted(tenors, t_tgt))
    if i == 0:
        w_tgt = float(w[0])
    else:
        t0, t1 = float(tenors[i - 1]), float(tenors[i])
        w0, w1 = float(w[i - 1]), float(w[i])
        a = 0.0 if t1 == t0 else (t_tgt - t0) / (t1 - t0)
        w_tgt = w0 + a * (w1 - w0)
    if w_tgt < 0 or t_tgt <= 0:
        return None
    return math.sqrt(w_tgt / t_tgt)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def prepare_chain(df: pd.DataFrame, root_pref: RootPref) -> pd.DataFrame:
    out = df.copy()
    out["root"] = out["ticker"].map(parse_root)
    out = out.dropna(subset=["root"])
    if root_pref in ("SPX", "SPXW"):
        out = out[out["root"] == root_pref]
    # auto: keep both but process separately later
    out["cp"] = out["contract_type"].map(_cp)
    out = out.dropna(subset=["cp", "close", "SPX_close", "strike_price", "expiration_date"])
    out = out[out["close"] > 0]
    out["as_of_date"] = pd.to_datetime(out["as_of_date"])
    out["expiration_date"] = pd.to_datetime(out["expiration_date"])
    return out


def build_day_tenor_curve(
    day: pd.DataFrame,
    root: str,
    flat_r: float,
    rate_curve: Optional[pd.DataFrame],
    atm_mode: AtmMode,
    min_dte: float,
) -> list[dict]:
    spot = float(day["SPX_close"].iloc[0])
    points = []
    for exp, g in day.groupby("expiration_date"):
        T = year_fraction_calendar(day["as_of_date"].iloc[0], exp, root)
        dte = float((pd.Timestamp(exp).normalize() - pd.Timestamp(day["as_of_date"].iloc[0]).normalize()).days)
        if dte < min_dte or T <= 0:
            continue
        r = rate_at_T(T, rate_curve, flat_r)

        # prices for PCP
        priced = []
        for row in g.itertuples(index=False):
            px, src = mid_price(row)
            if px is None:
                continue
            priced.append(
                {
                    "strike_price": float(row.strike_price),
                    "contract_type": row.contract_type,
                    "price": px,
                    "close": float(row.close) if pd.notna(row.close) else np.nan,
                    "bid": getattr(row, "bid", np.nan),
                    "ask": getattr(row, "ask", np.nan),
                    "days_to_strike": dte,
                    "price_src": src,
                }
            )
        if not priced:
            continue
        pg = pd.DataFrame(priced)
        calls = pg[pg["contract_type"].astype(str).str.lower().str.startswith("c")][
            ["strike_price", "price"]
        ].rename(columns={"price": "price_c"})
        puts = pg[pg["contract_type"].astype(str).str.lower().str.startswith("p")][
            ["strike_price", "price"]
        ].rename(columns={"price": "price_p"})
        fwd = implied_forward_pcp(calls, puts, T, r, spot)

        ivs = invert_slice_ivs(pg, fwd.F, T, r)
        atm = atm_iv_from_slice(ivs, fwd.F, spot, mode=atm_mode)
        if atm is None:
            continue
        points.append(
            {
                "expiry": pd.Timestamp(exp).strftime("%Y-%m-%d"),
                "T": T,
                "dte": dte,
                "F": fwd.F,
                "F_method": fwd.method,
                "r": r,
                "atm_iv": atm,
                "n_iv": int(len(ivs)),
                "price_src_mode": "mid" if (ivs["price_src"] == "mid").any() else "close",
            }
        )
    return points


def constant_maturity_series(
    chain: pd.DataFrame,
    targets_days: Iterable[float],
    flat_r: float,
    rate_curve: Optional[pd.DataFrame],
    atm_mode: AtmMode,
    min_dte: float,
    roots: Iterable[str],
) -> pd.DataFrame:
    rows = []
    for dt, day0 in chain.groupby("as_of_date", sort=True):
        for root in roots:
            day = day0[day0["root"] == root]
            if day.empty:
                continue
            pts = build_day_tenor_curve(day, root, flat_r, rate_curve, atm_mode, min_dte)
            if len(pts) < 1:
                continue
            tenors = np.array([p["T"] for p in pts], dtype=float)
            ivs = np.array([p["atm_iv"] for p in pts], dtype=float)
            max_dte = max(p["dte"] for p in pts)
            for d_tgt in targets_days:
                t_tgt = float(d_tgt) / 365.0
                sig = total_var_interp(tenors, ivs, t_tgt)
                rows.append(
                    {
                        "trade_date": pd.Timestamp(dt).strftime("%Y%m%d"),
                        "root": root,
                        "tenor_days": float(d_tgt),
                        "iv_cm": sig,  # None → NaN in frame
                        "n_slices": len(pts),
                        "max_listed_dte": max_dte,
                        "spot": float(day["SPX_close"].iloc[0]),
                        "atm_mode": atm_mode,
                        "source": "spx_atm_iv_standard",
                    }
                )
    out = pd.DataFrame(rows)
    if not out.empty:
        out["iv_cm"] = pd.to_numeric(out["iv_cm"], errors="coerce")
    return out


def main():
    ap = argparse.ArgumentParser(description="Standard SPX ATM IV (PCP forward + TV interp)")
    ap.add_argument("--parquet", default=str(DEFAULT_PARQUET))
    ap.add_argument("--out", default=str(ROOT / "data" / "spx_iv_cm_standard.parquet"))
    ap.add_argument("--r", type=float, default=0.04, help="flat CC rate if no curve")
    ap.add_argument("--root", choices=["SPXW", "SPX", "both"], default="SPXW")
    ap.add_argument("--atm-mode", choices=["spot", "forward"], default="spot")
    ap.add_argument("--min-dte", type=float, default=5.0)
    ap.add_argument("--tenors", default="30,90,180,360")
    args = ap.parse_args()

    cols = [
        "as_of_date",
        "ticker",
        "contract_type",
        "strike_price",
        "expiration_date",
        "days_to_strike",
        "SPX_close",
        "close",
    ]
    # optional bid/ask if present in future dumps
    schema_names = set(pq.read_schema(args.parquet).names)
    for opt in ("bid", "ask"):
        if opt in schema_names:
            cols.append(opt)

    print(f"Loading {args.parquet} ...", flush=True)
    df = pq.read_table(args.parquet, columns=cols).to_pandas()
    print(
        f"  rows={len(df):,} max_dte={df['days_to_strike'].max()} "
        f"has_bid_ask={('bid' in df.columns and 'ask' in df.columns)}",
        flush=True,
    )
    if df["days_to_strike"].max() < 300:
        print(
            "WARNING: dump max DTE < 300 — 360D constant-maturity will be NaN "
            "(data truncation, not only filter).",
            flush=True,
        )
    if "bid" not in df.columns:
        print(
            "WARNING: no bid/ask — using close (non-synchronous). Mid required for production.",
            flush=True,
        )

    chain = prepare_chain(df, "auto")
    roots = ["SPXW", "SPX"] if args.root == "both" else [args.root]
    targets = [float(x) for x in args.tenors.split(",")]
    out = constant_maturity_series(
        chain,
        targets_days=targets,
        flat_r=args.r,
        rate_curve=None,
        atm_mode=args.atm_mode,  # type: ignore[arg-type]
        min_dte=args.min_dte,
        roots=roots,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    print(out.groupby(["root", "tenor_days"])["iv_cm"].apply(lambda s: s.notna().mean()).to_string())
    print(f"Wrote {len(out)} rows -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
