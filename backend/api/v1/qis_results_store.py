"""Persistent SQLite cache for QIS backtest JSON payloads."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[3]  # CLSA_EQD
REPO = ROOT.parent

_DB_CANDIDATES = [
    ROOT / "data" / "qis_results.sqlite",
    Path("/app/data/qis_results.sqlite"),
]

_DEFAULT_TENOR = "3M-6M"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS qis_backtest_cache (
  strategy_id TEXT NOT NULL,
  z_window INTEGER NOT NULL,
  initial_nav REAL NOT NULL,
  tenor_pair TEXT NOT NULL DEFAULT '3M-6M',
  source_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair)
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


def _migrate(conn: sqlite3.Connection) -> None:
    """Add tenor_pair to legacy PK (strategy_id, z_window, initial_nav) if needed."""
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(qis_backtest_cache)").fetchall()
    }
    if not cols:
        return
    if "tenor_pair" in cols:
        return
    conn.executescript(
        """
        CREATE TABLE qis_backtest_cache_v2 (
          strategy_id TEXT NOT NULL,
          z_window INTEGER NOT NULL,
          initial_nav REAL NOT NULL,
          tenor_pair TEXT NOT NULL DEFAULT '3M-6M',
          source_key TEXT NOT NULL,
          payload TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair)
        );
        INSERT INTO qis_backtest_cache_v2
          (strategy_id, z_window, initial_nav, tenor_pair, source_key, payload, updated_at)
        SELECT strategy_id, z_window, initial_nav, '3M-6M', source_key, payload, updated_at
          FROM qis_backtest_cache;
        DROP TABLE qis_backtest_cache;
        ALTER TABLE qis_backtest_cache_v2 RENAME TO qis_backtest_cache;
        CREATE INDEX IF NOT EXISTS idx_qis_cache_src ON qis_backtest_cache(source_key);
        """
    )
    conn.commit()


def connect(create: bool = True) -> sqlite3.Connection:
    path = results_db_path(create_parent=create)
    conn = sqlite3.connect(str(path))
    if create:
        conn.executescript(_SCHEMA)
        conn.commit()
        _migrate(conn)
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
    tenor_pair: str = _DEFAULT_TENOR,
) -> Optional[Dict[str, Any]]:
    path = results_db_path(create_parent=False)
    if not path.exists():
        return None
    conn = connect(create=True)
    try:
        row = conn.execute(
            """
            SELECT payload, source_key FROM qis_backtest_cache
             WHERE strategy_id=? AND z_window=? AND initial_nav=? AND tenor_pair=?
            """,
            (strategy_id, int(z_window), float(initial_nav), str(tenor_pair)),
        ).fetchone()
        if not row:
            return None
        payload_s, stored_src = row
        if stored_src != src_key:
            return None
        data = json.loads(payload_s)
        data["_cache"] = {
            "hit": True,
            "source_key": stored_src,
            "db": str(path),
            "tenor_pair": tenor_pair,
        }
        return data
    finally:
        conn.close()


def put_cached(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    src_key: str,
    payload: Dict[str, Any],
    tenor_pair: str = _DEFAULT_TENOR,
) -> Path:
    path = results_db_path(create_parent=True)
    conn = connect(create=True)
    try:
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        conn.execute(
            """
            INSERT INTO qis_backtest_cache
              (strategy_id, z_window, initial_nav, tenor_pair, source_key, payload, updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(strategy_id, z_window, initial_nav, tenor_pair) DO UPDATE SET
              source_key=excluded.source_key,
              payload=excluded.payload,
              updated_at=excluded.updated_at
            """,
            (
                strategy_id,
                int(z_window),
                float(initial_nav),
                str(tenor_pair),
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
            SELECT strategy_id, z_window, initial_nav, tenor_pair, source_key, updated_at,
                   length(payload) AS bytes
              FROM qis_backtest_cache
             ORDER BY strategy_id, tenor_pair, z_window
            """
        ).fetchall()
        return [
            {
                "strategy_id": r[0],
                "z_window": r[1],
                "initial_nav": r[2],
                "tenor_pair": r[3],
                "source_key": r[4],
                "updated_at": r[5],
                "bytes": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()
