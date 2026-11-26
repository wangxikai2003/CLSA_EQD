"""QIS systematic strategy backtests."""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.v1 import synth_tenors as st

router = APIRouter(tags=["qis"])

_INITIAL_NAV = 100_000_000.0
_SLIPPAGE = 2e-4  # 2bp
_FEE = 0.0023 / 100.0  # 0.0023%
_COST_RATE = _SLIPPAGE + _FEE
_ANN = 252

_cache: Dict[str, Any] = {"key": None, "payload": None}


def run_cross_calendar_carry(
    initial_nav: float = _INITIAL_NAV,
    slippage: float = _SLIPPAGE,
    fee: float = _FEE,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Listed overlapping IM/IH calendar vintages (Vol-RV style). See engine module."""
    from engine.cross_calendar_carry_strategy import (
        run_cross_calendar_carry as _run_listed,
    )

    try:
        return _run_listed(initial_nav=initial_nav, slippage=slippage, fee=fee, **kwargs)
    except FileNotFoundError as e:
        raise HTTPException(503, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/strategies")
def list_strategies():
    from engine.calendar_vol_strategy import (
        SIDE_MODES,
        SIGNAL_MODES,
        SPREAD_MODES,
        TENOR_PAIRS,
        UNIVERSES,
    )

    cal = [
        {
            "id": u["strategy_id"],
            "name": u["strategy_name"],
            "status": "live",
            "blurb": (
                f"{u['index_name']} Calendar Vol ATM CT-IV; tenor 3M−6M | 1M−3M; "
                f"side both | long_only | short_only; spread iv | iv2; "
                f"signal quantile | garch_z | regime | regime_momentum | regime_fade | regime2; "
                f"t−1 signal / t close; {u['product']} straddles + {u['fut_root']} hedge"
            ),
            "family": "calendar_vol",
            "universe": key,
            "tenor_pairs": list(TENOR_PAIRS.keys()),
            "side_modes": list(SIDE_MODES),
            "spread_modes": list(SPREAD_MODES),
            "signal_modes": list(SIGNAL_MODES),
        }
        for key, u in UNIVERSES.items()
    ]
    from engine.csi300_vol_rv_strategy import STYLE_PRESETS, VOL_RV_UNIVERSES
    from engine.qis_basket import (
        BASKET_SIGNAL_CHOICES,
        BASKET_SIGNAL_LABELS,
        CARRY_MEMBER_ID,
        METHODS,
        vrv_member_id,
    )

    return {
        "strategies": [
            {
                "id": "cross_calendar_carry",
                "name": "Cross Calendar Carry",
                "status": "live",
                "blurb": (
                    "Daily overlapping listed vintages: +IM_near −IM_deferred −IH_near +IH_deferred; "
                    "exit n_roll sessions before short expiry (no constant-maturity lerp)"
                ),
                "family": "futures_carry",
            },
            *cal,
            {
                "id": "csi300_vol_rv",
                "name": "Vol RV",
                "status": "live",
                "blurb": (
                    "Near-month ATM short strangle + deferred OTM long strangle; "
                    "EOD futures hedge with long threshold; premium-inverse leverage. "
                    f"universes={list(VOL_RV_UNIVERSES)}; tilts={list(STYLE_PRESETS)}"
                ),
                "family": "csi300_vol_rv",
                "tilts": list(STYLE_PRESETS.keys()),
                "universes": [
                    {
                        "key": u["key"],
                        "label": u["label"],
                        "product": u["product"],
                        "fut_root": u["fut_root"],
                    }
                    for u in VOL_RV_UNIVERSES.values()
                ],
                "defaults": {
                    "universe": "300",
                    "tilt": "balanced",
                    "target_otm_delta": 0.15,
                    "long_delta_threshold": 0.05,
                    "hedger_mode": "eod_close",
                    **STYLE_PRESETS["balanced"],
                },
            },
            {
                "id": "qis_basket",
                "name": "QIS Basket",
                "status": "live",
                "blurb": (
                    "Risk parity / CVaR risk parity over Calendar Vol sub-indices, "
                    "Vol RV assets (each universe = one sub-index), and Cross Carry; "
                    "selectable target vol"
                ),
                "family": "qis_basket",
                "methods": list(METHODS),
                "signals": [
                    {"key": s, "label": BASKET_SIGNAL_LABELS[s]} for s in BASKET_SIGNAL_CHOICES
                ],
                "vol_rv_assets": [
                    {
                        "key": u["key"],
                        "label": u["label"],
                        "member_id": vrv_member_id(u["key"]),
                    }
                    for u in VOL_RV_UNIVERSES.values()
                ],
                "other_strategies": [
                    {"key": "carry", "label": "Cross Calendar Carry", "member_id": CARRY_MEMBER_ID}
                ],
                "defaults": {
                    "method": "vol_rp",
                    "target_vol": 0.10,
                    "cvar_alpha": 0.05,
                    "lookback": 63,
                    "rebalance": 21,
                    "universe": "300",
                    "signals": [],
                    "vol_rv_assets": ["300", "50", "1000"],
                    "include_carry": False,
                },
            },
        ],
        "tenor_pairs": list(TENOR_PAIRS.keys()),
        "side_modes": list(SIDE_MODES),
        "spread_modes": list(SPREAD_MODES),
        "signal_modes": list(SIGNAL_MODES),
        "vol_rv_tilts": list(STYLE_PRESETS.keys()),
        "vol_rv_universes": list(VOL_RV_UNIVERSES.keys()),
        "basket_methods": list(METHODS),
        "basket_signals": list(BASKET_SIGNAL_CHOICES),
        "basket_vol_rv_assets": list(VOL_RV_UNIVERSES.keys()),
    }


@router.get("/backtest/{strategy_id}")
def backtest(
    strategy_id: str,
    refresh: bool = Query(False),
    z_window: int = Query(
        252,
        ge=20,
        le=756,
        description="Rolling quantile window in trading days (default 252=1Y; prior data only)",
    ),
    tenor_pair: str = Query(
        "3M-6M",
        description="Calendar tenor pair: 3M-6M (default) or 1M-3M",
    ),
    side_mode: str = Query(
        "both",
        description="Position side: both | long_only | short_only",
    ),
    spread_mode: str = Query(
        "iv",
        description="Spread construction: iv (IV_near−IV_far) | iv2 (IV_near²−IV_far²)",
    ),
    signal_mode: str = Query(
        "quantile",
        description="Signal: quantile | garch_z | regime | regime_momentum | regime_fade | regime2",
    ),
    stop_loss: bool = Query(
        False,
        description="Quantile/regime2 only: flatten after entry-NAV drawdown, then wait for a spread retrace",
    ),
    max_drawdown_target: float = Query(
        0.10,
        gt=0,
        lt=1,
        description="Stop when NAV is this fraction below the entry-day NAV",
    ),
    rebound_ratio: float = Query(
        0.90,
        gt=0,
        le=1,
        description="Leave the stop once the spread has retraced this fraction of its adverse move",
    ),
    tilt: str = Query("balanced", description="Vol RV style: balanced | carry | defensive"),
    universe: str = Query(
        "300",
        description="Vol RV universe: 300|50|1000|AU|SC|GC (COMEX)|CL (NYMEX WTI)|ES (E-mini; hedge=ES fut)",
    ),
    target_otm_delta: float = Query(0.15, gt=0.01, lt=0.5, description="OTM long target |delta|"),
    long_delta_threshold: float = Query(
        0.05, ge=0.0, le=1.0, description="Hedge long book only when |net long delta| exceeds this"
    ),
    lambda_tilt: Optional[float] = Query(None, description="Override tilt λ; default from STYLE_PRESETS"),
    l_min: Optional[float] = Query(None, description="Override leverage floor"),
    l_max: Optional[float] = Query(None, description="Override leverage cap"),
    hedger_mode: str = Query("eod_close", description="eod_close | intraday_curve (stub)"),
    option_fee_pct: float = Query(0.0, ge=0.0, le=0.05),
    option_slippage_pct: float = Query(0.0, ge=0.0, le=0.05),
    futures_fee_pct: float = Query(0.0, ge=0.0, le=0.05),
    futures_slippage_pct: float = Query(0.0, ge=0.0, le=0.05),
    ablation: bool = Query(True, description="Return step1/2/3 ablation NAV paths"),
    # QIS Basket
    basket_method: str = Query(
        "vol_rp",
        description="Basket method: vol_rp (inverse-vol RP) | cvar_rp (inverse-CVaR RP)",
    ),
    target_vol: float = Query(
        0.10,
        gt=0.01,
        le=0.5,
        description="Basket target annualized vol (also scales CVaR-RP leverage)",
    ),
    cvar_alpha: float = Query(
        0.05,
        gt=0.01,
        le=0.25,
        description="Historical CVaR tail probability (e.g. 0.05 = 95% CVaR)",
    ),
    basket_lookback: int = Query(63, ge=20, le=504, description="Trailing days for risk estimate"),
    basket_rebalance: int = Query(21, ge=1, le=126, description="Rebalance every N trading days"),
    basket_members: Optional[str] = Query(
        None,
        description="Comma-separated members cal:<uni>:<signal>; default quantile+garch_z+regime2",
    ),
    basket_signals: Optional[str] = Query(
        None,
        description="Comma-separated signals (quantile,garch_z,regime2) under basket_universe",
    ),
    basket_universe: str = Query("300", description="Universe for basket_signals shortcut"),
    carry_open_every_n: int = Query(
        1, ge=1, le=63, description="Cross Carry: open a new vintage every N sessions (1=daily)"
    ),
    carry_n_roll: int = Query(
        2, ge=0, le=20, description="Cross Carry: exit this many sessions before short expiry"
    ),
    carry_budget_frac: float = Query(
        1.0 / 21.0,
        gt=0.005,
        le=1.0,
        description="Cross Carry: NAV fraction of gross point notional per vintage",
    ),
    carry_short_min_dte: int = Query(
        5, ge=1, le=60, description="Cross Carry: skip short months with fewer calendar days to expiry"
    ),
):
    db = st._equity_db()
    from api.v1 import qis_results_store as qrs

    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)

    if strategy_id == "qis_basket":
        from engine.qis_basket import (
            BASKET_SIGNAL_CHOICES,
            basket_cache_sig,
            normalize_method,
            parse_members,
            run_qis_basket,
        )
        from engine.calendar_vol_strategy import (
            normalize_side_mode,
            normalize_spread_mode,
            normalize_tenor_pair,
        )

        try:
            method = normalize_method(basket_method)
            tp = normalize_tenor_pair(tenor_pair)
            sm = normalize_side_mode(side_mode)
            spm = normalize_spread_mode(spread_mode)
            if basket_members:
                mems = parse_members(basket_members)
            elif basket_signals:
                sigs = [x.strip() for x in basket_signals.replace(";", ",").split(",") if x.strip()]
                bad = [s for s in sigs if s not in BASKET_SIGNAL_CHOICES]
                if bad:
                    raise ValueError(f"unknown basket signals {bad}; choose {list(BASKET_SIGNAL_CHOICES)}")
                mems = parse_members([f"cal:{basket_universe}:{s}" for s in sigs])
            else:
                from engine.qis_basket import DEFAULT_MEMBERS

                mems = parse_members(list(DEFAULT_MEMBERS))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        sig = basket_cache_sig(
            method,
            target_vol,
            cvar_alpha,
            basket_lookback,
            basket_rebalance,
            mems,
            tp,
            sm,
            spm,
        )
        # Encode basket fingerprint in signal_mode; other PK slots are sentinels.
        cache_tenor = "basket"
        cache_side = "n/a"
        cache_spread = "n/a"
        mem_key = (
            strategy_id,
            int(z_window),
            src,
            _INITIAL_NAV,
            cache_tenor,
            cache_side,
            cache_spread,
            sig,
        )
        if not refresh:
            hit = qrs.get_cached(
                strategy_id,
                int(z_window),
                _INITIAL_NAV,
                src,
                tenor_pair=cache_tenor,
                side_mode=cache_side,
                spread_mode=cache_spread,
                signal_mode=sig,
            )
            if hit is not None:
                return hit
            mem = _cache.get("basket_map") or {}
            if mem_key in mem:
                return qrs.attach_cache_meta(
                    mem[mem_key],
                    hit=True,
                    source="memory",
                    source_key=src,
                    tenor_pair=cache_tenor,
                    side_mode=cache_side,
                    spread_mode=cache_spread,
                    signal_mode=sig,
                )

        try:
            payload = run_qis_basket(
                initial_nav=_INITIAL_NAV,
                method=method,
                target_vol=target_vol,
                cvar_alpha=cvar_alpha,
                lookback=basket_lookback,
                rebalance=basket_rebalance,
                members=mems,
                universe=basket_universe,
                tenor_pair=tp,
                side_mode=sm,
                spread_mode=spm,
                z_window=int(z_window),
                source_key=src,
                refresh=bool(refresh),
            )
        except FileNotFoundError as e:
            raise HTTPException(503, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"qis_basket failed: {e}") from e

        qrs.put_cached(
            strategy_id,
            int(z_window),
            _INITIAL_NAV,
            src,
            payload,
            tenor_pair=cache_tenor,
            side_mode=cache_side,
            spread_mode=cache_spread,
            signal_mode=sig,
        )
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        mem = _cache.get("basket_map") or {}
        mem[mem_key] = clean
        _cache["basket_map"] = mem
        return qrs.attach_cache_meta(
            payload,
            hit=False,
            source="miss",
            source_key=src,
            persisted=True,
            tenor_pair=cache_tenor,
            side_mode=cache_side,
            spread_mode=cache_spread,
            signal_mode=sig,
        )

    if strategy_id == "csi300_vol_rv":
        from engine.csi300_vol_rv_strategy import (
            CACHE_SIDE,
            CACHE_SPREAD,
            cache_tenor_for,
            normalize_tilt,
            normalize_universe,
            run_csi300_vol_rv,
            vol_rv_cache_sig,
        )

        try:
            t = normalize_tilt(tilt)
            uni = normalize_universe(universe)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        cache_tenor = cache_tenor_for(uni)
        hm = str(hedger_mode or "eod_close").strip().lower()
        if hm not in ("eod_close", "eod", "close", "intraday_curve", "intraday", "curve"):
            raise HTTPException(400, f"unknown hedger_mode {hedger_mode!r}")
        # Stub: fall back to EOD until intraday bars exist; still fingerprint the request.
        run_hm = "eod_close"
        sig = vol_rv_cache_sig(
            t,
            target_otm_delta,
            long_delta_threshold,
            hm if hm.startswith("intraday") else run_hm,
            lambda_tilt,
            l_min,
            l_max,
            uni,
        )
        mem_key = (
            strategy_id,
            252,
            src,
            _INITIAL_NAV,
            cache_tenor,
            CACHE_SIDE,
            CACHE_SPREAD,
            sig,
            bool(ablation),
            round(float(option_fee_pct), 6),
            round(float(option_slippage_pct), 6),
            round(float(futures_fee_pct), 6),
            round(float(futures_slippage_pct), 6),
        )
        if not refresh:
            hit = qrs.get_cached(
                strategy_id,
                252,
                _INITIAL_NAV,
                src,
                tenor_pair=cache_tenor,
                side_mode=CACHE_SIDE,
                spread_mode=CACHE_SPREAD,
                signal_mode=sig,
            )
            if hit is not None:
                return hit
            mem = _cache.get("vol_rv_map") or {}
            if mem_key in mem:
                return qrs.attach_cache_meta(
                    mem[mem_key],
                    hit=True,
                    source="memory",
                    source_key=src,
                    tenor_pair=cache_tenor,
                    side_mode=CACHE_SIDE,
                    spread_mode=CACHE_SPREAD,
                    signal_mode=sig,
                )

        try:
            payload = run_csi300_vol_rv(
                initial_nav=_INITIAL_NAV,
                tilt=t,
                universe=uni,
                target_otm_delta=target_otm_delta,
                long_delta_threshold=long_delta_threshold,
                hedger_mode=run_hm,
                lambda_tilt=lambda_tilt,
                l_min=l_min,
                l_max=l_max,
                option_fee_pct=option_fee_pct,
                option_slippage_pct=option_slippage_pct,
                futures_fee_pct=futures_fee_pct,
                futures_slippage_pct=futures_slippage_pct,
                run_ablation=bool(ablation),
            )
        except FileNotFoundError as e:
            raise HTTPException(503, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"csi300_vol_rv failed: {e}") from e

        qrs.put_cached(
            strategy_id,
            252,
            _INITIAL_NAV,
            src,
            payload,
            tenor_pair=cache_tenor,
            side_mode=CACHE_SIDE,
            spread_mode=CACHE_SPREAD,
            signal_mode=sig,
        )
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        mem = _cache.get("vol_rv_map") or {}
        mem[mem_key] = clean
        _cache["vol_rv_map"] = mem
        return qrs.attach_cache_meta(
            payload,
            hit=False,
            source="miss",
            source_key=src,
            persisted=True,
            tenor_pair=cache_tenor,
            side_mode=CACHE_SIDE,
            spread_mode=CACHE_SPREAD,
            signal_mode=sig,
        )

    if strategy_id == "cross_calendar_carry":
        # Persist under sentinel PK; knobs live in signal_mode so caches do not collide.
        cs = qrs.cost_sig(
            _SLIPPAGE,
            _FEE,
            open_every_n=int(carry_open_every_n),
            n_roll=int(carry_n_roll),
            budget_frac=float(carry_budget_frac),
            short_min_dte=int(carry_short_min_dte),
        )
        mem_key = (
            strategy_id,
            qrs.CROSS_Z_WINDOW,
            src,
            _INITIAL_NAV,
            qrs.CROSS_TENOR,
            qrs.CROSS_SIDE,
            qrs.CROSS_SPREAD,
            cs,
        )
        if not refresh:
            hit = qrs.get_cached(
                strategy_id,
                qrs.CROSS_Z_WINDOW,
                _INITIAL_NAV,
                src,
                tenor_pair=qrs.CROSS_TENOR,
                side_mode=qrs.CROSS_SIDE,
                spread_mode=qrs.CROSS_SPREAD,
                signal_mode=cs,
            )
            if hit is not None:
                return hit
            mem = _cache.get("carry_map") or {}
            if mem_key in mem:
                return qrs.attach_cache_meta(
                    mem[mem_key],
                    hit=True,
                    source="memory",
                    source_key=src,
                    tenor_pair=qrs.CROSS_TENOR,
                    side_mode=qrs.CROSS_SIDE,
                    spread_mode=qrs.CROSS_SPREAD,
                    signal_mode=cs,
                )

        try:
            payload = run_cross_calendar_carry(
                open_every_n=int(carry_open_every_n),
                n_roll=int(carry_n_roll),
                daily_budget_frac=float(carry_budget_frac),
                short_min_dte=int(carry_short_min_dte),
            )
        except FileNotFoundError as e:
            raise HTTPException(503, str(e)) from e
        except (RuntimeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        qrs.put_cached(
            strategy_id,
            qrs.CROSS_Z_WINDOW,
            _INITIAL_NAV,
            src,
            payload,
            tenor_pair=qrs.CROSS_TENOR,
            side_mode=qrs.CROSS_SIDE,
            spread_mode=qrs.CROSS_SPREAD,
            signal_mode=cs,
        )
        mem = _cache.get("carry_map") or {}
        mem[mem_key] = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        _cache["carry_map"] = mem
        _cache["key"] = mem_key
        _cache["payload"] = mem[mem_key]
        return qrs.attach_cache_meta(
            payload,
            hit=False,
            source="miss",
            source_key=src,
            persisted=True,
            tenor_pair=qrs.CROSS_TENOR,
            side_mode=qrs.CROSS_SIDE,
            spread_mode=qrs.CROSS_SPREAD,
            signal_mode=cs,
        )

    from engine.calendar_vol_strategy import (
        STRATEGY_BY_ID,
        normalize_side_mode,
        normalize_signal_mode,
        normalize_spread_mode,
        normalize_tenor_pair,
        run_by_strategy_id,
    )

    if strategy_id in STRATEGY_BY_ID:
        try:
            tp = normalize_tenor_pair(tenor_pair)
            sm = normalize_side_mode(side_mode)
            spm = normalize_spread_mode(spread_mode)
            sig = normalize_signal_mode(signal_mode)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        use_stop = bool(stop_loss) and sig in ("quantile", "regime2")
        if not use_stop and not refresh:
            hit = qrs.get_cached(
                strategy_id,
                z_window,
                _INITIAL_NAV,
                src,
                tenor_pair=tp,
                side_mode=sm,
                spread_mode=spm,
                signal_mode=sig,
            )
            if hit is not None:
                return hit
        # in-process short cache
        mem_key = (
            strategy_id,
            z_window,
            src,
            _INITIAL_NAV,
            tp,
            sm,
            spm,
            sig,
            use_stop,
            round(float(max_drawdown_target), 4) if use_stop else None,
            round(float(rebound_ratio), 4) if use_stop else None,
        )
        mem = _cache.get("cal_vol_map") or {}
        if not refresh and mem_key in mem:
            return qrs.attach_cache_meta(
                mem[mem_key],
                hit=True,
                source="memory",
                source_key=src,
                tenor_pair=tp,
                side_mode=sm,
                spread_mode=spm,
                signal_mode=sig,
            )

        payload = run_by_strategy_id(
            strategy_id,
            _INITIAL_NAV,
            z_window=z_window,
            tenor_pair=tp,
            side_mode=sm,
            spread_mode=spm,
            signal_mode=sig,
            stop_loss=use_stop,
            max_drawdown_target=max_drawdown_target,
            rebound_ratio=rebound_ratio,
        )
        if not use_stop:
            qrs.put_cached(
                strategy_id,
                z_window,
                _INITIAL_NAV,
                src,
                payload,
                tenor_pair=tp,
                side_mode=sm,
                spread_mode=spm,
                signal_mode=sig,
            )
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        mem[mem_key] = clean
        _cache["cal_vol_map"] = mem
        return qrs.attach_cache_meta(
            payload,
            hit=False,
            source="miss",
            source_key=src,
            persisted=not use_stop,
            tenor_pair=tp,
            side_mode=sm,
            spread_mode=spm,
            signal_mode=sig,
        )

    raise HTTPException(404, f"Unknown strategy: {strategy_id}")


@router.get("/cache")
def cache_status():
    from api.v1 import qis_results_store as qrs

    return {"db": str(qrs.results_db_path(create_parent=False)), "entries": qrs.list_cached()}


@router.get("/backtest/{strategy_id}/holdings.xlsx")
def download_holdings(
    strategy_id: str,
    z_window: int = Query(252, ge=20, le=756),
    tenor_pair: str = Query("3M-6M", description="Calendar tenor pair: 3M-6M or 1M-3M"),
    side_mode: str = Query(
        "both", description="Position side: both | long_only | short_only"
    ),
    spread_mode: str = Query(
        "iv", description="Spread: iv (IV_near−IV_far) | iv2 (IV_near²−IV_far²)"
    ),
    signal_mode: str = Query(
        "quantile",
        description="Signal: quantile | garch_z | regime | regime_momentum | regime_fade | regime2",
    ),
    stop_loss: bool = Query(False),
    max_drawdown_target: float = Query(0.10, gt=0, lt=1),
    rebound_ratio: float = Query(0.90, gt=0, le=1),
    persist: bool = Query(
        True,
        description="Also write Excel under data/exports/ on the server/local disk",
    ),
    universe: str = Query("300", description="Vol RV universe"),
    tilt: str = Query("balanced", description="Vol RV tilt"),
    target_otm_delta: float = Query(0.15, gt=0.01, lt=0.5),
    long_delta_threshold: float = Query(0.05, ge=0.0, le=1.0),
    hedger_mode: str = Query("eod_close"),
    option_fee_pct: float = Query(0.0, ge=0.0, le=0.05),
    option_slippage_pct: float = Query(0.0, ge=0.0, le=0.05),
    futures_fee_pct: float = Query(0.0, ge=0.0, le=0.05),
    futures_slippage_pct: float = Query(0.0, ge=0.0, le=0.05),
):
    """Concrete holdings ledger (date/contract/dir/qty/price) for calendar-vol or Vol RV."""
    if strategy_id == "csi300_vol_rv":
        from engine.csi300_vol_rv_strategy import Csi300VolRvEngine

        eng = Csi300VolRvEngine(
            initial_nav=_INITIAL_NAV,
            universe=universe,
            tilt=tilt,
            target_otm_delta=target_otm_delta,
            long_delta_threshold=long_delta_threshold,
            hedger_mode=hedger_mode,
            option_fee_pct=option_fee_pct,
            option_slippage_pct=option_slippage_pct,
            futures_fee_pct=futures_fee_pct,
            futures_slippage_pct=futures_slippage_pct,
            run_ablation=False,
        )
        try:
            eng.run()
        except FileNotFoundError as e:
            raise HTTPException(503, str(e)) from e
        except (RuntimeError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        disk = None
        if persist:
            from pathlib import Path

            root = Path(__file__).resolve().parents[3]
            disk = eng.write_holdings_excel(
                root / "data" / "exports" / f"vol_rv_{universe}_{tilt}_holdings.xlsx"
            )
        buf = io.BytesIO()
        df = eng.holdings_dataframe()
        with pd.ExcelWriter(buf, engine="openpyxl") as xl:
            df.to_excel(xl, sheet_name="holdings", index=False)
            meta = pd.DataFrame(
                [
                    {"key": "strategy_id", "value": strategy_id},
                    {"key": "universe", "value": universe},
                    {"key": "tilt", "value": tilt},
                    {"key": "target_otm_delta", "value": target_otm_delta},
                    {"key": "long_delta_threshold", "value": long_delta_threshold},
                    {"key": "hedger_mode", "value": hedger_mode},
                    {"key": "price", "value": "option close (mid_price) / futures close"},
                    {"key": "n_rows", "value": len(df)},
                    {"key": "persisted_path", "value": str(disk) if disk else ""},
                ]
            )
            meta.to_excel(xl, sheet_name="meta", index=False)
        buf.seek(0)
        filename = f"csi300_vol_rv_{universe}_{tilt}_holdings.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if disk is not None:
            headers["X-Holdings-Path"] = str(disk)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

    from engine.calendar_vol_strategy import (
        STRATEGY_BY_ID,
        CalendarVolBacktestEngine,
        default_holdings_export_path,
        normalize_side_mode,
        normalize_signal_mode,
        normalize_spread_mode,
        normalize_tenor_pair,
    )

    if strategy_id not in STRATEGY_BY_ID:
        raise HTTPException(
            404,
            f"Holdings export only for calendar-vol or Vol RV; got {strategy_id}",
        )
    try:
        tp = normalize_tenor_pair(tenor_pair)
        sm = normalize_side_mode(side_mode)
        spm = normalize_spread_mode(spread_mode)
        sig = normalize_signal_mode(signal_mode)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    universe = STRATEGY_BY_ID[strategy_id]
    eng = CalendarVolBacktestEngine(
        universe=universe,
        initial_nav=_INITIAL_NAV,
        z_window=z_window,
        tenor_pair=tp,
        side_mode=sm,
        spread_mode=spm,
        signal_mode=sig,
        stop_loss=bool(stop_loss) and sig in ("quantile", "regime2"),
        max_drawdown_target=max_drawdown_target,
        rebound_ratio=rebound_ratio,
    )
    eng.run()
    if persist:
        disk = eng.write_holdings_excel(
            default_holdings_export_path(universe, tp, sm, spm, sig)
        )
    else:
        disk = None

    buf = io.BytesIO()
    df = eng.holdings_dataframe()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        df.to_excel(xl, sheet_name="holdings", index=False)
        meta = pd.DataFrame(
            [
                {"key": "strategy_id", "value": strategy_id},
                {"key": "universe", "value": universe},
                {"key": "tenor_pair", "value": tp},
                {"key": "side_mode", "value": sm},
                {"key": "spread_mode", "value": spm},
                {"key": "signal_mode", "value": sig},
                {"key": "signal_lag", "value": "t-1 signal → t close execution"},
                {"key": "price", "value": "option/futures close (no settle)"},
                {"key": "n_rows", "value": len(df)},
                {"key": "persisted_path", "value": str(disk) if disk else ""},
            ]
        )
        meta.to_excel(xl, sheet_name="meta", index=False)
    buf.seek(0)
    slug_parts = []
    if tp != "3M-6M":
        slug_parts.append(tp.lower().replace("-", ""))
    if sm != "both":
        slug_parts.append(sm)
    if spm != "iv":
        slug_parts.append(spm)
    if sig != "quantile":
        slug_parts.append(sig)
    slug = ("_" + "_".join(slug_parts)) if slug_parts else ""
    filename = f"{strategy_id}{slug}_holdings.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if disk is not None:
        headers["X-Holdings-Path"] = str(disk)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
