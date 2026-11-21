"""Executable split gates without a Home Assistant installation."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
HASE = ROOT / "custom_components" / "hase"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DuplicateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = types.ModuleType("split_code27")
        package.__path__ = [str(HASE / "providers" / "code27")]
        sys.modules["split_code27"] = package
        load("split_code27.const", HASE / "providers" / "code27" / "const.py")
        cls.duplicate = load("split_code27.duplicate", HASE / "providers" / "code27" / "duplicate.py")

    @classmethod
    def tearDownClass(cls):
        for name in ("split_code27.duplicate", "split_code27.const", "split_code27"):
            sys.modules.pop(name, None)

    def test_both_owner_directions_and_distinct_devices(self):
        hase = types.SimpleNamespace(
            entry_id="hase-a", domain="hase", unique_id="machine-a",
            data={"entry_kind": "code27_device", "machine_id": "machine-a"},
        )
        standalone = types.SimpleNamespace(
            entry_id="code27-b", domain="code27", unique_id="machine-b",
            data={"machine_id": "machine-b"},
        )
        manager = types.SimpleNamespace(async_entries=lambda domain: {
            "hase": [hase], "code27": [standalone]
        }[domain])
        hass = types.SimpleNamespace(config_entries=manager)
        self.assertIs(self.duplicate.find_existing_device_entry(hass, "machine-a"), hase)
        self.assertIs(self.duplicate.find_existing_device_entry(hass, "machine-b"), standalone)
        self.assertIsNone(self.duplicate.find_existing_device_entry(hass, "machine-c"))
        self.assertIsNone(self.duplicate.find_existing_device_entry(hass, "machine-a", excluding="hase-a"))


class ExternalBindingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        ha = types.ModuleType("homeassistant")
        ha.__path__ = []
        config = types.ModuleType("homeassistant.config_entries")
        config.ConfigEntry = object
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        issues = types.ModuleType("homeassistant.helpers.issue_registry")
        issues.IssueSeverity = types.SimpleNamespace(ERROR="error")
        issues.async_create_issue = lambda *args, **kwargs: None
        issues.async_delete_issue = lambda *args, **kwargs: None
        helpers.issue_registry = issues
        for name, module in {
            "homeassistant": ha, "homeassistant.config_entries": config,
            "homeassistant.core": core, "homeassistant.helpers": helpers,
            "homeassistant.helpers.issue_registry": issues,
        }.items():
            sys.modules[name] = module
        package = types.ModuleType("split_hase")
        package.__path__ = [str(HASE)]
        sys.modules["split_hase"] = package
        load("split_hase.const", HASE / "const.py")
        cls.module = load("split_hase.code27_external", HASE / "code27_external.py")

    @classmethod
    def tearDownClass(cls):
        for name in (
            "split_hase.code27_external", "split_hase.const", "split_hase",
            "homeassistant.helpers.issue_registry", "homeassistant.helpers",
            "homeassistant.core", "homeassistant.config_entries", "homeassistant",
        ):
            sys.modules.pop(name, None)

    def make_binding(self, version=1, state="loaded"):
        callbacks = []
        chats = []
        hub = types.SimpleNamespace(
            entry_id="code27-a",
            contract_version=version,
            snapshot=types.SimpleNamespace(
                available=True, current_slot={"slot_number": 1},
                system={"ai_language": "de"}, character_slots=(), notification_active=False,
            ),
            subscribe_updates=lambda callback: (lambda: None),
            subscribe_chat=lambda callback: chats.append(callback) or (lambda: chats.remove(callback)),
            send_chat_reply=AsyncMock(return_value="chat-1"),
            speak_text=AsyncMock(), trigger_notification=AsyncMock(),
            acknowledge_notification=AsyncMock(),
        )
        target = types.SimpleNamespace(
            entry_id="code27-a", domain="code27", state=state, runtime_data=hub,
            async_on_state_change=lambda callback: callbacks.append(callback) or (lambda: callbacks.remove(callback)),
        )
        binding_entry = types.SimpleNamespace(
            entry_id="hase-binding", data={"code27_entry_id": "code27-a"}
        )
        events = []
        hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_get_entry=lambda entry_id: target),
            bus=types.SimpleNamespace(
                fire=lambda kind, data: events.append((kind, data)),
                async_fire=Mock(side_effect=AssertionError("unsafe from executor")),
            ),
            async_create_task=lambda coroutine, name=None: asyncio.create_task(coroutine, name=name),
        )
        binding = self.module.Code27ExternalBinding(hass, binding_entry)
        return binding, target, hub, callbacks, chats, events

    async def test_matching_contract_reuses_runtime_and_relays_chat(self):
        binding, _, hub, _, chats, events = self.make_binding()
        await binding.async_start()
        self.assertIs(binding.hub, hub)
        self.assertEqual(binding.reason, "bound")
        self.assertEqual(await binding.async_send_chat_reply("hello", 1), "chat-1")
        chats[0]("user", {"config_entry_id": "code27-a", "text": "hello"})
        self.assertEqual(events[0][0], "hase_user_message")
        self.assertEqual(events[0][1]["config_entry_id"], "hase-binding")
        await binding.async_stop()
        self.assertFalse(chats)

    async def test_tool_dispatcher_is_handed_to_a_hub_that_supports_it(self):
        binding, _, hub, _, _, _ = self.make_binding()
        hub.set_tool_dispatcher = AsyncMock()
        binding.entry.options = {"codie_tool_bridge": "e:s"}
        sentinel = object()
        modules = {
            "split_hase.providers": types.ModuleType("split_hase.providers"),
            "split_hase.providers.code27": types.ModuleType("split_hase.providers.code27"),
            "split_hase.providers.code27.tool_bridge": types.SimpleNamespace(
                dispatcher_from_options=lambda hass, entry_id, options: sentinel if options.get("codie_tool_bridge") == "e:s" else None
            ),
        }
        with patch.dict(sys.modules, modules):
            await binding.async_start()
            hub.set_tool_dispatcher.assert_awaited_once_with(sentinel)
            await binding.async_stop()
        self.assertIsNone(hub.set_tool_dispatcher.await_args_list[-1].args[0])

    async def test_hub_without_tool_support_still_binds(self):
        binding, _, hub, _, _, _ = self.make_binding()
        self.assertFalse(hasattr(hub, "set_tool_dispatcher"))
        await binding.async_start()
        self.assertEqual(binding.reason, "bound")
        await binding.async_stop()

    async def test_chat_from_executor_uses_thread_safe_event_bus(self):
        binding, _, _, _, chats, events = self.make_binding()
        await binding.async_start()
        await asyncio.to_thread(chats[0], "user", {"text": "Lumos"})
        self.assertEqual(events[0][0], "hase_user_message")
        self.assertEqual(events[0][1]["config_entry_id"], "hase-binding")
        binding.hass.bus.async_fire.assert_not_called()
        await binding.async_stop()

    async def test_missing_and_incompatible_contract_fail_closed(self):
        for version in (None, 2):
            binding, _, hub, _, _, _ = self.make_binding(version)
            await binding.async_start()
            self.assertIsNone(binding.hub)
            self.assertIn("contract_", binding.reason)
            hub.send_chat_reply.assert_not_awaited()
            await binding.async_stop()

    async def test_deleted_standalone_entry_creates_actionable_invalid_config_issue(self):
        binding, _, _, _, _, _ = self.make_binding()
        binding.hass.config_entries.async_get_entry = lambda _entry_id: None
        create_issue = Mock()
        previous = self.module.ir.async_create_issue
        self.module.ir.async_create_issue = create_issue
        try:
            await binding.async_start()
            self.assertEqual(binding.reason, "missing_code27_entry")
            self.assertIsNone(binding.hub)
            self.assertEqual(
                create_issue.call_args.kwargs["translation_key"],
                "code27_external_missing",
            )
            await binding.async_stop()
        finally:
            self.module.ir.async_create_issue = previous

    async def test_matching_version_with_wrong_entry_or_shape_fails_closed(self):
        binding, target, hub, _, _, _ = self.make_binding()
        hub.entry_id = "another-device"
        await binding.async_start()
        self.assertEqual(binding.reason, "contract_shape_incomplete")
        self.assertIsNone(binding.hub)
        hub.entry_id = target.entry_id
        hub.send_chat_reply = None
        await binding.async_rebind()
        self.assertEqual(binding.reason, "contract_shape_incomplete")
        self.assertIsNone(binding.hub)
        await binding.async_stop()

    async def test_unload_and_reload_rebind(self):
        binding, target, hub, callbacks, chats, _ = self.make_binding()
        await binding.async_start()
        target.state = "unload_in_progress"
        callbacks[0]()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIsNone(binding.hub)
        self.assertFalse(chats)
        target.state = "loaded"
        callbacks[0]()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIs(binding.hub, hub)
        await binding.async_stop()


class ServiceForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_entry_routes_to_standalone_owner(self):
        source = (HASE / "providers" / "code27" / "services.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        register = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "async_register_services")
        factory = next(node for node in register.body if isinstance(node, ast.FunctionDef)
                       and node.name == "_owner_handler")
        manager = types.SimpleNamespace(async_call=AsyncMock(return_value={"ok": True}))
        binding = types.SimpleNamespace(target_entry_id="code27-a", hub=object(), reason="bound")
        runtime = {"hase-binding": binding}
        hass = types.SimpleNamespace(data={"hase": runtime}, services=manager)
        class ValidationError(Exception):
            pass
        namespace = {
            "hass": hass, "DOMAIN": "hase", "ServiceCall": object,
            "ServiceValidationError": ValidationError,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[])),
                     "<service-forwarding>", "exec"), namespace)
        local = AsyncMock(return_value="local")
        dispatch = namespace["_owner_handler"]("get_memory_cards", local, response=True)
        call = types.SimpleNamespace(data={"config_entry_id": "hase-binding", "character_id": "c"})
        self.assertEqual(await dispatch(call), {"ok": True})
        manager.async_call.assert_awaited_once_with(
            "code27", "get_memory_cards",
            {"config_entry_id": "code27-a", "character_id": "c"},
            blocking=True, return_response=True,
        )
        local.assert_not_awaited()
        binding.hub = None
        with self.assertRaises(ValidationError):
            await dispatch(call)
        runtime["hase-binding"] = object()
        self.assertEqual(await dispatch(call), "local")
