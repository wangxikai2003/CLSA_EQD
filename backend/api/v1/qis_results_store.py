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

try:
    from data_paths import qis_results as _dp_qis
except Exception:  # pragma: no cover
    _dp_qis = None  # type: ignore

_DEFAULT_TENOR = "3M-6M"
_DEFAULT_SIDE = "both"
_DEFAULT_SPREAD = "iv"
_DEFAULT_SIGNAL = "quantile"

# Cross Calendar Carry sentinel PK fields (cost + engine + knobs in signal_mode).
CROSS_Z_WINDOW = 0
CROSS_TENOR = "listed-roll-v2"
CROSS_SIDE = "n/a"
CROSS_SPREAD = "n/a"
CROSS_ENGINE = "listed-roll-v2"
CROSS_OPEN_EVERY_N = 1
CROSS_N_ROLL = 2
CROSS_BUDGET_FRAC = 1.0 / 21.0
CROSS_SHORT_MIN_DTE = 5


def cost_sig(
    slippage: float = 0.0,
    fee: float = 0.0,
    engine: str = CROSS_ENGINE,
    *,
    open_every_n: int = CROSS_OPEN_EVERY_N,
    n_roll: int = CROSS_N_ROLL,
    budget_frac: float = CROSS_BUDGET_FRAC,
    short_min_dte: int = CROSS_SHORT_MIN_DTE,
) -> str:
    """Stable carry fingerprint; used as signal_mode so knobs do not collide."""
    return (
        f"{float(slippage):.8g}|{float(fee):.8g}|{engine}"
        f"|oe{int(open_every_n)}|nr{int(n_roll)}"
        f"|bf{float(budget_frac):.6g}|dte{int(short_min_dte)}"
    )


