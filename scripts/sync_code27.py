"""Generate and verify the standalone CODE27 integration from HASE.

The HASE provider (custom_components/hase/providers/code27) is the canonical
source of the shared device code. The standalone integration is generated
from it with a few mechanical rewrites; its wrappers (setup, config flow,
external hub, manifest, strings, translations) are maintained by hand
under distribution/code27.

HASE-only modules are never copied: backend.py and
tool_bridge.py (glue that needs HASE or the MCP server). The shared files must not import them - the Codie tool gateway
(tool_executor.py) only knows an injected ToolDispatcher, so a standalone
CODE27 simply has no tools until a host (HASE) provides a dispatcher.

Usage:
    python scripts/sync_code27.py --standalone <path-to-ha-code27>
    python scripts/sync_code27.py --check --standalone <path-to-ha-code27>

Only writes working-tree files; never calls Git.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HASE_ROOT = Path(__file__).resolve().parents[1]
SOURCE = HASE_ROOT / "custom_components" / "hase" / "providers" / "code27"
WRAPPER_SOURCE = HASE_ROOT / "distribution" / "code27"
CARDS_SOURCE = HASE_ROOT / "custom_components" / "hase" / "cards"
DEFAULT_STANDALONE = HASE_ROOT.parent / "HA code27"

CORE_FILES = (
    "api.py", "websocket_client.py", "coordinator.py", "music.py", "music_model.py",
    "music_runtime.py", "music_api.py", "const.py", "entity.py", "binary_sensor.py",
    "button.py", "image.py", "light.py", "media_player.py", "number.py", "select.py",
    "sensor.py", "switch.py", "todo.py", "diagnostics.py", "repairs.py", "services.py",
    "duplicate.py", "tool_executor.py", "speech.py",
)
HASE_ONLY = ("backend.py", "tool_bridge.py")
STANDALONE_WRAPPERS = (
    "__init__.py", "config_flow.py", "external_hub.py", "manifest.json",
    "strings.json", "services.yaml", "translations/de.json", "translations/en.json",
)
CARD_FILES = ("code27-card.js", "haseamp-card.js", "portrait.png")


def generated_text(name: str) -> str:
    text = (SOURCE / name).read_text(encoding="utf-8")
    text = text.replace("from ...const import (DOMAIN)", "from .const import DOMAIN")
    text = text.replace("from ...const import DOMAIN", "from .const import DOMAIN")
    if name == "const.py":
        return 'DOMAIN = "code27"\n\n' + text
    if name == "music_runtime.py":
        return text.replace("hase.haseamp", "code27.haseamp")
    if name == "music_api.py":
        return text.replace("hase/code27/music", "code27/music")
    if name == "services.py":
        text = text.replace(
            "CODE27 device services exposed under the HASE domain.", "CODE27 standalone device services."
        )
        start = text.index("    def _owner_handler(")
        end = text.index("    def _get_coordinator(", start)
        text = text[:start] + text[end:]
        text = re.sub(
            r"_owner_handler\(SERVICE_[A-Z_]+, (_handle_[a-z_]+)(?:, response=True)?\)", r"\1", text
        )
        return text.replace(
            '        isinstance(value, Code27Coordinator) or getattr(value, "target_entry_id", None)\n',
            "        isinstance(value, Code27Coordinator)\n",
        )
    return text


def expected_files(standalone: Path) -> dict[Path, bytes]:
    target = standalone / "custom_components" / "code27"
    files: dict[Path, bytes] = {}
    for name in CORE_FILES:
        files[target / name] = generated_text(name).encode("utf-8")
    for name in STANDALONE_WRAPPERS:
        files[target / name] = (WRAPPER_SOURCE / name).read_bytes()
    for name in CARD_FILES:
        files[target / "cards" / name] = (CARDS_SOURCE / name).read_bytes()
    return files


def shared_import_problems() -> list[str]:
    """Shared files must not import HASE-only modules or HASE internals."""
    problems = []
    pattern = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import", re.MULTILINE)
    banned = {m[:-3] for m in HASE_ONLY} | {"mcp", "core", "spellbook_loader", "spellbooker_ha"}
    for name in CORE_FILES:
        for dots, module in pattern.findall(generated_text(name)):
            head = module.split(".")[0]
            if len(dots) >= 2 and head not in ("", "const"):
                problems.append(f"{name}: imports {dots}{module}")
            elif len(dots) == 1 and head in banned:
                problems.append(f"{name}: imports {dots}{module}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--standalone", type=Path, default=DEFAULT_STANDALONE)
    parser.add_argument("--check", action="store_true", help="only report drift")
    args = parser.parse_args()
    standalone: Path = args.standalone
    if not (standalone / "custom_components" / "code27").is_dir():
        print(f"Standalone integration directory not found: {standalone}", file=sys.stderr)
        return 2
    problems = shared_import_problems()
    if problems:
        print("\n".join(f"COUPLING: {p}" for p in problems), file=sys.stderr)
        return 1
    expected = expected_files(standalone)
    drift = [p for p, data in expected.items() if not p.exists() or p.read_bytes() != data]
    if args.check:
        for path in drift:
            print(f"DRIFT: {path.relative_to(standalone)}")
        print("CODE27 standalone matches HASE." if not drift else f"{len(drift)} file(s) differ.")
        return 1 if drift else 0
    for path in drift:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected[path])
        print(f"wrote {path.relative_to(standalone)}")
    # Files a HASE-only module would have produced must not linger.
    for name in HASE_ONLY:
        stale = standalone / "custom_components" / "code27" / name
        if stale.exists():
            print(f"STALE (remove by hand): {stale.relative_to(standalone)}")
    print(f"CODE27 standalone synchronized ({len(drift)} changed); no Git commands run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
