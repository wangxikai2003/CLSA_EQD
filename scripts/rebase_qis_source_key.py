#!/usr/bin/env python3
"""Point all qis_results.sqlite cache rows at the current equity-db / spot mtimes.

Used in Docker after COPY so shipped garch_z (and any other) payloads stay cache-hits
without a full rebuild.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from api.v1 import qis_results_store as qrs  # noqa: E402
from api.v1 import synth_tenors as st  # noqa: E402


def main() -> None:
    db = st._equity_db()
    spot = next((p for p in st._SPOT_CANDIDATES if p.exists()), None)
    src = qrs.source_key(db, spot)
    conn = qrs.connect(create=True)
    try:
        cur = conn.execute("UPDATE qis_backtest_cache SET source_key=?", (src,))
        conn.commit()
        n = cur.rowcount
    finally:
        conn.close()
    print(f"rebased source_key={src} rows={n}")


if __name__ == "__main__":
    main()
