#!/usr/bin/env python3
"""Precompute QIS backtest results into data/qis_results.sqlite."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from api.v1 import qis_results_store as qrs  # noqa: E402
from api.v1 import synth_tenors as st  # noqa: E402
from api.v1.qis_backtest import (  # noqa: E402
    _FEE,
    _INITIAL_NAV,
    _SLIPPAGE,
    run_cross_calendar_carry,
)
from engine.calendar_vol_strategy import (  # noqa: E402
    DEFAULT_Z_WINDOW,
    SIDE_MODES,
    SPREAD_MODES,
    TENOR_PAIRS,
    UNIVERSES,
    run_calendar_vol,
)

INITIAL_NAV = _INITIAL_NAV


def main() -> None:
    z_window = DEFAULT_Z_WINDOW
    if len(sys.argv) > 1:
        z_window = int(sys.argv[1])
    db = st._equity_db()
    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)
    print(f"source_key={src} z_window={z_window}")

    print("=== cross_calendar_carry ===", flush=True)
    carry = run_cross_calendar_carry(
        initial_nav=INITIAL_NAV, slippage=_SLIPPAGE, fee=_FEE
    )
    path = qrs.put_cached(
        "cross_calendar_carry",
        qrs.CROSS_Z_WINDOW,
        INITIAL_NAV,
        src,
        carry,
        tenor_pair=qrs.CROSS_TENOR,
        side_mode=qrs.CROSS_SIDE,
        spread_mode=qrs.CROSS_SPREAD,
        cost_sig_value=qrs.cost_sig(_SLIPPAGE, _FEE),
    )
    print(
        f"  start={carry['summary']['start']} end={carry['summary']['end']} "
        f"mult={carry['summary']['final_nav_multiple']} → {path}",
        flush=True,
    )

    for key, u in UNIVERSES.items():
        for tp in TENOR_PAIRS:
            for sm in SIDE_MODES:
                for spm in SPREAD_MODES:
                    print(
                        f"=== {key} {u['strategy_id']} tenor={tp} side={sm} spread={spm} ===",
                        flush=True,
                    )
                    payload = run_calendar_vol(
                        key,
                        initial_nav=INITIAL_NAV,
                        z_window=z_window,
                        tenor_pair=tp,
                        side_mode=sm,
                        spread_mode=spm,
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
                        cost_sig_value=qrs.cost_sig(0.0, 0.0),
                    )
                    print(
                        f"  start={payload['summary']['start']} end={payload['summary']['end']} "
                        f"mult={payload['summary']['final_nav_multiple']} → {path}",
                        flush=True,
                    )
    print("entries:", qrs.list_cached())


if __name__ == "__main__":
    main()
