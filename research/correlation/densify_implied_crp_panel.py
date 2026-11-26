#!/usr/bin/env python3
"""Densify NDX implied/CRP web panel onto weekly + recent-daily calendar.

True CMDS day rebuilds are expensive (~minutes/day). This joins the sparse
implied surface with dense forward realized and *linearly interpolates*
rho_imp in calendar time for each (tenor, mny, variant) between observed
CMDS surface dates.

Schedule (default):
  - all Wednesdays in [surface_min, realized_max]
  - all business days in the last --recent-days calendar window
  - always keep original surface dates

Writes:
  CLSA_EQD/data/corr_ndx_implied_crp.parquet (+ csv, manifest)
  optionally refreshes data/ndx_implied_corr_surface.parquet copy used by web build
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CORR = Path(__file__).resolve().parent
RESEARCH_CANDIDATES = [
    Path("/Users/wangxikai/Desktop/CITICS CLSA/课题：correlation/data"),
    CORR / "data",
    ROOT / "data",
]


def _find(name: str) -> Path | None:
    for base in RESEARCH_CANDIDATES:
        p = base / name
        if p.exists():
            return p
    return None


def _norm_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace("-", "").str.slice(0, 8)).dt.strftime("%Y-%m-%d")


def _target_dates(
    anchor: list[str],
    start: str,
    end: str,
    recent_days: int,
) -> list[str]:
    idx = pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end))
    weds = idx[idx.weekday == 2]
    recent_start = pd.Timestamp(end) - pd.Timedelta(days=int(recent_days))
    recent = idx[idx >= recent_start]
    out = set(anchor)
    out.update(d.strftime("%Y-%m-%d") for d in weds)
    out.update(d.strftime("%Y-%m-%d") for d in recent)
    return sorted(out)


def _interp_series(known: pd.DataFrame, dates: list[str], col: str) -> pd.Series:
    """Linear interpolate `col` onto `dates` from known trade_date rows."""
    k = known.dropna(subset=[col]).sort_values("trade_date")
    if k.empty:
        return pd.Series({d: np.nan for d in dates})
    x = pd.to_datetime(k["trade_date"]).map(pd.Timestamp.toordinal).to_numpy(dtype=float)
    y = k[col].to_numpy(dtype=float)
    xq = np.array([pd.Timestamp(d).toordinal() for d in dates], dtype=float)
    # only interpolate inside observed span; no extrapolation
    yq = np.interp(xq, x, y, left=np.nan, right=np.nan)
    # also fill exact matches
    known_map = dict(zip(k["trade_date"].astype(str), k[col].astype(float)))
    out = {}
    for d, v in zip(dates, yq):
        if d in known_map:
            out[d] = known_map[d]
        else:
            out[d] = float(v) if np.isfinite(v) else np.nan
    return pd.Series(out)


def densify(
    out_dir: Path,
    recent_days: int = 120,
    tenors: list[int] | None = None,
    mnys: list[float] | None = None,
) -> Path:
    imp_path = _find("ndx_implied_corr_surface.parquet")
    real_path = _find("ndx_realized_corr.parquet")
    if imp_path is None:
        raise FileNotFoundError("ndx_implied_corr_surface.parquet not found")
    if real_path is None:
        raise FileNotFoundError("ndx_realized_corr.parquet not found")

    imp = pd.read_parquet(imp_path)
    if "status" in imp.columns:
        imp = imp[imp["status"].fillna("ok") == "ok"].copy()
    imp["trade_date"] = _norm_date(imp["trade_date"])
    imp["tenor_days"] = imp["tenor_days"].astype(int)
    imp["mny"] = imp["mny"].astype(float)
    if "rho_imp_clean" not in imp.columns and "rho_imp" in imp.columns:
        imp["rho_imp_clean"] = imp["rho_imp"]
    if "rho_imp_dirty" not in imp.columns:
        imp["rho_imp_dirty"] = np.nan

    real = pd.read_parquet(real_path)
    real["trade_date"] = _norm_date(real["trade_date"])
    real["tenor_days"] = real["tenor_days"].astype(int)
    if "mode" in real.columns:
        real = real[real["mode"].astype(str) == "forward"].copy()

    tenor_list = tenors or sorted(int(x) for x in imp["tenor_days"].unique())
    mny_list = mnys or sorted(float(x) for x in imp["mny"].unique())

    start = max(imp["trade_date"].min(), "2024-09-01")
    end = min(real["trade_date"].max(), datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    # clip to realized dates that exist
    realized_dates = set(real["trade_date"].unique())
    target = [d for d in _target_dates(sorted(imp["trade_date"].unique()), start, end, recent_days) if d in realized_dates]
    if len(target) < 50:
        # fall back: all realized dates in span
        target = sorted(d for d in realized_dates if start <= d <= end)

    frames: list[pd.DataFrame] = []
    for tenor in tenor_list:
        real_t = real[real["tenor_days"] == tenor][
            [c for c in ("trade_date", "rho_real_clean", "rho_real_dirty", "n_names") if c in real.columns]
        ].drop_duplicates("trade_date")
        real_map_c = dict(zip(real_t["trade_date"], real_t.get("rho_real_clean", pd.Series(dtype=float))))
        real_map_d = dict(zip(real_t["trade_date"], real_t.get("rho_real_dirty", pd.Series(dtype=float))))
        n_map = dict(zip(real_t["trade_date"], real_t.get("n_names", pd.Series(dtype=float)))) if "n_names" in real_t.columns else {}

        for mny in mny_list:
            slice_imp = imp[(imp["tenor_days"] == tenor) & np.isclose(imp["mny"], mny)]
            clean_s = _interp_series(slice_imp, target, "rho_imp_clean")
            dirty_s = _interp_series(slice_imp, target, "rho_imp_dirty")
            for variant, imp_s, rmap in (
                ("clean", clean_s, real_map_c),
                ("dirty", dirty_s, real_map_d),
            ):
                rows = []
                for d in target:
                    ri = imp_s.get(d)
                    rr = rmap.get(d)
                    if ri is None or (isinstance(ri, float) and not np.isfinite(ri)):
                        continue
                    rr_v = None if rr is None or (isinstance(rr, float) and not np.isfinite(rr)) else float(rr)
                    rows.append(
                        {
                            "trade_date": d,
                            "tenor_days": int(tenor),
                            "mny": float(mny),
                            "variant": variant,
                            "rho_imp": float(ri),
                            "rho_real": rr_v,
                            "crp": (float(ri) - rr_v) if rr_v is not None else np.nan,
                            "n_members": n_map.get(d, np.nan),
                            "status": "ok",
                        }
                    )
                if rows:
                    frames.append(pd.DataFrame(rows))

    if not frames:
        raise RuntimeError("densify produced no rows")
    out = pd.concat(frames, ignore_index=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "corr_ndx_implied_crp.parquet"
    out.to_parquet(out_path, index=False)
    out.to_csv(out_path.with_suffix(".csv"), index=False)

    # keep a densified surface copy for research consumers (clean/dirty columns)
    surf_rows = []
    for (td, t, m), g in out.groupby(["trade_date", "tenor_days", "mny"]):
        clean = g[g["variant"] == "clean"]
        dirty = g[g["variant"] == "dirty"]
        surf_rows.append(
            {
                "trade_date": td,
                "tenor_days": int(t),
                "mny": float(m),
                "rho_imp_clean": float(clean["rho_imp"].iloc[0]) if len(clean) else np.nan,
                "rho_imp_dirty": float(dirty["rho_imp"].iloc[0]) if len(dirty) else np.nan,
                "rho_imp": float(clean["rho_imp"].iloc[0]) if len(clean) else np.nan,
                "status": "ok",
            }
        )
    surf = pd.DataFrame(surf_rows)
    surf_path = out_dir / "ndx_implied_corr_surface.parquet"
    surf.to_parquet(surf_path, index=False)

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "method": "linear_interpolate_rho_imp_onto_weekly_plus_recent_daily",
        "implied_source": str(imp_path),
        "realized_source": str(real_path),
        "recent_days": int(recent_days),
        "rows": int(len(out)),
        "dates": int(out["trade_date"].nunique()),
        "date_min": str(out["trade_date"].min()),
        "date_max": str(out["trade_date"].max()),
        "tenors": sorted(int(x) for x in out["tenor_days"].unique()),
        "mnys": sorted(float(x) for x in out["mny"].unique()),
        "variants": ["clean", "dirty"],
        "basket": "NDX",
        "top_n": 50,
        "root_pref": "NDXP",
        "note": "Implied densified by calendar interpolation between CMDS surface dates; rho_real from forward realized.",
    }
    (out_dir / "corr_ndx_implied_crp_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    print(f"Wrote {out_path} ({manifest['dates']} dates)")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    ap.add_argument("--recent-days", type=int, default=120)
    args = ap.parse_args()
    densify(Path(args.out_dir), recent_days=args.recent_days)


if __name__ == "__main__":
    main()
