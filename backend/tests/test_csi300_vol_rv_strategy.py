"""CSI 300 Vol RV: contract rules, leverage, attribution, optional e2e."""
from __future__ import annotations

import sqlite3
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
    VOL_RV_UNIVERSES,
    BookState,
    Csi300VolRvEngine,
    LegPos,
    Vintage,
    causal_premium_ref,
    dynamic_leverage,
    futures_expiry_by_code,
    futures_in_roll_window,
    normalize_tilt,
    normalize_universe,
    ranked_hedge_futures,
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
    a = vol_rv_cache_sig("balanced", 0.15, 0.05, "eod_close", universe="300")
    b = vol_rv_cache_sig("balanced", 0.15, 0.05, "eod_close", universe="300")
    assert a == b and "tilt_balanced|" in a and a.startswith("u300|") and a.endswith("|fr1")
    assert vol_rv_cache_sig("balanced", 0.15, 0.05, "eod_close", universe="AU").startswith("uAU|")


def test_universe_registry():
    assert set(VOL_RV_UNIVERSES) >= {"300", "50", "1000", "AU", "SC"}
    assert normalize_universe("ho") == "50"
    assert normalize_universe("gold") == "AU"
    assert normalize_universe("crude") == "SC"
    assert VOL_RV_UNIVERSES["AU"]["db"] == "commodity"
    assert VOL_RV_UNIVERSES["SC"]["product"] == "SC"
    assert VOL_RV_UNIVERSES["1000"]["m_fut"] == 200.0


@needs_vol_db
def test_e2e_payload_and_attribution():
    from engine.csi300_vol_rv_strategy import run_csi300_vol_rv

    # Short window keeps CI/local runtime sane; still exercises full bookkeeping.
    payload = run_csi300_vol_rv(
        tilt="balanced",
        universe="300",
        run_ablation=True,
        hedger_mode="eod_close",
        start_date="2024-01-02",
        end_date="2024-06-28",
    )
    assert payload["strategy_id"] == "csi300_vol_rv"
    assert payload["params"]["universe"] == "300"
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
            universe="300",
            run_ablation=False,
            start_date="2024-01-02",
            end_date="2024-06-28",
        )
        payload = eng.run()
        means[tilt] = float(payload["params"]["mean_leverage"] or 0.0)
    assert means["carry"] <= means["balanced"] + 1e-9
    assert means["balanced"] <= means["defensive"] + 1e-9


@needs_vol_db
@pytest.mark.parametrize("uni", ["50", "1000"])
def test_e2e_ho_mo_universes(uni):
    from engine.csi300_vol_rv_strategy import run_csi300_vol_rv

    payload = run_csi300_vol_rv(
        tilt="balanced",
        universe=uni,
        run_ablation=False,
        start_date="2024-01-02",
        end_date="2024-06-28",
    )
    assert payload["params"]["universe"] == uni
    assert payload["params"]["product"] == VOL_RV_UNIVERSES[uni]["product"]
    assert payload["summary"]["final_nav_multiple"] > 0


def _has_commodity_product(product: str) -> bool:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    parent = root.parent / "data" / "commodity_vol.sqlite"
    extract = root / "data" / f"commodity_vol_{product.lower()}.sqlite"
    legacy = root / "data" / "commodity_vol_au.sqlite" if product == "AU" else None
    for p in (extract, legacy, parent):
        if p is not None and p.exists() and p.stat().st_size > 0:
            return True
    return False


@needs_vol_db
@pytest.mark.parametrize("uni,product", [("AU", "AU"), ("SC", "SC")])
def test_e2e_commodity_universes(uni, product):
    from engine.csi300_vol_rv_strategy import run_csi300_vol_rv

    if not _has_commodity_product(product):
        pytest.skip(f"commodity {product} vol DB missing")
    payload = run_csi300_vol_rv(
        tilt="balanced",
        universe=uni,
        run_ablation=False,
        start_date="2024-01-02",
        end_date="2024-06-28",
    )
    assert payload["params"]["universe"] == uni
    assert payload["params"]["product"] == product
    assert payload["params"]["m_opt"] == 1000.0
    assert payload["summary"]["final_nav_multiple"] > 0


def test_holdings_snapshot_option_and_futures():
    eng = Csi300VolRvEngine(run_ablation=False)
    leg = LegPos(
        wind_code="IO2506-C-4000.CFE",
        expiry="2025-06-20",
        strike=4000.0,
        cp="C",
        n=-2.0,
        mid=80.0,
        iv=0.2,
        ttm=0.2,
        forward=4000.0,
        r=0.02,
        delta=0.5,
        gamma=0.0,
        vega=0.0,
        theta=0.0,
        leg="short",
    )
    book = BookState(
        vintages=[
            Vintage(
                vintage_id="v1",
                open_date="2025-01-02",
                short_expiry="2025-01-17",
                long_expiry="2025-06-20",
                exit_date="2025-01-16",
                legs=[leg],
            )
        ],
        fut_short=-1.5,
        fut_long=0.4,
        fut_short_code="IF2501.CFE",
        fut_long_code="IF2502.CFE",
    )
    eng._snapshot_holdings("2025-01-02", book, 4000.0, 3990.0)
    df = eng.holdings_dataframe()
    assert len(df) == 3
    opt = df[df["instrument"] == "option"].iloc[0]
    assert opt["direction"] == "short"
    assert opt["qty"] == -2.0
    assert opt["contract_ym"] == "2506"
    futs = df[df["instrument"] == "future"]
    assert set(futs["leg"]) == {"hedge_short", "hedge_long"}


