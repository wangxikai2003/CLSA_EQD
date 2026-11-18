"""Optional stop for quantile and regime2.

After an open, if NAV falls ``max_drawdown_target`` from the entry-day NAV,
flatten on the next close and enter an observation period. New entries stay
blocked until the spread has retraced ``rebound_ratio`` of its adverse
excursion from the worst print back toward the entry spread.

The rebound is measured on the spread because the book is flat during
observation, so NAV itself no longer moves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class StopBook:
    phase: str = "idle"  # idle | trade | observe
    direction: int = 0
    entry_spread: float = float("nan")
    entry_nav: float = float("nan")
    extreme: float = float("nan")


def _finite(x: float) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def update_extreme(direction: int, extreme: float, spread: float) -> float:
    if not _finite(spread):
        return extreme
    if not _finite(extreme):
        return float(spread)
    if int(direction) == 1:
        return max(float(extreme), float(spread))
    if int(direction) == -1:
        return min(float(extreme), float(spread))
    return float(extreme)


def rebound_progress(direction: int, entry_spread: float, extreme: float, spread: float) -> float:
    """Fraction of the adverse spread move that has been given back. 1 = full retrace."""
    if not (_finite(entry_spread) and _finite(extreme) and _finite(spread)):
        return 0.0
    if int(direction) == 1:
        adverse = float(extreme) - float(entry_spread)
        if adverse <= 1e-12:
            return 1.0
        return (float(extreme) - float(spread)) / adverse
    if int(direction) == -1:
        adverse = float(entry_spread) - float(extreme)
        if adverse <= 1e-12:
            return 1.0
        return (float(spread) - float(extreme)) / adverse
    return 1.0


def nav_drawdown_from_entry(entry_nav: float, nav: float) -> float:
    if not (_finite(entry_nav) and _finite(nav)) or float(entry_nav) <= 0:
        return 0.0
    return max(0.0, (float(entry_nav) - float(nav)) / float(entry_nav))


def step_stop(
    book: StopBook,
    *,
    position: int,
    nav: float,
    spread: float,
    raw_signal: int,
    max_drawdown_target: float,
    rebound_ratio: float,
) -> tuple[int, str | None, StopBook]:
    """Overlay one close. ``reason`` None keeps the quantile/regime2 reason.

    ``position`` is the book after today's execution. ``raw_signal`` is the
    next-close target before the stop. A stop flatten is itself a next-close
    target, same lag as the rest of the book.
    """
    pos = int(position)
    raw = int(raw_signal)

    if book.phase == "idle":
        if pos == 0 or not (_finite(spread) and _finite(nav)):
            return raw, None, StopBook()
        if raw == 0:
            return 0, None, StopBook()
        opened = StopBook(
            phase="trade",
            direction=pos,
            entry_spread=float(spread),
            entry_nav=float(nav),
            extreme=float(spread),
        )
        return raw, None, opened

    if book.phase == "trade":
        if pos == 0 or pos != int(book.direction):
            if pos != 0 and _finite(spread) and _finite(nav) and raw != 0:
                opened = StopBook(
                    phase="trade",
                    direction=pos,
                    entry_spread=float(spread),
                    entry_nav=float(nav),
                    extreme=float(spread),
                )
                return raw, None, opened
            return raw, None, StopBook()
        extreme = update_extreme(book.direction, book.extreme, spread)
        live = StopBook(
            phase="trade",
            direction=book.direction,
            entry_spread=book.entry_spread,
            entry_nav=book.entry_nav,
            extreme=extreme,
        )
        dd = nav_drawdown_from_entry(book.entry_nav, nav)
        if dd + 1e-9 >= float(max_drawdown_target):
            return 0, "stop_drawdown", StopBook(
                phase="observe",
                direction=live.direction,
                entry_spread=live.entry_spread,
                entry_nav=live.entry_nav,
                extreme=live.extreme,
            )
        if raw == 0:
            return 0, None, StopBook()
        return raw, None, live

    extreme = update_extreme(book.direction, book.extreme, spread)
    watching = StopBook(
        phase="observe",
        direction=book.direction,
        entry_spread=book.entry_spread,
        entry_nav=book.entry_nav,
        extreme=extreme,
    )
    progress = rebound_progress(book.direction, book.entry_spread, extreme, spread)
    if progress + 1e-9 < float(rebound_ratio):
        return 0, "stop_observe", watching
    if pos != 0:
        return 0, "stop_release", StopBook()
    return raw, "stop_release", StopBook()
