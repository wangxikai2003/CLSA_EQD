"""GCS → LIVE_DATA_DIR hot reload (no Cloud Run image rebuild).

Env:
  GCS_DATA_PREFIX   gs://bucket/prefix   (objects: prefix/cffex_vol.sqlite …)
  LIVE_DATA_DIR     default /tmp/clsa_live_data
  DATA_RELOAD_TOKEN shared secret for /api/admin/reload-data
  DATA_POLL_SECONDS background poll interval (default 300; 0 = off)
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_paths import HOT_FILES, LIVE_DATA_DIR, describe

log = logging.getLogger("clsa.data")

_GEN_FILE = ".gcs_generation"
_lock = threading.Lock()
_poll_stop = threading.Event()
_last_status: Dict[str, Any] = {"ok": True, "detail": "not_synced_yet"}


def gcs_prefix() -> Optional[str]:
    p = (os.environ.get("GCS_DATA_PREFIX") or "").strip().rstrip("/")
    return p or None


def _parse_gs(uri: str) -> Tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"GCS_DATA_PREFIX must be gs://… got {uri}")
    rest = uri[5:]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise ValueError(f"invalid GCS uri: {uri}")
    return bucket, prefix


def _generation_path() -> Path:
    return LIVE_DATA_DIR / _GEN_FILE


def local_generation() -> Optional[str]:
    p = _generation_path()
    if p.exists():
        return p.read_text().strip() or None
    return None


def _set_local_generation(gen: str) -> None:
    LIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _generation_path().write_text(gen)


def _remote_fingerprint(bucket_name: str, prefix: str) -> Tuple[str, List[Tuple[str, Any]]]:
    from google.cloud import storage  # type: ignore

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    found: List[Tuple[str, Any]] = []
    for name in HOT_FILES:
        blob_path = f"{prefix}/{name}" if prefix else name
        blob = bucket.blob(blob_path)
        if not blob.exists():
            continue
        blob.reload()
        found.append((name, blob.generation))
    if not found:
        raise FileNotFoundError(f"no hot files under gs://{bucket_name}/{prefix}")
    names = {n for n, _ in found}
    if "cffex_vol.sqlite" not in names:
        raise FileNotFoundError("cffex_vol.sqlite missing in GCS prefix")
    fp = "|".join(f"{n}:{g}" for n, g in sorted(found))
    return fp, found


def _download_blobs(bucket_name: str, prefix: str, staging: Path, found: List[Tuple[str, Any]]) -> None:
    from google.cloud import storage  # type: ignore

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    staging.mkdir(parents=True, exist_ok=True)
    for name, _gen in found:
        blob_path = f"{prefix}/{name}" if prefix else name
        dest = staging / name
        bucket.blob(blob_path).download_to_filename(str(dest))
        log.info(
            "downloaded gs://%s/%s → %s (%.1f MB)",
            bucket_name,
            blob_path,
            dest,
            dest.stat().st_size / 1e6,
        )


def _validate_sqlite(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 1000:
        raise ValueError(f"sqlite too small/missing: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if int(n) < 1:
            raise ValueError("products table empty")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "option_iv" not in tables and "svi_slices" not in tables:
            raise ValueError("neither option_iv nor svi_slices found")
    finally:
        conn.close()


def _promote(staging: Path, found: List[Tuple[str, Any]]) -> None:
    LIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, _ in found:
        src = staging / name
        dst = LIVE_DATA_DIR / name
        tmp = LIVE_DATA_DIR / f".{name}.tmp"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)


def _bust_caches() -> None:
    try:
        from api.v1 import synth_tenors as st

        if hasattr(st, "clear_memory_cache"):
            st.clear_memory_cache()
    except Exception as e:  # pragma: no cover
        log.warning("synth cache bust: %s", e)


def sync_from_gcs(*, force: bool = False) -> Dict[str, Any]:
    """Download hot files from GCS into LIVE_DATA_DIR when fingerprint changes."""
    global _last_status
    prefix_uri = gcs_prefix()
    if not prefix_uri:
        _last_status = {"ok": True, "skipped": True, "detail": "GCS_DATA_PREFIX unset"}
        return _last_status

    with _lock:
        try:
            bucket_name, prefix = _parse_gs(prefix_uri)
            fp, found = _remote_fingerprint(bucket_name, prefix)
            cur = local_generation()
            if not force and cur == fp:
                _last_status = {
                    "ok": True,
                    "unchanged": True,
                    "generation": fp,
                    "files": [n for n, _ in found],
                    "paths": describe(),
                }
                return _last_status

            staging = Path("/tmp") / f"clsa_stage_{int(time.time())}"
            try:
                _download_blobs(bucket_name, prefix, staging, found)
                _validate_sqlite(staging / "cffex_vol.sqlite")
                _promote(staging, found)
                _set_local_generation(fp)
                _bust_caches()
            finally:
                shutil.rmtree(staging, ignore_errors=True)

            _last_status = {
                "ok": True,
                "updated": True,
                "forced": force,
                "generation": fp,
                "files": [n for n, _ in found],
                "paths": describe(),
            }
            log.info("hot-reload ok generation=%s files=%s", fp, [n for n, _ in found])
            return _last_status
        except Exception as e:
            log.exception("hot-reload failed")
            _last_status = {"ok": False, "error": str(e), "paths": describe()}
            return _last_status


def last_status() -> Dict[str, Any]:
    return dict(_last_status)


def start_background_poll() -> None:
    sec = int(os.environ.get("DATA_POLL_SECONDS", "300") or "0")
    if sec <= 0 or not gcs_prefix():
        return

    def _loop() -> None:
        while not _poll_stop.wait(sec):
            try:
                sync_from_gcs(force=False)
            except Exception:  # pragma: no cover
                log.exception("poll sync failed")

    t = threading.Thread(target=_loop, name="gcs-data-poll", daemon=True)
    t.start()
    log.info("GCS data poll every %ss", sec)


def stop_background_poll() -> None:
    _poll_stop.set()
