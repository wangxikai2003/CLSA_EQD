#!/usr/bin/env python3
"""Precompute Equity Calendar Vol QIS results into data/qis_results.sqlite."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from api.v1 import qis_results_store as qrs  # noqa: E402
from api.v1 import synth_tenors as st  # noqa: E402
from engine.calendar_vol_strategy import (  # noqa: E402
    DEFAULT_Z_WINDOW,
    SIDE_MODES,
    SIGNAL_MODES,
    SPREAD_MODES,
    TENOR_PAIRS,
    UNIVERSES,
    run_calendar_vol,
)

INITIAL_NAV = 100_000_000.0


def main() -> None:
    z_window = DEFAULT_Z_WINDOW
    only_signal = None
    args = [a for a in sys.argv[1:] if a]
    for a in args:
        if a in SIGNAL_MODES:
            only_signal = a
        else:
            z_window = int(a)
    db = st._equity_db()
    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)
    modes = (only_signal,) if only_signal else SIGNAL_MODES
    print(f"source_key={src} z_window={z_window} signal_modes={list(modes)}")
    for key, u in UNIVERSES.items():
        for tp in TENOR_PAIRS:
            for sm in SIDE_MODES:
                for spm in SPREAD_MODES:
                    for sig in modes:
                        print(
                            f"=== {key} {u['strategy_id']} tenor={tp} side={sm} "
                            f"spread={spm} signal={sig} ===",
                            flush=True,
                        )
                        payload = run_calendar_vol(
                            key,
                            initial_nav=INITIAL_NAV,
                            z_window=z_window,
                            tenor_pair=tp,
                            side_mode=sm,
                            spread_mode=spm,
                            signal_mode=sig,
                        )
                        path = qrs.put_cached(
                            u["strategy_id"],
                            z_window,
                            INITIAL_NAV,
                            src,
                            payload,
                            tenor_pair=tp,
                            side_mode=sm,
                            spread_mode=spm,
                            signal_mode=sig,
                        )
                        print(
                            f"  start={payload['summary']['start']} end={payload['summary']['end']} "
                            f"mult={payload['summary']['final_nav_multiple']} → {path}",
                            flush=True,
                        )
    print("entries:", len(qrs.list_cached()))


if __name__ == "__main__":
    main()
