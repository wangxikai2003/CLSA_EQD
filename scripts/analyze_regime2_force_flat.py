#!/usr/bin/env python3
"""A/B: Regime2 stress block-only vs force-flat — local diagnostic (no deploy)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import engine.regime2_signal as r2
from engine.calendar_vol_strategy import run_calendar_vol
from engine.regime2_signal import (
    Regime2Decision,
    ZDay,
    _finite,
    quantile_raw_signal,
    stress_from_z_hist,
)


def decide_block_only(
    position: int,
    hold_days: int,
    spread: float,
    enter_hi: float,
    enter_lo: float,
    exit_hi: float,
    exit_lo: float,
    z_t: float,
    z_q10_t: float,
    z_q90_t: float,
    z_prev: Optional[float] = None,
    z_q10_prev: Optional[float] = None,
    z_q90_prev: Optional[float] = None,
    z_hist: Optional[Sequence[ZDay]] = None,
    stress_streak_days: int = r2.STRESS_STREAK_DAYS,
    max_hold_reverse: int = r2.MAX_HOLD_REVERSE,
) -> Regime2Decision:
    """Pre-force-flat Regime2: stress only blocks new opens."""
    if z_hist is None:
        hist: list[ZDay] = []
        if _finite(z_prev) or _finite(z_q10_prev) or _finite(z_q90_prev):
            hist.append((z_prev, z_q10_prev, z_q90_prev))
        hist.append((z_t, z_q10_t, z_q90_t))
    else:
        hist = list(z_hist)
    stress = stress_from_z_hist(hist, stress_streak_days=stress_streak_days)
    raw = quantile_raw_signal(
        position,
        hold_days,
        spread,
        enter_hi,
        enter_lo,
        exit_hi,
        exit_lo,
        max_hold_reverse=max_hold_reverse,
    )
    pos = int(position)
    import math

    if not all(math.isfinite(x) for x in (enter_hi, enter_lo, exit_hi, exit_lo, spread)):
        return Regime2Decision(pos, stress, "bands_nan_hold" if pos != 0 else "bands_nan")
    if pos == 0:
        if stress and raw != 0:
            return Regime2Decision(0, True, "stress_block_entry")
        if raw == 1:
            return Regime2Decision(1, stress, "quantile_enter_hi")
        if raw == -1:
            return Regime2Decision(-1, stress, "quantile_enter_lo")
        return Regime2Decision(0, stress, "stress_flat" if stress else "quantile_flat")
    if raw == 0:
        reason = "quantile_exit_band"
        if pos == -1 and int(hold_days) >= int(max_hold_reverse):
            if not (exit_lo <= spread <= exit_hi):
                reason = "quantile_max_hold_reverse"
        return Regime2Decision(0, stress, reason)
    return Regime2Decision(pos, stress, "quantile_hold")


def _pack(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    m = (payload.get("metrics") or {}).get("max") or {}
    s = payload.get("summary") or {}
    sig = payload.get("signal") or {}
    reasons = sig.get("signal_reason") or []
    pos = sig.get("position") or []
    dates = sig.get("dates") or []
    from collections import Counter

    rc = Counter([r for r in reasons if r])
    yearly = payload.get("yearly_metrics") or []
    return {
        "label": label,
        "final_mult": s.get("final_nav_multiple"),
        "ann_return": m.get("ann_return"),
        "ann_vol": m.get("ann_vol"),
        "sharpe": m.get("sharpe"),
        "mdd": m.get("mdd"),
        "reason_counts": dict(rc),
        "n_force_flat_signals": rc.get("stress_force_flat", 0),
        "n_block_entry_signals": rc.get("stress_block_entry", 0),
        "yearly": [
            {
                "year": y.get("year"),
                "sharpe": y.get("sharpe"),
                "ann_vol": y.get("ann_vol"),
                "mdd": y.get("mdd"),
                "ann_return": y.get("ann_return"),
            }
            for y in yearly
        ],
        "nav": payload.get("nav") or [],
        "dates": dates,
        "position": pos,
        "reasons": reasons,
        "spread": sig.get("spread") or [],
        "garch_z": sig.get("garch_z") or [],
    }


def _nav_series(nav_rows: List[Dict[str, Any]]) -> pd.Series:
    if not nav_rows:
        return pd.Series(dtype=float)
    return pd.Series({r["d"]: float(r["nav"]) for r in nav_rows}).sort_index()


def main() -> int:
    out_dir = ROOT / "data" / "exports" / "sweeps" / "regime2_force_flat_ab"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("==> run FORCE-FLAT (current)", flush=True)
    r2.decide_regime2_signal = r2.decide_regime2_signal  # ensure original
    # re-import binding used by engine via late import inside loop — patch module attr
    force_payload = run_calendar_vol(
        "300",
        signal_mode="regime2",
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        z_window=252,
    )
    force = _pack("force_flat", force_payload)

    print("==> run BLOCK-ONLY (legacy)", flush=True)
    r2.decide_regime2_signal = decide_block_only  # type: ignore[assignment]
    block_payload = run_calendar_vol(
        "300",
        signal_mode="regime2",
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        z_window=252,
    )
    block = _pack("block_only", block_payload)
    # restore
    import importlib

    importlib.reload(r2)

    nav_f = _nav_series(force["nav"])
    nav_b = _nav_series(block["nav"])
    common = nav_f.index.intersection(nav_b.index)
    diff = (nav_f.reindex(common) - nav_b.reindex(common)).dropna()
    # days where positions diverge
    df_f = pd.DataFrame(
        {
            "d": force["dates"],
            "pos_f": force["position"],
            "reason_f": force["reasons"],
            "spread_f": force["spread"],
            "z_f": force["garch_z"],
        }
    ).set_index("d")
    df_b = pd.DataFrame(
        {
            "d": block["dates"],
            "pos_b": block["position"],
            "reason_b": block["reasons"],
            "spread_b": block["spread"],
            "z_b": block["garch_z"],
        }
    ).set_index("d")
    j = df_f.join(df_b, how="inner")
    diverge = j[j["pos_f"] != j["pos_b"]].copy()

    # Force-flat signal days under current engine
    ff_days = j[j["reason_f"] == "stress_force_flat"].copy()

    # On force-flat days, what would block-only have done with its position?
    # After flatten, next days may stay flat while block-only still holds — measure
    # cumulative NAV gap around those episodes.
    episodes = []
    for d in ff_days.index.tolist():
        # look forward until positions both flat or 40 days
        loc = j.index.get_loc(d)
        if isinstance(loc, slice):
            continue
        end = min(int(loc) + 40, len(j) - 1)
        window = j.iloc[int(loc) : end + 1]
        # NAV at start/end if available
        d0, d1 = window.index[0], window.index[-1]
        n0f, n1f = nav_f.get(d0), nav_f.get(d1)
        n0b, n1b = nav_b.get(d0), nav_b.get(d1)
        # position path length while block held and force flat
        held_b = int((window["pos_b"] != 0).sum())
        held_f = int((window["pos_f"] != 0).sum())
        episodes.append(
            {
                "force_flat_signal_d": d,
                "pos_b_that_day": int(window.iloc[0]["pos_b"])
                if pd.notna(window.iloc[0]["pos_b"])
                else None,
                "reason_b_that_day": window.iloc[0]["reason_b"],
                "spread": window.iloc[0]["spread_f"],
                "z": window.iloc[0]["z_f"],
                "next40_days_held_block": held_b,
                "next40_days_held_force": held_f,
                "nav_force_start": n0f,
                "nav_force_end40": n1f,
                "nav_block_start": n0b,
                "nav_block_end40": n1b,
                "nav_gap_end_minus_start_force_vs_block": (
                    None
                    if None in (n0f, n1f, n0b, n1b)
                    else (float(n1f) - float(n0f)) - (float(n1b) - float(n0b))
                ),
            }
        )

    summary = {
        "force_flat": {
            k: force[k]
            for k in (
                "final_mult",
                "ann_return",
                "ann_vol",
                "sharpe",
                "mdd",
                "reason_counts",
                "yearly",
            )
        },
        "block_only": {
            k: block[k]
            for k in (
                "final_mult",
                "ann_return",
                "ann_vol",
                "sharpe",
                "mdd",
                "reason_counts",
                "yearly",
            )
        },
        "n_days_position_diverge": int(len(diverge)),
        "n_stress_force_flat_signals": int(len(ff_days)),
        "nav_diff_end": float(diff.iloc[-1]) if len(diff) else None,
        "nav_diff_min": float(diff.min()) if len(diff) else None,
        "nav_diff_max": float(diff.max()) if len(diff) else None,
        "force_flat_episodes_head": episodes[:20],
        "mean_episode_nav_gap_force_minus_block": (
            float(
                pd.Series(
                    [
                        e["nav_gap_end_minus_start_force_vs_block"]
                        for e in episodes
                        if e["nav_gap_end_minus_start_force_vs_block"] is not None
                    ]
                ).mean()
            )
            if episodes
            else None
        ),
    }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    diverge.reset_index().to_csv(out_dir / "position_diverge_days.csv", index=False)
    pd.DataFrame(episodes).to_csv(out_dir / "force_flat_episodes.csv", index=False)

    print("\n=== HEADLINE ===", flush=True)
    for lab, pack in ("block_only", block), ("force_flat", force):
        print(
            f"{lab}: sharpe={pack['sharpe']:.4f} vol={pack['ann_vol']:.4f} "
            f"mdd={pack['mdd']:.4f} mult={pack['final_mult']:.4f}",
            flush=True,
        )
    print("reason force:", force["reason_counts"], flush=True)
    print("reason block:", block["reason_counts"], flush=True)
    print(
        f"diverge_days={len(diverge)} force_flat_signals={len(ff_days)} "
        f"nav_diff_end={summary['nav_diff_end']}",
        flush=True,
    )
    print(f"wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
