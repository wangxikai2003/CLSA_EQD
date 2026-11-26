#!/usr/bin/env python3
"""Bootstrap SPX/HSI CRP panels from densified NDX for multi-basket UI wiring.

Replace with CMDS basket runs + build_basket_implied_crp.py when ready.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def seed(out_dir: Path | None = None) -> None:
    out_dir = out_dir or (ROOT / "data")
    ndx = pd.read_parquet(out_dir / "corr_ndx_implied_crp.parquet")
    for basket, shift, scale in (("SPX", 0.02, 0.98), ("HSI", -0.03, 1.05)):
        df = ndx.copy()
        df["rho_imp"] = (df["rho_imp"] * scale + shift).clip(-0.2, 1.2)
        df["crp"] = df["rho_imp"] - df["rho_real"]
        key = basket.lower()
        out = out_dir / f"corr_{key}_implied_crp.parquet"
        df.to_parquet(out, index=False)
        df.to_csv(out.with_suffix(".csv"), index=False)
        surf_rows = []
        for (td, t, m), g in df.groupby(["trade_date", "tenor_days", "mny"]):
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
        pd.DataFrame(surf_rows).to_parquet(out_dir / f"{key}_implied_corr_surface.parquet", index=False)
        man = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "basket": basket,
            "bootstrap_from_ndx": True,
            "rows": int(len(df)),
            "dates": int(df["trade_date"].nunique()),
            "date_min": str(df["trade_date"].min()),
            "date_max": str(df["trade_date"].max()),
            "tenors": sorted(int(x) for x in df["tenor_days"].unique()),
            "mnys": sorted(float(x) for x in df["mny"].unique()),
            "note": "Bootstrap from densified NDX CRP for multi-basket API/UI wiring; replace via CMDS basket run.",
        }
        (out_dir / f"corr_{key}_implied_crp_manifest.json").write_text(json.dumps(man, indent=2))
        print(basket, man["dates"], "dates ->", out)


if __name__ == "__main__":
    seed()
