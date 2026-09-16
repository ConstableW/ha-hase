"""OPT-IN end-to-end MCP check against a REAL, live Home Assistant
instance - see docs/HASE-MCP.md's own "Optional: E2E check against a real
host" section (section 7). Deliberately named without a `test_` prefix so
neither `python -B -m pytest` nor `python -B -m unittest discover` ever
picks this up automatically - it must be run explicitly, by a human, who
has already confirmed the target host and Bridge are the ones they intend
to use.

What this proves that no local/mocked test can: that a REAL deployed
`custom_components/hase` build, on a REAL Home Assistant instance, with
its REAL installed `mcp`/`uvicorn`/`starlette` package versions, actually
completes a real `initialize` + `tools/list` round-trip over the network -
the exact class of failure this session found live (a transient SSE
connection abort that no local test could have caught, since every local
test uses this same dev environment's own package versions).

This script only ever calls `initialize` and `tools/list` (never
`tools/call`) by default, with no override flag - a `tools/call` is only
ever attempted for a tool this script's OWN `tools/list` response marked
`read_only_hint: true` (an assist_redirect Spell, or any Spell with an
operation, is therefore structurally unreachable here even if named).
`read_only_hint` is not a server-side authorization mechanism (see
docs/HASE-MCP.md section 6) - nothing about this script's own choice of
which tools to call changes what the server would allow. It IS a
truthful, freshly-fetched description of what the very call about to be
made will do, computed by the same server code that decides real
execution - reading it from the live server this script is calling, and
refusing to call anything not marked read-only, is what makes this script
a safe, repeatable check rather than a general-purpose MCP client. It
never performs, and cannot be made to perform, a real device action.

Usage (PowerShell or bash, from the repo root, with `mcp`/`httpx`
installed - same `.venv` the rest of this suite uses):

    $env:HASE_E2E_URL = "http://testzwerg.local:8815/mcp"   # your MCP Server's own configured port - see "Setting one up" in docs/HASE-MCP.md; the general web-UI origin (http://testzwerg.local, no port) is a DIFFERENT service and never proxies this endpoint
    $env:HASE_E2E_TOKEN = "<a real Bridge bearer token>"     # never hardcode a token in this file or commit one anywhere
    python -B tests/e2e_testzwerg_mcp_check.py

Optional: `HASE_E2E_READONLY_TOOL=<spell_id>` additionally calls that ONE
tool (rejected if it is not `read_only_hint: true`) and prints its
response text, to prove a real `tools/call` round-trip too - still never a
device action, by the same read-only-hint gate.
"""
from __future__ import annotations

import asyncio
import os
import sys


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Set {name} before running this script - see the module docstring. Refusing to guess a default.")
        sys.exit(2)
    return value


async def main() -> int:
    url = _require_env("HASE_E2E_URL")
    token = _require_env("HASE_E2E_TOKEN")
    readonly_tool = os.environ.get("HASE_E2E_READONLY_TOOL")

    try:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    except ImportError:
        print("The 'mcp' package is not installed in this environment. Install requirements-test.txt first.")
        return 2

    print(f"Connecting to {url} ...")
    client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    ok = True
    try:
        async with streamable_http_client(url, http_client=client) as streams:
            read_stream, write_stream, *_ = streams
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                print(f"OK   initialize -> server {init_result.serverInfo.name} {init_result.serverInfo.version}")

                tools = await session.list_tools()
                print(f"OK   tools/list -> {len(tools.tools)} tools")
                for tool in tools.tools:
                    read_only = bool(tool.annotations and tool.annotations.read_only_hint)
                    print(f"       - {tool.name}  (read_only_hint={read_only})")

                if readonly_tool:
                    named = next((t for t in tools.tools if t.name == readonly_tool), None)
                    if named is None:
                        print(f"FAIL HASE_E2E_READONLY_TOOL={readonly_tool!r} is not in this Bridge's tools/list.")
                        ok = False
                    elif not (named.annotations and named.annotations.read_only_hint):
                        print(
                            f"FAIL HASE_E2E_READONLY_TOOL={readonly_tool!r} is not read_only_hint=true - "
                            "refusing to call it. This script never calls a tool that may perform a device action."
                        )
                        ok = False
                    else:
                        args = {name: "test" for name in (named.inputSchema or {}).get("required", [])}
                        result = await session.call_tool(readonly_tool, args)
                        text = result.content[0].text if result.content else ""
                        print(f"OK   tools/call {readonly_tool} -> {text!r}")
    except Exception as err:  # noqa: BLE001 - the whole point of this script is to report exactly this
        print(f"FAIL {type(err).__name__}: {err}")
        ok = False
    finally:
        await client.aclose()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
