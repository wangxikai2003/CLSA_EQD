"""
Phase-1 end-to-end sanity checks for NDX implied vs realized correlation.

Checks:
  1) Correlation skew: ρ(0.95) > ρ(1.00) > ρ(1.05) on most dates
  2) CRP = ρ_imp − ρ_real (forward) mean > 0
  3) Outlier flags: ρ_imp outside [-0.2, 1.0]
  4) Writes data/correlation_master_panel.parquet for API use
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent


def pct(s: pd.Series, q: float) -> float:
    return float(np.nanpercentile(s.to_numpy(dtype=float), q)) if len(s) else float("nan")


def summary_block(name: str, s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return {"series": name, "n": 0}
    return {
        "series": name,
        "n": int(len(s)),
        "now": float(s.iloc[-1]),
        "avg": float(s.mean()),
        "high": float(s.max()),
        "low": float(s.min()),
        "p10": pct(s, 10),
        "p25": pct(s, 25),
        "p50": pct(s, 50),
        "p75": pct(s, 75),
        "p90": pct(s, 90),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--implied", default=str(ROOT / "data" / "ndx_implied_corr_surface.parquet"))
    ap.add_argument("--realized", default=str(ROOT / "data" / "ndx_realized_corr.parquet"))
    ap.add_argument("--out", default=str(ROOT / "data" / "correlation_master_panel.parquet"))
    args = ap.parse_args()

    imp_path = Path(args.implied)
    real_path = Path(args.realized)
    if not imp_path.exists():
        raise SystemExit(f"missing implied surface: {imp_path}")

    imp = pd.read_parquet(imp_path)
    imp["trade_date"] = imp["trade_date"].astype(str)
    if "rho_imp_clean" not in imp.columns and "rho_imp" in imp.columns:
        imp["rho_imp_clean"] = imp["rho_imp"]
    if "rho_imp_dirty" not in imp.columns:
        imp["rho_imp_dirty"] = np.nan

    print("=== 1) Correlation skew structure ===")
    ok = imp[imp.get("status", "ok") == "ok"].copy() if "status" in imp.columns else imp.copy()
    wide = ok.pivot_table(
        index=["trade_date", "tenor_days"],
        columns="mny",
        values="rho_imp_clean",
        aggfunc="last",
    )
    skew_ok = 0
    skew_n = 0
    for _, row in wide.iterrows():
        cols = [c for c in (0.95, 1.00, 1.05) if c in row.index and pd.notna(row[c])]
        if len(cols) < 3:
            continue
        skew_n += 1
        if row[0.95] > row[1.00] > row[1.05]:
            skew_ok += 1
    print(f"  dates×tenors with full mny grid: {skew_n}")
    print(f"  satisfying ρ(0.95)>ρ(1.00)>ρ(1.05): {skew_ok} ({skew_ok/max(skew_n,1):.1%})")

    print("\n=== 2) Outlier scan ===")
    bad = ok[(ok["rho_imp_clean"] > 1.0) | (ok["rho_imp_clean"] < -0.2)]
    print(f"  rho_imp_clean outliers: {len(bad)}")
    if len(bad):
        print(bad[["trade_date", "tenor_days", "mny", "rho_imp_clean"]].head(20).to_string(index=False))

    master = ok.copy()
    if real_path.exists():
        real = pd.read_parquet(real_path)
        real["trade_date"] = real["trade_date"].astype(str)
        # prefer forward-looking for CRP
        fwd = real[real["mode"] == "forward"].copy() if "mode" in real.columns else real
        # ATM implied join
        atm = ok[np.isclose(ok["mny"].astype(float), 1.0)].copy()
        merged = atm.merge(
            fwd,
            on=["trade_date", "tenor_days"],
            how="inner",
            suffixes=("_imp", "_real"),
        )
        if len(merged):
            merged["crp_clean"] = merged["rho_imp_clean"] - merged["rho_real_clean"]
            merged["crp_dirty"] = merged["rho_imp_dirty"] - merged["rho_real_dirty"]
            print("\n=== 3) CRP summary (Lecture-style) ===")
            rows = [
                summary_block("rho_imp_clean(ATM)", merged["rho_imp_clean"]),
                summary_block("rho_real_clean(fwd)", merged["rho_real_clean"]),
                summary_block("CRP_clean", merged["crp_clean"]),
                summary_block("CRP_dirty", merged["crp_dirty"]),
            ]
            tab = pd.DataFrame(rows)
            print(tab.to_string(index=False))
            mean_crp = float(merged["crp_clean"].mean())
            print(f"\n  mean CRP_clean = {mean_crp:.4f}  ({'PASS >0' if mean_crp > 0 else 'WARN ≤0'})")
            master = merged
        else:
            print("\n=== 3) CRP skipped (no overlapping implied/realized dates) ===")
    else:
        print(f"\n=== 3) CRP skipped (missing {real_path}) ===")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    master.to_parquet(out, index=False)
    master.to_csv(out.with_suffix(".csv"), index=False)
    print(f"\nWrote master panel {len(master):,} rows -> {out}")


if __name__ == "__main__":
    main()
