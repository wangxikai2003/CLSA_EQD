"""Causal expanding GARCH(1,1)-t on Δspread → garch_z (no future data)."""
from __future__ import annotations

import math
import warnings
from typing import Tuple

import numpy as np
import pandas as pd

GARCH_WARM = 504
REFIT_EVERY = 5


def expanding_garch_z(
    spread: pd.Series,
    warm: int = GARCH_WARM,
    refit_every: int = REFIT_EVERY,
) -> Tuple[pd.Series, pd.Series]:
    """Return (garch_z, sigma) aligned to spread.index.

    Fit uses only Δspread[1..t] at date t (expanding). Weekly parameter refresh.
    z_t = ε_t / σ_t uses information through t close; engine still trades on t+1.
    """
    from arch import arch_model

    s = spread.astype(float)
    d_spread = s.diff()
    n = len(s)
    sigma = np.full(n, np.nan)
    garch_z = np.full(n, np.nan)
    omega = alpha = beta = float("nan")
    sig100 = float("nan")
    fits = 0

    for i in range(1, n):
        if i < warm:
            continue
        r_hist = d_spread.iloc[1 : i + 1].astype(float)
        r_hist = r_hist[np.isfinite(r_hist)]
        if len(r_hist) < warm:
            continue
        do_refit = fits == 0 or ((i - warm) % refit_every == 0)
        eps100 = float(d_spread.iloc[i]) * 100.0
        if not math.isfinite(eps100):
            continue
        if do_refit:
            y = r_hist * 100.0
            am = arch_model(y, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    res = am.fit(disp="off", show_warning=False)
                omega = float(res.params["omega"])
                alpha = float(res.params["alpha[1]"])
                beta = float(res.params["beta[1]"])
                sig100 = float(res.conditional_volatility.iloc[-1])
                fits += 1
            except Exception:
                if fits == 0:
                    continue
                do_refit = False
        if fits == 0:
            continue
        if not do_refit:
            if math.isfinite(sigma[i - 1]):
                eps_prev = float(d_spread.iloc[i - 1]) * 100.0
                if not math.isfinite(eps_prev):
                    eps_prev = 0.0
                sig_prev = float(sigma[i - 1]) * 100.0
                sig100 = math.sqrt(max(omega + alpha * eps_prev**2 + beta * sig_prev**2, 1e-12))
            elif not math.isfinite(sig100):
                continue
        sigma[i] = sig100 / 100.0
        garch_z[i] = eps100 / sig100 if sig100 > 1e-12 else float("nan")

    idx = s.index
    return (
        pd.Series(garch_z, index=idx, name="garch_z"),
        pd.Series(sigma, index=idx, name="garch_sigma"),
    )
