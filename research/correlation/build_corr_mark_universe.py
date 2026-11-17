"""Build data/corr_mark_universe.csv from coverage probe CSVs + internal baskets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def classify_eq_idx(r: pd.Series) -> str:
    status = r.get("price_status") or ""
    found = bool(r["found"]) if isinstance(r["found"], bool) else str(r["found"]).lower() == "true"
    if not found or status == "NO_SECURITY":
        return "skip_no_security"
    if status == "SEC_NO_EOD" or pd.isna(r.get("N")) or (pd.notna(r.get("N")) and r["N"] == 0):
        return "skip_no_security"
    n = r.get("N")
    if pd.notna(n) and n < 500:
        return "skip_short_hist"
    return "active"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec", default=str(ROOT / "data" / "corr_mark_cmds_security_coverage.csv"))
    ap.add_argument("--fx", default=str(ROOT / "data" / "corr_mark_cmds_fx_coverage.csv"))
    ap.add_argument("--baskets", default=str(ROOT / "data" / "corr_mark_internal_baskets.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "corr_mark_universe.csv"))
    args = ap.parse_args()

    sec = pd.read_csv(args.sec)
    fx = pd.read_csv(args.fx)
    internal = json.loads(Path(args.baskets).read_text(encoding="utf-8"))["internal_exclude_list"]

    rows = []
    for _, r in sec.iterrows():
        rows.append(
            {
                "calypso": r["calypso"],
                "type": r["type"],
                "status": classify_eq_idx(r),
                "sec_id": "" if pd.isna(r.get("sec_id")) else str(r["sec_id"]),
                "bb_ticker": "" if pd.isna(r.get("bb_ticker")) else str(r["bb_ticker"]),
                "wind_code": "" if pd.isna(r.get("wind_code")) else str(r["wind_code"]),
                "n_obs": "" if pd.isna(r.get("N")) else int(r["N"]),
                "d0": "" if pd.isna(r.get("D0")) else str(r["D0"]).split(".")[0],
                "d1": "" if pd.isna(r.get("D1")) else str(r["D1"]).split(".")[0],
                "fx_wind": "",
                "invert_of": "",
                "note": r.get("price_status") or "",
            }
        )

    invert_map = {
        "CHF/USD": "USD/CHF",
        "CNH/USD": "USD/CNH",
        "CNY/USD": "USD/CNY",
        "HKD/USD": "USD/HKD",
        "KRW/USD": "USD/KRW",
        "SGD/USD": "USD/SGD",
    }
    for _, r in fx.iterrows():
        found = bool(r["found"]) if isinstance(r["found"], bool) else str(r["found"]).lower() == "true"
        cal = r["calypso"]
        if found:
            rows.append(
                {
                    "calypso": cal,
                    "type": "FX",
                    "status": "active",
                    "sec_id": "",
                    "bb_ticker": "" if pd.isna(r.get("bb_ticker")) else str(r["bb_ticker"]),
                    "wind_code": "",
                    "n_obs": "" if pd.isna(r.get("N")) else int(r["N"]),
                    "d0": "" if pd.isna(r.get("D0")) else str(r["D0"]),
                    "d1": "" if pd.isna(r.get("D1")) else str(r["D1"]),
                    "fx_wind": r.get("wind_code") or r.get("fx_wind") or "",
                    "invert_of": "",
                    "note": "direct",
                }
            )
        elif cal in invert_map:
            rows.append(
                {
                    "calypso": cal,
                    "type": "FX",
                    "status": "active",
                    "sec_id": "",
                    "bb_ticker": "",
                    "wind_code": "",
                    "n_obs": "",
                    "d0": "",
                    "d1": "",
                    "fx_wind": "",
                    "invert_of": invert_map[cal],
                    "note": "invert_1x",
                }
            )
        else:
            rows.append(
                {
                    "calypso": cal,
                    "type": "FX",
                    "status": "skip_no_security",
                    "sec_id": "",
                    "bb_ticker": "",
                    "wind_code": "",
                    "n_obs": "",
                    "d0": "",
                    "d1": "",
                    "fx_wind": r.get("wind_code") or "",
                    "invert_of": "",
                    "note": "no_fxeod",
                }
            )

    for b in internal:
        rows.append(
            {
                "calypso": b,
                "type": "BASKET",
                "status": "skip_exclude_basket",
                "sec_id": "",
                "bb_ticker": "",
                "wind_code": "",
                "n_obs": "",
                "d0": "",
                "d1": "",
                "fx_wind": "",
                "invert_of": "",
                "note": "exclude_list_internal",
            }
        )

    uni = pd.DataFrame(rows).drop_duplicates("calypso", keep="first")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    uni.to_csv(out, index=False)
    print(uni.groupby(["type", "status"]).size().to_string())
    print(f"Wrote {out} rows={len(uni)}")


if __name__ == "__main__":
    main()
