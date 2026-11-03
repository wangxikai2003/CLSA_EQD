"""Timing + unified error helpers."""
from __future__ import annotations

import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("clsa.api")


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        response.headers["X-Process-Time-Ms"] = f"{ms:.1f}"
        if request.url.path.startswith("/api/"):
            logger.info("%s %s -> %s (%.1fms)", request.method, request.url.path, response.status_code, ms)
        return response
