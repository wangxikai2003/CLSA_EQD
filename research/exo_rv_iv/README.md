# exo_rv_iv (research originals)

Bloomberg + `eqdquant` batch scripts that inspired Market Data → **RV/IV Predict**.

| Script | Idea |
|--------|------|
| `exo_rv_predict.py` | Mid = RV(252); Bid/Offer from Distance percentiles vs system `shift_kv` |
| `exo_iv_predict.py` | Rolling OLS of stock RV vs benchmark RV; map benchmark ATM IV |

**Runtime** lives under `backend/engine/rv_iv_predict.py` and serves
`GET /api/v1/market/rv-iv-predict` for HO / IO / MO using local CFFEX futures
(or index spot) — no Bloomberg. These files are kept for algorithm reference only
and are **not** imported by the deployed app.
