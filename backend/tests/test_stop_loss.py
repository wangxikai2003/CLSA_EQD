"""Stop-loss overlay: entry-NAV drawdown, then spread-retrace observation."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.stop_loss import StopBook, rebound_progress, step_stop


def test_underwater_10pct_flattens_and_blocks_until_retrace():
    book = StopBook()
    # Open long at spread 2.0, NAV 100. Same-day signal stays long.
    sig, reason, book = step_stop(
        book, position=1, nav=100.0, spread=2.0, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 1 and reason is None and book.phase == "trade"

    # 5% underwater, spread wider: still hold.
    sig, reason, book = step_stop(
        book, position=1, nav=95.0, spread=2.4, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 1 and reason is None and book.phase == "trade"

    # 10% underwater. Flatten next close and start observation.
    sig, reason, book = step_stop(
        book, position=1, nav=90.0, spread=3.0, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 0 and reason == "stop_drawdown" and book.phase == "observe"

    # Flat, but a fresh quantile entry is blocked. Spread worsens to 4.0.
    sig, reason, book = step_stop(
        book, position=0, nav=90.0, spread=4.0, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 0 and reason == "stop_observe"
    # Adverse leg is 4.0 - 2.0 = 2.0. 90% retrace means spread <= 4.0 - 1.8 = 2.2.
    assert rebound_progress(1, 2.0, book.extreme, 2.2) >= 0.9 - 1e-9

    sig, reason, book = step_stop(
        book, position=0, nav=90.0, spread=2.5, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 0 and reason == "stop_observe"

    sig, reason, book = step_stop(
        book, position=0, nav=90.0, spread=2.2, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 1 and reason == "stop_release" and book.phase == "idle"


def test_q50_exit_does_not_open_observation():
    book = StopBook()
    _, _, book = step_stop(
        book, position=1, nav=100.0, spread=2.0, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    sig, reason, book = step_stop(
        book, position=1, nav=97.0, spread=1.0, raw_signal=0,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 0 and reason is None and book.phase == "idle"


def test_short_rebound_is_up_from_the_low():
    book = StopBook()
    _, _, book = step_stop(
        book, position=-1, nav=100.0, spread=1.0, raw_signal=-1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    sig, reason, book = step_stop(
        book, position=-1, nav=89.0, spread=0.0, raw_signal=-1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 0 and reason == "stop_drawdown"
    # Entry 1.0, extreme later  -0.5. Adverse = 1.5. 90% rebound → spread >= 0.85.
    sig, reason, book = step_stop(
        book, position=0, nav=89.0, spread=-0.5, raw_signal=-1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert reason == "stop_observe"
    sig, reason, book = step_stop(
        book, position=0, nav=89.0, spread=0.85, raw_signal=-1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == -1 and reason == "stop_release" and book.phase == "idle"


def test_profit_then_giveback_does_not_stop_while_above_entry_nav():
    book = StopBook()
    _, _, book = step_stop(
        book, position=1, nav=100.0, spread=2.0, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    sig, reason, book = step_stop(
        book, position=1, nav=108.0, spread=1.2, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 1 and book.phase == "trade"
    sig, reason, book = step_stop(
        book, position=1, nav=101.0, spread=1.9, raw_signal=1,
        max_drawdown_target=0.10, rebound_ratio=0.9,
    )
    assert sig == 1 and reason is None and book.phase == "trade"
