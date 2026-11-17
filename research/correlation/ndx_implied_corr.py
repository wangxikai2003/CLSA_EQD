"""
NDX implied average correlation surface (CMDS OPRA) — refactored.

Fixes:
  1) Separate AM (NDX) vs PM (NDXP); never mix roots in one tenor window
  2) Constant-maturity IV via linear total-variance across expiries
  3) Prefer mid + PCP Black-76; vendor LAST only as fallback (<3 mid IVs)
  4) No flat np.interp extrapolation beyond listed strikes (±0.5% tol)
  5) Clean vs Dirty implied correlation columns
  6) --start/--end daily batch + monthly weight cache

Clean:
  ρ = (σ_I² − Σ w²σ²) / [(Σ wσ)² − Σ w²σ²]
Dirty:
  ρ = σ_I² / (Σ wσ)²

Usage:
  export CMDS_USER=... CMDS_PASSWORD=... CMDS_HOST=... CMDS_PORT=...
  python ndx_implied_corr.py --pilot
  python ndx_implied_corr.py --start 20240901 --end 20241130 --tenors 30,60,90
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_env_pipe = os.environ.get("VOL_TRADING_PIPELINE", "").strip()
_VOL_PIPE = Path(_env_pipe).expanduser() if _env_pipe else Path()
if not _env_pipe or not _VOL_PIPE.exists():
    cand = ROOT.parent / "vol_trading" / "pipeline"
    if cand.exists():
        _VOL_PIPE = cand
if _VOL_PIPE.exists() and str(_VOL_PIPE) not in ("", "."):
    sys.path.insert(0, str(_VOL_PIPE))

from cmds_connect import DEFAULT_JAR, connect, get_cmds_creds  # noqa: E402
from vol_lib import implied_vol  # noqa: E402

NDX_SEC_ID = os.environ.get("CMDS_NDX_SEC_ID", "6NL341kna8pu")
AM_HOUR_ADJ = 6.5 / 24.0  # AM settles ~6.5h earlier than PM close


# ---------------------------------------------------------------------------
# CMDS session
# ---------------------------------------------------------------------------

class CmdsSession:
    def __init__(self):
        get_cmds_creds()
        self.conn = connect(jar=DEFAULT_JAR)
        self._weight_cache: dict[str, pd.DataFrame] = {}

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
# Root / time / mid / PCP
# ---------------------------------------------------------------------------

def parse_ndx_root(ric: str) -> Optional[str]:
    """NDXP = PM settlement; bare NDX = AM. Prefer matching longest token first."""
    s = str(ric or "").upper()
    if "NDXP" in s:
        return "NDXP"
    if s.startswith("NDX") or ".NDX" in s or "NDX" in s:
        # exclude accidental NDXP already handled
        if "NDXP" in s:
            return "NDXP"
        return "NDX"
    return None


def year_frac_from_dte(dte_cal: float, root: str) -> float:
    """ACT/365; AM roots subtract 6.5/24 day vs PM calendar DTE."""
    adj = AM_HOUR_ADJ if root == "NDX" else 0.0
    return max(float(dte_cal) - adj, 1e-6) / 365.0


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
        if mx > 0 and (a - b) / mx <= 0.35:
            return mx
    if m is not None and m > 0:
        return m
    if c is not None and c > 0:
        return c
    return None


def implied_forward_pcp(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    T: float,
    r: float,
    spot: float,
    top_n: int = 5,
) -> tuple[float, int]:
    """Median F from Top-N strikes with smallest |C-P| near spot."""
    if calls.empty or puts.empty:
        return float(spot), 0
    m = calls.merge(puts, on="K", suffixes=("_c", "_p"))
    if m.empty:
        return float(spot), 0
    band = 0.07 * spot
    m = m[(m["K"] - spot).abs() <= band].copy()
    if m.empty:
        return float(spot), 0
    m["diff"] = (m["px_c"] - m["px_p"]).abs()
    m = m.sort_values("diff").head(int(top_n))
    Fs = []
    for row in m.itertuples(index=False):
        Fk = float(row.K) + math.exp(r * T) * (float(row.px_c) - float(row.px_p))
        if np.isfinite(Fk) and Fk > 0:
            Fs.append(Fk)
    if not Fs:
        return float(spot), 0
    return float(np.median(Fs)), int(len(Fs))


def total_var_interp(tenors: np.ndarray, ivs: np.ndarray, t_tgt: float) -> Optional[float]:
    mask = np.isfinite(tenors) & np.isfinite(ivs) & (tenors > 0) & (ivs > 0)
    tenors, ivs = tenors[mask], ivs[mask]
    if len(tenors) < 1:
        return None
    order = np.argsort(tenors)
    tenors, ivs = tenors[order], ivs[order]
    w = ivs * ivs * tenors
    if t_tgt < tenors[0] or t_tgt > tenors[-1]:
        return None  # no hard extrapolate
    i = int(np.searchsorted(tenors, t_tgt))
    if i == 0:
        wt = float(w[0])
    else:
        t0, t1 = float(tenors[i - 1]), float(tenors[i])
        a = 0.0 if t1 == t0 else (t_tgt - t0) / (t1 - t0)
        wt = float(w[i - 1] + a * (w[i] - w[i - 1]))
    return math.sqrt(max(wt, 0.0) / t_tgt) if t_tgt > 0 else None


def interp_iv_at_strike(byk: pd.DataFrame, K_tgt: float, tol: float = 0.005) -> Optional[float]:
    """IV-space interp; None if K_tgt outside [Kmin,Kmax] beyond tol (no flat extrap)."""
    if byk is None or byk.empty:
        return None
    ks = byk["K"].to_numpy(dtype=float)
    sigs = byk["iv"].to_numpy(dtype=float)
    if len(ks) == 0:
        return None
    lo, hi = float(ks[0]), float(ks[-1])
    if K_tgt < lo * (1.0 - tol) or K_tgt > hi * (1.0 + tol):
        return None
    if len(ks) == 1:
        return float(sigs[0])
    K_clip = min(max(K_tgt, lo), hi)
    return float(np.interp(K_clip, ks, sigs))


def iv_nodes_by_expiry(
    g: pd.DataFrame,
    spot: float,
    r: float,
    k_target_mny: float,
    root_pref: str = "NDXP",
) -> list[dict]:
    """
    Per (exp, root) slice → one IV at K=spot*mny.
    Prefer root_pref (NDXP); never mix AM/PM in same expiry window.
    """
    if g is None or g.empty or "exp" not in g.columns:
        return []
    nodes = []
    roots_present = (
        set(g["root"].dropna().astype(str)) if "root" in g.columns else set()
    )
    # Equity chains: no AM/PM — keep all. Index: prefer NDXP then NDX, never mix.
    if root_pref in ("NDX", "NDXP"):
        if root_pref in roots_present:
            use_root = root_pref
        elif "NDXP" in roots_present:
            use_root = "NDXP"
        elif "NDX" in roots_present:
            use_root = "NDX"
        else:
            use_root = None
        gg = g if use_root is None or "root" not in g.columns else g[g["root"] == use_root]
    else:
        use_root = "EQ"
        gg = g.copy()
        if "root" not in gg.columns:
            gg["root"] = "EQ"
    if gg.empty:
        return []

    for (exp, root), sl0 in gg.groupby(["exp", "root"]):
        sl = sl0.copy()
        root_s = str(root) if pd.notna(root) else (use_root or "NDXP")
        dte = float(sl["dte"].median())
        if dte < 5:
            continue
        T = year_frac_from_dte(dte, root_s)
        calls = sl[sl["cp"] == "C"][["K", "px"]].rename(columns={"px": "px_c"})
        puts = sl[sl["cp"] == "P"][["K", "px"]].rename(columns={"px": "px_p"})
        F, n_pcp = implied_forward_pcp(calls, puts, T, r, spot, top_n=5)
        K_tgt = float(spot) * float(k_target_mny)

        def _iv_table(prefer_mid: bool) -> tuple[Optional[pd.DataFrame], str]:
            if prefer_mid:
                iv_rows = []
                for row in sl.itertuples(index=False):
                    sig = implied_vol(float(row.px), F, float(row.K), T, r, str(row.cp))
                    if sig is not None and 0.01 < sig < 3.0:
                        iv_rows.append((float(row.K), str(row.cp), float(sig)))
                if len(iv_rows) >= 3:
                    use = pd.DataFrame(iv_rows, columns=["K", "cp", "iv"])
                    return use, "black76_mid"
            if "iv_last" in sl.columns and sl["iv_last"].notna().sum() >= 3:
                tmp = sl.dropna(subset=["iv_last"]).copy()
                tmp["iv"] = tmp["iv_last"].astype(float)
                tmp.loc[tmp["iv"] > 3.0, "iv"] = tmp.loc[tmp["iv"] > 3.0, "iv"] / 100.0
                iv_rows = [
                    (float(r.K), str(r.cp), float(r.iv))
                    for r in tmp.itertuples(index=False)
                    if 0.01 < float(r.iv) < 3.0
                ]
                if len(iv_rows) >= 3:
                    return pd.DataFrame(iv_rows, columns=["K", "cp", "iv"]), "vendor_last"
            return None, ""

        use, method = _iv_table(prefer_mid=True)
        atm = None
        n_use = 0
        for attempt in range(2):
            if use is None:
                break
            u = use.copy()
            u["otm"] = ((u["cp"] == "C") & (u["K"] >= F)) | ((u["cp"] == "P") & (u["K"] <= F))
            u = u[u["otm"]] if u["otm"].any() else u
            byk = u.groupby("K", as_index=False)["iv"].median().sort_values("K")
            atm = interp_iv_at_strike(byk, K_tgt, tol=0.005)
            n_use = int(len(u))
            if atm is not None:
                break
            # mid path produced strikes but K_tgt OOB / sparse → try vendor once
            if attempt == 0 and method == "black76_mid":
                use, method = _iv_table(prefer_mid=False)
            else:
                break
        if atm is None:
            continue
        nodes.append(
            {
                "exp": str(exp),
                "root": root_s,
                "T": T,
                "dte": dte,
                "iv": float(atm),
                "F": F,
                "n_pcp": n_pcp,
                "method": method,
                "n": n_use,
            }
        )
    return nodes


def constant_maturity_iv(nodes: list[dict], tenor_days: float) -> Optional[dict]:
    if not nodes:
        return None
    tenors = np.array([n["T"] for n in nodes], dtype=float)
    ivs = np.array([n["iv"] for n in nodes], dtype=float)
    t_tgt = float(tenor_days) / 365.0
    sig = total_var_interp(tenors, ivs, t_tgt)
    order = np.argsort(tenors)
    tenors_s, nodes_s = tenors[order], [nodes[i] for i in order]
    if sig is None:
        # nearest listed only if within ±7 calendar days — still no beyond-curve extrapolate
        j = int(np.argmin(np.abs(tenors_s - t_tgt)))
        if abs(tenors_s[j] - t_tgt) <= 7.0 / 365.0:
            n0 = nodes_s[j]
            return {
                "atm_iv": float(n0["iv"]),
                "T": t_tgt,
                "method": f"nearest_listed:{n0['method']}",
                "root": n0["root"],
                "F": float(n0["F"]),
                "n_pcp": int(n0.get("n_pcp", 0)),
                "dte_left": n0["dte"],
                "dte_right": n0["dte"],
            }
        return None
    i = int(np.searchsorted(tenors_s, t_tgt))
    left = nodes_s[max(i - 1, 0)]
    right = nodes_s[min(i, len(nodes_s) - 1)]
    return {
        "atm_iv": float(sig),
        "T": t_tgt,
        "method": f"tv_interp:{left['method']}/{right['method']}",
        "root": left["root"],
        "F": float(np.mean([left["F"], right["F"]])),
        "n_pcp": int(max(left.get("n_pcp", 0), right.get("n_pcp", 0))),
        "dte_left": left["dte"],
        "dte_right": right["dte"],
    }


def implied_corr_clean(sig_I: float, w: np.ndarray, sig: np.ndarray) -> Optional[float]:
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
    return float((s2 - sum_w2_s2) / den)


def implied_corr_dirty(sig_I: float, w: np.ndarray, sig: np.ndarray) -> Optional[float]:
    w = np.asarray(w, dtype=float)
    sig = np.asarray(sig, dtype=float)
    mask = np.isfinite(w) & np.isfinite(sig) & (w > 0) & (sig > 0)
    w, sig = w[mask], sig[mask]
    if len(w) < 5 or not np.isfinite(sig_I) or sig_I <= 0:
        return None
    w = w / w.sum()
    sum_ws = float(np.sum(w * sig))
    if sum_ws <= 1e-12:
        return None
    return float((sig_I * sig_I) / (sum_ws * sum_ws))


# ---------------------------------------------------------------------------
# Data pulls
# ---------------------------------------------------------------------------

@dataclass
class DayInputs:
    trade_date: str
    ndx_spot: float
    weights: pd.DataFrame
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


def fetch_weights(
    s: CmdsSession,
    trade_date: str,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Cache by YYYYMM to cut Oracle round-trips within a month."""
    key = trade_date[:6]
    if key not in s._weight_cache:
        # load month window once
        start = key + "01"
        # next month start approx
        y, m = int(key[:4]), int(key[4:6])
        if m == 12:
            end = f"{y+1}0101"
        else:
            end = f"{y}{m+1:02d}01"
        raw = s.read_sql(
            f"""
            SELECT TRADE_DATE, CONSTITUENT_SEC_ID, CONSTITUENT_WINDCODE, CONSTITUENT_WEIGHT
            FROM CDM.INDEXCONSTITUENTS
            WHERE INDEX_SEC_ID='{NDX_SEC_ID}'
              AND TRADE_DATE >= '{start}' AND TRADE_DATE < '{end}'
              AND CONSTITUENT_WEIGHT IS NOT NULL
            """
        )
        s._weight_cache[key] = raw
    raw = s._weight_cache[key]
    if raw.empty:
        raise RuntimeError(f"No NDX weights in month {key}")
    # nearest available date <= trade_date
    dates = sorted(raw["TRADE_DATE"].astype(str).unique())
    use_dt = None
    for d in dates:
        if d <= trade_date:
            use_dt = d
    if use_dt is None:
        use_dt = dates[0]
    df = raw[raw["TRADE_DATE"].astype(str) == use_dt].copy()
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
    df["weight"] = df["weight"] / df["weight"].sum() * 100.0
    df["weight_asof"] = use_dt
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


