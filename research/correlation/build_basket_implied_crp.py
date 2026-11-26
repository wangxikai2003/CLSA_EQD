#!/usr/bin/env python3
"""Build web CRP parquet for a basket from surface + forward realized.

Naming:
  input:  {basket_lower}_implied_corr_surface.parquet
          {basket_lower}_realized_corr.parquet  (optional; NDX uses ndx_realized_corr)
  output: corr_{basket_lower}_implied_crp.parquet

For SPX/HSI, drop research surfaces into research/correlation/data/ or CLSA_EQD/data/
then run:
  python build_basket_implied_crp.py --basket SPX
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
CANDIDATES = [
    Path("/Users/wangxikai/Desktop/CITICS CLSA/课题：correlation/data"),
    CORR / "data",
    ROOT / "data",
]


def _find(name: str) -> Path | None:
    for base in CANDIDATES:
        p = base / name
        if p.exists():
            return p
    return None


def _norm_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace("-", "").str.slice(0, 8)).dt.strftime("%Y-%m-%d")


def build(basket: str, out_dir: Path) -> Path:
    b = basket.strip().upper()
    key = b.lower()
    imp_path = _find(f"{key}_implied_corr_surface.parquet")
    if imp_path is None and b == "NDX":
        imp_path = _find("ndx_implied_corr_surface.parquet")
    if imp_path is None:
        raise FileNotFoundError(f"{key}_implied_corr_surface.parquet not found")

    real_path = _find(f"{key}_realized_corr.parquet")
    if real_path is None and b == "NDX":
        real_path = _find("ndx_realized_corr.parquet")

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

    fwd = None
    if real_path is not None:
        real = pd.read_parquet(real_path)
        real["trade_date"] = _norm_date(real["trade_date"])
        real["tenor_days"] = real["tenor_days"].astype(int)
        if "mode" in real.columns:
            real = real[real["mode"].astype(str) == "forward"]
        fwd = real[
            [c for c in ("trade_date", "tenor_days", "rho_real_clean", "rho_real_dirty", "n_names") if c in real.columns]
        ].copy()

    merged = imp.copy()
    if fwd is not None:
        merged = merged.merge(fwd, on=["trade_date", "tenor_days"], how="left", suffixes=("", "_real"))

    frames = []
    n_col = "n_names" if "n_names" in merged.columns else None
    for variant, icol, rcol in (
        ("clean", "rho_imp_clean", "rho_real_clean"),
        ("dirty", "rho_imp_dirty", "rho_real_dirty"),
    ):
        sub = merged.copy()
        sub["variant"] = variant
        sub["rho_imp"] = pd.to_numeric(sub[icol], errors="coerce")
        sub["rho_real"] = pd.to_numeric(sub[rcol], errors="coerce") if rcol in sub.columns else np.nan
        sub["crp"] = sub["rho_imp"] - sub["rho_real"]
        if n_col and n_col in sub.columns:
            sub["n_members"] = pd.to_numeric(sub[n_col], errors="coerce")
        else:
            sub["n_members"] = np.nan
        sub["status"] = sub["status"] if "status" in sub.columns else "ok"
        frames.append(
            sub[
                [
                    "trade_date",
                    "tenor_days",
                    "mny",
                    "variant",
                    "rho_imp",
                    "rho_real",
                    "crp",
                    "n_members",
                    "status",
                ]
            ]
        )

    out = pd.concat(frames, ignore_index=True).dropna(subset=["rho_imp"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"corr_{key}_implied_crp.parquet"
    out.to_parquet(out_path, index=False)
    out.to_csv(out_path.with_suffix(".csv"), index=False)
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "basket": b,
        "implied_source": str(imp_path),
        "realized_source": str(real_path) if real_path else None,
        "rows": int(len(out)),
        "dates": int(out["trade_date"].nunique()),
        "date_min": str(out["trade_date"].min()),
        "date_max": str(out["trade_date"].max()),
        "tenors": sorted(int(x) for x in out["tenor_days"].unique()),
        "mnys": sorted(float(x) for x in out["mny"].unique()),
    }
    (out_dir / f"corr_{key}_implied_crp_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    print(f"Wrote {out_path}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--basket", required=True, help="NDX | SPX | HSI")
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    args = ap.parse_args()
    build(args.basket, Path(args.out_dir))


if __name__ == "__main__":
    main()
