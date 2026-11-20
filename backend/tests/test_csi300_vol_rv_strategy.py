"""CSI 300 Vol RV: contract rules, leverage, attribution, optional e2e."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_VOL_DB = ROOT / "data" / "cffex_vol.sqlite"
needs_vol_db = pytest.mark.skipif(
    not (_VOL_DB.exists() and _VOL_DB.stat().st_size > 0),
    reason="data/cffex_vol.sqlite is local-only",
)

from engine.csi300_vol_rv_strategy import (
    STYLE_PRESETS,
    causal_premium_ref,
    dynamic_leverage,
    normalize_tilt,
    select_short_expiry,
    trading_days_to_expiry,
    vol_rv_cache_sig,
)


def test_normalize_tilt_and_presets():
    assert normalize_tilt("balanced") == "balanced"
    assert normalize_tilt("GSVLBRR1") == "balanced"
    assert STYLE_PRESETS["carry"]["lambda_tilt"] < STYLE_PRESETS["balanced"]["lambda_tilt"]
    assert STYLE_PRESETS["balanced"]["lambda_tilt"] < STYLE_PRESETS["defensive"]["lambda_tilt"]


def test_short_expiry_rolls_when_dte_below_5():
    dates = [f"2024-01-{d:02d}" for d in range(2, 32)] + [f"2024-02-{d:02d}" for d in range(1, 29)]
    rows = []
    for exp in ("2024-01-10", "2024-02-21"):
        rows.append(
            {
                "expiry": exp,
                "strike": 4000.0,
                "cp": "C",
                "forward": 4000.0,
                "mid_price": 50.0,
                "iv": 0.2,
                "ttm": 0.1,
                "r": 0.02,
                "wind_code": f"IO_{exp}_C",
            }
        )
    day = pd.DataFrame(rows)
    today = "2024-01-08"
    assert trading_days_to_expiry(dates, today, "2024-01-10") < 5
    assert select_short_expiry(day, today, dates, min_dte=5) == "2024-02-21"
    assert select_short_expiry(day, "2024-01-02", dates, min_dte=5) == "2024-01-10"


def test_premium_ref_no_lookahead():
    hist = [0.01, 0.02, 0.03, 0.04]
    ref = causal_premium_ref(hist, window=3, fallback=0.02)
    assert ref == pytest.approx(float(np.median([0.02, 0.03, 0.04])))
    assert causal_premium_ref([], fallback=0.025) == pytest.approx(0.025)


def test_dynamic_leverage_iv_and_clip():
    hi = dynamic_leverage(0.01, 0.02, lambda_tilt=1.0, l_min=0.5, l_max=2.5)
    assert hi == pytest.approx(2.0)
    lo = dynamic_leverage(0.08, 0.02, lambda_tilt=1.0, l_min=0.5, l_max=2.5)
    assert lo == pytest.approx(0.5)
    c = dynamic_leverage(0.02, 0.02, **{k: STYLE_PRESETS["carry"][k] for k in ("lambda_tilt", "l_min", "l_max")})
    d = dynamic_leverage(0.02, 0.02, **{k: STYLE_PRESETS["defensive"][k] for k in ("lambda_tilt", "l_min", "l_max")})
    assert c < d


def test_cache_sig_stable():
    a = vol_rv_cache_sig("balanced", 0.15, 0.05, "eod_close")
    b = vol_rv_cache_sig("balanced", 0.15, 0.05, "eod_close")
    assert a == b and a.startswith("tilt_balanced|")


@needs_vol_db
def test_e2e_payload_and_attribution():
    from engine.csi300_vol_rv_strategy import run_csi300_vol_rv

    # Short window keeps CI/local runtime sane; still exercises full bookkeeping.
    payload = run_csi300_vol_rv(
        tilt="balanced",
        run_ablation=True,
        hedger_mode="eod_close",
        start_date="2024-01-02",
        end_date="2024-06-28",
    )
    assert payload["strategy_id"] == "csi300_vol_rv"
    assert payload["summary"]["final_nav_multiple"] > 0
    assert "step1_eod_vanilla" in payload["ablation"]
    assert "step2_long_threshold" in payload["ablation"]
    assert "step3_dynamic_leverage" in payload["ablation"]
    attr = payload["pnl_attribution"]
    for i in range(len(attr["dates"])):
        lhs = attr["net_pnl"][i]
        rhs = (
            attr["short_option_pnl"][i]
            + attr["long_option_pnl"][i]
            + attr["futures_hedge_pnl"][i]
            - attr["transaction_cost"][i]
        )
        assert abs(lhs - rhs) < 1e-3
    decomp = payload["pnl_decomposition"]
    daily = payload["pnl_daily_greeks"]
    assert len(decomp["dates"]) == len(daily["dates"]) > 0
    for k in ("vega_pnl", "theta_pnl", "gamma_pnl", "basis_pnl", "net_delta_pnl", "residual_pnl"):
        assert len(decomp[k]) == len(decomp["dates"])
    for k in ("vega_pnl", "theta_pnl", "gamma_pnl"):
        assert len(daily[k]) == len(daily["dates"])
    assert payload["pnl_contribution"]["total_pnl"] is not None


@needs_vol_db
def test_tilt_mean_leverage_ordering():
    from engine.csi300_vol_rv_strategy import Csi300VolRvEngine

    means = {}
    for tilt in ("carry", "balanced", "defensive"):
        eng = Csi300VolRvEngine(
            tilt=tilt,
            run_ablation=False,
            start_date="2024-01-02",
            end_date="2024-06-28",
        )
        payload = eng.run()
        means[tilt] = float(payload["params"]["mean_leverage"] or 0.0)
    assert means["carry"] <= means["balanced"] + 1e-9
    assert means["balanced"] <= means["defensive"] + 1e-9
