"""QIS Basket: combine calendar-vol sub-indices / strategies via risk parity.

Methods
-------
- vol_rp: inverse-volatility risk parity, then lever to target vol
- cvar_rp: inverse historical CVaR risk parity, then lever to target vol
  (CVaR used both for weights and for the risk scale when scaling)

Weights are estimated on a trailing lookback window and applied starting the
next rebalance date (no look-ahead). Between rebalances weights are held
constant (buy-and-hold on daily returns with fixed mix).
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ANN = 252
DEFAULT_METHOD = "vol_rp"
METHODS = ("vol_rp", "cvar_rp")
DEFAULT_TARGET_VOL = 0.10
DEFAULT_CVAR_ALPHA = 0.05
DEFAULT_LOOKBACK = 63
DEFAULT_REBALANCE = 21
# Default Basket = Vol RV assets only (Cal Vol optional in UI).
DEFAULT_MEMBERS = ("vrv:300:balanced", "vrv:50:balanced", "vrv:1000:balanced")
DEFAULT_VRV_TILT = "balanced"
CARRY_MEMBER_ID = "carry:cross:na"

# Calendar-vol legs (sub-indices + Regime strategy).
BASKET_SIGNAL_CHOICES = ("quantile", "garch_z", "regime2")
BASKET_SIGNAL_LABELS = {
    "quantile": "Quantile",
    "garch_z": "GARCH-z",
    "regime2": "Regime",
}
# Member kinds: cal = Calendar Vol, vrv = Vol RV (one sub-index per asset), carry = Cross Carry.
BASKET_MEMBER_KINDS = ("cal", "vrv", "carry")


def normalize_method(method: str) -> str:
    key = str(method or DEFAULT_METHOD).strip().lower().replace("-", "_")
    aliases = {
        "vol": "vol_rp",
        "rp": "vol_rp",
        "risk_parity": "vol_rp",
        "volatility": "vol_rp",
        "cvar": "cvar_rp",
        "cvar_risk_parity": "cvar_rp",
        "cvarparity": "cvar_rp",
    }
    key = aliases.get(key, key)
    if key not in METHODS:
        raise ValueError(f"unknown basket method {method!r}; choose {list(METHODS)}")
    return key


def member_id(universe: str, signal_mode: str) -> str:
    return f"cal:{universe}:{signal_mode}"


def vrv_member_id(universe: str, tilt: str = DEFAULT_VRV_TILT) -> str:
    return f"vrv:{universe}:{tilt or DEFAULT_VRV_TILT}"


def parse_member_id(mid: str) -> Tuple[str, str, str]:
    """Return (kind, universe_or_key, signal_or_tilt).

    Formats
    -------
    - cal:<universe>:<signal>   e.g. cal:300:quantile
    - vrv:<universe>:<tilt>     e.g. vrv:AU:balanced  (each Vol RV asset = one sub-index)
    - carry:cross:na            Cross Calendar Carry
    """
    parts = str(mid).strip().split(":")
    if len(parts) != 3:
        raise ValueError(
            f"unsupported basket member {mid!r}; "
            "expected cal:<uni>:<signal> | vrv:<uni>:<tilt> | carry:cross:na"
        )
    kind, a, b = parts[0].lower(), parts[1], parts[2]
    if kind not in BASKET_MEMBER_KINDS:
        raise ValueError(f"unknown member kind {kind!r} in {mid!r}")
    if kind == "carry":
        if a.lower() not in ("cross", "cross_calendar_carry", "xcal"):
            raise ValueError(f"unsupported carry member {mid!r}")
        return "carry", "cross", "na"
    if kind == "vrv":
        if not a:
            raise ValueError(f"vrv member missing universe: {mid!r}")
        return "vrv", a, (b or DEFAULT_VRV_TILT).lower()
    # cal
    if a not in ("50", "300", "1000") and a not in BASKET_SIGNAL_CHOICES:
        # allow any calendar UNIVERSES key; validated at load
        pass
    if b not in BASKET_SIGNAL_CHOICES:
        raise ValueError(
            f"unknown calendar signal {b!r} in {mid!r}; choose {list(BASKET_SIGNAL_CHOICES)}"
        )
    return "cal", a, b


def parse_members(raw: Optional[str | Sequence[str]]) -> List[str]:
    if raw is None:
        return list(DEFAULT_MEMBERS)
    if isinstance(raw, str):
        items = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    else:
        items = [str(x).strip() for x in raw if str(x).strip()]
    if not items:
        raise ValueError("basket members empty")
    out: List[str] = []
    seen = set()
    for m in items:
        kind, a, b = parse_member_id(m)
        if kind == "carry":
            norm = CARRY_MEMBER_ID
        elif kind == "vrv":
            norm = vrv_member_id(a, b)
        else:
            norm = member_id(a, b)
        if norm not in seen:
            out.append(norm)
            seen.add(norm)
    if len(out) < 2:
        raise ValueError("basket needs at least 2 members")
    return out


def member_label(mid: str) -> str:
    kind, a, b = parse_member_id(mid)
    if kind == "carry":
        return "Cross Calendar Carry"
    if kind == "vrv":
        try:
            from engine.csi300_vol_rv_strategy import VOL_RV_UNIVERSES, normalize_universe

            u = normalize_universe(a)
            lab = VOL_RV_UNIVERSES[u].get("label", u)
        except Exception:
            lab = a
        tilt = b if b != DEFAULT_VRV_TILT else ""
        return f"Vol RV {lab}" + (f" ({tilt})" if tilt else "")
    # cal
    try:
        from engine.calendar_vol_strategy import UNIVERSES

        uni_lab = UNIVERSES.get(a, {}).get("index_name", a)
    except Exception:
        uni_lab = a
    return f"{uni_lab} {BASKET_SIGNAL_LABELS.get(b, b)}"


def basket_cache_sig(
    method: str,
    target_vol: float,
    cvar_alpha: float,
    lookback: int,
    rebalance: int,
    members: Sequence[str],
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
) -> str:
    mem = "+".join(members)
    return (
        f"basket|{method}|tv{float(target_vol):.4g}|a{float(cvar_alpha):.4g}"
        f"|lb{int(lookback)}|rb{int(rebalance)}|m={mem}"
        f"|tp{tenor_pair}|sm{side_mode}|sp{spread_mode}"
    )


def nav_series_from_payload(payload: Dict[str, Any]) -> pd.Series:
    rows = payload.get("nav") or []
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        {str(r["d"]): float(r["nav"]) for r in rows if r.get("d") is not None and r.get("nav") is not None}
    ).sort_index()


def _asset_vol(rets: pd.Series) -> float:
    r = rets.dropna()
    if len(r) < 5:
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(ANN))


def _asset_cvar(rets: pd.Series, alpha: float) -> float:
    """Historical left-tail CVaR as a positive loss number (higher = riskier)."""
    r = rets.dropna().astype(float)
    if len(r) < 10:
        return float("nan")
    a = float(alpha)
    if not (0.0 < a < 0.5):
        a = DEFAULT_CVAR_ALPHA
    q = float(np.quantile(r.to_numpy(), a))
    tail = r[r <= q]
    if tail.empty:
        return abs(q)
    # Expected shortfall of losses
    return float(-tail.mean())


def _inverse_risk_weights(risk: np.ndarray) -> np.ndarray:
    r = np.asarray(risk, dtype=float)
    r = np.where(np.isfinite(r) & (r > 1e-12), r, np.nan)
    if not np.isfinite(r).any():
        n = len(r)
        return np.ones(n) / n
    inv = 1.0 / r
    inv = np.where(np.isfinite(inv), inv, 0.0)
    s = inv.sum()
    if s <= 0:
        n = len(r)
        return np.ones(n) / n
    return inv / s


def _port_vol(w: np.ndarray, cov: np.ndarray) -> float:
    v = float(w @ cov @ w)
    return math.sqrt(max(v, 0.0) * ANN)


def _port_cvar(w: np.ndarray, ret_mat: np.ndarray, alpha: float) -> float:
    pr = ret_mat @ w
    if pr.size < 10:
        return float("nan")
    a = float(alpha) if 0.0 < float(alpha) < 0.5 else DEFAULT_CVAR_ALPHA
    q = float(np.quantile(pr, a))
    tail = pr[pr <= q]
    if tail.size == 0:
        return abs(q)
    return float(-tail.mean())


def estimate_weights(
    window: pd.DataFrame,
    method: str,
    cvar_alpha: float,
) -> np.ndarray:
    """Unlevered positive weights summing to 1 on the lookback window."""
    cols = list(window.columns)
    n = len(cols)
    if n == 0:
        return np.array([])
    if window.dropna(how="any").shape[0] < max(10, n + 2):
        return np.ones(n) / n

    if method == "cvar_rp":
        risks = np.array([_asset_cvar(window[c], cvar_alpha) for c in cols], dtype=float)
        return _inverse_risk_weights(risks)

    # vol_rp
    risks = np.array([_asset_vol(window[c]) for c in cols], dtype=float)
    return _inverse_risk_weights(risks)


def scale_to_target(
    w: np.ndarray,
    window: pd.DataFrame,
    method: str,
    target_vol: float,
    cvar_alpha: float,
) -> Tuple[np.ndarray, float, float]:
    """Return (levered weights, gross leverage, realized risk used for scale)."""
    clean = window.dropna(how="any")
    if clean.shape[0] < 10 or w.size == 0:
        return w, 1.0, float("nan")
    mat = clean.to_numpy(dtype=float)
    if method == "cvar_rp":
        risk = _port_cvar(w, mat, cvar_alpha)
        # Map CVaR target ≈ target_vol * k; use Gaussian ES factor for α=5% ≈ 2.06 σ
        # so target_cvar ≈ target_vol / sqrt(252) * φ(z)/α  daily… Simpler: scale by
        # annualized vol proxy: treat CVaR*sqrt(252) like a vol-ish number.
        risk_ann = risk * math.sqrt(ANN) if risk == risk else float("nan")
    else:
        cov = np.cov(mat, rowvar=False, ddof=1)
        if cov.ndim == 0:
            cov = np.array([[float(cov)]])
        risk_ann = _port_vol(w, cov)

    if not (risk_ann == risk_ann) or risk_ann <= 1e-8:
        return w, 1.0, risk_ann
    lev = float(target_vol) / risk_ann
    # Cap extreme leverage
    lev = float(max(0.05, min(lev, 5.0)))
    return w * lev, lev, risk_ann


def build_basket_returns(
    rets: pd.DataFrame,
    method: str = DEFAULT_METHOD,
    target_vol: float = DEFAULT_TARGET_VOL,
    cvar_alpha: float = DEFAULT_CVAR_ALPHA,
    lookback: int = DEFAULT_LOOKBACK,
    rebalance: int = DEFAULT_REBALANCE,
) -> Tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    """Walk-forward basket.

    Returns
    -------
    basket_ret : daily portfolio return (aligned to rets.index[lookback:])
    weights : DataFrame of levered weights on each date
    meta : DataFrame with leverage / risk columns on rebalance dates
    """
    method = normalize_method(method)
    rets = rets.sort_index().astype(float)
    cols = list(rets.columns)
    n = len(cols)
    lb = max(20, int(lookback))
    rb = max(1, int(rebalance))
    if len(rets) <= lb + 2:
        raise ValueError(f"insufficient overlapping history ({len(rets)}d) for lookback={lb}")

    w_hist = pd.DataFrame(index=rets.index, columns=cols, dtype=float)
    meta_rows: List[Dict[str, Any]] = []
    cur_w = np.ones(n) / n
    cur_lev = 1.0
    next_rebalance_i = lb

    for i in range(lb, len(rets)):
        if i == next_rebalance_i or i == lb:
            window = rets.iloc[i - lb : i]
            raw_w = estimate_weights(window, method, cvar_alpha)
            lev_w, lev, risk_ann = scale_to_target(raw_w, window, method, target_vol, cvar_alpha)
            cur_w = lev_w
            cur_lev = lev
            next_rebalance_i = i + rb
            meta_rows.append(
                {
                    "d": str(rets.index[i]),
                    "leverage": round(float(cur_lev), 4),
                    "risk_ann": None if risk_ann != risk_ann else round(float(risk_ann), 6),
                    **{c: round(float(cur_w[j]), 6) for j, c in enumerate(cols)},
                }
            )
        w_hist.iloc[i] = cur_w

    # Apply previous close weights to today's return (weights set at i use returns[i])
    aligned = w_hist.iloc[lb:].copy()
    r_slice = rets.iloc[lb:]
    port = (aligned.to_numpy(dtype=float) * r_slice.to_numpy(dtype=float)).sum(axis=1)
    basket_ret = pd.Series(port, index=r_slice.index, name="basket")
    return basket_ret, aligned, pd.DataFrame(meta_rows)


def _window_metrics(nav: pd.Series, daily_ret: pd.Series) -> Dict[str, Any]:
    if len(nav) < 2:
        return {
            "n_days": int(len(nav)),
            "ann_return": None,
            "ann_vol": None,
            "sharpe": None,
            "mdd": None,
            "mdd_vol": None,
            "pct_positive_months": None,
            "start": None,
            "end": None,
        }
    r = daily_ret.dropna()
    n = len(r)
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
        "start": str(nav.index[0]),
        "end": str(nav.index[-1]),
    }


def _slice_from_end(
    nav: pd.Series, ret: pd.Series, years: Optional[float]
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


def _yearly_metrics(nav: pd.Series, daily_ret: pd.Series) -> List[Dict[str, Any]]:
    if len(nav) < 2:
        return []
    idx = pd.to_datetime(nav.index)
    years = sorted({int(t.year) for t in idx})
    out: List[Dict[str, Any]] = []
    for y in years:
        mask = pd.Series([int(t.year) == y for t in idx], index=nav.index)
        n = nav.loc[mask]
        r = daily_ret.reindex(n.index).dropna()
        if len(n) < 2:
            continue
        base = float(n.iloc[0])
        n_reb = n / base if base else n
        # daily_ret is already day-over-day on the same index as nav_mult
        r_use = daily_ret.loc[n.index].iloc[1:] if len(daily_ret.loc[n.index]) == len(n) else r
        m = _window_metrics(n_reb, r_use)
        m["year"] = y
        out.append(m)
    return out


def load_calendar_member_payload(
    universe: str,
    signal_mode: str,
    *,
    initial_nav: float,
    z_window: int,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    source_key: str,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Prefer SQLite/memory cache; otherwise run calendar-vol engine and persist."""
    from api.v1 import qis_results_store as qrs
    from engine.calendar_vol_strategy import (
        UNIVERSES,
        normalize_signal_mode,
        normalize_side_mode,
        normalize_spread_mode,
        normalize_tenor_pair,
        run_by_strategy_id,
    )

    if universe not in UNIVERSES:
        raise ValueError(f"unknown universe {universe!r}")
    strategy_id = UNIVERSES[universe]["strategy_id"]
    tp = normalize_tenor_pair(tenor_pair)
    sm = normalize_side_mode(side_mode)
    spm = normalize_spread_mode(spread_mode)
    sig = normalize_signal_mode(signal_mode)

    if not refresh:
        hit = qrs.get_cached(
            strategy_id,
            z_window,
            initial_nav,
            source_key,
            tenor_pair=tp,
            side_mode=sm,
            spread_mode=spm,
            signal_mode=sig,
        )
        if hit is not None:
            return hit

    payload = run_by_strategy_id(
        strategy_id,
        initial_nav,
        z_window=z_window,
        tenor_pair=tp,
        side_mode=sm,
        spread_mode=spm,
        signal_mode=sig,
        stop_loss=False,
    )
    qrs.put_cached(
        strategy_id,
        z_window,
        initial_nav,
        source_key,
        payload,
        tenor_pair=tp,
        side_mode=sm,
        spread_mode=spm,
        signal_mode=sig,
    )
    return payload


