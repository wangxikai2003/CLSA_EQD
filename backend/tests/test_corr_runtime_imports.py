"""Correlation API imports these modules from research/correlation at request time.

They are runtime code, not optional research scripts. Missing them 500s pairwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORR = ROOT / "research" / "correlation"


def test_corr_mark_modules_are_shipped():
    assert (CORR / "corr_mark_cmds.py").is_file()
    assert (CORR / "corr_mark_local.py").is_file()
    assert (CORR / "corr_mark_cmds_mapping.py").is_file()
    if str(CORR) not in sys.path:
        sys.path.insert(0, str(CORR))
    import corr_mark_cmds
    import corr_mark_cmds_mapping
    import corr_mark_local

    assert callable(corr_mark_cmds.compute_pair)
    assert callable(corr_mark_local.calc_corr)
    assert callable(corr_mark_cmds_mapping.load_universe)
