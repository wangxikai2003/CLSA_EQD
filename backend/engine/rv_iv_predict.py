"""
RV → IV prediction adapted from research/exo_rv_iv.

Two modes (Market Data tab):
  - rv:    Mid from long-run RV(252); Bid/Offer from Distance percentiles
           (exo_rv_predict.py)
  - bench: roll OLS of product RV vs benchmark RV, map benchmark ATM IV
           onto the product (exo_iv_predict.py, HO/IO/MO subset)

Price source is local CFFEX near-month futures close (or index spot when
requested) — no Bloomberg.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

try:
    from data_paths import cffex_vol as _dp_cffex, index_spot as _dp_spot
except Exception:  # pragma: no cover
    _dp_cffex = _dp_spot = None  # type: ignore

_DB_CANDIDATES = [
    ROOT / "data" / "cffex_vol.sqlite",
    Path("/app/data/cffex_vol.sqlite"),
]
_SPOT_CANDIDATES = [
    ROOT / "data" / "index_spot.parquet",
    Path("/app/data/index_spot.parquet"),
    ROOT.parent / "data" / "index_spot.parquet",
]

# Option product → spot / default benchmark
PRODUCT_META: Dict[str, Dict[str, str]] = {
    "HO": {"spot": "000016.SH", "name": "SSE50", "bench": "IO", "bid_to_mid": "1.0"},
    "IO": {"spot": "000300.SH", "name": "CSI300", "bench": "IO", "bid_to_mid": "1.0"},
    "MO": {"spot": "000852.SH", "name": "CSI1000", "bench": "IO", "bid_to_mid": "1.0"},
}

RV_WINDOWS = (21, 42, 63, 126, 252)
IV_WINDOWS = (21, 42, 63, 126)


def _db() -> Path:
    if _dp_cffex is not None:
        try:
            return _dp_cffex()
        except FileNotFoundError:
            pass
    for p in _DB_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("cffex_vol.sqlite not found")


def _spot_path() -> Optional[Path]:
    if _dp_spot is not None:
        p = _dp_spot()
        if p is not None and p.exists():
            return p
    for p in _SPOT_CANDIDATES:
        if p.exists():
            return p
    return None


def load_near_month_close(product: str) -> pd.Series:
    """Near-month futures close series (trade_date index, float close)."""
    prod = product.upper()
    conn = sqlite3.connect(_db())
    fut = pd.read_sql(
        """
        SELECT trade_date, contract_ym, close FROM futures_eod
         WHERE product=? AND close IS NOT NULL AND close > 0
         ORDER BY trade_date, contract_ym
        """,
        conn,
        params=(prod,),
    )
    conn.close()
    if fut.empty:
        raise ValueError(f"No futures for {prod}")
    daily = fut.sort_values(["trade_date", "contract_ym"]).groupby("trade_date", as_index=False).first()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily = daily.sort_values("trade_date").set_index("trade_date")
    return daily["close"].astype(float)


def load_index_spot(wind_code: str) -> pd.Series:
    path = _spot_path()
    if path is None:
        raise FileNotFoundError("index_spot.parquet not found")
    df = pd.read_parquet(path)
    sub = df[df["wind_code"] == wind_code].copy()
    if sub.empty:
        raise ValueError(f"No spot for {wind_code}")
    sub["trade_date"] = pd.to_datetime(sub["trade_date"])
    sub = sub.sort_values("trade_date").set_index("trade_date")
    return sub["close"].astype(float)


def load_price_series(product: str, source: str = "futures") -> Tuple[pd.Series, str]:
    prod = product.upper()
    meta = PRODUCT_META.get(prod)
    if meta is None:
        raise ValueError(f"unsupported product {prod}; choose {list(PRODUCT_META)}")
    if source == "spot":
        return load_index_spot(meta["spot"]), f"index_spot {meta['spot']}"
    return load_near_month_close(prod), f"futures_eod {prod} near-month close"


def compute_rv_frame(spot: pd.Series) -> pd.DataFrame:
    """Log-return RV (%) for windows in RV_WINDOWS — vectorized exo_rv formula."""
    s = spot.dropna().astype(float)
    out = pd.DataFrame({"Spot": s})
    out["Return"] = np.log(out["Spot"]).diff()
    out["ReturnSquared"] = out["Return"] ** 2
    for rw in RV_WINDOWS:
        # 100 * sqrt(252 * mean(r^2)), floor 0.01
        mean_sq = out["ReturnSquared"].rolling(rw, min_periods=rw).mean()
        out[f"RV_{rw}"] = (100.0 * np.sqrt(252.0 * mean_sq)).clip(lower=0.01)
    return out


def predict_from_rv(rv: pd.DataFrame) -> Dict[str, Any]:
    """
    exo_rv_predict core:
      Mid_* = RV_252
      Distance = log(RV_w / Mid)
      Bid/Offer = Mid * exp(percentile of Distance)
    """
    data = rv.copy()
    for w in (21, 42, 63):
        data[f"RV_{w}_MidPredict"] = data["RV_252"]
        data[f"RV_{w}_Distance"] = np.log(data[f"RV_{w}"] / data[f"RV_{w}_MidPredict"])

    data["RV_63_LookForward"] = data["RV_63"].shift(-64)
    data["RV_63_AbsError"] = data["RV_63_LookForward"] - data["RV_63_MidPredict"]
    data["RV_63_PctError"] = np.log(data["RV_63_LookForward"] / data["RV_63_MidPredict"])

    stats: Dict[str, Any] = {}
    for col in ("RV_63_AbsError", "RV_63_Distance", "RV_63_PctError", "RV_21_Distance", "RV_42_Distance"):
        err = data[col].dropna()
        if len(err) > 8:
            stats[f"{col}_mean"] = float(err.mean())
            stats[f"{col}_std"] = float(err.std())
            stats[f"{col}_length"] = int(len(err))
            stats[f"{col}_10Pct"] = float(np.percentile(err, 10))
            stats[f"{col}_25Pct"] = float(np.percentile(err, 25))
            stats[f"{col}_75Pct"] = float(np.percentile(err, 75))

    quotes: Dict[str, Optional[float]] = {}
    n_dist = int(stats.get("RV_63_Distance_length", 0))
    if n_dist > 252:
        d25 = min(float(stats["RV_63_Distance_25Pct"]), 0.0)
        d10 = min(float(stats["RV_63_Distance_10Pct"]), 0.0)
        d75 = max(float(stats["RV_63_Distance_75Pct"]), 0.0)
        d42_75 = max(float(stats.get("RV_42_Distance_75Pct", 0.0)), 0.0)
        d21_75 = max(float(stats.get("RV_21_Distance_75Pct", 0.0)), 0.0)

        data["RV_63_BidPredict"] = np.exp(d25) * data["RV_63_MidPredict"]
        data["RV_63_BidPredict2"] = np.exp(d10) * data["RV_63_MidPredict"]
        data["RV_63_OfferPredict"] = np.exp(d75) * data["RV_63_MidPredict"]
        data["RV_42_OfferPredict"] = np.exp(d42_75) * data["RV_42_MidPredict"]
        data["RV_21_OfferPredict"] = np.exp(d21_75) * data["RV_21_MidPredict"]

        mapping = [
            ("Bid_3M", "RV_63_BidPredict"),
            ("Bid2_3M", "RV_63_BidPredict2"),
            ("Mid_3M", "RV_63_MidPredict"),
            ("Offer_3M", "RV_63_OfferPredict"),
            ("Offer_2M", "RV_42_OfferPredict"),
            ("Offer_1M", "RV_21_OfferPredict"),
        ]
        for new_label, old_label in mapping:
            series = data[old_label].dropna()
            if len(series) > 126:
                quotes[new_label] = float(series.iloc[-1])
            else:
                quotes[new_label] = None

        if quotes.get("Mid_3M") is not None and quotes.get("Bid_3M") is not None:
            quotes["Mid-Bid_3M"] = quotes["Mid_3M"] - quotes["Bid_3M"]
        if quotes.get("Offer_3M") is not None and quotes.get("Bid_3M") is not None:
            quotes["Offer-Bid_3M"] = quotes["Offer_3M"] - quotes["Bid_3M"]
    else:
        # Short history: still expose Mid from RV_252
        mid = data["RV_252"].dropna()
        if len(mid):
            quotes["Mid_3M"] = float(mid.iloc[-1])

    current = {
        f"rv_{w}": float(data[f"RV_{w}"].dropna().iloc[-1]) if data[f"RV_{w}"].dropna().shape[0] else None
        for w in RV_WINDOWS
    }

    # Downsample series for chart (keep last ~500 pts)
    series_cols = ["RV_21", "RV_42", "RV_63", "RV_126", "RV_252"]
    if "RV_63_BidPredict" in data.columns:
        series_cols += ["RV_63_BidPredict", "RV_63_MidPredict", "RV_63_OfferPredict"]
    chart = data[series_cols].dropna(how="all").copy()
    if len(chart) > 500:
        step = math.ceil(len(chart) / 500)
        chart = chart.iloc[::step]
        if chart.index[-1] != data.index[-1]:
            chart = pd.concat([chart, data[series_cols].iloc[[-1]]])

    series: List[Dict[str, Any]] = []
    for idx, row in chart.iterrows():
        pt: Dict[str, Any] = {"d": pd.Timestamp(idx).strftime("%Y-%m-%d")}
        for c in series_cols:
            v = row.get(c)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                key = c.lower().replace("rv_63_bidpredict", "bid").replace(
                    "rv_63_midpredict", "mid"
                ).replace("rv_63_offerpredict", "offer")
                if key.startswith("rv_"):
                    pt[key] = float(v)
                else:
                    pt[key] = float(v)
        series.append(pt)

    return {
        "quotes": quotes,
        "stats": stats,
        "current": current,
        "series": series,
        "sample_length": int(len(data.dropna(subset=["Spot"]))),
        "first_date": data.index[0].strftime("%Y-%m-%d") if len(data) else None,
        "end_date": data.index[-1].strftime("%Y-%m-%d") if len(data) else None,
    }


def load_atm_iv_history(product: str, windows: Tuple[int, ...] = IV_WINDOWS) -> pd.DataFrame:
    """
    Daily ATM IV (%) from svi_slices.jw_v ≈ ATM total variance;
    for each window pick the slice whose calendar days ≈ window.
    """
    prod = product.upper()
    conn = sqlite3.connect(_db())
    rows = pd.read_sql(
        """
        SELECT trade_date, ttm, jw_v FROM svi_slices
         WHERE product=? AND side='otm' AND jw_v IS NOT NULL AND jw_v > 0
         ORDER BY trade_date, ttm
        """,
        conn,
        params=(prod,),
    )
    conn.close()
    if rows.empty:
        return pd.DataFrame()

    rows["trade_date"] = pd.to_datetime(rows["trade_date"])
    rows["days"] = (rows["ttm"] * 365.0).round().astype(int)
    rows["atm_iv"] = np.sqrt(rows["jw_v"].clip(lower=0.0)) * 100.0

    out: Dict[str, pd.Series] = {}
    for w in windows:
        # nearest tenor per date
        pick = (
            rows.assign(dist=(rows["days"] - w).abs())
            .sort_values(["trade_date", "dist"])
            .groupby("trade_date", as_index=True)
            .first()
        )
        out[f"IV_{w}_BM"] = pick["atm_iv"]
    return pd.DataFrame(out)


def predict_from_benchmark(
    base_rv: pd.DataFrame,
    bench_rv: pd.DataFrame,
    bench_iv: pd.DataFrame,
    *,
    base_label: str,
    bench_label: str,
    bid_to_mid: float = 1.0,
) -> Dict[str, Any]:
    """Rolling no-intercept OLS: bench_RV ≈ β * base_RV → Mid = IV_bm / β."""
    joined = base_rv.join(bench_rv, rsuffix="_BM").join(bench_iv, how="left")
    stats: Dict[str, Any] = {}
    quotes: Dict[str, Optional[float]] = {}
    series_frames: List[pd.DataFrame] = []

    for rw in IV_WINDOWS:
        col_x = f"RV_{rw}"
        col_y = f"RV_{rw}_BM"
        col_iv = f"IV_{rw}_BM"
        if col_y not in joined.columns or col_iv not in joined.columns:
            continue
        clean = joined[[col_x, col_y, col_iv]].dropna()
        if len(clean) < rw + 5:
            continue

        betas = []
        mids = []
        bids = []
        asks = []
        dates = []
        x_arr = clean[col_x].values
        y_arr = clean[col_y].values
        iv_arr = clean[col_iv].values
        idx = clean.index

        for i in range(rw, len(clean) + 1):
            x = x_arr[i - rw : i]
            y = y_arr[i - rw : i]
            # OLS no intercept: β = (x·y) / (x·x)
            xx = float(np.dot(x, x))
            if xx <= 1e-12:
                continue
            beta = float(np.dot(x, y) / xx)
            if beta <= 0:
                continue
            resid = y - beta * x
            resid_std = float(resid.std(ddof=1)) if len(resid) > 1 else 0.0
            iv_bm = float(iv_arr[i - 1])
            rv_last = float(x[-1])
            mid = iv_bm / beta
            # Sharpe=1 bid/ask from exo_iv_predict
            iv_bm_bid = iv_bm - bid_to_mid
            d1 = iv_bm_bid * iv_bm_bid - 1.0 * resid_std * 2.0 * beta * rv_last / math.sqrt(252.0 / rw)
            bid = mid + ((math.sqrt(d1) - iv_bm) / beta) if d1 > 0 else None
            iv_bm_ask = iv_bm + bid_to_mid
            d2 = iv_bm_ask * iv_bm_ask + 1.0 * resid_std * 2.0 * beta * rv_last / math.sqrt(252.0 / rw)
            ask = mid + ((math.sqrt(d2) - iv_bm) / beta) if d2 > 0 else mid

            dates.append(idx[i - 1])
            betas.append(1.0 / beta)
            mids.append(mid)
            bids.append(bid if bid is not None else float("nan"))
            asks.append(ask)

        if not dates:
            continue
        frame = pd.DataFrame(
            {
                f"RVBeta_{rw}": betas,
                f"IV_{rw}_Mid": mids,
                f"IV_{rw}_Bid": bids,
                f"IV_{rw}_Ask": asks,
            },
            index=pd.DatetimeIndex(dates),
        )
        series_frames.append(frame)
        stats[f"RVBeta_{rw}"] = float(betas[-1])
        stats[f"IV_{rw}_Mid"] = float(mids[-1])
        if not math.isnan(bids[-1]):
            stats[f"IV_{rw}_Bid"] = float(bids[-1])
            quotes[f"Bid_{rw}"] = float(bids[-1])
        stats[f"IV_{rw}_Ask"] = float(asks[-1])
        quotes[f"Mid_{rw}"] = float(mids[-1])
        quotes[f"Ask_{rw}"] = float(asks[-1])

    # Convenience 3M aliases matching rv mode
    if "Mid_63" in quotes:
        quotes["Mid_3M"] = quotes["Mid_63"]
    if "Bid_63" in quotes:
        quotes["Bid_3M"] = quotes["Bid_63"]
    if "Ask_63" in quotes:
        quotes["Offer_3M"] = quotes["Ask_63"]
    if quotes.get("Mid_3M") is not None and quotes.get("Bid_3M") is not None:
        quotes["Mid-Bid_3M"] = quotes["Mid_3M"] - quotes["Bid_3M"]
    if quotes.get("Offer_3M") is not None and quotes.get("Bid_3M") is not None:
        quotes["Offer-Bid_3M"] = quotes["Offer_3M"] - quotes["Bid_3M"]

    chart_df = joined[["RV_21", "RV_63", "RV_252"]].copy()
    if series_frames:
        for f in series_frames:
            chart_df = chart_df.join(f, how="left")
    chart_df = chart_df.dropna(subset=["RV_63"], how="all")
    if len(chart_df) > 500:
        step = math.ceil(len(chart_df) / 500)
        keep = chart_df.iloc[::step]
        if keep.index[-1] != chart_df.index[-1]:
            keep = pd.concat([keep, chart_df.iloc[[-1]]])
        chart_df = keep

    series: List[Dict[str, Any]] = []
    for idx, row in chart_df.iterrows():
        pt: Dict[str, Any] = {"d": pd.Timestamp(idx).strftime("%Y-%m-%d")}
        for k, v in row.items():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            key = str(k).lower()
            if key == "iv_63_mid":
                key = "mid"
            elif key == "iv_63_bid":
                key = "bid"
            elif key == "iv_63_ask":
                key = "offer"
            pt[key] = float(v)
        series.append(pt)

    current = {
        f"rv_{w}": float(base_rv[f"RV_{w}"].dropna().iloc[-1])
        if base_rv[f"RV_{w}"].dropna().shape[0]
        else None
        for w in RV_WINDOWS
    }

    return {
        "quotes": quotes,
        "stats": stats,
        "current": current,
        "series": series,
        "benchmark": {"product": bench_label, "base": base_label, "bid_to_mid": bid_to_mid},
        "sample_length": int(joined["RV_63"].dropna().shape[0]) if "RV_63" in joined.columns else int(len(joined)),
        "first_date": joined.index[0].strftime("%Y-%m-%d") if len(joined) else None,
        "end_date": joined.index[-1].strftime("%Y-%m-%d") if len(joined) else None,
    }


def latest_atm_iv(product: str) -> Optional[float]:
    """Nearest-to-63D ATM IV (%) on latest surface date."""
    prod = product.upper()
    conn = sqlite3.connect(_db())
    date = conn.execute(
        "SELECT MAX(trade_date) FROM surface_meta WHERE product=? AND side='otm'",
        (prod,),
    ).fetchone()[0]
    if not date:
        conn.close()
        return None
    rows = conn.execute(
        """
        SELECT ttm, jw_v FROM svi_slices
         WHERE product=? AND trade_date=? AND side='otm' ORDER BY ttm
        """,
        (prod, date),
    ).fetchall()
    conn.close()
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(float(r[0]) * 365 - 63))
    return float(math.sqrt(max(float(best[1]), 0.0)) * 100.0)


def run_predict(
    product: str,
    mode: str = "rv",
    source: str = "futures",
    benchmark: Optional[str] = None,
) -> Dict[str, Any]:
    prod = product.upper()
    mode = (mode or "rv").lower()
    source = (source or "futures").lower()
    if prod not in PRODUCT_META:
        raise ValueError(f"unsupported product {prod}")
    if mode not in ("rv", "bench"):
        raise ValueError("mode must be rv|bench")
    if source not in ("futures", "spot"):
        raise ValueError("source must be futures|spot")

    spot, src_label = load_price_series(prod, source)
    rv = compute_rv_frame(spot)
    atm = latest_atm_iv(prod)

    if mode == "rv":
        payload = predict_from_rv(rv)
        payload["mode"] = "rv"
        payload["method"] = "exo_rv_predict: Mid=RV_252; Bid/Offer from Distance percentiles"
    else:
        bench_prod = (benchmark or PRODUCT_META[prod]["bench"]).upper()
        if bench_prod not in PRODUCT_META:
            raise ValueError(f"unsupported benchmark {bench_prod}")
        bench_spot, _ = load_price_series(bench_prod, source)
        bench_rv = compute_rv_frame(bench_spot)
        # Rename to avoid clash before join — predict_from_benchmark expects RV_*_BM via rsuffix
        bench_rv_only = bench_rv[[f"RV_{w}" for w in RV_WINDOWS]].copy()
        bench_iv = load_atm_iv_history(bench_prod)
        if bench_iv.empty:
            raise ValueError(f"No ATM IV history for benchmark {bench_prod}")
        bid_to_mid = float(PRODUCT_META[bench_prod]["bid_to_mid"])
        payload = predict_from_benchmark(
            rv,
            bench_rv_only,
            bench_iv,
            base_label=prod,
            bench_label=bench_prod,
            bid_to_mid=bid_to_mid,
        )
        payload["mode"] = "bench"
        payload["method"] = "exo_iv_predict: rolling OLS map of benchmark ATM IV"

    payload["product"] = prod
    payload["product_name"] = PRODUCT_META[prod]["name"]
    payload["source"] = src_label
    payload["asof"] = payload.get("end_date")
    payload["current"] = {**payload.get("current", {}), "atm_iv": atm}
    if atm is not None and payload.get("quotes", {}).get("Mid_3M") is not None:
        payload["quotes"]["atm_vs_mid"] = atm - float(payload["quotes"]["Mid_3M"])
    return payload
