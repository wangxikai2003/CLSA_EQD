"""Admin endpoints for GCS data hot-reload (no image rebuild)."""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException

from data_hot_reload import last_status, sync_from_gcs
from data_paths import describe

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _check_token(token: str | None) -> None:
    expected = (os.environ.get("DATA_RELOAD_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(503, "DATA_RELOAD_TOKEN not configured on server")
    if not token or token.strip() != expected:
        raise HTTPException(401, "invalid or missing X-Data-Reload-Token")


@router.get("/data-status")
def data_status(x_data_reload_token: str | None = Header(default=None)) -> Dict[str, Any]:
    _check_token(x_data_reload_token)
    return {"paths": describe(), "sync": last_status()}


@router.post("/reload-data")
def reload_data(
    force: bool = True,
    x_data_reload_token: str | None = Header(default=None),
) -> Dict[str, Any]:
    """Pull latest objects from GCS_DATA_PREFIX into LIVE_DATA_DIR."""
    _check_token(x_data_reload_token)
    out = sync_from_gcs(force=force)
    if not out.get("ok"):
        raise HTTPException(500, out.get("error") or "reload failed")
    return out
