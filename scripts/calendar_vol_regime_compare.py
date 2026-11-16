#!/usr/bin/env python3
"""Compare quantile / garch_z / regime Calendar Vol on CSI300 3M–6M iv both.

Outputs (under data/exports/):
  - calendar_vol_regime_compare_metrics.json
  - calendar_vol_regime_compare_summary.csv

Windows:
  - full sample (perf start → end per mode)
  - fair window ≥ 2023-02-08
  - 924 window 2024-09-01 → 2024-10-31 (PnL / Vega / Gamma attribution)
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EXPORT_DIR = ROOT / "data" / "exports"
FAIR_START = "2023-02-08"
WIN_924 = ("2024-09-01", "2024-10-31")
MODES = ("quantile", "garch_z", "regime")
UNIVERSE = "300"
TENOR = "3M-6M"
SIDE = "both"
SPREAD = "iv"
INITIAL_NAV = 100_000_000.0


def _run_with_daily(mode: str) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """Run backtest and recover daily NAV/PnL frame from engine rows."""
    from engine.calendar_vol_strategy import CalendarVolBacktestEngine

    eng = CalendarVolBacktestEngine(
        universe=UNIVERSE,
        initial_nav=INITIAL_NAV,
        z_window=252,
        tenor_pair=TENOR,
        side_mode=SIDE,
        spread_mode=SPREAD,
        signal_mode=mode,
    )
    rows_box: Dict[str, Any] = {}
    orig_pack = eng._pack_payload

    def _pack(rows, first_open_i):  # type: ignore[no-untyped-def]
        rows_box["rows"] = rows
        rows_box["first_open_i"] = first_open_i
        return orig_pack(rows, first_open_i)

    eng._pack_payload = _pack  # type: ignore[method-assign]
    t0 = time.time()
    print(f"==> running signal_mode={mode}", flush=True)
    payload = eng.run()
    print(
        f"    done in {time.time()-t0:.1f}s  first_open={payload['summary']['first_open']} "
        f"mult={payload['summary']['final_nav_multiple']}",
        flush=True,
    )
    rows = rows_box["rows"]
    df = pd.DataFrame(rows)
    df["d"] = pd.to_datetime(df["d"])
    return payload, df


def _window_stats(df: pd.DataFrame, start: Optional[str], end: Optional[str] = None) -> Dict[str, Any]:
    """Ann return / vol / Sharpe / MDD on NAV path within [start, end]."""
    w = df
    if start:
        w = w[w["d"] >= pd.Timestamp(start)]
    if end:
        w = w[w["d"] <= pd.Timestamp(end)]
    if len(w) < 2:
        return {
            "n_days": int(len(w)),
            "start": None,
            "end": None,
            "ann_return": None,
            "ann_vol": None,
            "sharpe": None,
            "mdd": None,
            "total_pnl": None,
            "nav_multiple": None,
        }
    nav = w["nav"].astype(float)
    # daily return from NAV
    rets = nav.pct_change().dropna()
    n = len(rets)
    if n == 0 or float(nav.iloc[0]) <= 0:
        return {
            "n_days": 0,
            "start": str(w["d"].iloc[0].date()),
            "end": str(w["d"].iloc[-1].date()),
            "ann_return": None,
            "ann_vol": None,
            "sharpe": None,
            "mdd": None,
            "total_pnl": None,
            "nav_multiple": None,
        }
    total_mult = float(nav.iloc[-1] / nav.iloc[0])
    years = n / 252.0
    ann_ret = total_mult ** (1.0 / years) - 1.0 if years > 0 else None
    vol = float(rets.std(ddof=1) * math.sqrt(252)) if n > 1 else None
    sharpe = (float(rets.mean()) * 252 / vol) if vol and vol > 1e-12 else None
    peak = nav.cummax()
    dd = nav / peak - 1.0
    mdd = float(dd.min())
    return {
        "n_days": int(n),
        "start": str(w["d"].iloc[0].date()),
        "end": str(w["d"].iloc[-1].date()),
        "ann_return": None if ann_ret is None else round(ann_ret, 6),
        "ann_vol": None if vol is None else round(vol, 6),
        "sharpe": None if sharpe is None else round(sharpe, 4),
        "mdd": round(mdd, 6),
        "total_pnl": round(float(nav.iloc[-1] - nav.iloc[0]), 2),
        "nav_multiple": round(total_mult, 6),
    }


def _window_attribution(df: pd.DataFrame, start: str, end: str) -> Dict[str, Any]:
    w = df[(df["d"] >= pd.Timestamp(start)) & (df["d"] <= pd.Timestamp(end))].copy()
    if w.empty:
        return {
            "n_days": 0,
            "total_pnl": None,
            "vega_pnl": None,
            "gamma_pnl": None,
            "theta_pnl": None,
            "basis_pnl": None,
            "net_delta_pnl": None,
            "residual_pnl": None,
            "nav_start": None,
            "nav_end": None,
        }
    # Prefer sum of daily greek PnL inside window (mark-to-market on that day).
    out = {
        "n_days": int(len(w)),
        "start": str(w["d"].iloc[0].date()),
        "end": str(w["d"].iloc[-1].date()),
        "total_pnl": round(float(w["actual_pnl"].sum()), 2),
        "vega_pnl": round(float(w["vega_pnl"].sum()), 2),
        "gamma_pnl": round(float(w["gamma_pnl"].sum()), 2),
        "theta_pnl": round(float(w["theta_pnl"].sum()), 2),
        "basis_pnl": round(float(w["basis_pnl"].sum()), 2),
        "net_delta_pnl": round(float(w["net_delta_pnl"].sum()), 2),
        "residual_pnl": round(float(w["residual_pnl"].sum()), 2),
        "nav_start": round(float(w["nav"].iloc[0]), 2),
        "nav_end": round(float(w["nav"].iloc[-1]), 2),
        "nav_pnl": round(float(w["nav"].iloc[-1] - w["nav"].iloc[0]), 2),
    }
    if "regime_mode" in w.columns:
        # Count executed opens by mode: position change into non-zero with that mode.
        opens_fade = opens_mom = 0
        fade_pnl = mom_pnl = flat_pnl = 0.0
        for i in range(len(w)):
            mode = w["regime_mode"].iloc[i]
            pnl = float(w["actual_pnl"].iloc[i])
            if mode == "fade":
                fade_pnl += pnl
            elif mode == "momentum":
                mom_pnl += pnl
            else:
                flat_pnl += pnl
            if i == 0:
                continue
            prev = int(w["position"].iloc[i - 1])
            curr = int(w["position"].iloc[i])
            if prev == 0 and curr != 0:
                if mode == "fade":
                    opens_fade += 1
                elif mode == "momentum":
                    opens_mom += 1
        out["opens_fade"] = opens_fade
        out["opens_momentum"] = opens_mom
        out["pnl_while_fade"] = round(fade_pnl, 2)
        out["pnl_while_momentum"] = round(mom_pnl, 2)
        out["pnl_while_flat"] = round(flat_pnl, 2)
    return out


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: Dict[str, Any] = {
        "meta": {
            "universe": UNIVERSE,
            "tenor_pair": TENOR,
            "side_mode": SIDE,
            "spread_mode": SPREAD,
            "initial_nav": INITIAL_NAV,
            "fair_start": FAIR_START,
            "window_924": {"start": WIN_924[0], "end": WIN_924[1]},
            "modes": list(MODES),
        },
        "modes": {},
    }
    summary_rows: List[Dict[str, Any]] = []

    for mode in MODES:
        payload, df = _run_with_daily(mode)
        # Align fair window to each mode's available history (garch/regime start later).
        full = _window_stats(df, start=str(payload["summary"]["start"]))
        # Fair: from max(FAIR_START, first_open+1-ish) using perf rows with nav after open
        fair_df = df[df["d"] >= pd.Timestamp(FAIR_START)]
        # Drop leading flat-before-open if any by requiring nav changes from first open day
        first_open = pd.Timestamp(payload["summary"]["first_open"])
        fair_df = fair_df[fair_df["d"] >= first_open]
        fair = _window_stats(fair_df, start=None)
        attr_924 = _window_attribution(df, WIN_924[0], WIN_924[1])

        block = {
            "summary": payload["summary"],
            "params": {
                k: payload["params"].get(k)
                for k in (
                    "signal_mode",
                    "budget_fade",
                    "budget_momentum",
                    "first_open",
                    "perf_start",
                    "z_window",
                )
            },
            "full_sample": full,
            "fair_from_2023_02_08": fair,
            "window_924": attr_924,
            "metrics_max": payload.get("metrics", {}).get("max"),
        }
        results["modes"][mode] = block
        summary_rows.append(
            {
                "mode": mode,
                "first_open": payload["summary"]["first_open"],
                "full_ann_return": full["ann_return"],
                "full_sharpe": full["sharpe"],
                "full_mdd": full["mdd"],
                "full_mult": full["nav_multiple"],
                "fair_ann_return": fair["ann_return"],
                "fair_sharpe": fair["sharpe"],
                "fair_mdd": fair["mdd"],
                "fair_mult": fair["nav_multiple"],
                "924_total_pnl": attr_924["total_pnl"],
                "924_vega": attr_924["vega_pnl"],
                "924_gamma": attr_924["gamma_pnl"],
                "924_theta": attr_924["theta_pnl"],
                "924_opens_fade": attr_924.get("opens_fade"),
                "924_opens_momentum": attr_924.get("opens_momentum"),
                "924_pnl_fade": attr_924.get("pnl_while_fade"),
                "924_pnl_momentum": attr_924.get("pnl_while_momentum"),
            }
        )

    out_json = EXPORT_DIR / "calendar_vol_regime_compare_metrics.json"
    out_csv = EXPORT_DIR / "calendar_vol_regime_compare_summary.csv"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(summary_rows).to_csv(out_csv, index=False)
    print(f"wrote {out_json}", flush=True)
    print(f"wrote {out_csv}", flush=True)
    print(pd.DataFrame(summary_rows).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
