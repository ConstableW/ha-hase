"""Run the frontend node:test suites as part of the pytest run.

Covers the Studio panel and the HASEAMP card. Skipped (not failed) when Node.js
is unavailable, so a Python-only environment keeps working; where Node exists,
frontend regressions (e.g. Explain rendering, conflict badges, i18n coverage,
HASEAMP capability gating) fail the suite.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SUITES = [
    "test_studio_frontend.mjs",
    "test_studio_explain_frontend.mjs",
    "test_studio_book_tabs.mjs",
    "test_haseamp_frontend.mjs",
    "test_code27_owner_frontend.mjs",
]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize("suite", SUITES)
def test_frontend_node_suite(suite):
    completed = subprocess.run(
        [NODE, "--test", str(TESTS / suite)],
        cwd=TESTS.parent, capture_output=True, text=True, timeout=180,
    )
    assert completed.returncode == 0, completed.stdout[-4000:] + completed.stderr[-2000:]
