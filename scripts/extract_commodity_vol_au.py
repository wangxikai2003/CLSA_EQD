#!/usr/bin/env python3
"""Extract AU option_iv + futures_eod into data/commodity_vol_au.sqlite (~80MB).

Source: parent vol_trading/data/commodity_vol.sqlite (full ~1.3GB, not shipped).
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
DST = ROOT / "data" / "commodity_vol_au.sqlite"


def main() -> None:
    src = next((p for p in SRC_CANDIDATES if p.exists() and p.stat().st_size > 0), None)
    if src is None:
        print("commodity_vol.sqlite not found", file=sys.stderr)
        sys.exit(1)
    if DST.exists():
        DST.unlink()
    d = sqlite3.connect(DST)
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
          FROM src.option_iv WHERE product='AU'
        """
    )
    d.execute(
        """
        INSERT INTO futures_eod
        SELECT product, trade_date, wind_code, contract_ym, close
          FROM src.futures_eod WHERE product='AU'
        """
    )
    d.commit()
    d.execute("VACUUM")
    d.close()
    print(f"wrote {DST} ({DST.stat().st_size / 1e6:.1f} MB) from {src}")


if __name__ == "__main__":
    main()