def _normalize_chain(raw: pd.DataFrame, trade_date: str, parse_root: bool = False) -> pd.DataFrame:
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
        exp_raw = r.EXPIRATION_DATE
        try:
            if isinstance(exp_raw, (int, float)) or str(exp_raw).replace(".", "", 1).isdigit():
                exp = pd.Timestamp(str(int(float(exp_raw))))
            else:
                exp = pd.Timestamp(exp_raw)
        except Exception:
            continue
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
        ric = getattr(r, "RIC", None)
        root = parse_ndx_root(ric) if parse_root else None
        rows.append(
            {
                "underlying": getattr(r, "UNDERLYING", None),
                "ric": ric,
                "root": root,
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
    td = pd.Timestamp(trade_date)
    exp_hi = (td + pd.Timedelta(days=dte_max + 5)).strftime("%Y%m%d")
    raw = s.read_sql(
        f"""
        SELECT o.UNDERLYING, o.RIC, o.SEC_ID, o.STRIKE_PRICE, o.EXPIRATION_DATE, o.PUT_CALL_FLAG,
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
    return _normalize_chain(raw, trade_date, parse_root=True)


def fetch_member_chains(
    s: CmdsSession,
    trade_date: str,
    underlyings: list[str],
    spots_by_und: dict[str, float],
    dte_max: int = 120,
    mny_band: float = 0.18,
) -> dict[str, pd.DataFrame]:
    if not underlyings:
        return {}
    td = pd.Timestamp(trade_date)
    exp_hi = (td + pd.Timedelta(days=dte_max + 5)).strftime("%Y%m%d")
    raw = s.read_sql(
        f"""
        SELECT o.UNDERLYING, o.RIC, o.STRIKE_PRICE, o.EXPIRATION_DATE, o.PUT_CALL_FLAG,
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
    chain = _normalize_chain(raw, trade_date, parse_root=False)
    out: dict[str, pd.DataFrame] = {}
    if chain.empty:
        return out
    for und, g in chain.groupby("underlying"):
        spot = spots_by_und.get(str(und))
        if spot is None or spot <= 0:
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
    print(f"[{trade_date}] equity spots n={len(sec_ids)} (weights asof {w['weight_asof'].iloc[0]})...", flush=True)
    spots = fetch_equity_spots(s, trade_date, sec_ids)
    sid_to_und = dict(zip(w["sec_id"].astype(str), w["underlying"].astype(str)))
    und_spot = {sid_to_und[sid]: px for sid, px in spots.items() if sid in sid_to_und}
    w["spot"] = w["sec_id"].astype(str).map(spots)

    print(f"[{trade_date}] NDX chain (AM/PM tagged)...", flush=True)
    ndx_chain = fetch_ndx_chain(s, trade_date, dte_max=dte_max)
    if not ndx_chain.empty and "root" in ndx_chain.columns:
        vc = ndx_chain["root"].value_counts(dropna=False).to_dict()
        print(f"[{trade_date}] NDX quotes={len(ndx_chain)} roots={vc}", flush=True)
    else:
        print(f"[{trade_date}] NDX quotes={len(ndx_chain)}", flush=True)

    print(f"[{trade_date}] member chains ({len(w)})...", flush=True)
    member_chains = fetch_member_chains(
        s,
        trade_date,
        underlyings=w["underlying"].tolist(),
        spots_by_und=und_spot,
        dte_max=dte_max,
        mny_band=0.18,
    )
    print(
        f"[{trade_date}] members with chain={sum(1 for v in member_chains.values() if len(v))} "
        f"quotes={sum(len(v) for v in member_chains.values())}",
        flush=True,
    )
    return DayInputs(trade_date, spot, w, ndx_chain, member_chains)


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------

def build_day_surface(
    day: DayInputs,
    tenors: list[float],
    mny_grid: list[float],
    r: float,
    min_names: int = 40,
    min_weight_cov: float = 0.70,
    root_pref: str = "NDXP",
) -> pd.DataFrame:
    rows = []
    wdf = day.weights.copy()
    wdf["w"] = pd.to_numeric(wdf["weight"], errors="coerce") / 100.0

    for ten in tenors:
        for mny in mny_grid:
            ndx_nodes = iv_nodes_by_expiry(
                day.ndx_chain, day.ndx_spot, r, mny, root_pref=root_pref
            )
            ndx = constant_maturity_iv(ndx_nodes, ten)
            if ndx is None:
                rows.append(
                    {
                        "trade_date": day.trade_date,
                        "tenor_days": ten,
                        "mny": mny,
                        "rho_imp_clean": np.nan,
                        "rho_imp_dirty": np.nan,
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
                    sp = float(ch["K"].median())
                if "root" not in ch.columns:
                    ch = ch.assign(root="EQ")
                nodes = iv_nodes_by_expiry(ch, sp, r, mny, root_pref="EQ")
                cm = constant_maturity_iv(nodes, ten)
                if cm is None:
                    continue
                sigs.append(cm["atm_iv"])
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
                        "rho_imp_clean": np.nan,
                        "rho_imp_dirty": np.nan,
                        "rho_imp": np.nan,
                        "sig_ndx": ndx["atm_iv"],
                        "n_names": len(names),
                        "weight_cov": cov,
                        "ndx_method": ndx["method"],
                        "ndx_root": ndx.get("root"),
                        "status": "member_coverage_fail",
                    }
                )
                continue

            ws_a = ws_a / ws_a.sum()
            rho_c = implied_corr_clean(ndx["atm_iv"], ws_a, sig_a)
            rho_d = implied_corr_dirty(ndx["atm_iv"], ws_a, sig_a)
            avg_sig = float(np.sum(ws_a * sig_a))
            ok = (
                rho_c is not None
                and rho_d is not None
                and -0.2 <= rho_c <= 1.5
                and -0.2 <= rho_d <= 1.5
            )
            rows.append(
                {
                    "trade_date": day.trade_date,
                    "tenor_days": ten,
                    "mny": mny,
                    "rho_imp_clean": rho_c,
                    "rho_imp_dirty": rho_d,
                    "rho_imp": rho_c,  # backward-compat alias = Clean
                    "sig_ndx": ndx["atm_iv"],
                    "sig_basket": avg_sig,
                    "n_names": len(names),
                    "weight_cov": cov,
                    "ndx_method": ndx["method"],
                    "ndx_root": ndx.get("root"),
                    "ndx_F": ndx.get("F"),
                    "ndx_dte_left": ndx.get("dte_left"),
                    "ndx_dte_right": ndx.get("dte_right"),
                    "status": "ok" if ok else "rho_oor",
                }
            )
    return pd.DataFrame(rows)


def default_pilot_dates() -> list[str]:
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


def date_range_yyyymmdd(start: str, end: str) -> list[str]:
    idx = pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end))
    return [d.strftime("%Y%m%d") for d in idx]


def nearest_available_dates(s: CmdsSession, candidates: list[str]) -> list[str]:
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
            print(f"  date {d} SKIP", flush=True)
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--dates", default="", help="YYYYMMDD,YYYYMMDD,...")
    ap.add_argument("--start", default="", help="YYYYMMDD inclusive (with --end)")
    ap.add_argument("--end", default="", help="YYYYMMDD inclusive (with --start)")
    ap.add_argument("--tenors", default="30,60,90")
    ap.add_argument("--mny", default="0.95,1.00,1.05")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--r", type=float, default=0.04)
    ap.add_argument("--dte-max", type=int, default=120)
    ap.add_argument("--root-pref", default="NDXP", choices=["NDXP", "NDX"])
    ap.add_argument("--out", default=str(ROOT / "data" / "ndx_implied_corr_surface.parquet"))
    args = ap.parse_args()

    if args.start and args.end:
        dates = date_range_yyyymmdd(args.start, args.end)
    elif args.pilot:
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
        # for dense --start/--end, only resolve a subsample check on first/last
        # then filter by spot existence cheaply
        if args.start and args.end and len(dates) > 40:
            # keep Wednesdays to control cost unless user wants all — still honor daily
            # but resolve in place without 8-day backtrack spam: check each day lightly
            resolved = []
            for d in dates:
                try:
                    fetch_ndx_spot(s, d)
                    resolved.append(d)
                except Exception:
                    continue
            dates = resolved
            print(f"Business days with NDX spot: {len(dates)}", flush=True)
        else:
            dates = nearest_available_dates(s, dates)
        print(f"Using {len(dates)} dates", flush=True)

        panels = []
        for d in dates:
            try:
                day = load_day(s, d, top_n=top_n, dte_max=args.dte_max)
                surf = build_day_surface(
                    day, tenors, mny_grid, r=args.r, root_pref=args.root_pref
                )
                panels.append(surf)
                ok = surf[surf["status"] == "ok"]
                med = ok["rho_imp_clean"].median() if len(ok) else float("nan")
                print(
                    f"[{d}] rows={len(surf)} ok={len(ok)} rho_clean_med={med:.3f}",
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
        out.to_csv(out_path.with_suffix(".csv"), index=False)

        print("\n=== SUMMARY (Clean) ===", flush=True)
        ok = out[out["status"] == "ok"]
        if len(ok):
            print(
                ok.groupby(["tenor_days", "mny"])[["rho_imp_clean", "rho_imp_dirty"]]
                .agg(["count", "mean", "median"])
                .round(4)
                .to_string(),
                flush=True,
            )
        print(f"\nWrote {len(out)} rows -> {out_path}", flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    main()
