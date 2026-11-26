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

CREATE TABLE IF NOT EXISTS qis_position_ledger (
  strategy_id TEXT NOT NULL,
  z_window INTEGER NOT NULL,
  initial_nav REAL NOT NULL,
  tenor_pair TEXT NOT NULL,
  side_mode TEXT NOT NULL,
  spread_mode TEXT NOT NULL,
  signal_mode TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  asset_type TEXT,
  leg_role TEXT,
  multiplier REAL,
  prev_close REAL,
  close_price REAL,
  prev_pos REAL,
  trade_qty REAL,
  avg_trade_price REAL,
  curr_pos REAL,
  holding_pnl REAL,
  trading_pnl REAL,
  total_pnl REAL,
  eod_notional REAL,
  eod_delta REAL,
  eod_gamma REAL,
  eod_vega REAL,
  eod_theta REAL,
  strike REAL,
  cp TEXT,
  expiry TEXT,
  spot REAL,
  PRIMARY KEY (
    strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode,
    trade_date, ticker
  )
);
CREATE INDEX IF NOT EXISTS idx_qis_ledger_date ON qis_position_ledger(strategy_id, signal_mode, trade_date);

CREATE TABLE IF NOT EXISTS qis_trade_blotter (
  strategy_id TEXT NOT NULL,
  z_window INTEGER NOT NULL,
  initial_nav REAL NOT NULL,
  tenor_pair TEXT NOT NULL,
  side_mode TEXT NOT NULL,
  spread_mode TEXT NOT NULL,
  signal_mode TEXT NOT NULL,
  seq INTEGER NOT NULL,
  trade_date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  asset_type TEXT,
  leg_role TEXT,
  action_type TEXT,
  vintage_id TEXT,
  qty REAL,
  trade_price REAL,
  notional REAL,
  multiplier REAL,
  PRIMARY KEY (
    strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode, seq
  )
);
CREATE INDEX IF NOT EXISTS idx_qis_blotter_date ON qis_trade_blotter(strategy_id, signal_mode, trade_date, ticker);
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
        ledger_rows = clean.pop("position_pnl_ledger", None)
        blotter_rows = clean.pop("trade_blotter", None)
        payload.pop("position_pnl_ledger", None)
        payload.pop("trade_blotter", None)
        if isinstance(clean.get("ledger"), dict) and "position_pnl_ledger" not in clean:
            payload["ledger"] = clean["ledger"]
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
        if ledger_rows is not None or blotter_rows is not None:
            _replace_vol_rv_ledger(
                conn,
                strategy_id,
                int(z_window),
                float(initial_nav),
                str(tenor_pair),
                str(side_mode),
                str(spread_mode),
                str(signal_mode),
                list(ledger_rows or []),
                list(blotter_rows or []),
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


_LEDGER_VALUE_COLS = (
    "asset_type",
    "leg_role",
    "multiplier",
    "prev_close",
    "close_price",
    "prev_pos",
    "trade_qty",
    "avg_trade_price",
    "curr_pos",
    "holding_pnl",
    "trading_pnl",
    "total_pnl",
    "eod_notional",
    "eod_delta",
    "eod_gamma",
    "eod_vega",
    "eod_theta",
    "strike",
    "cp",
    "expiry",
    "spot",
)


def _key_args(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    signal_mode: str,
):
    return (
        strategy_id,
        int(z_window),
        float(initial_nav),
        str(tenor_pair),
        str(side_mode),
        str(spread_mode),
        str(signal_mode),
    )


def _replace_vol_rv_ledger(
    conn: sqlite3.Connection,
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    signal_mode: str,
    ledger_rows: list,
    blotter_rows: list,
) -> None:
    key = _key_args(strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode)
    where = (
        "strategy_id=? AND z_window=? AND initial_nav=? AND tenor_pair=? "
        "AND side_mode=? AND spread_mode=? AND signal_mode=?"
    )
    conn.execute(f"DELETE FROM qis_position_ledger WHERE {where}", key)
    conn.execute(f"DELETE FROM qis_trade_blotter WHERE {where}", key)
    if ledger_rows:
        sql = (
            "INSERT INTO qis_position_ledger ("
            "strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode, "
            "trade_date, ticker, " + ", ".join(_LEDGER_VALUE_COLS) + ") VALUES ("
            + ",".join(["?"] * (9 + len(_LEDGER_VALUE_COLS)))
            + ")"
        )
        payload = []
        for rec in ledger_rows:
            payload.append(
                key
                + (
                    str(rec.get("trade_date") or "")[:10],
                    str(rec.get("ticker") or ""),
                )
                + tuple(rec.get(c) for c in _LEDGER_VALUE_COLS)
            )
        conn.executemany(sql, payload)
    if blotter_rows:
        sql = (
            "INSERT INTO qis_trade_blotter ("
            "strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode, "
            "seq, trade_date, ticker, asset_type, leg_role, action_type, vintage_id, qty, trade_price, "
            "notional, multiplier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        payload = []
        for i, rec in enumerate(blotter_rows):
            payload.append(
                key
                + (
                    int(i),
                    str(rec.get("trade_date") or "")[:10],
                    str(rec.get("ticker") or ""),
                    rec.get("asset_type"),
                    rec.get("leg_role"),
                    rec.get("action_type"),
                    rec.get("vintage_id"),
                    rec.get("qty"),
                    rec.get("trade_price"),
                    rec.get("notional"),
                    rec.get("multiplier"),
                )
            )
        conn.executemany(sql, payload)


def _rows_to_dicts(cur: sqlite3.Cursor) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def ledger_dates(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    signal_mode: str,
) -> list:
    conn = connect(create=True)
    try:
        key = _key_args(strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode)
        cur = conn.execute(
            """
            SELECT DISTINCT trade_date FROM qis_position_ledger
             WHERE strategy_id=? AND z_window=? AND initial_nav=? AND tenor_pair=?
               AND side_mode=? AND spread_mode=? AND signal_mode=?
             ORDER BY trade_date
            """,
            key,
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def ledger_day(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    signal_mode: str,
    trade_date: str,
) -> Dict[str, Any]:
    conn = connect(create=True)
    try:
        key = _key_args(
            strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode
        ) + (str(trade_date)[:10],)
        where = (
            "strategy_id=? AND z_window=? AND initial_nav=? AND tenor_pair=? "
            "AND side_mode=? AND spread_mode=? AND signal_mode=? AND trade_date=?"
        )
        rows = _rows_to_dicts(
            conn.execute(
                f"SELECT * FROM qis_position_ledger WHERE {where} ORDER BY ticker",
                key,
            )
        )
        blotter = _rows_to_dicts(
            conn.execute(
                f"SELECT * FROM qis_trade_blotter WHERE {where} ORDER BY seq",
                key,
            )
        )
        return {"rows": rows, "blotter": blotter}
    finally:
        conn.close()


def ledger_aggregate(
    strategy_id: str,
    z_window: int,
    initial_nav: float,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    signal_mode: str,
) -> list:
    conn = connect(create=True)
    try:
        key = _key_args(strategy_id, z_window, initial_nav, tenor_pair, side_mode, spread_mode, signal_mode)
        cur = conn.execute(
            """
            WITH keyed AS (
              SELECT * FROM qis_position_ledger
               WHERE strategy_id=? AND z_window=? AND initial_nav=? AND tenor_pair=?
                 AND side_mode=? AND spread_mode=? AND signal_mode=?
            ),
            sums AS (
              SELECT ticker,
                     SUM(holding_pnl) AS holding_pnl,
                     SUM(trading_pnl) AS trading_pnl,
                     SUM(total_pnl) AS total_pnl,
                     SUM(CASE WHEN ABS(COALESCE(trade_qty, 0)) > 1e-12 THEN 1 ELSE 0 END) AS trade_days
                FROM keyed
               GROUP BY ticker
            ),
            last AS (
              SELECT ticker, asset_type, leg_role, curr_pos, close_price, multiplier, expiry, cp, strike,
                     ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS rn
                FROM keyed
            )
            SELECT s.ticker, l.asset_type, l.leg_role, l.multiplier, l.curr_pos, l.close_price,
                   l.strike, l.cp, l.expiry,
                   s.holding_pnl, s.trading_pnl, s.total_pnl, s.trade_days
              FROM sums s
              JOIN last l ON l.ticker = s.ticker AND l.rn = 1
             ORDER BY s.total_pnl
            """,
            key,
        )
        return _rows_to_dicts(cur)
    finally:
        conn.close()
