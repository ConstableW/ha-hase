"""Release boundaries the historical AST-stripped tests cannot cover."""

import ast
import json
from pathlib import Path
import subprocess
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/hase"


class HasePackageTests(unittest.TestCase):
    def test_single_domain_and_matching_manifest(self):
        folders = {
            p.name
            for p in (ROOT / "custom_components").iterdir()
            if p.is_dir() and p.name != "__pycache__"
        }
        self.assertEqual(folders, {"hase"})
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["domain"], "hase")
        self.assertTrue(manifest["config_flow"])
        self.assertIn("ha-hase", manifest["documentation"])

    def test_core_has_no_provider_or_ha_imports(self):
        for path in (COMPONENT / "core").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("homeassistant", node.module or "", str(path))
                    self.assertNotIn("providers", node.module or "", str(path))
                    self.assertLessEqual(node.level, 1, str(path))
                elif isinstance(node, ast.Import):
                    self.assertTrue(
                        all(
                            not a.name.startswith(
                                ("homeassistant", "custom_components")
                            )
                            for a in node.names
                        ),
                        str(path),
                    )

    def test_actual_module_imports_and_service_lifecycle(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/_hase_import_smoke.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_public_names_and_translation_structure(self):
        services = yaml.safe_load(
            (COMPONENT / "services.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(services),
            {
                "create_alarm",
                "update_alarm",
                "delete_alarm",
                "send_chat_message",
                "notify",
                "notify_clear",
                "speak",
            },
        )
        for value in services.values():
            self.assertEqual(
                value["fields"]["config_entry_id"]["selector"]["config_entry"][
                    "integration"
                ],
                "hase",
            )
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

        def keys(value, prefix=""):
            return {prefix + key for key in value} | set().union(
                *(
                    keys(v, prefix + k + ".")
                    for k, v in value.items()
                    if isinstance(v, dict)
                )
            )

        for language in ("de", "en"):
            translation = json.loads(
                (COMPONENT / f"translations/{language}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(keys(strings), keys(translation))
        card = (ROOT / "www/hase/code27-card.js").read_text(encoding="utf-8")
        self.assertIn('this._call("hase", "send_chat_message"', card)
        self.assertIn('data.domain !== "hase"', card)

    def test_resource_inventory_not_empty_after_move(self):
        expected = {
            "providers/code27/spellbooks": {"middle_earth", "potter_verse"},
            "spellbooker_books": {"demo", "middle_earth", "potter_verse"},
            "vacuum_providers": {"dreame_vacuum", "roborock", "xiaomi_miio"},
        }
        for directory, names in expected.items():
            files = list((COMPONENT / directory).glob("*.yaml"))
            self.assertEqual({f.stem for f in files}, names)
            for file in files:
                self.assertIsInstance(
                    yaml.safe_load(file.read_text(encoding="utf-8")), dict
                )

    def test_no_old_runtime_imports_or_user_directories(self):
        forbidden = (
            "custom_components.code27",
            "code27_spellbooks",
            "code27_spellbooker_books",
            "code27_vacuum_providers",
        )
        for path in COMPONENT.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, source, str(path))
