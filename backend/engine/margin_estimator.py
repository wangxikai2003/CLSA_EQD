"""Post-process margin from a position ledger. Does not mutate the ledger.

CFFEX equity option shorts (IO/HO/MO) use the exchange-style formula.
Other option underlyings are left blank. Futures use |Q| * multiplier * close * rate.
"""
from __future__ import annotations

import math
import re
from typing import Optional

import pandas as pd

_CFFEX_OPT = re.compile(r"^(IO|HO|MO)\d{3,4}-([CP])-(\d+(?:\.\d+)?)", re.IGNORECASE)
_DEFAULT_RATE = 0.12


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _parse_option(ticker: str):
    m = _CFFEX_OPT.match(str(ticker or "").upper())
    if not m:
        return None
    return m.group(1), m.group(2), float(m.group(3))


def _otm_amount(cp: str, strike: float, spot: float) -> float:
    if str(cp).upper().startswith("P"):
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def estimate_margin(
    ledger: pd.DataFrame,
    spot: Optional[pd.Series] = None,
    nav: Optional[pd.Series] = None,
    margin_rate: float = _DEFAULT_RATE,
) -> pd.DataFrame:
    """Daily account margin. Reads ``ledger`` only; the caller's frame is unchanged."""
    cols = [
        "trade_date",
        "option_short_margin",
        "futures_margin",
        "total_margin_required",
        "margin_to_nav_ratio",
        "margin_note",
    ]
    if ledger is None or ledger.empty:
        return pd.DataFrame(columns=cols)

    spot_map = {}
    if spot is not None:
        s = spot.copy()
        s.index = s.index.astype(str).str[:10]
        spot_map = {str(k)[:10]: _num(v) for k, v in s.items()}
    nav_map = {}
    if nav is not None:
        n = nav.copy()
        n.index = n.index.astype(str).str[:10]
        nav_map = {str(k)[:10]: _num(v) for k, v in n.items()}

    rows = []
    view = ledger.copy(deep=True)
    for trade_date, g in view.groupby("trade_date", sort=True):
        d = str(trade_date)[:10]
        opt_margin = 0.0
        fut_margin = 0.0
        blocked = False
        for rec in g.itertuples(index=False):
            asset = str(getattr(rec, "asset_type", "") or "")
            qty = _num(getattr(rec, "curr_pos", None)) or 0.0
            px = _num(getattr(rec, "close_price", None))
            mult = _num(getattr(rec, "multiplier", None)) or 1.0
            if asset == "FUTURE":
                if px is None or abs(qty) <= 1e-12:
                    continue
                fut_margin += abs(qty) * mult * px * float(margin_rate)
                continue
            if asset != "OPTION" or qty >= -1e-12:
                continue
            ticker = str(getattr(rec, "ticker", "") or "")
            parsed = _parse_option(ticker)
            row_spot = _num(getattr(rec, "spot", None))
            if row_spot is None:
                row_spot = spot_map.get(d)
            strike = _num(getattr(rec, "strike", None))
            cp = getattr(rec, "cp", None)
            if parsed is not None:
                if cp is None:
                    cp = parsed[1]
                if strike is None:
                    strike = parsed[2]
            if parsed is None or px is None or row_spot is None or strike is None or not cp:
                blocked = True
                continue
            otm = _otm_amount(str(cp), float(strike), float(row_spot))
            extra = max(0.12 * float(row_spot) - otm, 0.07 * float(row_spot))
            opt_margin += abs(qty) * mult * (px + extra)

        note = None
        if blocked:
            note = "non-CFFEX option margin not estimated"
            opt_out = None
            total = fut_margin
        else:
            opt_out = opt_margin
            total = opt_margin + fut_margin
        nav_v = nav_map.get(d)
        ratio = None
        if nav_v is not None and abs(nav_v) > 1e-9:
            ratio = total / nav_v
        rows.append(
            {
                "trade_date": d,
                "option_short_margin": opt_out,
                "futures_margin": fut_margin,
                "total_margin_required": total,
                "margin_to_nav_ratio": ratio,
                "margin_note": note,
            }
        )
    return pd.DataFrame(rows, columns=cols)
