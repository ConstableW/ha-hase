"""HASE MCP architecture, Phase 4: wraps _mcp_protocol_check.py (a real
network Streamable HTTP round-trip via the official mcp SDK client
against the actual providers.mcp.transport code) the same way
test_hase_package.py wraps _hase_import_smoke.py - run in a subprocess so
its sys.modules Home Assistant stubs never leak into this pytest session,
and because it binds a real (ephemeral) TCP port.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class McpProtocolTests(unittest.TestCase):
    def test_real_streamable_http_round_trip_via_official_mcp_client(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/_mcp_protocol_check.py")],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
