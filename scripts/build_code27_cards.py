"""Embed the shared owner resolver into each distributable, single-file card.

The canonical resolver lives in scripts/code27_owner_shared.js. The checked-in
cards under custom_components/hase/cards are complete browser modules served by the integration.
No Git commands are run.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "scripts" / "code27_owner_shared.js"
CARDS = {
    ROOT / "custom_components" / "hase" / "cards" / "code27-card.js":
        'import { code27Devices, code27Owner } from "./code27-owner.js";',
    ROOT / "custom_components" / "hase" / "cards" / "haseamp-card.js":
        'import { code27MusicOwner as musicOwnerFor, code27MusicTargets as musicTargetsFor } from "./code27-owner.js";',
}
START = "/* CODE27 owner resolver: generated from scripts/code27_owner_shared.js (start) */"
END = "/* CODE27 owner resolver: generated from scripts/code27_owner_shared.js (end) */"


def generated_block() -> str:
    source = SHARED.read_text(encoding="utf-8").strip()
    source = re.sub(r"^export ", "", source, flags=re.MULTILINE)
    source += "\n\nconst musicOwnerFor = code27MusicOwner;\nconst musicTargetsFor = code27MusicTargets;"
    return f"{START}\n{source}\n{END}"


def expected(source: str, import_line: str) -> str:
    block = generated_block()
    if START in source or END in source:
        if source.count(START) != 1 or source.count(END) != 1:
            raise ValueError("owner resolver markers are missing or duplicated")
        start = source.index(START)
        end = source.index(END, start) + len(END)
        return source[:start] + block + source[end:]
    if source.count(import_line) != 1:
        raise ValueError("owner resolver import is missing or duplicated")
    return source.replace(import_line, block)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift = []
    for path, import_line in CARDS.items():
        source = path.read_text(encoding="utf-8")
        output = expected(source, import_line)
        if output != source:
            if args.check:
                drift.append(str(path))
            else:
                path.write_text(output, encoding="utf-8")
    if drift:
        print("Owner resolver drift in: " + ", ".join(drift))
        return 1
    print("Single-file CODE27 cards verified." if args.check else "Single-file CODE27 cards generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
