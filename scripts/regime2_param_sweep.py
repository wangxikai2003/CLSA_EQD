#!/usr/bin/env python3
"""Light Regime2 parameter sweep (OFAT ±delta step 1) → local parquet/csv/json.

Default baseline (percent ints): enter=90, exit_lo=40, exit_hi=60, stress_streak=2,
max_hold_reverse=20. One-factor-at-a-time around baseline keeps compute mild.

Usage (from CLSA_EQD/):
  PYTHONPATH=backend python scripts/regime2_param_sweep.py
  PYTHONPATH=backend python scripts/regime2_param_sweep.py --n-jobs 2 --delta 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.calendar_vol_strategy import run_calendar_vol  # noqa: E402

BASE = {
    "enter_pct": 90,
    "exit_lo_pct": 40,
    "exit_hi_pct": 60,
    "stress_streak_days": 2,
    "max_hold_reverse": 20,
}


def _pct_to_q(pct: int) -> float:
    return float(pct) / 100.0


def _metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
    m = payload.get("metrics") or {}
    mx = m.get("max") or {}
    y1 = m.get("1y") or {}
    summary = payload.get("summary") or {}
    params = payload.get("params") or {}
    yearly = payload.get("yearly_metrics") or []
    by_year = {
        str(r.get("year")): {
            "ann_return": r.get("ann_return"),
            "ann_vol": r.get("ann_vol"),
            "sharpe": r.get("sharpe"),
            "mdd": r.get("mdd"),
        }
        for r in yearly
        if r.get("year") is not None
    }
    return {
        "final_nav_multiple": summary.get("final_nav_multiple"),
        "full_ann_return": mx.get("ann_return"),
        "full_ann_vol": mx.get("ann_vol"),
        "full_sharpe": mx.get("sharpe"),
        "full_mdd": mx.get("mdd"),
        "y1_sharpe": y1.get("sharpe"),
        "y1_mdd": y1.get("mdd"),
        "first_open": params.get("first_open"),
        "perf_start": params.get("perf_start"),
        "yearly": by_year,
    }


def build_ofat_grid(delta: int = 5, step: int = 1) -> List[Dict[str, int]]:
    """One-factor-at-a-time: each knob ∈ [base−delta, base+delta] step ``step``."""
    trials: List[Dict[str, int]] = []
    seen = set()

    def _add(row: Dict[str, int]) -> None:
        key = tuple(sorted(row.items()))
        if key in seen:
            return
        # Keep exit band ordered and non-degenerate.
        if not (0 < row["exit_lo_pct"] < row["exit_hi_pct"] < 100):
            return
        if not (50 < row["enter_pct"] < 100):
            return
        if row["stress_streak_days"] < 1 or row["max_hold_reverse"] < 1:
            return
        seen.add(key)
        trials.append(dict(row))

    _add(dict(BASE))

    def _range(center: int) -> range:
        return range(center - delta, center + delta + 1, step)

    for v in _range(BASE["enter_pct"]):
        row = dict(BASE)
        row["enter_pct"] = v
        _add(row)
    for v in _range(BASE["exit_lo_pct"]):
        row = dict(BASE)
        row["exit_lo_pct"] = v
        _add(row)
    for v in _range(BASE["exit_hi_pct"]):
        row = dict(BASE)
        row["exit_hi_pct"] = v
        _add(row)
    for v in _range(BASE["stress_streak_days"]):
        row = dict(BASE)
        row["stress_streak_days"] = v
        _add(row)
    for v in _range(BASE["max_hold_reverse"]):
        row = dict(BASE)
        row["max_hold_reverse"] = v
        _add(row)
    return trials


def run_one(
    trial: Dict[str, int],
    universe: str,
    tenor_pair: str,
    side_mode: str,
    spread_mode: str,
    z_window: int,
    initial_nav: float,
) -> Dict[str, Any]:
    t0 = time.time()
    try:
        payload = run_calendar_vol(
            universe=universe,
            initial_nav=initial_nav,
            z_window=z_window,
            tenor_pair=tenor_pair,
            side_mode=side_mode,
            spread_mode=spread_mode,
            signal_mode="regime2",
            enter_q=_pct_to_q(trial["enter_pct"]),
            exit_lo_q=_pct_to_q(trial["exit_lo_pct"]),
            exit_hi_q=_pct_to_q(trial["exit_hi_pct"]),
            stress_streak_days=trial["stress_streak_days"],
            max_hold_reverse=trial["max_hold_reverse"],
        )
        met = _metrics(payload)
        err = None
    except Exception as exc:  # noqa: BLE001 — sweep must continue
        met = {}
        err = f"{type(exc).__name__}: {exc}"
    elapsed = time.time() - t0
    row = {
        **trial,
        **{k: v for k, v in met.items() if k != "yearly"},
        "yearly_json": json.dumps(met.get("yearly") or {}, ensure_ascii=False),
        "error": err,
        "elapsed_sec": round(elapsed, 2),
    }
    tag = (
        f"e{trial['enter_pct']}_lo{trial['exit_lo_pct']}_hi{trial['exit_hi_pct']}"
        f"_s{trial['stress_streak_days']}_h{trial['max_hold_reverse']}"
    )
    print(
        f"[{tag}] sharpe={row.get('full_sharpe')} mdd={row.get('full_mdd')} "
        f"mult={row.get('final_nav_multiple')}  ({elapsed:.1f}s)"
        + (f" ERR {err}" if err else ""),
        flush=True,
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Regime2 light OFAT parameter sweep")
    ap.add_argument("--delta", type=int, default=5, help="± range around baseline")
    ap.add_argument("--step", type=int, default=1, help="grid step (integer pct / days)")
    ap.add_argument("--n-jobs", type=int, default=1, help="parallel workers (joblib)")
    ap.add_argument("--universe", default="300")
    ap.add_argument("--tenor", default="3M-6M")
    ap.add_argument("--side", default="both")
    ap.add_argument("--spread", default="iv")
    ap.add_argument("--z-window", type=int, default=252)
    ap.add_argument("--nav", type=float, default=100_000_000.0)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="default: data/exports/sweeps/regime2_ofat_<ts>",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="if >0, only run first N trials (smoke)",
    )
    args = ap.parse_args()

    trials = build_ofat_grid(delta=args.delta, step=args.step)
    if args.limit and args.limit > 0:
        trials = trials[: args.limit]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or (ROOT / "data" / "exports" / "sweeps" / f"regime2_ofat_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "created": ts,
        "mode": "ofat",
        "delta": args.delta,
        "step": args.step,
        "baseline": BASE,
        "n_trials": len(trials),
        "universe": args.universe,
        "tenor_pair": args.tenor,
        "side_mode": args.side,
        "spread_mode": args.spread,
        "z_window": args.z_window,
        "signal_mode": "regime2",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Regime2 OFAT sweep: {len(trials)} trials → {out_dir}", flush=True)

    rows: List[Dict[str, Any]]
    if args.n_jobs and args.n_jobs > 1:
        from joblib import Parallel, delayed

        rows = Parallel(n_jobs=args.n_jobs, verbose=5)(
            delayed(run_one)(
                t,
                args.universe,
                args.tenor,
                args.side,
                args.spread,
                args.z_window,
                args.nav,
            )
            for t in trials
        )
    else:
        rows = [
            run_one(
                t,
                args.universe,
                args.tenor,
                args.side,
                args.spread,
                args.z_window,
                args.nav,
            )
            for t in trials
        ]

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "trials.csv", index=False)
    try:
        df.to_parquet(out_dir / "trials.parquet", index=False)
    except Exception:  # noqa: BLE001
        pass

    ok = df[df["error"].isna()].copy() if "error" in df.columns else df.copy()
    if not ok.empty and "full_sharpe" in ok.columns:
        ok_sorted = ok.sort_values(
            ["full_sharpe", "full_mdd"], ascending=[False, True], na_position="last"
        )
        best = ok_sorted.head(10).to_dict(orient="records")
        # Drop bulky yearly for best.json readability
        for b in best:
            b.pop("yearly_json", None)
        (out_dir / "best.json").write_text(
            json.dumps({"top10_by_sharpe": best, "baseline": BASE}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print("\nTop by full_sharpe:", flush=True)
        cols = [
            "enter_pct",
            "exit_lo_pct",
            "exit_hi_pct",
            "stress_streak_days",
            "max_hold_reverse",
            "full_sharpe",
            "full_mdd",
            "final_nav_multiple",
        ]
        print(ok_sorted[cols].head(8).to_string(index=False), flush=True)

    print(f"\nWrote {out_dir / 'trials.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
