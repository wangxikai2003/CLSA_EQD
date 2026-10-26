"""
FastAPI backend for CFFEX vol DB + JW SVI surfaces.
Serve API under /api and (optionally) built frontend from ../CLSA_EQD/dist.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
_DB_CANDIDATES = [
    ROOT / "data" / "cffex_vol.sqlite",
    Path("/app/data/cffex_vol.sqlite"),
    Path("/tmp/cffex_vol.sqlite"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p.exists()), _DB_CANDIDATES[0])
DIST = ROOT / "dist"

app = FastAPI(title="CLSA EQD Vol Surface API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/products")
def products() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT product, name, fut_prefix FROM products ORDER BY product").fetchall()
        out = []
        for r in rows:
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
                }
            )
        return out


@app.get("/api/dates/{product}")
def dates(product: str) -> List[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT trade_date FROM surface_meta WHERE product=? AND n_slices>0 ORDER BY trade_date",
            (product.upper(),),
        ).fetchall()
        return [r["trade_date"] for r in rows]


@app.get("/api/surface/{product}")
def surface(
    product: str,
    date: Optional[str] = Query(None, description="YYYY-MM-DD; default = latest"),
) -> Dict[str, Any]:
    product = product.upper()
    with connect() as conn:
        if date is None:
            row = conn.execute(
                "SELECT trade_date FROM surface_meta WHERE product=? AND n_slices>0 ORDER BY trade_date DESC LIMIT 1",
                (product,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"No surface for {product}")
            date = row["trade_date"]

        meta = conn.execute(
            "SELECT * FROM surface_meta WHERE product=? AND trade_date=?",
            (product, date),
        ).fetchone()
        if not meta:
            raise HTTPException(404, f"No meta for {product} {date}")

        slices = conn.execute(
            "SELECT * FROM svi_slices WHERE product=? AND trade_date=? ORDER BY ttm",
            (product, date),
        ).fetchall()
        if not slices:
            raise HTTPException(404, f"No SVI slices for {product} {date}")

        iv_points = conn.execute(
            """
            SELECT strike, ttm, iv, log_moneyness, cp, expiry
              FROM option_iv
             WHERE product=? AND trade_date=?
             ORDER BY ttm, strike
            """,
            (product, date),
        ).fetchall()

        spot = meta["spot_proxy"]
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

        # Dense grid for Plotly: moneyness x ttm
        strikes = sorted({p["strike"] for p in iv_points})
        if not strikes:
            # synthetic grid around spot
            strikes = [spot * x for x in [0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2]]

        def predict(strike: float, ttm: float) -> float:
            if ttm <= 0:
                return slice_iv(math.log(strike / spot), slices[0]["ttm"], slices[0])
            # surrounding slices by ttm
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
                    # linear in total variance
                    a = (ttm - ts[i]) / (ts[i + 1] - ts[i])
                    w = w1 + a * (w2 - w1)
                    return math.sqrt(max(w, 1e-12) / ttm)
            F = slices[-1]["forward"]
            return slice_iv(math.log(strike / F), ts[-1], slices[-1])

        # sample points for frontend VolSurfacePoint[]
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
            "spot": spot,
            "n_slices": meta["n_slices"],
            "n_iv": meta["n_iv"],
            "slices": slice_payload,
            "points": grid_points,
            "market_points": market_points,
            "model": "JW-SVI",
        }


@app.get("/api/predict/{product}")
def predict_iv(
    product: str,
    strike: float,
    ttm: float,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    surf = surface(product, date)
    # reuse predict by reconstructing from response slices — call internal again
    product = product.upper()
    with connect() as conn:
        if date is None:
            date = surf["trade_date"]
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
        return {"product": product, "date": date, "strike": strike, "ttm": ttm, "iv": iv}


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
