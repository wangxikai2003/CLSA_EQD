#!/usr/bin/env python3
"""Precompute Vol RV (universes × tilts) into data/qis_results.sqlite."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from api.v1 import qis_results_store as qrs  # noqa: E402
from api.v1 import synth_tenors as st  # noqa: E402
from engine.csi300_vol_rv_strategy import (  # noqa: E402
    CACHE_SIDE,
    CACHE_SPREAD,
    STYLE_PRESETS,
    VOL_RV_UNIVERSES,
    cache_tenor_for,
    run_csi300_vol_rv,
    vol_rv_cache_sig,
)

INITIAL_NAV = 100_000_000.0


def main() -> None:
    universes = list(VOL_RV_UNIVERSES.keys())
    tilts = list(STYLE_PRESETS.keys())
    args = [a for a in sys.argv[1:] if a]
    if args:
        want_u = []
        for a in args:
            au = a.upper() if a.upper() in ("AU", "SC") else a
            if au in VOL_RV_UNIVERSES:
                want_u.append(au)
        want_t = [a for a in args if a in STYLE_PRESETS]
        if want_u:
            universes = want_u
        if want_t:
            tilts = want_t
        leftover = [a for a in args if a not in want_u and a not in want_t]
        if leftover:
            print(f"unknown args {leftover}; universes={list(VOL_RV_UNIVERSES)} tilts={list(STYLE_PRESETS)}", file=sys.stderr)
            sys.exit(2)

    db = st._equity_db()
    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)
    print(f"source_key={src} universes={universes} tilts={tilts}", flush=True)

    for uni in universes:
        for tilt in tilts:
            print(f"=== csi300_vol_rv universe={uni} tilt={tilt} ===", flush=True)
            try:
                payload = run_csi300_vol_rv(
                    initial_nav=INITIAL_NAV,
                    universe=uni,
                    tilt=tilt,
                    run_ablation=True,
                    hedger_mode="eod_close",
                )
            except Exception as e:
                print(f"  FAILED: {e}", flush=True)
                raise
            sig = vol_rv_cache_sig(tilt, 0.15, 0.05, "eod_close", universe=uni)
            tenor = cache_tenor_for(uni)
            path = qrs.put_cached(
                "csi300_vol_rv",
                252,
                INITIAL_NAV,
                src,
                payload,
                tenor_pair=tenor,
                side_mode=CACHE_SIDE,
                spread_mode=CACHE_SPREAD,
                signal_mode=sig,
            )
            s = payload["summary"]
            print(
                f"  start={s['start']} end={s['end']} mult={s['final_nav_multiple']} "
                f"mean_L={payload['params'].get('mean_leverage')} → {path}",
                flush=True,
            )
    print("entries:", len(qrs.list_cached()))


if __name__ == "__main__":
    main()