def attach_cache_meta(
    payload: Dict[str, Any],
    *,
    hit: bool,
    source: str,
    source_key: str,
    persisted: bool = False,
    tenor_pair: str = _DEFAULT_TENOR,
    side_mode: str = _DEFAULT_SIDE,
    spread_mode: str = _DEFAULT_SPREAD,
    signal_mode: str = _DEFAULT_SIGNAL,
    db: Optional[str] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "hit": bool(hit),
        "source": source,
        "source_key": source_key,
        "tenor_pair": tenor_pair,
        "side_mode": side_mode,
        "spread_mode": spread_mode,
        "signal_mode": signal_mode,
    }
    if persisted:
        meta["persisted"] = True
    if db:
        meta["db"] = db
    return {**payload, "_cache": meta}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS qis_backtest_cache (
  strategy_id TEXT NOT NULL,
  z_window INTEGER NOT NULL,
  initial_nav REAL NOT NULL,
  tenor_pair TEXT NOT NULL DEFAULT '3M-6M',
  side_mode TEXT NOT NULL DEFAULT 'both',
  spread_mode TEXT NOT NULL DEFAULT 'iv',
  signal_mode TEXT NOT NULL DEFAULT 'quantile',
  source_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode)
);
CREATE INDEX IF NOT EXISTS idx_qis_cache_src ON qis_backtest_cache(source_key);
"""


def results_db_path(create_parent: bool = True) -> Path:
    if _dp_qis is not None:
        try:
            p = _dp_qis()
            if create_parent:
                p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    # prefer local app data dir
    p = _DB_CANDIDATES[0]
    if create_parent:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _migrate(conn: sqlite3.Connection) -> None:
    """Evolve PK: add tenor_pair → side_mode → spread_mode → signal_mode when missing."""
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(qis_backtest_cache)").fetchall()
    }
    if not cols:
        return
    if "tenor_pair" not in cols:
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
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(qis_backtest_cache)").fetchall()
        }
    if "side_mode" not in cols:
        conn.executescript(
            """
            CREATE TABLE qis_backtest_cache_v3 (
              strategy_id TEXT NOT NULL,
              z_window INTEGER NOT NULL,
              initial_nav REAL NOT NULL,
              tenor_pair TEXT NOT NULL DEFAULT '3M-6M',
              side_mode TEXT NOT NULL DEFAULT 'both',
              source_key TEXT NOT NULL,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair, side_mode)
            );
            INSERT INTO qis_backtest_cache_v3
              (strategy_id, z_window, initial_nav, tenor_pair, side_mode,
               source_key, payload, updated_at)
            SELECT strategy_id, z_window, initial_nav, tenor_pair, 'both',
                   source_key, payload, updated_at
              FROM qis_backtest_cache;
            DROP TABLE qis_backtest_cache;
            ALTER TABLE qis_backtest_cache_v3 RENAME TO qis_backtest_cache;
            CREATE INDEX IF NOT EXISTS idx_qis_cache_src ON qis_backtest_cache(source_key);
            """
        )
        conn.commit()
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(qis_backtest_cache)").fetchall()
        }
    if "spread_mode" not in cols:
        conn.executescript(
            """
            CREATE TABLE qis_backtest_cache_v4 (
              strategy_id TEXT NOT NULL,
              z_window INTEGER NOT NULL,
              initial_nav REAL NOT NULL,
              tenor_pair TEXT NOT NULL DEFAULT '3M-6M',
              side_mode TEXT NOT NULL DEFAULT 'both',
              spread_mode TEXT NOT NULL DEFAULT 'iv',
              source_key TEXT NOT NULL,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode)
            );
            INSERT INTO qis_backtest_cache_v4
              (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode,
               source_key, payload, updated_at)
            SELECT strategy_id, z_window, initial_nav, tenor_pair, side_mode, 'iv',
                   source_key, payload, updated_at
              FROM qis_backtest_cache;
            DROP TABLE qis_backtest_cache;
            ALTER TABLE qis_backtest_cache_v4 RENAME TO qis_backtest_cache;
            CREATE INDEX IF NOT EXISTS idx_qis_cache_src ON qis_backtest_cache(source_key);
            """
        )
        conn.commit()
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(qis_backtest_cache)").fetchall()
        }
    if "signal_mode" in cols:
        return
    conn.executescript(
        """
        CREATE TABLE qis_backtest_cache_v5 (
          strategy_id TEXT NOT NULL,
          z_window INTEGER NOT NULL,
          initial_nav REAL NOT NULL,
          tenor_pair TEXT NOT NULL DEFAULT '3M-6M',
          side_mode TEXT NOT NULL DEFAULT 'both',
          spread_mode TEXT NOT NULL DEFAULT 'iv',
          signal_mode TEXT NOT NULL DEFAULT 'quantile',
          source_key TEXT NOT NULL,
          payload TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode)
        );
        INSERT INTO qis_backtest_cache_v5
          (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode,
           source_key, payload, updated_at)
        SELECT strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, 'quantile',
               source_key, payload, updated_at
          FROM qis_backtest_cache;
        DROP TABLE qis_backtest_cache;
        ALTER TABLE qis_backtest_cache_v5 RENAME TO qis_backtest_cache;
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
    side_mode: str = _DEFAULT_SIDE,
    spread_mode: str = _DEFAULT_SPREAD,
    signal_mode: str = _DEFAULT_SIGNAL,
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
               AND tenor_pair=? AND side_mode=? AND spread_mode=? AND signal_mode=?
            """,
            (
                strategy_id,
                int(z_window),
                float(initial_nav),
                str(tenor_pair),
                str(side_mode),
                str(spread_mode),
                str(signal_mode),
            ),
        ).fetchone()
        if not row:
            return None
        payload_s, stored_src = row
        if stored_src != src_key:
            return None
        data = json.loads(payload_s)
        return attach_cache_meta(
            data,
            hit=True,
            source="db",
            source_key=stored_src,
            tenor_pair=tenor_pair,
            side_mode=side_mode,
            spread_mode=spread_mode,
            signal_mode=signal_mode,
            db=str(path),
        )
    finally:
        conn.close()


def put_cached(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    src_key: str,
    payload: Dict[str, Any],
    tenor_pair: str = _DEFAULT_TENOR,
    side_mode: str = _DEFAULT_SIDE,
    spread_mode: str = _DEFAULT_SPREAD,
    signal_mode: str = _DEFAULT_SIGNAL,
) -> Path:
    path = results_db_path(create_parent=True)
    conn = connect(create=True)
    try:
        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        conn.execute(
            """
            INSERT INTO qis_backtest_cache
              (strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode,
               source_key, payload, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode)
            DO UPDATE SET
              source_key=excluded.source_key,
              payload=excluded.payload,
              updated_at=excluded.updated_at
            """,
            (
                strategy_id,
                int(z_window),
                float(initial_nav),
                str(tenor_pair),
                str(side_mode),
                str(spread_mode),
                str(signal_mode),
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
            SELECT strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode,
                   source_key, updated_at, length(payload) AS bytes
              FROM qis_backtest_cache
             ORDER BY strategy_id, tenor_pair, side_mode, spread_mode, signal_mode, z_window
            """
        ).fetchall()
        return [
            {
                "strategy_id": r[0],
                "z_window": r[1],
                "initial_nav": r[2],
                "tenor_pair": r[3],
                "side_mode": r[4],
                "spread_mode": r[5],
                "signal_mode": r[6],
                "source_key": r[7],
                "updated_at": r[8],
                "bytes": r[9],
            }
            for r in rows
        ]
    finally:
        conn.close()
