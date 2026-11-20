"""Central data-file resolution with optional live overlay (GCS hot-reload).

Priority for each file:
  1. LIVE_DATA_DIR / name   (writable; hot-updated from GCS)
  2. bundled candidates      (/app/data, CLSA_EQD/data, repo data/)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]  # CLSA_EQD
REPO = ROOT.parent  # vol_trading
BACKEND = Path(__file__).resolve().parent

# Writable overlay used by hot-reload (Cloud Run: /tmp is writable)
LIVE_DATA_DIR = Path(os.environ.get("LIVE_DATA_DIR", "/tmp/clsa_live_data"))

# Bundled / checked-in locations (image COPY or local dev)
_BUNDLED_DIRS = [
    Path("/app/data"),
    ROOT / "data",
    REPO / "data",
]

# Core artifacts that can be hot-swapped
HOT_FILES = (
    "cffex_vol.sqlite",
    "commodity_fut.sqlite",
    "commodity_vol_au.sqlite",
    "commodity_vol.sqlite",
    "qis_results.sqlite",
    "rates_eod.parquet",
    "index_spot.parquet",
    "vol_spread_levels.parquet",
    "synth_tenors_cache.json.gz",
)


def live_dir() -> Path:
    return LIVE_DATA_DIR


def bundled_dirs() -> List[Path]:
    return [p for p in _BUNDLED_DIRS]


def resolve(name: str, *, required: bool = False) -> Optional[Path]:
    """Return first existing path for `name` (live overlay wins)."""
    live = LIVE_DATA_DIR / name
    if live.exists():
        return live
    for d in _BUNDLED_DIRS:
        p = d / name
        if p.exists():
            return p
    if required:
        raise FileNotFoundError(name)
    return None


def resolve_any(names: Iterable[str], *, required: bool = False) -> Optional[Path]:
    for n in names:
        p = resolve(n)
        if p is not None:
            return p
    if required:
        raise FileNotFoundError(",".join(names))
    return None


def cffex_vol() -> Path:
    p = resolve("cffex_vol.sqlite")
    if p is None:
        raise FileNotFoundError("cffex_vol.sqlite not found")
    return p


def commodity_db() -> Optional[Path]:
    """Futures-oriented commodity DB (slim commodity_fut preferred for Corr)."""
    return resolve_any(["commodity_fut.sqlite", "commodity_vol.sqlite"])


def commodity_vol() -> Optional[Path]:
    """Option-IV commodity surfaces. Prefer full DB, then AU/lite extract for deploy."""
    return resolve_any(
        [
            "commodity_vol.sqlite",
            "commodity_vol_lite.sqlite",
            "commodity_vol_au.sqlite",
        ]
    )


def qis_results() -> Path:
    p = resolve("qis_results.sqlite")
    if p is not None:
        return p
    # allow create under live or local app data
    out = LIVE_DATA_DIR / "qis_results.sqlite"
    if not LIVE_DATA_DIR.exists():
        out = ROOT / "data" / "qis_results.sqlite"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def rates_eod() -> Optional[Path]:
    return resolve("rates_eod.parquet")


def index_spot() -> Optional[Path]:
    return resolve("index_spot.parquet")


def vol_spread_levels() -> Optional[Path]:
    return resolve("vol_spread_levels.parquet")


def synth_tenors_cache() -> Optional[Path]:
    return resolve("synth_tenors_cache.json.gz")


def describe() -> dict:
    files = {}
    for name in HOT_FILES:
        p = resolve(name)
        files[name] = {
            "path": str(p) if p else None,
            "exists": p is not None,
            "live": bool(p is not None and p.parent.resolve() == LIVE_DATA_DIR.resolve()),
            "size_mb": round(p.stat().st_size / 1e6, 2) if p and p.exists() else None,
        }
    return {
        "live_data_dir": str(LIVE_DATA_DIR),
        "gcs_prefix": os.environ.get("GCS_DATA_PREFIX") or None,
        "files": files,
    }
