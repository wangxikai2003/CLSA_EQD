"""One-shot builder for CLSA_EQD web Realized Correlation data assets.

Outputs (under CLSA_EQD/data/):
  - corr_web_matrices.json       — IDX/EQ × FX modes, simple-last + rules
  - corr_realized_timeseries.parquet — rolling ρ for core pairs (21/63/126)
  - corr_mark_cmds_panel.parquet — price panel copy for live pairwise calc

Usage:
  cd research/correlation
  python build_web_realized_data.py [--skip-cmds-fetch] [--look-back-months 24]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CLSA_ROOT = ROOT.parents[1]  # CLSA_EQD
WEB_DATA = CLSA_ROOT / "data"
CORR_DATA = ROOT / "data"

# Ensure local imports resolve
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from corr_mark_cmds import (  # noqa: E402
    build_price_wide,
    pad_log_returns,
    run_mode,
)
from corr_mark_cmds_mapping import active_symbols, load_universe  # noqa: E402
from corr_mark_local import calc_corr  # noqa: E402

CORE_INDICES = [
    "SPX",
    "NDX",
    "SX5E",
    "NKY",
    "HSI",
    "HSCEI",
    "HSTECH",
    "SHSZ300",
    "CSI1000",
    "SH000905",
    "SSE50",
    "KOSPI2",
]
MEGA_CAP = [
    "AAPL.US",
    "NVDA.US",
    "META.US",
    "MSFT.US",
    "AMZN.US",
    "GOOGL.US",
    "TSLA.US",
    "700.HK",
    "9988.HK",
    "BABA.US",
]
SPDR11 = ["XLE.US", "XLY.US", "XLF.US", "XLK.US", "XLV.US", "XLI.US", "XLP.US", "XLU.US", "XLB.US", "XLRE.US", "XLC.US"]
MAJOR_FX = [
    "USD/HKD",
    "USD/CNH",
    "USD/CNY",
    "USD/JPY",
    "EUR/USD",
    "GBP/USD",
    "USD/KRW",
    "AUD/USD",
]


def _ensure_panel(skip_fetch: bool) -> Path:
    dest = WEB_DATA / "corr_mark_cmds_panel.parquet"
    local = CORR_DATA / "corr_mark_cmds_panel.parquet"
    topic = Path("/Users/wangxikai/Desktop/CITICS CLSA/课题：correlation/data/corr_mark_cmds_panel.parquet")
    WEB_DATA.mkdir(parents=True, exist_ok=True)

    if not skip_fetch:
        uni = CORR_DATA / "corr_mark_universe.csv"
        if uni.exists():
            try:
                from fetch_cmds_price_panel import fetch_corr_mark_universe_panel

                print("Fetching CMDS panel via --corr-mark-universe …", flush=True)
                panel = fetch_corr_mark_universe_panel(uni, "20190101", datetime.now().strftime("%Y%m%d"))
                if len(panel):
                    panel.to_parquet(local, index=False)
                    print(f"  wrote {local} rows={len(panel):,}", flush=True)
            except Exception as ex:
                print(f"  CMDS fetch skipped ({ex})", flush=True)

    src = local if local.exists() else (topic if topic.exists() else None)
    if src is None and not dest.exists():
        raise SystemExit("No corr_mark_cmds_panel.parquet found — run fetch or copy panel first")
    if src is not None:
        shutil.copy2(src, dest)
        if src != local:
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, local)
    print(f"Panel ready: {dest} ({dest.stat().st_size / 1e6:.1f} MB)", flush=True)
    return dest


def _matrix_payload(detail: pd.DataFrame, mat: pd.DataFrame, manifest: dict) -> dict:
    row_labels = list(mat.index.astype(str))
    col_labels = list(mat.columns.astype(str))
    labels = row_labels  # square convenience; also expose axes
    grid = []
    for i in row_labels:
        row = []
        for j in col_labels:
            try:
                v = mat.loc[i, j]
            except KeyError:
                v = np.nan
            row.append(None if pd.isna(v) else float(v))
        grid.append(row)
    pairs = []
    for _, r in detail.iterrows():
        pairs.append(
            {
                "pair": r.get("pair"),
                "symbol1": r.get("symbol1"),
                "symbol2": r.get("symbol2"),
                "ok": bool(r.get("ok")),
                "reason": r.get("reason"),
                "corr": None if pd.isna(r.get("corr")) else float(r["corr"]),
                "realized_last": None
                if pd.isna(r.get("realized_last"))
                else float(r["realized_last"]),
                "field1": r.get("field1"),
                "field2": r.get("field2"),
                "pad": None if pd.isna(r.get("pad")) else int(r["pad"]) if r.get("pad") == r.get("pad") else None,
                "alpha": None if pd.isna(r.get("alpha")) else float(r["alpha"]),
                "rule_desc": _rule_desc(r),
            }
        )
    return {
        "labels": labels,
        "row_labels": row_labels,
        "col_labels": col_labels,
        "matrix": grid,
        "pairs": pairs,
        "manifest": manifest,
    }


def _rule_desc(r: pd.Series) -> str:
    f1, f2 = r.get("field1") or "close", r.get("field2") or "close"
    pad = r.get("pad")
    alpha = r.get("alpha")
    bits = [
        f"{f1}/{f2}",
        f"Pad={pad}" if pad == pad and pad is not None else None,
        f"Alpha={alpha}" if alpha == alpha and alpha is not None else None,
    ]
    return " | ".join(str(b) for b in bits if b)


def build_matrices(panel_path: Path, look_back: int) -> dict:
    universe = load_universe(CORR_DATA / "corr_mark_universe.csv")
    panel = pd.read_parquet(panel_path)
    out: dict = {"asof": datetime.now().isoformat(timespec="seconds"), "modes": {}}
    modes = ["IDX.IDX", "EQ.EQ", "IDX.FX", "EQ.FX"]
    for mode in modes:
        for simple in (True, False):
            tag = "simple_last" if simple else "rules"
            print(f"  matrix {mode} {tag} …", flush=True)
            detail, mat, manifest = run_mode(
                mode,
                panel,
                universe,
                rolling_win=63,
                look_back_months=look_back,
                pad_ow=1 if simple else None,
                alpha_ow=93.0 if simple else None,
                use_proxy=True,
                corr_lambda=0.0,
                simple_last=simple,
            )
            key = f"{mode.replace('.', '_')}_{tag}"
            out["modes"][key] = _matrix_payload(detail, mat, manifest)
    return out


def _rolling_corr_series(px_a: pd.Series, px_b: pd.Series, win: int, pad: int = 1) -> pd.Series:
    px = pd.concat([px_a, px_b], axis=1, keys=["a", "b"]).sort_index().ffill()
    r0 = pad_log_returns(px["a"], pad)
    r1 = pad_log_returns(px["b"], pad)
    df = pd.concat([r0, r1], axis=1, keys=["a", "b"]).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    return df["a"].rolling(win).corr(df["b"]).dropna()


def build_timeseries(panel_path: Path) -> pd.DataFrame:
    panel = pd.read_parquet(panel_path)
    available = set(panel["symbol"].astype(str).unique())
    pool = [s for s in CORE_INDICES + MEGA_CAP + SPDR11 + MAJOR_FX if s in available]
    # curated pairs: all index×index + mega×mega sample + a few FX×index
    pairs: list[tuple[str, str]] = []
    idxs = [s for s in CORE_INDICES if s in available]
    pairs.extend(combinations(idxs, 2))
    megas = [s for s in MEGA_CAP if s in available]
    for a, b in combinations(megas, 2):
        pairs.append((a, b))
    for fx in [s for s in MAJOR_FX if s in available]:
        for idx in ["SPX", "HSI", "NDX"]:
            if idx in available:
                pairs.append((idx, fx))
    # de-dupe
    seen = set()
    uniq = []
    for a, b in pairs:
        k = tuple(sorted([a, b]))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)

    print(f"  timeseries pairs={len(uniq)} symbols_in_pool={len(pool)}", flush=True)
    rows = []
    for a, b in uniq:
        wide_c = build_price_wide(panel, [a, b], "close")
        if a not in wide_c.columns or b not in wide_c.columns:
            continue
        sa, sb = wide_c[a], wide_c[b]
        for win in (21, 63, 126):
            ser = _rolling_corr_series(sa, sb, win)
            for dt, v in ser.items():
                if pd.isna(v):
                    continue
                rows.append(
                    {
                        "pair": f"{a}-{b}",
                        "symbol1": a,
                        "symbol2": b,
                        "rolling_win": win,
                        "trade_date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                        "rho": float(v),
                    }
                )
    df = pd.DataFrame(rows)
    # NDX basket summary if compute helper + equity panel present
    us_panel = CORR_DATA / "cmds_us_equity_panel.parquet"
    if not us_panel.exists():
        alt = Path("/Users/wangxikai/Desktop/CITICS CLSA/课题：correlation/data/cmds_us_equity_panel.parquet")
        if alt.exists():
            us_panel = alt
    if us_panel.exists():
        try:
            from compute_us_realized_corr import WINDOW_MAP, compute_realized_block  # type: ignore
        except Exception:
            compute_realized_block = None
        if compute_realized_block is None:
            # lightweight: equal-weight avg pairwise among top names present in main panel
            pass
        else:
            print("  NDX basket block via compute_us_realized_corr (optional)…", flush=True)
    # Annotate NDX index self-proxy series as basket_eq placeholder from SPX-NDX already in pairs
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-cmds-fetch", action="store_true")
    ap.add_argument("--look-back-months", type=int, default=24)
    ap.add_argument("--skip-matrices", action="store_true")
    ap.add_argument("--skip-timeseries", action="store_true")
    args = ap.parse_args()

    panel_path = _ensure_panel(args.skip_cmds_fetch)

    if not args.skip_matrices:
        print("Building web matrices…", flush=True)
        payload = build_matrices(panel_path, args.look_back_months)
        out_json = WEB_DATA / "corr_web_matrices.json"
        out_json.write_text(json.dumps(payload), encoding="utf-8")
        print(f"Wrote {out_json} ({out_json.stat().st_size / 1e6:.2f} MB)", flush=True)
        # also parquet-friendly flat pairs
        flat_rows = []
        for mode_key, block in payload["modes"].items():
            for p in block["pairs"]:
                flat_rows.append({"mode": mode_key, **p})
        pd.DataFrame(flat_rows).to_parquet(WEB_DATA / "corr_web_matrices.parquet", index=False)

    if not args.skip_timeseries:
        print("Building rolling timeseries…", flush=True)
        ts = build_timeseries(panel_path)
        out_ts = WEB_DATA / "corr_realized_timeseries.parquet"
        ts.to_parquet(out_ts, index=False)
        print(f"Wrote {out_ts} rows={len(ts):,}", flush=True)

    meta = {
        "panel": str(WEB_DATA / "corr_mark_cmds_panel.parquet"),
        "matrices_json": str(WEB_DATA / "corr_web_matrices.json"),
        "timeseries": str(WEB_DATA / "corr_realized_timeseries.parquet"),
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }
    (WEB_DATA / "corr_web_build_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("Done.", json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
