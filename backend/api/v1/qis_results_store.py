"""Persistent SQLite cache for QIS backtest JSON payloads."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]  # CLSA_EQD
REPO = ROOT.parent

_DB_CANDIDATES = [
    ROOT / "data" / "qis_results.sqlite",
    Path("/app/data/qis_results.sqlite"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS qis_backtest_cache (
  strategy_id TEXT NOT NULL,
  z_window INTEGER NOT NULL,
  initial_nav REAL NOT NULL,
  source_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (strategy_id, z_window, initial_nav)
);
CREATE INDEX IF NOT EXISTS idx_qis_cache_src ON qis_backtest_cache(source_key);
"""


def results_db_path(create_parent: bool = True) -> Path:
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    # prefer local app data dir
    p = _DB_CANDIDATES[0]
    if create_parent:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect(create: bool = True) -> sqlite3.Connection:
    path = results_db_path(create_parent=create)
    conn = sqlite3.connect(str(path))
    if create:
        conn.executescript(_SCHEMA)
        conn.commit()
    return conn


def source_key(equity_db: Path, spot: Optional[Path]) -> str:
    parts = [f"db:{equity_db.stat().st_mtime_ns}"]
    if spot and spot.exists():
        parts.append(f"spot:{spot.stat().st_mtime_ns}")
    return "|".join(parts)


def get_cached(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    src_key: str,
) -> Optional[Dict[str, Any]]:
    path = results_db_path(create_parent=False)
    if not path.exists():
        return None
    conn = connect(create=True)
    try:
        row = conn.execute(
            """
            SELECT payload, source_key FROM qis_backtest_cache
             WHERE strategy_id=? AND z_window=? AND initial_nav=?
            """,
            (strategy_id, int(z_window), float(initial_nav)),
        ).fetchone()
        if not row:
            return None
        payload_s, stored_src = row
        if stored_src != src_key:
            return None
        data = json.loads(payload_s)
        data["_cache"] = {"hit": True, "source_key": stored_src, "db": str(path)}
        return data
    finally:
        conn.close()


def put_cached(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    src_key: str,
    payload: Dict[str, Any],
) -> Path:
    path = results_db_path(create_parent=True)
    conn = connect(create=True)
    try:
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        conn.execute(
            """
            INSERT INTO qis_backtest_cache
              (strategy_id, z_window, initial_nav, source_key, payload, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(strategy_id, z_window, initial_nav) DO UPDATE SET
              source_key=excluded.source_key,
              payload=excluded.payload,
              updated_at=excluded.updated_at
            """,
            (
                strategy_id,
                int(z_window),
                float(initial_nav),
                src_key,
                json.dumps(clean, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def list_cached() -> list:
    path = results_db_path(create_parent=False)
    if not path.exists():
        return []
    conn = connect(create=True)
    try:
        rows = conn.execute(
            """
            SELECT strategy_id, z_window, initial_nav, source_key, updated_at,
                   length(payload) AS bytes
              FROM qis_backtest_cache
             ORDER BY strategy_id, z_window
            """
        ).fetchall()
        return [
            {
                "strategy_id": r[0],
                "z_window": r[1],
                "initial_nav": r[2],
                "source_key": r[3],
                "updated_at": r[4],
                "bytes": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()
