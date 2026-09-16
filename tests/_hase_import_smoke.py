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
            "issue_registry",
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

# Working (not just inert) issue_registry fake - providers.mcp.repairs
# calls ir.async_get(hass).issues, ir.async_create_issue/async_delete_issue
# via plain attribute access (never `from homeassistant.helpers.
# issue_registry import X`), so the generic ImportFrom-scanning stub
# generator above never sees these names to stub individually.
_issue_store: dict[tuple[str, str], object] = {}


class _FakeIssueEntry:
    def __init__(self, domain, issue_id):
        self.domain = domain
        self.issue_id = issue_id


class _FakeIssueRegistry:
    issues = _issue_store


def _fake_async_create_issue(hass, domain, issue_id, **kwargs):
    _issue_store[(domain, issue_id)] = _FakeIssueEntry(domain, issue_id)


def _fake_async_delete_issue(hass, domain, issue_id):
    _issue_store.pop((domain, issue_id), None)


issue_registry = module("homeassistant.helpers.issue_registry")
issue_registry.async_get = lambda hass: _FakeIssueRegistry()
issue_registry.async_create_issue = _fake_async_create_issue
issue_registry.async_delete_issue = _fake_async_delete_issue
issue_registry.IssueSeverity = types.SimpleNamespace(WARNING="warning", ERROR="error", CRITICAL="critical")

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
mcp_provider = loaded["custom_components.hase.providers.mcp"]
mcp_credentials = loaded["custom_components.hase.providers.mcp.credentials"]
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
            async_entries=lambda domain: [],
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
    config_flow = flow.HaseConfigFlow()
    config_flow.hass = hass
    menu = await config_flow.async_step_user()
    # No MCP Server entry exists yet in this fresh hass - the menu offers
    # creating one; async_mcp_server_exists() itself is covered directly
    # in tests/test_mcp_config_flow.py.
    assert menu["menu_options"] == ["code27_device", "spellbooker", "mcp_server"]

    # Real providers.mcp.async_setup_mcp_server_entry/async_rebuild_
    # credential_index/async_unload_mcp_server_entry, exercised end to end
    # (Store itself auto-stubbed like everything else homeassistant.* -
    # only its actual persistence is swapped for a fixed in-memory secret,
    # same "patch just the one real I/O boundary" pattern used for
    # Code27Client/Code27Coordinator above).
    #
    # async_start_transport's own first-import of uvicorn/mcp runs via
    # hass.async_add_executor_job (real UAT against testzwerg caught HA's
    # blocking-call detector firing on that cold import otherwise - see
    # providers/mcp/__init__.py's _build_uvicorn_server) - a real,
    # synchronously-executing fake here is what proves that path actually
    # still works, not just that the attribute exists.
    async def mcp_executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = mcp_executor

    class FixedSecretStore:
        SECRET = b"smoke-test-per-installation-secret-000!"

        def __init__(self, hass):
            pass

        async def async_get_secret(self):
            return self.SECRET

    with patch.object(mcp_provider, "McpSecretStore", FixedSecretStore):
        secret = FixedSecretStore.SECRET
        issued_jan = mcp_credentials.issue_credential(secret)
        issued_master = mcp_credentials.issue_credential(secret)
        mcp_entry = N(
            entry_id="mcp-server-1",
            # port 0 - let the OS assign a free ephemeral port, so this
            # real start/stop cycle (proving the actual uvicorn/mcp
            # packages import and wire up cleanly end to end) never
            # collides with anything else running on the host.
            options={"mcp_server_port": 0},
            subentries={
                "bridge-jan": N(
                    subentry_type="mcp_bridge", subentry_id="bridge-jan", title="Bridge JAN",
                    data={"mcp_bridge_credential_verifier": issued_jan.verifier},
                ),
                "bridge-master": N(
                    subentry_type="mcp_bridge", subentry_id="bridge-master", title="Bridge MASTER",
                    data={"mcp_bridge_credential_verifier": issued_master.verifier},
                ),
            },
            async_on_unload=lambda f: None,
            add_update_listener=lambda f: lambda: None,
        )
        assert await mcp_provider.async_setup_mcp_server_entry(hass, mcp_entry)

        # Resource/lifecycle: a real running task and a real bound socket
        # exist right after setup, both are gone from hass.data right after
        # unload, and the exact port is immediately rebindable - not a
        # blanket asyncio.all_tasks() equality (fragile: other tasks the
        # test process itself schedules would make that flaky), but
        # targeted checks against the one task/server this integration
        # itself owns.
        mcp_bucket = hass.data["hase"]["mcp"]
        uv_server = mcp_bucket["mcp_uvicorn_server"]
        uv_task = mcp_bucket["mcp_uvicorn_task"]
        for _ in range(200):
            if getattr(uv_server, "started", False):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("uvicorn did not report started")
        assert not uv_task.done(), "transport task exited before unload"
        bound_port = uv_server.servers[0].sockets[0].getsockname()[1]

        index = hass.data["hase"]["mcp"]["mcp_credential_index"]
        jan_identity = index.resolve(secret, issued_jan.token)
        assert jan_identity is not None and jan_identity.subentry_id == "bridge-jan"
        assert index.resolve(secret, issued_master.token).subentry_id == "bridge-master"
        # No client-supplied bridge_id path exists at all - resolve() takes
        # only the token.
        assert "bridge_id" not in mcp_credentials.CredentialIndex.resolve.__code__.co_varnames

        # Simulate Bridge "jan" being deleted: a fresh rebuild (exactly
        # what the entry's own update listener triggers via reload) with
        # only "master" left synchronously drops jan's verifier.
        mcp_entry.subentries = {"bridge-master": mcp_entry.subentries["bridge-master"]}
        await mcp_provider.async_rebuild_credential_index(hass, mcp_entry)
        index = hass.data["hase"]["mcp"]["mcp_credential_index"]
        assert index.resolve(secret, issued_jan.token) is None
        assert index.resolve(secret, issued_master.token) is not None

        assert await mcp_provider.async_unload_mcp_server_entry(hass, mcp_entry)
        assert "mcp_credential_index" not in hass.data["hase"]["mcp"]
        assert "mcp_uvicorn_server" not in hass.data["hase"]["mcp"], (
            "no live server reference may remain in hass.data after unload"
        )
        assert "mcp_uvicorn_task" not in hass.data["hase"]["mcp"], (
            "no live task reference may remain in hass.data after unload"
        )
        assert uv_task.done(), "transport task must have actually finished by the time unload returns"

        # The exact port is immediately rebindable - proves the OS socket
        # was really released, not merely dereferenced.
        rebind_entry = N(
            entry_id="mcp-server-1",
            options={"mcp_server_port": bound_port},
            subentries={},
            async_on_unload=lambda f: None,
            add_update_listener=lambda f: lambda: None,
        )
        assert await mcp_provider.async_setup_mcp_server_entry(hass, rebind_entry)
        rebind_bucket = hass.data["hase"]["mcp"]
        rebind_server = rebind_bucket["mcp_uvicorn_server"]
        for _ in range(200):
            if getattr(rebind_server, "started", False):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("port was not immediately rebindable after unload")
        assert rebind_server.servers[0].sockets[0].getsockname()[1] == bound_port
        assert await mcp_provider.async_unload_mcp_server_entry(hass, rebind_entry)

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
