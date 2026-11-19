#!/usr/bin/env python3
"""Precompute CSI 300 Vol RV (3 tilts) into data/qis_results.sqlite."""
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
    CACHE_TENOR,
    STYLE_PRESETS,
    run_csi300_vol_rv,
    vol_rv_cache_sig,
)

INITIAL_NAV = 100_000_000.0


def main() -> None:
    tilts = list(STYLE_PRESETS.keys())
    args = [a for a in sys.argv[1:] if a]
    if args:
        tilts = [a for a in args if a in STYLE_PRESETS]
        if not tilts:
            print(f"unknown tilts {args}; choose {list(STYLE_PRESETS)}", file=sys.stderr)
            sys.exit(2)

    db = st._equity_db()
    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)
    print(f"source_key={src} tilts={tilts}", flush=True)

    for tilt in tilts:
        print(f"=== csi300_vol_rv tilt={tilt} ===", flush=True)
        payload = run_csi300_vol_rv(
            initial_nav=INITIAL_NAV,
            tilt=tilt,
            run_ablation=True,
            hedger_mode="eod_close",
        )
        sig = vol_rv_cache_sig(tilt, 0.15, 0.05, "eod_close")
        path = qrs.put_cached(
            "csi300_vol_rv",
            252,
            INITIAL_NAV,
            src,
            payload,
            tenor_pair=CACHE_TENOR,
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