def test_futures_roll_window_matches_option_session_before_expiry():
    # Wed, Thu, Fri. Expiry is Friday. n_roll=1 drops the contract on Thursday.
    dates = ["2026-10-14", "2026-10-15", "2026-10-16"]
    assert futures_in_roll_window("2026-10-14", "2026-10-16", dates, 1) is False
    assert futures_in_roll_window("2026-10-15", "2026-10-16", dates, 1) is True
    assert futures_in_roll_window("2026-10-16", "2026-10-16", dates, 1) is True
    # Sample ends well before a later expiry: do not treat the last day as a roll.
    short = ["2026-10-23", "2026-10-26"]
    assert futures_in_roll_window("2026-10-26", "2026-11-20", short, 1) is False
    # Sample ends the business day before expiry, and that expiry is not in the calendar yet.
    assert futures_in_roll_window("2026-11-19", "2026-11-20", ["2026-11-18", "2026-11-19"], 1) is True


def test_finished_future_expiry_is_last_print_not_nominal_friday():
    # IF2402's third Friday was 2024-02-16 (CNY). The last print is 2024-02-19.
    rows = []
    for d, code, ym in (
        ("2024-02-08", "IF2402.CFE", "2402"),
        ("2024-02-19", "IF2402.CFE", "2402"),
        ("2024-02-19", "IF2403.CFE", "2403"),
        ("2024-02-20", "IF2403.CFE", "2403"),
    ):
        rows.append({"trade_date": d, "wind_code": code, "contract_ym": ym, "close": 3000.0})
    fut = pd.DataFrame(rows)
    expiry = futures_expiry_by_code(fut, "IF")
    assert expiry["IF2402.CFE"] == "2024-02-19"
    dates = ["2024-02-08", "2024-02-19", "2024-02-20"]
    # Feb 8 is the previous trading session before the postponed expiry, so IF2402 is already out.
    day = fut[fut["trade_date"] == "2024-02-08"]
    ranked = ranked_hedge_futures(day, "IF", "2024-02-08", dates, expiry, 1)
    assert list(ranked["wind_code"]) == []


@needs_vol_db
def test_if2610_rolls_off_at_prior_close_not_settle():
    con = sqlite3.connect(str(_VOL_DB))
    fut = pd.read_sql(
        """
        SELECT trade_date, wind_code, contract_ym, close
          FROM futures_eod
         WHERE product='IO' AND trade_date BETWEEN '2026-09-01' AND '2026-10-20'
        """,
        con,
    )
    px = con.execute(
        "SELECT close, settle FROM futures_eod WHERE wind_code='IF2610.CFE' AND trade_date='2026-10-15'"
    ).fetchone()
    con.close()
    fut["trade_date"] = fut["trade_date"].astype(str).str[:10]
    expiry = futures_expiry_by_code(fut, "IF")
    assert expiry["IF2610.CFE"] == "2026-10-16"
    dates = sorted(fut["trade_date"].unique())
    held = ranked_hedge_futures(
        fut[fut["trade_date"] == "2026-10-14"], "IF", "2026-10-14", dates, expiry, 1
    )
    rolled = ranked_hedge_futures(
        fut[fut["trade_date"] == "2026-10-15"], "IF", "2026-10-15", dates, expiry, 1
    )
    assert held.iloc[0]["wind_code"] == "IF2610.CFE"
    assert "IF2610.CFE" not in set(rolled["wind_code"])
    assert rolled.iloc[0]["wind_code"] == "IF2611.CFE"
    assert abs(float(px[0]) - float(px[1])) > 1.0

    eng = Csi300VolRvEngine(
        universe="300",
        tilt="balanced",
        run_ablation=False,
        start_date="2026-07-01",
        end_date="2026-10-20",
    )
    out = eng.run()
    assert "settle unused" in out["params"]["price"]
    led = pd.DataFrame(out["position_pnl_ledger"])
    hedges = led[led["leg_role"] == "HEDGE_FUT"]

    def open_codes(day: str) -> set:
        sub = hedges[(hedges["trade_date"] == day) & (hedges["curr_pos"].abs() > 1e-6)]
        return set(sub["ticker"])

    assert "IF2610.CFE" in open_codes("2026-10-14")
    assert "IF2610.CFE" not in open_codes("2026-10-15")
    assert "IF2610.CFE" not in open_codes("2026-10-16")
    blot = pd.DataFrame(out["trade_blotter"])
    roll = blot[
        (blot["trade_date"] == "2026-10-15")
        & (blot["ticker"] == "IF2610.CFE")
        & (blot["action_type"] == "FUTURES_ROLL")
    ]
    assert not roll.empty
    assert float(roll["trade_price"].iloc[0]) == pytest.approx(float(px[0]))
    assert float(roll["trade_price"].iloc[0]) != pytest.approx(float(px[1]))
