"""Re-run CMDS coverage probe against data/corr_mark_universe.csv."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from cmds_connect import read_sql
from corr_mark_cmds_mapping import (
    DEFAULT_UNIVERSE,
    IDX_BB,
    eq_bb_candidates,
    eq_wind_candidates,
    fx_windcode,
    load_universe,
)

ROOT = Path(__file__).resolve().parent


def _in_list(values: list[str]) -> str:
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)


def probe(universe: pd.DataFrame, start: str, end: str) -> dict:
    idx = universe[universe["type"] == "IDX"]
    eq = universe[universe["type"] == "EQ"]
    fx = universe[universe["type"] == "FX"]

    # Resolve SEC for rows missing sec_id
    all_bb: list[str] = []
    for _, r in idx.iterrows():
        all_bb.extend(IDX_BB.get(r["calypso"], [f"{r['calypso']} Index", r["calypso"]]))
        if r["bb_ticker"]:
            all_bb.append(r["bb_ticker"])
    for _, r in eq.iterrows():
        all_bb.extend(eq_bb_candidates(r["calypso"]))
        if r["bb_ticker"]:
            all_bb.append(r["bb_ticker"])
    all_bb = sorted(set(b for b in all_bb if b))

    sec_frames = []
    for i in range(0, len(all_bb), 80):
        chunk = all_bb[i : i + 80]
        sec_frames.append(
            read_sql(
                f"""
                SELECT SEC_ID, BB_TICKER, WIND_CODE, ASSET_TYPE
                FROM CDM.SECURITYCODE
                WHERE BB_TICKER IN ({_in_list(chunk)})
                """
            )
        )
    sec = pd.concat(sec_frames, ignore_index=True) if sec_frames else pd.DataFrame()
    by_bb = {str(r.BB_TICKER): r for _, r in sec.iterrows()} if len(sec) else {}

    wind_cands: list[str] = []
    for _, r in eq.iterrows():
        wind_cands.extend(eq_wind_candidates(r["calypso"]))
        if r["wind_code"]:
            wind_cands.append(r["wind_code"])
    wind_cands = sorted(set(wind_cands))
    sec_w = (
        read_sql(
            f"""
            SELECT SEC_ID, BB_TICKER, WIND_CODE, ASSET_TYPE
            FROM CDM.SECURITYCODE
            WHERE WIND_CODE IN ({_in_list(wind_cands)})
            """
        )
        if wind_cands
        else pd.DataFrame()
    )

    rows = []
    for _, r in pd.concat([idx, eq], ignore_index=True).iterrows():
        cal = r["calypso"]
        cands = (
            IDX_BB.get(cal, [f"{cal} Index", cal])
            if r["type"] == "IDX"
            else eq_bb_candidates(cal)
        )
        hit = None
        how = None
        for c in cands:
            if c in by_bb:
                hit = by_bb[c]
                how = "BB_TICKER"
                break
        if hit is None and len(sec_w):
            for w in eq_wind_candidates(cal):
                m = sec_w[sec_w["WIND_CODE"] == w]
                if len(m):
                    hit = m.iloc[0]
                    how = "WIND_CODE"
                    break
        rows.append(
            {
                "calypso": cal,
                "type": r["type"],
                "universe_status": r["status"],
                "found": hit is not None,
                "match_how": how,
                "sec_id": None if hit is None else str(hit.SEC_ID),
                "bb_ticker": None if hit is None else hit.BB_TICKER,
                "wind_code": None if hit is None else hit.WIND_CODE,
            }
        )
    ref = pd.DataFrame(rows)

    # EOD counts
    eod_parts = []
    for typ, table, extra in [
        ("IDX", "CDM.INDEXEOD", ""),
        (
            "EQ",
            "CDM.EQUITYEOD",
            ", SUM(CASE WHEN ADJCLOSE_FORWARD IS NOT NULL THEN 1 ELSE 0 END) N_ADJ",
        ),
    ]:
        ids = ref.loc[(ref["type"] == typ) & ref["found"], "sec_id"].dropna().astype(str).tolist()
        for i in range(0, len(ids), 15):
            chunk = ids[i : i + 15]
            eod_parts.append(
                read_sql(
                    f"""
                    SELECT SEC_ID, COUNT(*) N, MIN(TRADE_DATE) D0, MAX(TRADE_DATE) D1,
                           SUM(CASE WHEN OPEN_PRICE IS NOT NULL THEN 1 ELSE 0 END) N_OPEN,
                           SUM(CASE WHEN CLOSE_PRICE IS NOT NULL THEN 1 ELSE 0 END) N_CLOSE
                           {extra}
                    FROM {table}
                    WHERE SEC_ID IN ({_in_list(chunk)})
                      AND TRADE_DATE BETWEEN '{start}' AND '{end}'
                    GROUP BY SEC_ID
                    """
                )
            )
    eod = pd.concat(eod_parts, ignore_index=True) if eod_parts else pd.DataFrame()
    if len(eod):
        eod["SEC_ID"] = eod["SEC_ID"].astype(str)
        ref = ref.merge(eod, left_on="sec_id", right_on="SEC_ID", how="left")

    # FX
    winds = []
    for _, r in fx.iterrows():
        if r["fx_wind"]:
            winds.append(r["fx_wind"])
        else:
            winds.append(fx_windcode(r["calypso"]))
    winds = sorted(set(winds))
    fx_raw = read_sql(
        f"""
        SELECT WINDCODE, COUNT(*) N, MIN(TRADE_DATE) D0, MAX(TRADE_DATE) D1,
               SUM(CASE WHEN CLOSE IS NOT NULL THEN 1 ELSE 0 END) N_CLOSE
        FROM CDM.FXEOD
        WHERE WINDCODE IN ({_in_list(winds)})
          AND TRADE_DATE BETWEEN '{start}' AND '{end}'
        GROUP BY WINDCODE
        """
    )
    fx_hit = set(fx_raw["WINDCODE"].astype(str)) if len(fx_raw) else set()
    fx_rows = []
    for _, r in fx.iterrows():
        w = r["fx_wind"] or fx_windcode(r["calypso"])
        inv = r.get("invert_of") or ""
        direct = w in fx_hit
        fx_rows.append(
            {
                "calypso": r["calypso"],
                "type": "FX",
                "universe_status": r["status"],
                "found": bool(direct or inv),
                "fx_wind": w,
                "invert_of": inv,
                "direct": direct,
            }
        )
    fx_df = pd.DataFrame(fx_rows)

    summary = {
        "start": start,
        "end": end,
        "idx_found": int(ref[ref.type == "IDX"].found.sum()),
        "idx_total": int((ref.type == "IDX").sum()),
        "eq_found": int(ref[ref.type == "EQ"].found.sum()),
        "eq_total": int((ref.type == "EQ").sum()),
        "fx_ok": int(fx_df.found.sum()),
        "fx_total": len(fx_df),
        "universe_active": int((universe.status == "active").sum()),
    }
    return {"summary": summary, "security": ref, "fx": fx_df}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--start", default="20180101")
    ap.add_argument("--end", default="20261231")
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    args = ap.parse_args()

    uni = load_universe(args.universe)
    result = probe(uni, args.start, args.end)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result["security"].to_csv(out_dir / "corr_mark_cmds_security_coverage.csv", index=False)
    result["fx"].to_csv(out_dir / "corr_mark_cmds_fx_coverage.csv", index=False)
    (out_dir / "corr_mark_cmds_coverage.json").write_text(
        json.dumps(result["summary"], indent=2), encoding="utf-8"
    )
    print(json.dumps(result["summary"], indent=2))
    print(f"Wrote coverage CSVs + JSON under {out_dir}")


if __name__ == "__main__":
    main()
