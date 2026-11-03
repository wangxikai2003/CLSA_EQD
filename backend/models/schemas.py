"""Shared model dataclasses / typing helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class JwParams:
    v: float
    psi: float
    p: float
    c: float
    vtild: float

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class RawSviParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class CalibResult:
    jw: JwParams
    raw: RawSviParams
    rmse: float
    mae: float
    butterfly_ok: bool
    g_min: float
    max_residual: float = 0.0
    has_butterfly_arbitrage: bool = False
    has_calendar_arbitrage: bool = False
    calendar_min_gap: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        arb_check = {
            "min_g": self.g_min,
            "has_butterfly_arbitrage": self.has_butterfly_arbitrage,
            "has_calendar_arbitrage": self.has_calendar_arbitrage,
            "calendar_min_gap": self.calendar_min_gap,
        }
        return {
            "jw": self.jw.to_dict(),
            "raw": self.raw.to_dict(),
            "rmse": self.rmse,
            "mae": self.mae,
            "max_residual": self.max_residual,
            # legacy + new
            "arbitrage": {"butterfly_ok": self.butterfly_ok, "g_min": self.g_min},
            "arbitrage_check": arb_check,
        }
