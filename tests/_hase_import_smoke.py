"""Import real HASE modules in a subprocess with only HA's external API stubbed.

Not a Home Assistant compatibility test: no integration source/import is stripped.
The subprocess keeps these stubs out of the existing behavioral test suite.
"""

import ast
import asyncio
import importlib
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMPONENT = ROOT / "custom_components/hase"


class SurfaceMeta(type):
    def __getattr__(cls, name):
        # Feature flags only need to combine at module import time.
        value = (
            1 << len(cls.__dict__) if cls.__name__.endswith("Feature") else name.lower()
        )
        setattr(cls, name, value)
        return value


class Surface(metaclass=SurfaceMeta):
    def __init_subclass__(cls, **kwargs):
        cls.flow_domain = kwargs.get("domain")

    def __init__(self, *args, **kwargs):
        self.__dict__.update(kwargs)

    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def async_show_menu(self, **kwargs):
        return kwargs

    def async_show_form(self, **kwargs):
        return kwargs

    def async_create_entry(self, **kwargs):
        return kwargs


def module(name):
    if name in sys.modules:
        return sys.modules[name]
    result = types.ModuleType(name)
    result.__path__ = []
    sys.modules[name] = result
    if "." in name:
        parent, child = name.rsplit(".", 1)
        setattr(module(parent), child, result)
    return result


# Explicitly discover the imported HA surface, including function-local imports.
imports = []
for path in COMPONENT.rglob("*.py"):
    imports.extend(
        n
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.ImportFrom)
        and n.module
        and n.module.startswith("homeassistant")
    )
for node in imports:
    module(node.module)
for node in imports:
    target = module(node.module)
    for alias in node.names:
        if hasattr(target, alias.name):
            continue
        if alias.name.isupper():
            value = alias.name.removeprefix("CONF_").removeprefix("ATTR_").lower()
        elif alias.name[0].isupper():
            base = Exception if alias.name.endswith(("Error", "Failed")) else Surface
            value = type(alias.name, (base,), {})
        elif alias.name in {
            "selector",
            "intent",
            "config_validation",
            "conversation",
            "area_registry",
            "device_registry",
            "entity_registry",
            "websocket_api",
            "panel_custom",
        }:
            value = module(node.module + "." + alias.name)
        else:
            value = lambda *args, **kwargs: None
        setattr(target, alias.name, value)

# Studio decorators are external HA registration surfaces, not engine logic.
ws = module("homeassistant.components.websocket_api")
ws.websocket_command = lambda schema: lambda fn: fn
ws.require_admin = lambda fn: fn
ws.async_response = lambda fn: fn
module("homeassistant.core").callback = lambda fn: fn
module("homeassistant.helpers.config_validation").ensure_list = lambda value: (
    value if isinstance(value, list) else [value]
)
selector = module("homeassistant.helpers.selector")
for path in COMPONENT.rglob("*.py"):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "selector"
        ):
            setattr(selector, node.attr, type(node.attr, (Surface,), {}))

loaded = {}
for path in sorted(COMPONENT.rglob("*.py")):
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    name = ".".join(parts)
    loaded[name] = importlib.import_module(name)

integration = loaded["custom_components.hase"]
provider = loaded["custom_components.hase.providers.code27"]
services = loaded["custom_components.hase.providers.code27.services"]
flow = loaded["custom_components.hase.config_flow"]
assert flow.HaseConfigFlow.flow_domain == "hase"
assert flow.HaseConfigFlow()._port == 8081
assert "custom_components.code27" not in sys.modules
for name in (
    "sensor",
    "binary_sensor",
    "button",
    "light",
    "media_player",
    "select",
    "switch",
    "todo",
):
    assert (
        loaded[f"custom_components.hase.{name}"].async_setup_entry
        is loaded[f"custom_components.hase.providers.code27.{name}"].async_setup_entry
    )
coordinator_module = loaded["custom_components.hase.providers.code27.coordinator"]
assert coordinator_module.EVENT_USER_MESSAGE == "hase_user_message"
assert coordinator_module.EVENT_ASSISTANT_MESSAGE == "hase_assistant_message"


class Services:
    def __init__(self):
        self.handlers = {}

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, schema, **kwargs):
        assert (domain, service) not in self.handlers
        self.handlers[domain, service] = (handler, schema)

    def async_remove(self, domain, service):
        self.handlers.pop((domain, service))


