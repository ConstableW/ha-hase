"""Build the local HASE installation archive from an explicit file allowlist."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def build() -> Path:
    component = ROOT / "custom_components/hase"
    manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
    if manifest["domain"] != "hase":
        raise ValueError("Release requires the HASE integration")
    files = [
        ROOT / name for name in ("README.md", "README.en.md", "LICENSE", "hacs.json")
    ]
    for directory in ("tests", "scripts"):
        files.extend(p for p in (ROOT / directory).glob("*.py") if p.is_file())
    files.append(ROOT / "requirements-test.txt")
    files.extend(
        p
        for p in component.rglob("*")
        if p.is_file() and p.suffix in {".py", ".json", ".yaml", ".png"}
    )
    files.extend(p for p in (ROOT / "www/hase").rglob("*") if p.is_file())
    files.extend(
        ROOT / "docs" / name
        for name in ("HASE-ARCHITECTURE.md", "HASE-CUTOVER.md", "HASE-VALIDATION.md")
    )
    files.extend(ROOT / "docs/spellbook-engine" / name for name in (
        "etappe-8-unified-spellbooks.md", "migration-inventory.json",
    ))
    for directory in ("docs/branding", "docs/screenshots"):
        files.extend(p for p in (ROOT / directory).rglob("*.png") if p.is_file())
    # Existing README media remains available, marked as pre-rebrand examples.
    files.extend(
        ROOT / "docs" / name
        for name in ("sample_usage.mp4", "CODE27-HomeAssistant-Integration.pdf")
    )
    output = ROOT / "dist" / f"ha-hase-{manifest['version']}.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Release archive failed integrity check")
    return output


if __name__ == "__main__":
    print(build())
