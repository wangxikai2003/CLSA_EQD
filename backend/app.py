"""
FastAPI backend for CFFEX vol DB + JW SVI surfaces + /api/v1 quantitative kernel.
Serve API under /api and (optionally) built frontend from ../CLSA_EQD/dist.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_DB_CANDIDATES = [
    ROOT / "data" / "cffex_vol.sqlite",
    Path("/app/data/cffex_vol.sqlite"),
    Path("/tmp/cffex_vol.sqlite"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p.exists()), _DB_CANDIDATES[0])
DIST = ROOT / "dist"

app = FastAPI(title="CLSA EQD Vol Surface API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from api.middleware import TimingMiddleware
    from api.v1 import calibration as calib_routes
    from api.v1 import market as market_routes
    from api.v1 import pricing as pricing_routes
    from api.v1 import stream as stream_routes

    app.add_middleware(TimingMiddleware)
    app.include_router(pricing_routes.router)
    app.include_router(calib_routes.router)
    app.include_router(stream_routes.router)
    app.include_router(market_routes.router)
except Exception as _v1_err:  # pragma: no cover
    import logging

    logging.getLogger("clsa").warning("api v1 routers not loaded: %s", _v1_err)


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, f"Database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def raw_svi_w(k: float, a: float, b: float, rho: float, m: float, sigma: float) -> float:
    x = k - m
    return a + b * (rho * x + math.sqrt(x * x + sigma * sigma))


def slice_iv(k: float, t: float, row: sqlite3.Row) -> float:
    w = raw_svi_w(k, row["a"], row["b"], row["rho"], row["m"], row["sigma"])
    return math.sqrt(max(w, 1e-12) / max(t, 1e-8))


@app.get("/api/health")
def health() -> Dict[str, Any]:
    ok = DB_PATH.exists()
    return {"ok": ok, "db": str(DB_PATH), "size_mb": round(DB_PATH.stat().st_size / 1e6, 2) if ok else 0}


def _meta_has_side(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(surface_meta)").fetchall()}
    return "side" in cols


def _norm_side(side: Optional[str]) -> str:
    s = (side or "otm").lower()
    if s not in ("otm", "call", "put"):
        raise HTTPException(400, "side must be otm|call|put")
    return s


@app.get("/api/products")
def products() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT product, name, fut_prefix FROM products ORDER BY product").fetchall()
        has_side = _meta_has_side(conn)
        out = []
        for r in rows:
            if has_side:
                meta = conn.execute(
                    """SELECT COUNT(*) n, MIN(trade_date) start, MAX(trade_date) end
                         FROM surface_meta WHERE product=? AND side='otm'""",
                    (r["product"],),
                ).fetchone()
            else:
                meta = conn.execute(
                    "SELECT COUNT(*) n, MIN(trade_date) start, MAX(trade_date) end FROM surface_meta WHERE product=?",
                    (r["product"],),
                ).fetchone()
            out.append(
                {
                    "product": r["product"],
                    "name": r["name"],
                    "fut_prefix": r["fut_prefix"],
                    "n_dates": meta["n"],
                    "start": meta["start"],
                    "end": meta["end"],
                    "sides": ["otm", "call", "put"] if has_side else ["otm"],
                }
            )
        return out


@app.get("/api/dates/{product}")
def dates(
    product: str,
    side: str = Query("otm", description="otm|call|put"),
) -> List[str]:
    side = _norm_side(side)
    with connect() as conn:
        if _meta_has_side(conn):
            rows = conn.execute(
                """SELECT trade_date FROM surface_meta
                    WHERE product=? AND side=? AND n_slices>0 ORDER BY trade_date""",
                (product.upper(), side),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT trade_date FROM surface_meta WHERE product=? AND n_slices>0 ORDER BY trade_date",
                (product.upper(),),
            ).fetchall()
        return [r["trade_date"] for r in rows]


def _resolve_date(
    conn: sqlite3.Connection, product: str, date: Optional[str], side: str = "otm"
) -> str:
    if date:
        return date
    if _meta_has_side(conn):
        row = conn.execute(
            """SELECT trade_date FROM surface_meta
                WHERE product=? AND side=? AND n_slices>0
                ORDER BY trade_date DESC LIMIT 1""",
            (product, side),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT trade_date FROM surface_meta WHERE product=? AND n_slices>0 ORDER BY trade_date DESC LIMIT 1",
            (product,),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"No surface for {product}")
    return row["trade_date"]


def _load_matrix(
    conn: sqlite3.Connection, product: str, date: str, side: str = "otm"
) -> Optional[Dict[str, Any]]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(svi_matrices)").fetchall()}
    if "side" in cols:
        row = conn.execute(
            """SELECT matrix_json, n_rows, n_cols, spot FROM svi_matrices
                WHERE product=? AND trade_date=? AND side=?""",
            (product, date, side),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT matrix_json, n_rows, n_cols, spot FROM svi_matrices WHERE product=? AND trade_date=?",
            (product, date),
        ).fetchone()
    if not row:
        return None
    mat = json.loads(row["matrix_json"])
    mat["n_rows"] = row["n_rows"]
    mat["n_cols"] = row["n_cols"]
    return mat


@app.get("/api/matrix/{product}")
def matrix(
    product: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD; default = latest"),
    side: str = Query("otm", description="otm|call|put"),
) -> Dict[str, Any]:
    """Precomputed JW-SVI IV matrix: rows=ttm, cols=moneyness/strikes."""
    product = product.upper()
    side = _norm_side(side)
    with connect() as conn:
        date = _resolve_date(conn, product, date, side)
        mat = _load_matrix(conn, product, date, side)
        if not mat:
            raise HTTPException(404, f"No matrix for {product} {date} {side}")
        return {
            "product": product,
            "trade_date": date,
            "side": side,
            "model": "JW-SVI",
            "matrix": mat,
        }


@app.get("/api/surface/{product}")
def surface(
    product: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD; default = latest"),
    side: str = Query("otm", description="otm|call|put"),
) -> Dict[str, Any]:
    product = product.upper()
    side = _norm_side(side)
    with connect() as conn:
        date = _resolve_date(conn, product, date, side)
        has_side = _meta_has_side(conn)

        if has_side:
            meta = conn.execute(
                "SELECT * FROM surface_meta WHERE product=? AND trade_date=? AND side=?",
                (product, date, side),
            ).fetchone()
            slices = conn.execute(
                "SELECT * FROM svi_slices WHERE product=? AND trade_date=? AND side=? ORDER BY ttm",
                (product, date, side),
            ).fetchall()
        else:
            meta = conn.execute(
                "SELECT * FROM surface_meta WHERE product=? AND trade_date=?",
                (product, date),
            ).fetchone()
            slices = conn.execute(
                "SELECT * FROM svi_slices WHERE product=? AND trade_date=? ORDER BY ttm",
                (product, date),
            ).fetchall()
        if not meta:
            raise HTTPException(404, f"No meta for {product} {date} {side}")
        if not slices:
            raise HTTPException(404, f"No SVI slices for {product} {date} {side}")

        iv_sql = """
            SELECT strike, ttm, iv, log_moneyness, cp, expiry
              FROM option_iv
             WHERE product=? AND trade_date=?
        """
        iv_args: list[Any] = [product, date]
        if side == "call":
            iv_sql += " AND cp='C'"
        elif side == "put":
            iv_sql += " AND cp='P'"
        iv_sql += " ORDER BY ttm, strike"
        iv_points = conn.execute(iv_sql, iv_args).fetchall()

        spot = meta["spot_proxy"]
        # For OTM side, keep only OTM quotes in market scatter (aligned with smile construction)
        if side == "otm" and spot:
            iv_points = [
                p
                for p in iv_points
                if (p["cp"] == "C" and p["log_moneyness"] >= -0.02)
                or (p["cp"] == "P" and p["log_moneyness"] <= 0.02)
            ]
        slice_payload = [
            {
                "expiry": s["expiry"],
                "ttm": s["ttm"],
                "forward": s["forward"],
                "n_quotes": s["n_quotes"],
                "raw": {"a": s["a"], "b": s["b"], "rho": s["rho"], "m": s["m"], "sigma": s["sigma"]},
                "jw": {
                    "v": s["jw_v"],
                    "psi": s["jw_psi"],
                    "p": s["jw_p"],
                    "c": s["jw_c"],
                    "vtild": s["jw_vtild"],
                },
                "w_atm": s["w_atm"],
                "rmse": s["rmse"],
                "atm_iv": math.sqrt(max(s["w_atm"], 1e-12) / max(s["ttm"], 1e-8)),
            }
            for s in slices
        ]

        mat = _load_matrix(conn, product, date, side)

        def predict(strike: float, ttm: float) -> float:
            if ttm <= 0:
                return slice_iv(math.log(strike / spot), slices[0]["ttm"], slices[0])
            ts = [s["ttm"] for s in slices]
            if ttm <= ts[0]:
                F = slices[0]["forward"]
                return slice_iv(math.log(strike / F), ts[0], slices[0])
            if ttm >= ts[-1]:
                F = slices[-1]["forward"]
                return slice_iv(math.log(strike / F), ts[-1], slices[-1])
            for i in range(len(ts) - 1):
                if ts[i] <= ttm <= ts[i + 1]:
                    s1, s2 = slices[i], slices[i + 1]
                    k1 = math.log(strike / s1["forward"])
                    k2 = math.log(strike / s2["forward"])
                    w1 = raw_svi_w(k1, s1["a"], s1["b"], s1["rho"], s1["m"], s1["sigma"])
                    w2 = raw_svi_w(k2, s2["a"], s2["b"], s2["rho"], s2["m"], s2["sigma"])
                    a = (ttm - ts[i]) / (ts[i + 1] - ts[i])
                    w = w1 + a * (w2 - w1)
                    return math.sqrt(max(w, 1e-12) / ttm)
            F = slices[-1]["forward"]
            return slice_iv(math.log(strike / F), ts[-1], slices[-1])

        if mat and mat.get("iv") and mat.get("strikes") and mat.get("ttms"):
            grid_points = []
            for i, t in enumerate(mat["ttms"]):
                for j, k in enumerate(mat["strikes"]):
                    v = mat["iv"][i][j]
                    if v is None:
                        continue
                    grid_points.append(
                        {"strike": round(float(k), 2), "ttm": round(float(t), 6), "iv": round(float(v), 6)}
                    )
        else:
            grid_points = []
            for s in slices:
                for mny in [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]:
                    k = s["forward"] * mny
                    grid_points.append(
                        {
                            "strike": round(k, 2),
                            "ttm": round(s["ttm"], 6),
                            "iv": round(predict(k, s["ttm"]), 6),
                        }
                    )

        market_points = [
            {
                "strike": p["strike"],
                "ttm": p["ttm"],
                "iv": p["iv"],
                "cp": p["cp"],
                "expiry": p["expiry"],
                "k": p["log_moneyness"],
            }
            for p in iv_points
        ]

        return {
            "product": product,
            "trade_date": date,
            "side": side,
            "spot": spot,
            "r": meta["r"] if "r" in meta.keys() else None,
            "rate_code": meta["rate_code"] if "rate_code" in meta.keys() else None,
            "n_slices": meta["n_slices"],
            "n_iv": meta["n_iv"],
            "slices": slice_payload,
            "points": grid_points,
            "market_points": market_points,
            "matrix": mat,
            "model": "JW-SVI",
        }


@app.get("/api/predict/{product}")
def predict_iv(
    product: str,
    strike: float,
    ttm: float,
    date: Optional[str] = None,
    side: str = Query("otm"),
) -> Dict[str, Any]:
    side = _norm_side(side)
    surf = surface(product, date, side)
    product = product.upper()
    with connect() as conn:
        if date is None:
            date = surf["trade_date"]
        if _meta_has_side(conn):
            slices = conn.execute(
                "SELECT * FROM svi_slices WHERE product=? AND trade_date=? AND side=? ORDER BY ttm",
                (product, date, side),
            ).fetchall()
        else:
            slices = conn.execute(
                "SELECT * FROM svi_slices WHERE product=? AND trade_date=? ORDER BY ttm",
                (product, date),
            ).fetchall()
        if not slices:
            raise HTTPException(404, "no slices")
        ts = [s["ttm"] for s in slices]
        if ttm <= ts[0]:
            iv = slice_iv(math.log(strike / slices[0]["forward"]), ts[0], slices[0])
        elif ttm >= ts[-1]:
            iv = slice_iv(math.log(strike / slices[-1]["forward"]), ts[-1], slices[-1])
        else:
            iv = None
            for i in range(len(ts) - 1):
                if ts[i] <= ttm <= ts[i + 1]:
                    s1, s2 = slices[i], slices[i + 1]
                    k1 = math.log(strike / s1["forward"])
                    k2 = math.log(strike / s2["forward"])
                    w1 = raw_svi_w(k1, s1["a"], s1["b"], s1["rho"], s1["m"], s1["sigma"])
                    w2 = raw_svi_w(k2, s2["a"], s2["b"], s2["rho"], s2["m"], s2["sigma"])
                    a = (ttm - ts[i]) / (ts[i + 1] - ts[i])
                    w = w1 + a * (w2 - w1)
                    iv = math.sqrt(max(w, 1e-12) / ttm)
                    break
            if iv is None:
                iv = slice_iv(math.log(strike / slices[-1]["forward"]), ts[-1], slices[-1])
        return {"product": product, "date": date, "side": side, "strike": strike, "ttm": ttm, "iv": iv}


# Static frontend (production) — mount only when Vite build artifacts exist
_assets = DIST / "assets"
if DIST.exists() and (DIST / "index.html").exists():
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        index = DIST / "index.html"
        candidate = DIST / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
