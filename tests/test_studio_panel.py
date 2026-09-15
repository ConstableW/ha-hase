"""Exercise panel lifecycle with HA's registration API replaced by recording fakes."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]


class PanelTests(IsolatedAsyncioTestCase):
    def setUp(self):
        source = ast.parse((ROOT / "custom_components/hase/studio_api.py").read_text(encoding="utf-8"))
        nodes = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name in {"_register_panel", "async_update_panel", "async_entry_unloaded"}]
        self.frontend = SimpleNamespace(async_register_built_in_panel=Mock(), async_remove_panel=Mock())
        self.custom = SimpleNamespace(async_register_panel=AsyncMock())
        self.scope = {"frontend": self.frontend, "panel_custom": self.custom}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "studio_api.py", "exec"), self.scope)  # noqa: S102 - compile only repository-owned functions for HA API fakes.
        self.hass = SimpleNamespace(data={})

    async def test_default_off_keeps_registered_route(self):
        await self.scope["_register_panel"](self.hass)
        kwargs = self.custom.async_register_panel.call_args.kwargs
        self.assertIsNone(kwargs["sidebar_title"])
        self.assertEqual(kwargs["frontend_url_path"], "hase")
        # The panel route itself must stay reachable by any authenticated
        # user - real Studio authorization is enforced by studio_command()'s
        # WebSocket-level check, not by this HA panel flag.
        self.assertFalse(kwargs["require_admin"])

    async def test_visibility_updates_keep_route_and_module(self):
        for enabled in (True, False):
            self.hass.data["hase_studio_sidebar"] = enabled
            self.scope["async_update_panel"](self.hass)
            kwargs = self.frontend.async_register_built_in_panel.call_args.kwargs
            self.assertEqual(kwargs["sidebar_title"], "HASE" if enabled else None)
            self.assertEqual(kwargs["frontend_url_path"], "hase")
            self.assertTrue(kwargs["update"])
            self.assertFalse(kwargs["require_admin"])
            self.assertIn("studio.js", kwargs["config"]["_panel_custom"]["module_url"])
        self.frontend.async_remove_panel.assert_not_called()

    async def test_unloading_one_of_multiple_clients_keeps_panel(self):
        self.hass.data.update(hase_studio=SimpleNamespace(entries={"a", "b"}), hase_studio_panel=True)
        self.scope["async_entry_unloaded"](self.hass, SimpleNamespace(entry_id="a"))
        self.frontend.async_remove_panel.assert_not_called()
        self.scope["async_entry_unloaded"](self.hass, SimpleNamespace(entry_id="b"))
        self.frontend.async_remove_panel.assert_called_once_with(self.hass, "hase")
