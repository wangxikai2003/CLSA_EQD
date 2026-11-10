#!/usr/bin/env python3
"""Precompute Equity 3-6 Calendar Vol QIS results into data/qis_results.sqlite."""
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
    STRATEGIES,
    run_by_strategy_id,
)

INITIAL_NAV = 100_000_000.0


def main() -> None:
    z_window = DEFAULT_Z_WINDOW
    if len(sys.argv) > 1:
        z_window = int(sys.argv[1])
    db = st._equity_db()
    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)
    print(f"source_key={src} z_window={z_window}")
    # Stable order: quantile then zscore, 50/300/1000
    mode_rank = {"quantile": 0, "zscore": 1}
    uni_rank = {"50": 0, "300": 1, "1000": 2}
    for sid, u in sorted(
        STRATEGIES.items(),
        key=lambda kv: (
            mode_rank.get(kv[1]["signal_mode"], 9),
            uni_rank.get(kv[1]["universe"], 9),
        ),
    ):
        print(f"=== {sid} ({u['signal_mode']}/{u['universe']}) ===", flush=True)
        payload = run_by_strategy_id(sid, initial_nav=INITIAL_NAV, z_window=z_window)
        path = qrs.put_cached(sid, z_window, INITIAL_NAV, src, payload)
        print(
            f"  start={payload['summary']['start']} end={payload['summary']['end']} "
            f"mult={payload['summary']['final_nav_multiple']} → {path}",
            flush=True,
        )
    print("entries:", qrs.list_cached())


if __name__ == "__main__":
    main()
