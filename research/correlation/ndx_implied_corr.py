"""
NDX implied average correlation surface (CMDS OPRA).

Model (constant pairwise ρ):
  σ_I² = Σ_i w_i² σ_i² + ρ [ (Σ_i w_i σ_i)² − Σ_i w_i² σ_i² ]
  ρ   = (σ_I² − Σ w_i² σ_i²) / [ (Σ w_i σ_i)² − Σ w_i² σ_i² ]

Per trade date × tenor × moneyness (spot K/S target):
  - NDX / member ATM-ish IVs from option mid (Black-76) or vendor LAST
  - Official INDEXCONSTITUENTS weights (renormalized over names with valid IV)

Usage:
  export CMDS_USER=... CMDS_PASSWORD=...
  # or: export CMDS_CREDS_DOCX=".../CMDS数据库.docx"
  /Users/wangxikai/anaconda3/bin/python ndx_implied_corr.py --pilot
  /Users/wangxikai/anaconda3/bin/python ndx_implied_corr.py --dates 20261102,20261001 --tenors 30,90
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path("/Users/wangxikai/Desktop/CITICS CLSA/vol_trading/pipeline")))

from cmds_connect import DEFAULT_JAR, connect, get_cmds_creds  # noqa: E402
from vol_lib import implied_vol  # noqa: E402

NDX_SEC_ID = "6NL341kna8pu"
DEFAULT_DOCX = Path("/Users/wangxikai/Desktop/CITICS CLSA/公司数据库/CMDS数据库.docx")


# ---------------------------------------------------------------------------
# CMDS session (reuse connection)
# ---------------------------------------------------------------------------

class CmdsSession:
    def __init__(self):
        self._ensure_creds()
        self.conn = connect(jar=DEFAULT_JAR)

    @staticmethod
    def _ensure_creds() -> None:
        try:
            get_cmds_creds()
            return
        except Exception:
            pass
        if DEFAULT_DOCX.exists():
            with zipfile.ZipFile(DEFAULT_DOCX) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            texts = [
                t.text or ""
                for t in root.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            ]
            full = "".join(texts)
            m = re.search(r"密码[：:]\s*(\S+)", full)
            if m and "xikaiwang" in full:
                os.environ.setdefault("CMDS_USER", "xikaiwang")
                os.environ.setdefault("CMDS_PASSWORD", m.group(1))
            else:
                os.environ.setdefault("CMDS_CREDS_DOCX", str(DEFAULT_DOCX))
        get_cmds_creds()

    def read_sql(self, sql: str) -> pd.DataFrame:
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
        df = pd.DataFrame(rows, columns=cols)
        for c in df.columns:
            df[c] = df[c].map(
                lambda v: None
                if v is None
                else (float(v) if hasattr(v, "doubleValue") else v)
            )
        return df

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def _in_list(vals: Iterable[str]) -> str:
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def year_frac(dte: float) -> float:
    return max(float(dte), 0.0) / 365.0


def mid_from_row(bid, ask, close, mid) -> Optional[float]:
    try:
        b = float(bid) if bid is not None and pd.notna(bid) else None
        a = float(ask) if ask is not None and pd.notna(ask) else None
        m = float(mid) if mid is not None and pd.notna(mid) else None
        c = float(close) if close is not None and pd.notna(close) else None
    except Exception:
        return None
    if b is not None and a is not None and b > 0 and a >= b:
        mx = 0.5 * (a + b)
        if (a - b) / mx <= 0.35:
            return mx
    if m is not None and m > 0:
        return m
    if c is not None and c > 0:
        return c
    return None


def implied_forward_pcp(calls: pd.DataFrame, puts: pd.DataFrame, T: float, r: float, spot: float) -> float:
    if calls.empty or puts.empty:
        return float(spot)
    m = calls.merge(puts, on="K", suffixes=("_c", "_p"))
    if m.empty:
        return float(spot)
    band = 0.07 * spot
    m = m[(m["K"] - spot).abs() <= band]
    if m.empty:
        return float(spot)
    m = m.assign(diff=(m["px_c"] - m["px_p"]).abs())
    row = m.loc[m["diff"].idxmin()]
    F = float(row["K"]) + math.exp(r * T) * (float(row["px_c"]) - float(row["px_p"]))
    return F if np.isfinite(F) and F > 0 else float(spot)


def atm_iv_slice(
    g: pd.DataFrame,
    spot: float,
    r: float,
    k_target_mny: float,
    dte_lo: float,
    dte_hi: float,
) -> Optional[dict]:
    """
    g columns: K, cp, px, dte, iv_last(optional)
    Returns dict with atm_iv, F, T, n, method.
    """
    if g is None or g.empty or "dte" not in g.columns:
        return None
    g = g[(g["dte"] >= dte_lo) & (g["dte"] <= dte_hi) & (g["px"] > 0)].copy()
    if g.empty:
        return None
    # pick expiry closest to mid of band
    target_dte = 0.5 * (dte_lo + dte_hi)
    exp_dte = (
        g.groupby(g["dte"].round(0))
        .size()
        .rename("n")
        .reset_index()
        .assign(err=lambda x: (x["dte"] - target_dte).abs())
        .sort_values(["err", "n"], ascending=[True, False])
    )
    dte_star = float(exp_dte.iloc[0]["dte"])
    sl = g[np.isclose(g["dte"], dte_star, atol=0.6)].copy()
    if sl.empty:
        sl = g.copy()
        dte_star = float(sl["dte"].median())
    T = year_frac(dte_star)
    if T <= 0:
        return None

    calls = sl[sl["cp"] == "C"][["K", "px"]].rename(columns={"px": "px_c"})
    puts = sl[sl["cp"] == "P"][["K", "px"]].rename(columns={"px": "px_p"})
    F = implied_forward_pcp(calls, puts, T, r, spot)

    # prefer vendor last if dense
    if "iv_last" in sl.columns and sl["iv_last"].notna().sum() >= 5:
        use = sl.dropna(subset=["iv_last"]).copy()
        use["iv"] = use["iv_last"].astype(float)
        use.loc[use["iv"] > 3.0, "iv"] = use.loc[use["iv"] > 3.0, "iv"] / 100.0
        method = "vendor_last"
    else:
        ivs = []
        for row in sl.itertuples(index=False):
            sig = implied_vol(float(row.px), F, float(row.K), T, r, str(row.cp))
            if sig is not None and 0.01 < sig < 3.0:
                ivs.append((float(row.K), str(row.cp), float(sig)))
        if len(ivs) < 3:
            return None
        use = pd.DataFrame(ivs, columns=["K", "cp", "iv"])
        method = "black76_mid"

    # OTM relative to F
    use["otm"] = ((use["cp"] == "C") & (use["K"] >= F)) | ((use["cp"] == "P") & (use["K"] <= F))
    u = use[use["otm"]] if use["otm"].any() else use
    byk = u.groupby("K", as_index=False)["iv"].median().sort_values("K")
    K_tgt = float(spot) * float(k_target_mny)
    if len(byk) == 1:
        atm = float(byk["iv"].iloc[0])
    else:
        atm = float(np.interp(K_tgt, byk["K"].to_numpy(), byk["iv"].to_numpy()))
    return {
        "atm_iv": atm,
        "F": F,
        "T": T,
        "dte": dte_star,
        "n": int(len(u)),
        "method": method,
        "spot": float(spot),
    }


def implied_corr(sig_I: float, w: np.ndarray, sig: np.ndarray) -> Optional[float]:
    w = np.asarray(w, dtype=float)
    sig = np.asarray(sig, dtype=float)
    mask = np.isfinite(w) & np.isfinite(sig) & (w > 0) & (sig > 0)
    w, sig = w[mask], sig[mask]
    if len(w) < 5 or not np.isfinite(sig_I) or sig_I <= 0:
        return None
    w = w / w.sum()
    s2 = float(sig_I * sig_I)
    sum_w2_s2 = float(np.sum(w * w * sig * sig))
    sum_ws = float(np.sum(w * sig))
    den = sum_ws * sum_ws - sum_w2_s2
    if den <= 1e-12:
        return None
    rho = (s2 - sum_w2_s2) / den
    return float(rho)


def total_var_interp(tenors: np.ndarray, ivs: np.ndarray, t_tgt: float) -> Optional[float]:
    mask = np.isfinite(tenors) & np.isfinite(ivs) & (tenors > 0) & (ivs > 0)
    tenors, ivs = tenors[mask], ivs[mask]
    if len(tenors) < 1:
        return None
    order = np.argsort(tenors)
    tenors, ivs = tenors[order], ivs[order]
    w = ivs * ivs * tenors
    if t_tgt < tenors[0] or t_tgt > tenors[-1]:
        return None
    i = int(np.searchsorted(tenors, t_tgt))
    if i == 0:
        wt = float(w[0])
    else:
        t0, t1 = float(tenors[i - 1]), float(tenors[i])
        a = 0.0 if t1 == t0 else (t_tgt - t0) / (t1 - t0)
        wt = float(w[i - 1] + a * (w[i] - w[i - 1]))
    return math.sqrt(max(wt, 0.0) / t_tgt) if t_tgt > 0 else None


# ---------------------------------------------------------------------------
# Data pulls
# ---------------------------------------------------------------------------

@dataclass
class DayInputs:
    trade_date: str
    ndx_spot: float
    weights: pd.DataFrame  # underlying, weight, sec_id
    ndx_chain: pd.DataFrame
    member_chains: dict[str, pd.DataFrame]


def fetch_ndx_spot(s: CmdsSession, trade_date: str) -> float:
    df = s.read_sql(
        f"""
        SELECT CLOSE_PRICE FROM CDM.INDEXEOD
        WHERE SEC_ID='{NDX_SEC_ID}' AND TRADE_DATE='{trade_date}'
        """
    )
    if df.empty or pd.isna(df.iloc[0]["CLOSE_PRICE"]):
        raise RuntimeError(f"No NDX spot on {trade_date}")
    return float(df.iloc[0]["CLOSE_PRICE"])


def fetch_weights(s: CmdsSession, trade_date: str, top_n: Optional[int] = None) -> pd.DataFrame:
    df = s.read_sql(
        f"""
        SELECT CONSTITUENT_SEC_ID, CONSTITUENT_WINDCODE, CONSTITUENT_WEIGHT
        FROM CDM.INDEXCONSTITUENTS
        WHERE INDEX_SEC_ID='{NDX_SEC_ID}' AND TRADE_DATE='{trade_date}'
          AND CONSTITUENT_WEIGHT IS NOT NULL
        """
    )
    if df.empty:
        raise RuntimeError(f"No NDX weights on {trade_date}")
    df = df.rename(
        columns={
            "CONSTITUENT_SEC_ID": "sec_id",
            "CONSTITUENT_WINDCODE": "underlying",
            "CONSTITUENT_WEIGHT": "weight",
        }
    )
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df = df.dropna(subset=["underlying", "weight"])
    df["underlying"] = df["underlying"].astype(str)
    df = df[df["weight"] > 0].sort_values("weight", ascending=False)
    if top_n:
        df = df.head(int(top_n)).copy()
    df["weight"] = df["weight"] / df["weight"].sum() * 100.0  # keep percent scale internally then /100 later
    return df.reset_index(drop=True)


def fetch_equity_spots(s: CmdsSession, trade_date: str, sec_ids: list[str]) -> dict[str, float]:
    if not sec_ids:
        return {}
    df = s.read_sql(
        f"""
        SELECT SEC_ID, CLOSE_PRICE FROM CDM.EQUITYEOD
        WHERE TRADE_DATE='{trade_date}' AND SEC_ID IN ({_in_list(sec_ids)})
        """
    )
    out = {}
    for r in df.itertuples(index=False):
        if r.CLOSE_PRICE is not None and pd.notna(r.CLOSE_PRICE) and float(r.CLOSE_PRICE) > 0:
            out[str(r.SEC_ID)] = float(r.CLOSE_PRICE)
    return out


def _normalize_chain(raw: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    if raw.empty:
        return raw
    rows = []
    td = pd.Timestamp(trade_date)
    for r in raw.itertuples(index=False):
        px = mid_from_row(
            getattr(r, "BID_PRICE", None),
            getattr(r, "ASK_PRICE", None),
            getattr(r, "CLOSE_PRICE", None),
            getattr(r, "MID_PRICE", None),
        )
        if px is None:
            continue
        exp = pd.Timestamp(str(int(float(r.EXPIRATION_DATE))) if str(r.EXPIRATION_DATE).isdigit() else r.EXPIRATION_DATE)
        dte = (exp.normalize() - td.normalize()).days
        if dte < 5:
            continue
        cp = str(r.PUT_CALL_FLAG).upper()
        if cp.startswith("C"):
            cp = "C"
        elif cp.startswith("P"):
            cp = "P"
        else:
            continue
        iv_last = getattr(r, "IMPLIED_VOLATILITY_LAST", None)
        rows.append(
            {
                "underlying": getattr(r, "UNDERLYING", None),
                "K": float(r.STRIKE_PRICE),
                "cp": cp,
                "px": float(px),
                "dte": float(dte),
                "exp": exp.strftime("%Y%m%d"),
                "iv_last": float(iv_last) if iv_last is not None and pd.notna(iv_last) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def fetch_ndx_chain(s: CmdsSession, trade_date: str, dte_max: int = 120) -> pd.DataFrame:
    # bound expiry to cut join size
    td = pd.Timestamp(trade_date)
    exp_hi = (td + pd.Timedelta(days=dte_max + 5)).strftime("%Y%m%d")
    raw = s.read_sql(
        f"""
        SELECT o.UNDERLYING, o.STRIKE_PRICE, o.EXPIRATION_DATE, o.PUT_CALL_FLAG,
               e.BID_PRICE, e.ASK_PRICE, e.MID_PRICE, e.CLOSE_PRICE,
               v.IMPLIED_VOLATILITY_LAST
        FROM CDM.OPTIONEOD e
        JOIN CDM.OPTIONDESCRIPTION o ON o.SEC_ID = e.SEC_ID
        LEFT JOIN CDM.OPTIONVOLATILITY v
          ON v.SEC_ID = e.SEC_ID AND v.TRADE_DATE = e.TRADE_DATE
        WHERE o.UNDERLYING = '.NDX' AND o.MRKT_ID = 'OPRA'
          AND e.TRADE_DATE = '{trade_date}'
          AND o.EXPIRATION_DATE <= '{exp_hi}'
          AND e.MID_PRICE IS NOT NULL
        """
    )
    return _normalize_chain(raw, trade_date)


def fetch_member_chains(
    s: CmdsSession,
    trade_date: str,
    underlyings: list[str],
    spots_by_und: dict[str, float],
    dte_max: int = 120,
    mny_band: float = 0.12,
) -> dict[str, pd.DataFrame]:
    """Pull member options near spot for speed."""
    if not underlyings:
        return {}
    td = pd.Timestamp(trade_date)
    exp_hi = (td + pd.Timedelta(days=dte_max + 5)).strftime("%Y%m%d")
    raw = s.read_sql(
        f"""
        SELECT o.UNDERLYING, o.STRIKE_PRICE, o.EXPIRATION_DATE, o.PUT_CALL_FLAG,
               e.BID_PRICE, e.ASK_PRICE, e.MID_PRICE, e.CLOSE_PRICE,
               v.IMPLIED_VOLATILITY_LAST
        FROM CDM.OPTIONEOD e
        JOIN CDM.OPTIONDESCRIPTION o ON o.SEC_ID = e.SEC_ID
        LEFT JOIN CDM.OPTIONVOLATILITY v
          ON v.SEC_ID = e.SEC_ID AND v.TRADE_DATE = e.TRADE_DATE
        WHERE o.MRKT_ID = 'OPRA'
          AND o.UNDERLYING IN ({_in_list(underlyings)})
          AND e.TRADE_DATE = '{trade_date}'
          AND o.EXPIRATION_DATE <= '{exp_hi}'
          AND e.MID_PRICE IS NOT NULL
        """
    )
    chain = _normalize_chain(raw, trade_date)
    out: dict[str, pd.DataFrame] = {}
    if chain.empty:
        return out
    for und, g in chain.groupby("underlying"):
        spot = spots_by_und.get(str(und))
        if spot is None or spot <= 0:
            # keep all if spot missing (rare)
            out[str(und)] = g.reset_index(drop=True)
            continue
        gg = g[(g["K"] - spot).abs() / spot <= mny_band].copy()
        out[str(und)] = gg.reset_index(drop=True) if not gg.empty else g.reset_index(drop=True)
    return out


def load_day(
    s: CmdsSession,
    trade_date: str,
    top_n: Optional[int],
    dte_max: int,
) -> DayInputs:
    print(f"[{trade_date}] spot+weights...", flush=True)
    spot = fetch_ndx_spot(s, trade_date)
    w = fetch_weights(s, trade_date, top_n=top_n)
    sec_ids = w["sec_id"].astype(str).tolist()
    print(f"[{trade_date}] equity spots n={len(sec_ids)}...", flush=True)
    spots = fetch_equity_spots(s, trade_date, sec_ids)
    # map underlying -> spot via sec_id
    sid_to_und = dict(zip(w["sec_id"].astype(str), w["underlying"].astype(str)))
    und_spot = {sid_to_und[sid]: px for sid, px in spots.items() if sid in sid_to_und}
    # attach
    w["spot"] = w["sec_id"].astype(str).map(spots)

    print(f"[{trade_date}] NDX chain...", flush=True)
    ndx_chain = fetch_ndx_chain(s, trade_date, dte_max=dte_max)
    print(f"[{trade_date}] NDX quotes={len(ndx_chain)}", flush=True)

    print(f"[{trade_date}] member chains ({len(w)})...", flush=True)
    member_chains = fetch_member_chains(
        s,
        trade_date,
        underlyings=w["underlying"].tolist(),
        spots_by_und=und_spot,
        dte_max=dte_max,
    )
    print(
        f"[{trade_date}] members with chain={sum(1 for v in member_chains.values() if len(v))} "
        f"quotes={sum(len(v) for v in member_chains.values())}",
        flush=True,
    )
    return DayInputs(trade_date, spot, w, ndx_chain, member_chains)


# ---------------------------------------------------------------------------
# Surface build
# ---------------------------------------------------------------------------

def tenor_band(tenor_days: float) -> tuple[float, float]:
    # half-width grows mildly with tenor; keep 1M band wide enough for weekly listings
    hw = max(10.0, 0.35 * tenor_days)
    return max(5.0, tenor_days - hw), tenor_days + hw


def build_day_surface(
    day: DayInputs,
    tenors: list[float],
    mny_grid: list[float],
    r: float,
    min_names: int = 40,
    min_weight_cov: float = 0.70,
) -> pd.DataFrame:
    rows = []
    wdf = day.weights.copy()
    wdf["w"] = pd.to_numeric(wdf["weight"], errors="coerce") / 100.0

    for ten in tenors:
        dlo, dhi = tenor_band(ten)
        for mny in mny_grid:
            ndx = atm_iv_slice(day.ndx_chain, day.ndx_spot, r, mny, dlo, dhi)
            if ndx is None:
                rows.append(
                    {
                        "trade_date": day.trade_date,
                        "tenor_days": ten,
                        "mny": mny,
                        "rho_imp": np.nan,
                        "sig_ndx": np.nan,
                        "status": "ndx_iv_fail",
                    }
                )
                continue

            sigs, ws, names = [], [], []
            for r0 in wdf.itertuples(index=False):
                und = str(r0.underlying)
                ch = day.member_chains.get(und)
                if ch is None or ch.empty:
                    continue
                sp = float(r0.spot) if pd.notna(getattr(r0, "spot", np.nan)) else None
                if sp is None or sp <= 0:
                    # approximate spot from nearest strike median
                    sp = float(ch["K"].median())
                iv = atm_iv_slice(ch, sp, r, mny, dlo, dhi)
                if iv is None:
                    continue
                sigs.append(iv["atm_iv"])
                ws.append(float(r0.w))
                names.append(und)

            ws_a = np.asarray(ws, dtype=float)
            sig_a = np.asarray(sigs, dtype=float)
            cov = float(ws_a.sum()) if len(ws_a) else 0.0
            if len(names) < min_names or cov < min_weight_cov:
                rows.append(
                    {
                        "trade_date": day.trade_date,
                        "tenor_days": ten,
                        "mny": mny,
                        "rho_imp": np.nan,
                        "sig_ndx": ndx["atm_iv"],
                        "n_names": len(names),
                        "weight_cov": cov,
                        "ndx_method": ndx["method"],
                        "ndx_dte": ndx["dte"],
                        "status": "member_coverage_fail",
                    }
                )
                continue

            # renormalize available weights
            ws_a = ws_a / ws_a.sum()
            rho = implied_corr(ndx["atm_iv"], ws_a, sig_a)
            avg_sig = float(np.sum(ws_a * sig_a))
            rows.append(
                {
                    "trade_date": day.trade_date,
                    "tenor_days": ten,
                    "mny": mny,
                    "rho_imp": rho,
                    "sig_ndx": ndx["atm_iv"],
                    "sig_basket": avg_sig,
                    "n_names": len(names),
                    "weight_cov": cov,
                    "ndx_method": ndx["method"],
                    "ndx_dte": ndx["dte"],
                    "ndx_F": ndx["F"],
                    "status": "ok" if rho is not None and -0.2 <= rho <= 1.5 else "rho_oor",
                }
            )
    return pd.DataFrame(rows)


def default_pilot_dates() -> list[str]:
    # Month-start proxies across the mid-available window (~2024-09+)
    return [
        "20240906",
        "20241004",
        "20241108",
        "20241206",
        "20250110",
        "20250307",
        "20250606",
        "20250905",
        "20251001",
        "20251107",
        "20260109",
        "20260306",
        "20260605",
        "20260904",
        "20261106",
    ]


def nearest_available_dates(s: CmdsSession, candidates: list[str]) -> list[str]:
    """Keep dates with NDX spot + weights + option mid; shift back up to 7 calendar days."""
    out = []
    for d in candidates:
        td = pd.Timestamp(d)
        found = None
        for k in range(0, 8):
            ds = (td - pd.Timedelta(days=k)).strftime("%Y%m%d")
            try:
                fetch_ndx_spot(s, ds)
                w = s.read_sql(
                    f"""
                    SELECT COUNT(*) N FROM CDM.INDEXCONSTITUENTS
                    WHERE INDEX_SEC_ID='{NDX_SEC_ID}' AND TRADE_DATE='{ds}'
                      AND CONSTITUENT_WEIGHT IS NOT NULL
                    """
                )
                if int(w.iloc[0]["N"]) < 80:
                    continue
                opt = s.read_sql(
                    f"""
                    SELECT CASE WHEN EXISTS (
                      SELECT 1 FROM CDM.OPTIONEOD e
                      JOIN CDM.OPTIONDESCRIPTION o ON o.SEC_ID=e.SEC_ID
                      WHERE o.UNDERLYING='.NDX' AND o.MRKT_ID='OPRA'
                        AND e.TRADE_DATE='{ds}' AND e.MID_PRICE IS NOT NULL
                        AND ROWNUM = 1
                    ) THEN 1 ELSE 0 END AS HAS_OPT FROM DUAL
                    """
                )
                if int(opt.iloc[0]["HAS_OPT"]) != 1:
                    continue
                found = ds
                break
            except Exception:
                continue
        if found:
            out.append(found)
            print(f"  date {d} -> {found}", flush=True)
        else:
            print(f"  date {d} SKIP (no spot/weights/options)", flush=True)
    # unique preserve order
    seen = set()
    uniq = []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="run default pilot date set")
    ap.add_argument("--dates", default="", help="YYYYMMDD,YYYYMMDD,...")
    ap.add_argument("--tenors", default="30,60,90")
    ap.add_argument("--mny", default="0.95,1.00,1.05", help="spot moneyness K/S grid")
    ap.add_argument("--top-n", type=int, default=50, help="top-N weights (0=all)")
    ap.add_argument("--r", type=float, default=0.04)
    ap.add_argument("--dte-max", type=int, default=120)
    ap.add_argument("--out", default=str(ROOT / "data" / "ndx_implied_corr_surface.parquet"))
    args = ap.parse_args()

    if args.pilot:
        dates = default_pilot_dates()
    elif args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        dates = default_pilot_dates()[-4:]

    tenors = [float(x) for x in args.tenors.split(",")]
    mny_grid = [float(x) for x in args.mny.split(",")]
    top_n = None if int(args.top_n) <= 0 else int(args.top_n)

    s = CmdsSession()
    try:
        print("Resolving trade dates...", flush=True)
        dates = nearest_available_dates(s, dates)
        print(f"Using {len(dates)} dates: {dates}", flush=True)

        panels = []
        for d in dates:
            try:
                day = load_day(s, d, top_n=top_n, dte_max=args.dte_max)
                surf = build_day_surface(day, tenors, mny_grid, r=args.r)
                panels.append(surf)
                ok = surf[surf["status"] == "ok"]
                print(
                    f"[{d}] surface rows={len(surf)} ok={len(ok)} "
                    f"rho_med={ok['rho_imp'].median() if len(ok) else float('nan'):.3f}",
                    flush=True,
                )
            except Exception as e:
                print(f"[{d}] FAIL {type(e).__name__}: {e}", flush=True)

        if not panels:
            raise SystemExit("No successful dates")
        out = pd.concat(panels, ignore_index=True)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(out_path, index=False)
        csv_path = out_path.with_suffix(".csv")
        out.to_csv(csv_path, index=False)

        print("\n=== SUMMARY ===", flush=True)
        ok = out[out["status"] == "ok"]
        if len(ok):
            print(
                ok.groupby(["tenor_days", "mny"])["rho_imp"]
                .agg(["count", "mean", "median", "std"])
                .round(4)
                .to_string(),
                flush=True,
            )
            print("\nTail:", flush=True)
            print(ok.tail(12).to_string(index=False), flush=True)
        print(f"\nWrote {len(out)} rows -> {out_path}", flush=True)
        print(f"Also CSV -> {csv_path}", flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    main()
