#!/usr/bin/env python3
"""Extract one commodity product option_iv + futures_eod into data/commodity_vol_{P}.sqlite.

Source: parent vol_trading/data/commodity_vol.sqlite (full ~1.3GB, not shipped).
Usage: python scripts/extract_commodity_vol_product.py SC
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_CANDIDATES = [
    ROOT.parent / "data" / "commodity_vol.sqlite",
    ROOT / "data" / "commodity_vol.sqlite",
]


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: extract_commodity_vol_product.py PRODUCT", file=sys.stderr)
        sys.exit(2)
    product = sys.argv[1].strip().upper()
    src = next((p for p in SRC_CANDIDATES if p.exists() and p.stat().st_size > 0), None)
    if src is None:
        print("commodity_vol.sqlite not found", file=sys.stderr)
        sys.exit(1)
    dst = ROOT / "data" / f"commodity_vol_{product.lower()}.sqlite"
    if dst.exists():
        dst.unlink()
    d = sqlite3.connect(dst)
    d.execute(
        """
        CREATE TABLE option_iv (
          product TEXT, trade_date TEXT, wind_code TEXT, expiry TEXT, ttm REAL,
          strike REAL, cp TEXT, forward REAL, mid_price REAL, iv REAL,
          log_moneyness REAL, r REAL,
          PRIMARY KEY(product, trade_date, wind_code)
        )
        """
    )
    d.execute(
        """
        CREATE TABLE futures_eod (
          product TEXT, trade_date TEXT, wind_code TEXT, contract_ym TEXT, close REAL,
          PRIMARY KEY(product, trade_date, wind_code)
        )
        """
    )
    d.execute("ATTACH DATABASE ? AS src", (str(src),))
    d.execute(
        """
        INSERT INTO option_iv
        SELECT product, trade_date, wind_code, expiry, ttm, strike, cp,
               forward, mid_price, iv, log_moneyness, r
          FROM src.option_iv WHERE product=?
        """,
        (product,),
    )
    d.execute(
        """
        INSERT INTO futures_eod
        SELECT product, trade_date, wind_code, contract_ym, close
          FROM src.futures_eod WHERE product=?
        """,
        (product,),
    )
    n_opt = d.execute("SELECT COUNT(*) FROM option_iv").fetchone()[0]
    n_fut = d.execute("SELECT COUNT(*) FROM futures_eod").fetchone()[0]
    if n_opt <= 0:
        d.close()
        dst.unlink(missing_ok=True)
        print(f"no option_iv rows for {product}", file=sys.stderr)
        sys.exit(1)
    d.commit()
    d.execute("VACUUM")
    d.close()
    print(f"wrote {dst} ({dst.stat().st_size / 1e6:.1f} MB) opt={n_opt} fut={n_fut} from {src}")


if __name__ == "__main__":
    main()
