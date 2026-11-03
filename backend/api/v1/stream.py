"""WebSocket /api/v1/pricing/stream — batched progress for long MC (no double-run)."""
from __future__ import annotations

import asyncio
import math
import uuid
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from products.registry import price_product

router = APIRouter(tags=["pricing-stream"])


def _merge_batches(partials: List[Tuple[int, Dict[str, Any]]]) -> Dict[str, Any]:
    """Combine independent MC batches into a single price / stderr estimate."""
    n_tot = sum(n for n, _ in partials)
    if n_tot <= 0:
        return {"price": 0.0, "stderr": 0.0, "price_ci_95": [0.0, 0.0]}
    # weighted mean of batch means
    price = sum(n * out["price"] for n, out in partials) / n_tot
    # pooled stderr from batch stderrs (approx independent)
    var = 0.0
    for n, out in partials:
        se = float(out.get("stderr") or 0.0)
        # se^2 ≈ var/n_batch ⇒ var ≈ se^2 * n_batch; pool then / n_tot
        var += (se * se) * max(n, 1)
    stderr = math.sqrt(var) / math.sqrt(n_tot) if n_tot else 0.0
    last = partials[-1][1]
    return {
        "price": price,
        "stderr": stderr,
        "price_ci_95": [price - 1.96 * stderr, price + 1.96 * stderr],
        "diagnostics": {
            **(last.get("diagnostics") or {}),
            "n_paths_merged": n_tot,
            "n_batches": len(partials),
        },
        "greeks": {},
        "meta": {**(last.get("meta") or {}), "n_paths": n_tot, "merged": True},
        "raw_profile": last.get("raw_profile"),
        "shifted_profile": last.get("shifted_profile"),
        "reserve_metrics": last.get("reserve_metrics"),
    }


@router.websocket("/api/v1/pricing/stream")
async def pricing_stream(ws: WebSocket):
    await ws.accept()
    cancelled = asyncio.Event()
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "cancel":
                cancelled.set()
                await ws.send_json({"type": "cancelled"})
                continue
            if mtype != "start":
                await ws.send_json({"type": "error", "message": "expected type=start|cancel"})
                continue

            cancelled.clear()
            task_id = msg.get("task_id") or str(uuid.uuid4())
            req: Dict[str, Any] = msg.get("request") or {}
            sim = dict(req.get("sim") or {})
            greeks_cfg = dict(req.get("greeks") or {"enabled": False})
            # profiles only on final merged greeks pass if requested — keep batches light
            greeks_batches = {**greeks_cfg, "enabled": False, "return_profiles": False}
            total = int(sim.get("n_paths", 20000))
            batch = max(total // 10, 1000)
            partials: List[Tuple[int, Dict[str, Any]]] = []
            n_done = 0
            seed0 = int(sim.get("seed", 42))
            market = req.get("market") or {"spots": [100.0], "r": 0.02, "q": [0.0], "flat_vols": [0.2]}
            product_type = req.get("product_type", "snowball")
            product = req.get("product") or {}

            while n_done < total:
                if cancelled.is_set():
                    await ws.send_json({"type": "cancelled", "task_id": task_id})
                    break
                n_batch = min(batch, total - n_done)
                sim_b = dict(sim)
                sim_b["n_paths"] = n_batch
                sim_b["seed"] = seed0 + n_done
                stage = "Simulating paths"
                if n_done + n_batch >= total and greeks_cfg.get("enabled"):
                    stage = "Computing Delta/Gamma Sensitivities"
                out = await asyncio.to_thread(
                    price_product,
                    product_type,
                    product,
                    market,
                    sim_b,
                    greeks_batches,
                )
                partials.append((n_batch, out))
                n_done += n_batch
                wsum = sum(n * o["price"] for n, o in partials)
                running = wsum / n_done
                # running stderr from batch stderrs
                var = sum((float(o.get("stderr") or 0.0) ** 2) * n for n, o in partials)
                se = math.sqrt(var) / math.sqrt(n_done) if n_done else 0.0
                await ws.send_json(
                    {
                        "type": "progress",
                        "task_id": task_id,
                        "progress": round(n_done / total, 4),
                        "pct": round(100.0 * n_done / total, 2),
                        "current_paths": n_done,
                        "total_paths": total,
                        "running_price": running,
                        "std_error": se,
                        "stage": stage,
                        "partial_price": running,
                        "n_done": n_done,
                    }
                )
                await asyncio.sleep(0)

            if cancelled.is_set():
                continue

            merged = _merge_batches(partials)
            # Optional single Greeks / dual-profile pass (once) when requested
            if greeks_cfg.get("enabled") or greeks_cfg.get("return_profiles") or greeks_cfg.get(
                "barrier_shift_bps"
            ):
                await ws.send_json(
                    {
                        "type": "progress",
                        "task_id": task_id,
                        "progress": 0.95,
                        "pct": 95.0,
                        "current_paths": total,
                        "total_paths": total,
                        "running_price": merged["price"],
                        "std_error": merged["stderr"],
                        "stage": "Computing Delta/Gamma Sensitivities",
                    }
                )
                if cancelled.is_set():
                    await ws.send_json({"type": "cancelled", "task_id": task_id})
                    continue
                final = await asyncio.to_thread(
                    price_product,
                    product_type,
                    product,
                    market,
                    sim,
                    greeks_cfg,
                )
                # keep merged MC stderr/price if greeks-only; prefer final price when profiles
                if greeks_cfg.get("return_profiles") or greeks_cfg.get("barrier_shift_bps"):
                    payload = final
                else:
                    payload = {**merged, "greeks": final.get("greeks") or {}}
            else:
                payload = merged

            await ws.send_json({"type": "result", "task_id": task_id, "payload": payload})
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
