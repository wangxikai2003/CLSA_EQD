"""Batch-pull US IV points + CBOE COR* via pdblp. Run on a machine with Bloomberg."""
import pdblp
import pandas as pd

INDEX = ["SPX Index", "NDX Index"]
SPDR = [f"{x} US Equity" for x in
        ["XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB","XLRE","XLC"]]
# fill TOP50 with point-in-time list from weights script
TOP50 = [f"{x} US Equity" for x in ["AAPL","MSFT","NVDA","AMZN","META"]]

FIELDS = [
    "30DAY_IMPVOL_100.0%MNY_DF",
    "90DAY_IMPVOL_100.0%MNY_DF",
    "180DAY_IMPVOL_100.0%MNY_DF",
    "360DAY_IMPVOL_100.0%MNY_DF",
    "30DAY_IMPVOL_25%DELTA_DF",
    "90DAY_IMPVOL_25%DELTA_DF",
]
COR = ["COR1M Index", "COR3M Index", "COR6M Index", "COR1Y Index"]

def chunk(xs, n=40):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

def pull(start="20190101", end="20261113"):
    frames = []
    with pdblp.bopen(timeout=20000) as con:
        for fld in FIELDS:
            for part in chunk(INDEX + SPDR + TOP50):
                try:
                    df = con.bdh(tickers=part, flds=fld, start_date=start, end_date=end)
                    long = df.stack(level=0).reset_index()
                    long.columns = ["date", "ticker", "value"]
                    long["field"] = fld
                    frames.append(long)
                except Exception as e:
                    print("skip", fld, part[:2], e)
        cor = con.bdh(tickers=COR, flds="PX_LAST", start_date=start, end_date=end)
        c = cor.stack(level=0).reset_index()
        c.columns = ["date", "ticker", "value"]
        c["field"] = "PX_LAST"
        frames.append(c)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet("data/bbg_us_iv_cor_panel.parquet", index=False)
    return out

if __name__ == "__main__":
    pull()
