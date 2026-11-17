"""CMDS-backed realized / bumped correlation (corr_mark core, no desk deps).

Modes: IDX.IDX, EQ.EQ, EQ.FX, IDX.FX
Phase B: same-region LAST×LAST
Phase C: cross-region OPEN/LAST, proxy/force_proxy, FXConv (no T170)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from itertools import combinations, product
from pathlib import Path

import numpy as np
import pandas as pd

from corr_mark_cmds_mapping import (
    DEFAULT_UNIVERSE,
    FORCE_PROXY,
    PROXY_INFO,
    active_symbols,
    currency_of,
    load_universe,
    region_of,
    subregion_of,
)
from corr_mark_local import calc_corr

ROOT = Path(__file__).resolve().parent
DEFAULT_PANEL = ROOT / "data" / "corr_mark_cmds_panel.parquet"


def build_price_wide(panel: pd.DataFrame, symbols: list[str], field: str) -> pd.DataFrame:
    """field: close|open — prefers adj_* then raw.

    For open: adj_open → open → adj_close → close (HSTECH partial OPEN falls back to CLOSE).
    """
    sub = panel[panel["symbol"].isin(symbols)].copy()
    if field == "close":
        cascade = ["adj_close", "close"]
    else:
        cascade = ["adj_open", "open", "adj_close", "close"]
    cascade = [c for c in cascade if c in sub.columns]
    if not cascade:
        raise ValueError(f"panel missing price columns for field={field}")
    sub["_px"] = sub[cascade[0]]
    for c in cascade[1:]:
        sub["_px"] = sub["_px"].where(sub["_px"].notna(), sub[c])
    wide = (
        sub.pivot_table(index="trade_date", columns="symbol", values="_px", aggfunc="last")
        .sort_index()
        .ffill()
    )
    wide.index = pd.to_datetime(wide.index.astype(str))
    return wide


def pad_log_returns(prices: pd.Series, pad: int) -> pd.Series:
    return np.log(prices / prices.shift(pad))


def apply_fx_conv(
    prices: pd.Series, fx_prices: pd.Series | None, divide: bool = True
) -> pd.Series:
    if fx_prices is None:
        return prices
    aligned = fx_prices.reindex(prices.index).ffill()
    if divide:
        return prices / aligned
    return prices * aligned


def pair_rules(
    u1: str,
    u2: str,
    pad_ow: int | None,
    alpha_ow: float | None,
    use_proxy: bool,
) -> list[dict]:
    """Simplified get_pair_list_one → list of leg specs."""
    r1, r2 = region_of(u1), region_of(u2)
    s1, s2 = subregion_of(u1), subregion_of(u2)
    legs = [
        {"calypso": u1, "region": r1, "subregion": s1, "currency": currency_of(u1)},
        {"calypso": u2, "region": r2, "subregion": s2, "currency": currency_of(u2)},
    ]

    # force proxy → price under mapped equity symbol
    for leg in legs:
        fp = FORCE_PROXY.get(leg["calypso"])
        if fp:
            leg["proxy_symbol"] = _bb_to_calypso_equity(fp[0])
            if fp[1] == "HKDCNH":
                leg["fxconv"] = "HKD/CNH"
            elif fp[1] == "USDHKD":
                leg["fxconv"] = "USD/HKD"
            elif fp[1] == "HKDUSD":
                leg["fxconv"] = "HKD/USD"
            leg["currency"] = fp[2]

    # ADR proxy when crossing regions (Phase C)
    if use_proxy:
        for i, j in ((0, 1), (1, 0)):
            info = PROXY_INFO.get(legs[i]["calypso"])
            if not info:
                continue
            # apply when other leg region is APAC/EMEA for US ADR, or NAM for HK
            other_r = legs[j]["region"]
            src_r = legs[i]["region"]
            if src_r == "NAM" and other_r in ("APAC", "EMEA"):
                legs[i]["proxy_symbol"] = _bb_to_calypso_equity(info[0])
                legs[i]["fxconv"] = "USD/HKD" if info[1] == "USDHKD" else None
            elif src_r == "APAC" and other_r == "NAM":
                legs[i]["proxy_symbol"] = _bb_to_calypso_equity(info[0])
                legs[i]["fxconv"] = "HKD/USD" if info[1] == "HKDUSD" else None

    # FX leg modes handled by caller; here equity/index fields
    both_eq = "." in u1 and "." in u2
    regions = sorted([legs[0]["region"], legs[1]["region"]])

    configs: list[tuple[str, str, int, float]] = []  # field1, field2, pad, alpha

    if legs[0]["subregion"] == legs[1]["subregion"] != "Unknown":
        pad = pad_ow if pad_ow is not None else 1
        alpha = alpha_ow if alpha_ow is not None else (90.0 if both_eq else 93.0)
        configs.append(("close", "close", pad, alpha))
    elif legs[0]["region"] == legs[1]["region"] != "Unknown":
        pad = pad_ow if pad_ow is not None else 3
        alpha = alpha_ow if alpha_ow is not None else (95.0 if both_eq else 93.0)
        configs.append(("close", "close", pad, alpha))
    elif regions == ["APAC", "EMEA"]:
        pad = pad_ow if pad_ow is not None else (5 if both_eq else 3)
        alpha = alpha_ow if alpha_ow is not None else (99.0 if both_eq else 93.0)
        # APAC LAST, EMEA OPEN
        f = ["close" if legs[i]["region"] == "APAC" else "open" for i in (0, 1)]
        configs.append((f[0], f[1], pad, alpha))
    elif regions == ["APAC", "NAM"]:
        pad = pad_ow if pad_ow is not None else (5 if both_eq else 3)
        alpha = alpha_ow if alpha_ow is not None else (99.0 if both_eq else 93.0)
        f = ["close" if legs[i]["region"] == "APAC" else "open" for i in (0, 1)]
        configs.append((f[0], f[1], pad, alpha))
    elif regions == ["EMEA", "NAM"]:
        pad = pad_ow if pad_ow is not None else 5
        alpha = alpha_ow if alpha_ow is not None else (99.0 if both_eq else 90.0)
        for f1, f2 in product(["open", "close"], repeat=2):
            configs.append((f1, f2, pad, alpha))
    else:
        # fallback LAST×LAST
        pad = pad_ow if pad_ow is not None else 1
        alpha = alpha_ow if alpha_ow is not None else 93.0
        configs.append(("close", "close", pad, alpha))

    out = []
    for f1, f2, pad, alpha in configs:
        spec = {
            "legs": [
                {**legs[0], "field": f1, "pad": pad, "alpha": alpha},
                {**legs[1], "field": f2, "pad": pad, "alpha": alpha},
            ],
            "pad": pad,
            "alpha": alpha,
        }
        out.append(spec)
    return out


def _bb_to_calypso_equity(bb: str) -> str:
    # "9988 HK" → "9988.HK"; "300750 C2" → "300750.CH"; "BABA US" → "BABA.US"
    parts = bb.replace(" Equity", "").split()
    if len(parts) >= 2:
        suf = parts[1]
        if suf in ("C1", "C2", "CH", "CG"):
            return f"{parts[0]}.CH"
        return f"{parts[0]}.{suf}"
    return bb


def resolve_leg_symbol(leg: dict) -> str:
    return leg.get("proxy_symbol") or leg["calypso"]


def fx_pair_specs(eq_or_idx: str, fx: str, pad_ow, alpha_ow) -> list[dict]:
    pad = pad_ow if pad_ow is not None else 3
    alpha = alpha_ow if alpha_ow is not None else 95.0
    return [
        {
            "legs": [
                {
                    "calypso": eq_or_idx,
                    "region": region_of(eq_or_idx),
                    "subregion": subregion_of(eq_or_idx),
                    "currency": currency_of(eq_or_idx),
                    "field": "close",
                    "pad": pad,
                    "alpha": alpha,
                },
                {
                    "calypso": fx,
                    "region": "FX",
                    "subregion": "FX",
                    "currency": "FX",
                    "field": "close",
                    "pad": pad,
                    "alpha": alpha,
                },
            ],
            "pad": pad,
            "alpha": alpha,
            "fx_mode": True,
        }
    ]


def series_for_leg(panel: pd.DataFrame, leg: dict, price_cache: dict) -> pd.Series | None:
    sym = resolve_leg_symbol(leg)
    field = leg["field"]
    key = (sym, field)
    if key not in price_cache:
        wide = build_price_wide(panel, [sym], field)
        if sym not in wide.columns:
            price_cache[key] = None
        else:
            price_cache[key] = wide[sym]
    px = price_cache[key]
    if px is None:
        return None
    fxconv = leg.get("fxconv")
    if fxconv:
        fx_key = (fxconv, "close")
        if fx_key not in price_cache:
            fw = build_price_wide(panel, [fxconv], "close")
            price_cache[fx_key] = fw[fxconv] if fxconv in fw.columns else None
        fx_px = price_cache[fx_key]
        if fx_px is None:
            return None
        # USDHKD: divide HK price by USDHKD when converting US ADR→HK path etc.
        # Mirror source: divide equity by FXConv spot
        px = apply_fx_conv(px, fx_px, divide=True)
    return px


def compute_pair(
    panel: pd.DataFrame,
    spec: dict,
    rolling_win: int,
    el: int,
    up: bool,
    price_cache: dict,
    look_back_months: int = 24,
) -> dict:
    legs = spec["legs"]
    s0 = series_for_leg(panel, legs[0], price_cache)
    s1 = series_for_leg(panel, legs[1], price_cache)
    if s0 is None or s1 is None:
        return {
            "ok": False,
            "reason": "missing_price",
            "corr": np.nan,
            "realized_corr": np.nan,
            "lambda": np.nan,
            "window": "NA",
            "n_obs": 0,
        }
    pad = spec["pad"]
    # Union calendar + ffill (matches corr_mark_local / BBG outer-join behaviour)
    px = pd.concat([s0, s1], axis=1, keys=["a", "b"]).sort_index().ffill()
    r0 = pad_log_returns(px["a"], pad)
    r1 = pad_log_returns(px["b"], pad)
    df = pd.concat([r0, r1], axis=1, keys=["a", "b"]).dropna()
    df = df[(df.abs() > 1e-8).any(axis=1)]
    if len(df):
        end = df.index.max()
        start = end - pd.DateOffset(months=look_back_months + 2)
        df = df.loc[start:end].iloc[-el:]
    if len(df) < rolling_win + 5:
        return {
            "ok": False,
            "reason": "hist_short",
            "corr": np.nan,
            "realized_corr": np.nan,
            "lambda": np.nan,
            "window": f"n={len(df)}",
            "n_obs": len(df),
        }
    l, corr, realized, date_str = calc_corr(df, rolling_win, spec["alpha"], el, up)
    return {
        "ok": True,
        "reason": "ok",
        "corr": corr,
        "realized_corr": realized,
        "lambda": l,
        "window": date_str,
        "n_obs": len(df),
        "field1": legs[0]["field"],
        "field2": legs[1]["field"],
        "pad": pad,
        "alpha": spec["alpha"],
        "sym1": resolve_leg_symbol(legs[0]),
        "sym2": resolve_leg_symbol(legs[1]),
    }


def run_mode(
    mode: str,
    panel: pd.DataFrame,
    universe: pd.DataFrame,
    rolling_win: int,
    look_back_months: int,
    pad_ow: int | None,
    alpha_ow: float | None,
    use_proxy: bool,
    corr_lambda: float,
    simple_last: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    el = look_back_months * 21
    a_type, b_type = mode.split(".")
    type_map = {"IDX": "IDX", "EQ": "EQ", "FX": "FX"}
    axis0 = active_symbols(universe, [type_map[a_type]])["calypso"].tolist()
    axis1 = active_symbols(universe, [type_map[b_type]])["calypso"].tolist()
    # prefer KOSPI2 over duplicate KOSPI200 in square modes
    if a_type == "IDX":
        axis0 = [x for x in axis0 if x != "KOSPI200"]
    if b_type == "IDX":
        axis1 = [x for x in axis1 if x != "KOSPI200"]

    panel_syms = set(panel["symbol"].unique())
    missing0 = sorted(set(axis0) - panel_syms)
    missing1 = sorted(set(axis1) - panel_syms)
    axis0 = [x for x in axis0 if x in panel_syms]
    axis1 = [x for x in axis1 if x in panel_syms]

    price_cache: dict = {}
    detail_rows = []
    is_square = a_type == b_type

    if is_square:
        pairs = list(combinations(sorted(set(axis0) | set(axis1)), 2))
    else:
        pairs = list(product(axis0, axis1))

    for u1, u2 in pairs:
        if a_type in ("EQ", "IDX") and b_type == "FX":
            specs = fx_pair_specs(u1, u2, pad_ow, alpha_ow)
        elif simple_last:
            both_eq = "." in u1 and "." in u2
            pad = pad_ow if pad_ow is not None else 1
            alpha = alpha_ow if alpha_ow is not None else (90.0 if both_eq else 93.0)
            specs = [
                {
                    "legs": [
                        {
                            "calypso": u1,
                            "region": region_of(u1),
                            "subregion": subregion_of(u1),
                            "currency": currency_of(u1),
                            "field": "close",
                            "pad": pad,
                            "alpha": alpha,
                        },
                        {
                            "calypso": u2,
                            "region": region_of(u2),
                            "subregion": subregion_of(u2),
                            "currency": currency_of(u2),
                            "field": "close",
                            "pad": pad,
                            "alpha": alpha,
                        },
                    ],
                    "pad": pad,
                    "alpha": alpha,
                }
            ]
        else:
            specs = pair_rules(u1, u2, pad_ow, alpha_ow, use_proxy)

        # pick best among multi-field configs (max abs bumped for up direction)
        candidates = []
        for spec in specs:
            res = compute_pair(
                panel, spec, rolling_win, el, True, price_cache, look_back_months
            )
            candidates.append(res)
        best = max(
            candidates,
            key=lambda r: (-1 if not r["ok"] or pd.isna(r["corr"]) else 0, r.get("corr") or -999),
        )
        # if all failed, keep first failure reason
        if not best["ok"]:
            for c in candidates:
                if c["reason"] != "missing_price":
                    best = c
                    break

        bumped = best["corr"]
        if best["ok"] and corr_lambda and not pd.isna(bumped):
            from corr_mark_local import lambda_bump

            bumped = lambda_bump(float(bumped), float(corr_lambda), True)

        detail_rows.append(
            {
                "pair": f"{u1}-{u2}",
                "symbol1": u1,
                "symbol2": u2,
                "ok": best["ok"],
                "reason": best["reason"],
                "corr": bumped if best["ok"] else np.nan,
                "realized_last": best.get("realized_corr"),
                "lambda": best.get("lambda"),
                "window": best.get("window"),
                "n_obs": best.get("n_obs"),
                "field1": best.get("field1"),
                "field2": best.get("field2"),
                "pad": best.get("pad"),
                "alpha": best.get("alpha"),
                "priced1": best.get("sym1"),
                "priced2": best.get("sym2"),
            }
        )

    detail = pd.DataFrame(detail_rows)
    labels = sorted(set(axis0) | set(axis1)) if is_square else None
    if is_square:
        mat = pd.DataFrame(np.nan, index=labels, columns=labels, dtype=float)
        for lab in labels:
            mat.loc[lab, lab] = 1.0
        for _, r in detail.iterrows():
            if r["ok"] and pd.notna(r["corr"]):
                mat.loc[r["symbol1"], r["symbol2"]] = r["corr"]
                mat.loc[r["symbol2"], r["symbol1"]] = r["corr"]
    else:
        mat = pd.DataFrame(np.nan, index=axis0, columns=axis1, dtype=float)
        for _, r in detail.iterrows():
            if r["ok"] and pd.notna(r["corr"]):
                mat.loc[r["symbol1"], r["symbol2"]] = r["corr"]

    # adj_source stats
    adj_stats = {}
    if "adj_source" in panel.columns:
        adj_stats = (
            panel[panel["symbol"].isin(set(axis0) | set(axis1))]
            .groupby("adj_source")["symbol"]
            .nunique()
            .to_dict()
        )

    manifest = {
        "mode": mode,
        "rolling_win": rolling_win,
        "look_back_months": look_back_months,
        "el": el,
        "pad_override": pad_ow,
        "alpha_override": alpha_ow,
        "use_proxy": use_proxy,
        "simple_last": simple_last,
        "corr_lambda": corr_lambda,
        "axis0_n": len(axis0),
        "axis1_n": len(axis1),
        "pairs": len(detail),
        "ok_pairs": int(detail["ok"].sum()) if len(detail) else 0,
        "missing_panel_axis0": missing0,
        "missing_panel_axis1": missing1,
        "fail_reasons": detail["reason"].value_counts().to_dict() if len(detail) else {},
        "adj_source_symbol_counts": adj_stats,
        "hist_short_pairs": detail.loc[detail["reason"] == "hist_short", "pair"].tolist()
        if len(detail)
        else [],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    return detail, mat, manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="IDX.IDX", choices=["IDX.IDX", "EQ.EQ", "EQ.FX", "IDX.FX"])
    ap.add_argument("--panel", default=str(DEFAULT_PANEL))
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--rolling_win", type=int, default=63)
    ap.add_argument("--look_back_months", type=int, default=24)
    ap.add_argument("--pad", type=int, default=None)
    ap.add_argument("--alpha", type=float, default=None)
    ap.add_argument("--corr_lambda", type=float, default=0.0)
    ap.add_argument("--proxy", default="True")
    ap.add_argument("--simple-last", action="store_true", help="force LAST×LAST pad=1")
    ap.add_argument("--rules", action="store_true", help="Phase C region OPEN/LAST + proxy")
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    args = ap.parse_args()

    panel_path = Path(args.panel)
    if not panel_path.exists():
        raise SystemExit(
            f"Panel missing: {panel_path}. "
            "Run: python fetch_cmds_price_panel.py --corr-mark-universe data/corr_mark_universe.csv "
            "--out data/corr_mark_cmds_panel.parquet"
        )

    panel = pd.read_parquet(panel_path)
    universe = load_universe(args.universe)
    use_proxy = str(args.proxy).lower() in {"1", "true", "yes", "y"}
    if args.rules:
        simple = False
    elif args.simple_last:
        simple = True
    else:
        # default Phase B for square modes; rules for FX modes
        simple = args.mode in ("IDX.IDX", "EQ.EQ")

    detail, mat, manifest = run_mode(
        args.mode,
        panel,
        universe,
        args.rolling_win,
        args.look_back_months,
        args.pad,
        args.alpha,
        use_proxy,
        args.corr_lambda,
        simple_last=simple,
    )
    manifest["rules"] = not simple

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = args.mode.replace(".", "_")
    suffix = "" if simple else "_rules"
    detail_path = out_dir / f"corr_mark_cmds_detail_{tag}{suffix}.csv"
    mat_path = out_dir / f"corr_mark_cmds_matrix_{tag}{suffix}.csv"
    man_path = out_dir / f"corr_mark_cmds_run_manifest_{tag}{suffix}.json"
    detail.to_csv(detail_path, index=False)
    mat.to_csv(mat_path)
    man_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.mode == "IDX.IDX" and simple:
        mat.to_csv(out_dir / "corr_mark_cmds_matrix.csv")
        (out_dir / "run_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    print(f"mode={args.mode} simple_last={simple} ok={manifest['ok_pairs']}/{manifest['pairs']}")
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in ("fail_reasons", "adj_source_symbol_counts", "missing_panel_axis0")
            },
            ensure_ascii=False,
        )
    )
    print(f"Wrote {detail_path}\nWrote {mat_path}\nWrote {man_path}")


if __name__ == "__main__":
    main()
