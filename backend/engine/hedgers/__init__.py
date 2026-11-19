"""Delta hedger strategy pattern for CSI 300 Vol RV."""
from __future__ import annotations

from typing import Literal

from engine.hedgers.base_hedger import (
    BaseDeltaHedger,
    HedgeContext,
    HedgeDecision,
    OptionLot,
)
from engine.hedgers.eod_close_hedger import EodCloseDeltaHedger
from engine.hedgers.intraday_curve_hedger import IntradayCurveAdjustedHedger

HedgerMode = Literal["eod_close", "intraday_curve"]


def get_hedger(
    mode: HedgerMode = "eod_close",
    **kwargs,
) -> BaseDeltaHedger:
    key = str(mode or "eod_close").strip().lower()
    if key in ("eod", "eod_close", "close"):
        return EodCloseDeltaHedger(**kwargs)
    if key in ("intraday", "intraday_curve", "curve"):
        return IntradayCurveAdjustedHedger(**kwargs)
    raise ValueError(f"unknown hedger_mode {mode!r}; choose eod_close | intraday_curve")


__all__ = [
    "BaseDeltaHedger",
    "EodCloseDeltaHedger",
    "HedgeContext",
    "HedgeDecision",
    "HedgerMode",
    "IntradayCurveAdjustedHedger",
    "OptionLot",
    "get_hedger",
]