def load_vol_rv_member_payload(
    universe: str,
    tilt: str = DEFAULT_VRV_TILT,
    *,
    initial_nav: float,
    source_key: str,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Load one Vol RV asset NAV (ablation off for speed)."""
    from api.v1 import qis_results_store as qrs
    from engine.csi300_vol_rv_strategy import (
        CACHE_SIDE,
        CACHE_SPREAD,
        cache_tenor_for,
        normalize_tilt,
        normalize_universe,
        run_csi300_vol_rv,
        vol_rv_cache_sig,
    )

    uni = normalize_universe(universe)
    t = normalize_tilt(tilt)
    cache_tenor = cache_tenor_for(uni)
    # Match live default knobs used by the Vol RV UI.
    target_otm_delta = 0.15
    long_delta_threshold = 0.05
    hedger_mode = "eod_close"
    sig = vol_rv_cache_sig(
        t,
        target_otm_delta,
        long_delta_threshold,
        hedger_mode,
        None,
        None,
        None,
        uni,
    )
    # Prefer rows written by the main Vol RV API (ablation may be present).
    if not refresh:
        hit = qrs.get_cached(
            "csi300_vol_rv",
            252,
            initial_nav,
            source_key,
            tenor_pair=cache_tenor,
            side_mode=CACHE_SIDE,
            spread_mode=CACHE_SPREAD,
            signal_mode=sig,
        )
        if hit is not None:
            return hit

    payload = run_csi300_vol_rv(
        initial_nav=initial_nav,
        tilt=t,
        universe=uni,
        target_otm_delta=target_otm_delta,
        long_delta_threshold=long_delta_threshold,
        hedger_mode=hedger_mode,
        run_ablation=False,
    )
    qrs.put_cached(
        "csi300_vol_rv",
        252,
        initial_nav,
        source_key,
        payload,
        tenor_pair=cache_tenor,
        side_mode=CACHE_SIDE,
        spread_mode=CACHE_SPREAD,
        signal_mode=sig,
    )
    return payload


def load_carry_member_payload(
    *,
    initial_nav: float,
    source_key: str,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Load Cross Calendar Carry NAV via the same cache PK as the carry API."""
    from api.v1 import qis_results_store as qrs

    # Mirror qis_backtest cost defaults (avoid circular import at module load).
    slippage = 2e-4
    fee = 0.0023 / 100.0
    cs = qrs.cost_sig(slippage, fee)
    if not refresh:
        hit = qrs.get_cached(
            "cross_calendar_carry",
            qrs.CROSS_Z_WINDOW,
            initial_nav,
            source_key,
            tenor_pair=qrs.CROSS_TENOR,
            side_mode=qrs.CROSS_SIDE,
            spread_mode=qrs.CROSS_SPREAD,
            signal_mode=cs,
        )
        if hit is not None:
            return hit

    from api.v1 import qis_backtest as qb

    payload = qb.run_cross_calendar_carry(initial_nav=initial_nav)
    qrs.put_cached(
        "cross_calendar_carry",
        qrs.CROSS_Z_WINDOW,
        initial_nav,
        source_key,
        payload,
        tenor_pair=qrs.CROSS_TENOR,
        side_mode=qrs.CROSS_SIDE,
        spread_mode=qrs.CROSS_SPREAD,
        signal_mode=cs,
    )
    return payload


def load_member_payload(
    mid: str,
    *,
    initial_nav: float,
    z_window: int,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    source_key: str,
    refresh: bool = False,
) -> Dict[str, Any]:
    kind, a, b = parse_member_id(mid)
    if kind == "cal":
        return load_calendar_member_payload(
            a,
            b,
            initial_nav=initial_nav,
            z_window=z_window,
            tenor_pair=tenor_pair,
            side_mode=side_mode,
            spread_mode=spread_mode,
            source_key=source_key,
            refresh=refresh,
        )
    if kind == "vrv":
        return load_vol_rv_member_payload(
            a,
            b,
            initial_nav=initial_nav,
            source_key=source_key,
            refresh=refresh,
        )
    return load_carry_member_payload(
        initial_nav=initial_nav,
        source_key=source_key,
        refresh=refresh,
    )


def run_qis_basket(
    *,
    initial_nav: float = 100_000_000.0,
    method: str = DEFAULT_METHOD,
    target_vol: float = DEFAULT_TARGET_VOL,
    cvar_alpha: float = DEFAULT_CVAR_ALPHA,
    lookback: int = DEFAULT_LOOKBACK,
    rebalance: int = DEFAULT_REBALANCE,
    members: Optional[str | Sequence[str]] = None,
    universe: str = "300",
    signals: Optional[Sequence[str]] = None,
    tenor_pair: str = "3M-6M",
    side_mode: str = "both",
    spread_mode: str = "iv",
    z_window: int = 252,
    source_key: str = "",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Build basket payload compatible with QisView charts."""
    method = normalize_method(method)
    tv = float(target_vol)
    if not (0.01 <= tv <= 0.5):
        raise ValueError("target_vol must be in [0.01, 0.5]")
    alpha = float(cvar_alpha)
    if not (0.01 <= alpha <= 0.25):
        raise ValueError("cvar_alpha must be in [0.01, 0.25]")
    lb = int(lookback)
    rb = int(rebalance)
    if lb < 20 or lb > 504:
        raise ValueError("lookback must be in [20, 504]")
    if rb < 1 or rb > 126:
        raise ValueError("rebalance must be in [1, 126]")

    if members is not None:
        mem_ids = parse_members(members)
    elif signals:
        sigs = list(signals)
        for s in sigs:
            if s not in BASKET_SIGNAL_CHOICES:
                raise ValueError(f"unknown signal {s!r}; choose {list(BASKET_SIGNAL_CHOICES)}")
        mem_ids = parse_members([member_id(universe, s) for s in sigs])
    else:
        mem_ids = parse_members(list(DEFAULT_MEMBERS))

    navs: Dict[str, pd.Series] = {}
    labels: Dict[str, str] = {}
    for mid in mem_ids:
        payload = load_member_payload(
            mid,
            initial_nav=initial_nav,
            z_window=z_window,
            tenor_pair=tenor_pair,
            side_mode=side_mode,
            spread_mode=spread_mode,
            source_key=source_key or "basket",
            refresh=refresh,
        )
        s = nav_series_from_payload(payload)
        if s.empty:
            raise ValueError(f"member {mid} has empty NAV")
        navs[mid] = s
        labels[mid] = member_label(mid)

    nav_df = pd.DataFrame(navs).dropna(how="any").sort_index()
    if nav_df.shape[0] < lb + 5:
        raise ValueError(
            f"overlapping NAV too short ({nav_df.shape[0]}d) for lookback={lb}; "
            "try fewer members or a shorter lookback"
        )
    rets = nav_df.pct_change().dropna(how="any")
    basket_ret, w_hist, rebalance_meta = build_basket_returns(
        rets,
        method=method,
        target_vol=tv,
        cvar_alpha=alpha,
        lookback=lb,
        rebalance=rb,
    )

    # Basket NAV starting at 1.0 then scale to initial_nav for display multiple
    nav_mult = (1.0 + basket_ret).cumprod()
    nav_mult = nav_mult / float(nav_mult.iloc[0])
    nav_abs = nav_mult * float(initial_nav)

    # Equal-weight unlevered overlay on same dates (for reference)
    ew = rets.loc[basket_ret.index].mean(axis=1)
    ew_nav = (1.0 + ew).cumprod()
    ew_nav = ew_nav / float(ew_nav.iloc[0])

    # Component NAVs rebased on basket start
    comp_start = str(basket_ret.index[0])
    components = []
    for mid in mem_ids:
        s = nav_df[mid].loc[comp_start:]
        if s.empty:
            continue
        reb = s / float(s.iloc[0])
        components.append(
            {
                "id": mid,
                "label": labels[mid],
                "nav": [{"d": str(d), "nav": round(float(v), 6)} for d, v in reb.items()],
            }
        )

    daily_ret = basket_ret
    metrics = {
        "1y": {
            **_window_metrics(*_slice_from_end(nav_mult, daily_ret, 1.0)),
        },
        "3y": {
            **_window_metrics(*_slice_from_end(nav_mult, daily_ret, 3.0)),
        },
        "5y": {
            **_window_metrics(*_slice_from_end(nav_mult, daily_ret, 5.0)),
        },
        "max": {
            **_window_metrics(nav_mult, daily_ret),
        },
    }
    yearly = _yearly_metrics(nav_mult, daily_ret)

    latest_w = {}
    if not w_hist.empty:
        last = w_hist.iloc[-1]
        latest_w = {c: round(float(last[c]), 6) for c in w_hist.columns}

    weight_series = []
    for d, row in w_hist.iloc[:: max(1, rb)].iterrows():
        weight_series.append(
            {
                "d": str(d),
                **{c: round(float(row[c]), 6) for c in w_hist.columns},
            }
        )

    method_label = "Vol Risk Parity" if method == "vol_rp" else "CVaR Risk Parity"
    member_labels = [labels[m] for m in mem_ids]

    return {
        "strategy_id": "qis_basket",
        "strategy_name": f"QIS Basket — {method_label}",
        "description": (
            f"{method_label} over {', '.join(member_labels)}; "
            f"target vol {tv*100:.1f}%"
            + (f", CVaR α={alpha:.0%}" if method == "cvar_rp" else "")
            + f"; lookback {lb}d / rebalance every {rb}d; "
            f"weights from trailing risk only (no look-ahead)."
        ),
        "legs": [
            {
                "id": mid,
                "weight": float(latest_w.get(mid, 0.0)),
                "product": labels[mid],
                "tenor": "basket",
            }
            for mid in mem_ids
        ],
        "params": {
            "initial_nav": initial_nav,
            "slippage_bp": 0.0,
            "fee_pct": 0.0,
            "cost_rate": 0.0,
            "rebalance": f"every {rb} trading days",
            "signal_lag": "weights from prior lookback only",
            "price": "component QIS NAV multiples",
            "method": method,
            "target_vol": tv,
            "cvar_alpha": alpha,
            "lookback": lb,
            "rebalance_days": rb,
            "members": mem_ids,
            "member_labels": member_labels,
            "universe": universe,
            "tenor_pair": tenor_pair,
            "side_mode": side_mode,
            "spread_mode": spread_mode,
            "z_window": z_window,
            "latest_weights": latest_w,
        },
        "nav": [
            {
                "d": str(d),
                "nav": round(float(nav_mult.loc[d]), 6),
                "sos": round(float(ew_nav.loc[d]), 6) if d in ew_nav.index else None,
            }
            for d in nav_mult.index
        ],
        "metrics": metrics,
        "yearly_metrics": yearly,
        "summary": {
            "start": str(nav_mult.index[0]),
            "end": str(nav_mult.index[-1]),
            "n_days": int(len(nav_mult)),
            "final_nav": round(float(nav_abs.iloc[-1]), 2),
            "final_nav_multiple": round(float(nav_mult.iloc[-1]), 6),
            "total_cost": 0.0,
        },
        "basket": {
            "method": method,
            "target_vol": tv,
            "cvar_alpha": alpha,
            "lookback": lb,
            "rebalance_days": rb,
            "members": [
                {"id": mid, "label": labels[mid], "weight": float(latest_w.get(mid, 0.0))}
                for mid in mem_ids
            ],
            "weights": weight_series,
            "rebalance_log": rebalance_meta.to_dict(orient="records"),
            "components": components,
            "equal_weight_label": "Equal-weight (unlevered)",
        },
    }