async def verify():
    N = types.SimpleNamespace
    hass = N(
        data={},
        services=Services(),
        config=N(language="en"),
        config_entries=N(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )
    entry = N(
        entry_id="device-new",
        data={
            "host": "device",
            "port": 8081,
            "user_id": "user",
            "local_secret": "secret",
        },
        options={},
        subentries={},
        async_on_unload=lambda f: None,
        add_update_listener=lambda f: lambda: None,
    )
    client = N(
        create_alarm=AsyncMock(),
        update_alarm=AsyncMock(),
        delete_alarm=AsyncMock(),
        send_chat_text=AsyncMock(),
    )
    coordinator = N(
        entry=entry,
        client=client,
        data=N(current_slot={"slot_number": 1}),
        async_config_entry_first_refresh=AsyncMock(),
        async_start_realtime=lambda: None,
        async_stop_realtime=AsyncMock(),
        async_request_refresh=AsyncMock(),
        async_trigger_notification=AsyncMock(),
        async_acknowledge_notification=AsyncMock(),
    )
    with (
        patch.object(provider, "Code27Client", return_value=client),
        patch.object(provider, "Code27Coordinator", return_value=coordinator),
        patch.object(provider, "async_setup_bridge"),
    ):
        assert await integration.async_setup_entry(hass, entry)
    assert hass.data["hase"][entry.entry_id] is coordinator
    assert len(hass.services.handlers) == 15
    assert {domain for domain, _ in hass.services.handlers} == {"hase"}
    services.async_register_services(
        hass
    )  # another device setup must not register twice
    payloads = {
        "create_alarm": {"hour": 7, "minute": 30},
        "update_alarm": {"alarm_id": "a", "hour": 8, "minute": 0},
        "delete_alarm": {"alarm_id": "a"},
        "send_chat_message": {"message": "hello"},
        "notify": {"message": "notice"},
        "notify_clear": {},
        "speak": {"text": "hello"},
    }
    with patch.object(services, "async_speak_text", new_callable=AsyncMock) as speak:
        for name, payload in payloads.items():
            handler, schema = hass.services.handlers["hase", name]
            await handler(
                N(data=schema({"config_entry_id": entry.entry_id, **payload}))
            )
        speak.assert_awaited_once()
    client.create_alarm.assert_awaited_once()
    client.update_alarm.assert_awaited_once()
    client.delete_alarm.assert_awaited_once()
    client.send_chat_text.assert_awaited_once_with("hello", 1, None)
    coordinator.async_trigger_notification.assert_awaited_once_with(
        "notice", (255, 0, 0)
    )
    assert await integration.async_unload_entry(hass, entry)
    assert hass.services.handlers == {}
    assert hass.data["hase"] == {}
    standalone = N(
        entry_id="agent-new",
        data={"entry_kind": "spellbooker"},
        async_on_unload=lambda f: None,
        add_update_listener=lambda f: lambda: None,
    )
    with patch.object(
        provider,
        "Code27Client",
        side_effect=AssertionError("standalone touched device"),
    ):
        assert await integration.async_setup_entry(hass, standalone)
    assert hass.services.handlers == {}
    menu = await flow.HaseConfigFlow().async_step_user()
    assert menu["menu_options"] == ["code27_device", "spellbooker"]

    # Follow the backend's real function-local coordinator import and new event.
    listeners = {}

    def listen(event_type, callback):
        assert event_type == "hase_assistant_message"
        listeners[event_type] = callback
        return lambda: listeners.pop(event_type)

    async def send_reply(text, slot):
        listeners["hase_assistant_message"](
            N(
                data={
                    "config_entry_id": entry.entry_id,
                    "slot": slot,
                    "text": "reply",
                }
            )
        )

    hass.bus = N(async_listen=listen)
    coordinator.async_send_chat_reply = AsyncMock(side_effect=send_reply)
    backend_module = loaded["custom_components.hase.providers.code27.backend"]
    backend = backend_module.Code27ConversationBackend(hass, coordinator)
    second = backend_module.Code27ConversationBackend(hass, coordinator)
    assert backend._lock is second._lock
    scope = loaded["custom_components.hase.core.session"].backend_scope(
        entry.entry_id, "1"
    )
    result = await backend.converse("hello", "en", scope)
    assert result.response_text == "reply" and result.proposals == ()
    assert listeners == {}
    mismatch = loaded["custom_components.hase.core.session"].backend_scope(
        entry.entry_id, "2"
    )
    assert (await backend.converse("hello", "en", mismatch)).status == "unavailable"
    coordinator.async_send_chat_reply.assert_awaited_once()

    # Load both real YAML formats from their relocated __file__ paths.
    async def executor(fn, *args):
        return fn(*args)

    with tempfile.TemporaryDirectory() as config_dir:
        hass.config.path = lambda *parts: str(Path(config_dir).joinpath(*parts))
        hass.async_add_executor_job = executor
        engine = loaded["custom_components.hase.spellbook_loader"]
        assert {
            key for key, _ in await engine.async_discover_spellbooks(hass)
        } == {"demo", "middle_earth", "potter_verse"}


        overrides = Path(config_dir) / "hase_spellbooks"
        overrides.mkdir()
        contents = (COMPONENT / "spellbooks/demo.yaml").read_text(encoding="utf-8")
        book = yaml.safe_load(contents)
        book["name"] = "My override"
        (overrides / "demo.yaml").write_text(yaml.safe_dump(book), encoding="utf-8")
        assert (
            await engine.async_get_spellbook(hass, "demo")
        ).name == "My override"
    print(
        f"Imported {len(loaded)} real HASE modules; provider lifecycle and all seven services passed."
    )


asyncio.run(verify())
