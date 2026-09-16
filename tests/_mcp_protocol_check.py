"""HASE MCP architecture, Phase 4: real network protocol check.

Runs the ACTUAL providers.mcp.transport code (real `mcp`/`uvicorn`
packages, real Streamable HTTP over a real TCP socket) against a
hand-built-but-functionally-real Home Assistant surface, driven by the
OFFICIAL mcp Python SDK client - not direct Python calls, not mocked
protocol calls.

Importing custom_components.hase.providers.mcp.transport pulls in the
WHOLE custom_components.hase package (its own __init__.py imports every
provider). Rather than hand-fake each homeassistant.* symbol that could
reach, this reuses the exact generic auto-stub-generation technique
tests/_hase_import_smoke.py already proves works for that: scan every .py
file under custom_components/hase for `from homeassistant.x import Y` and
synthesize a plausible stand-in for Y, then override the handful of
symbols that need to be FUNCTIONALLY real for this check (the area/
device/entity registries and Assist-exposure filtering) with working
fakes instead of inert stand-ins.

Runs as its own subprocess (like _hase_import_smoke.py) so these
sys.modules fakes never leak into the rest of the pytest session.
tests/test_mcp_protocol.py wraps this script and asserts a clean exit.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMPONENT = ROOT / "custom_components" / "hase"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"OK   {label}")
    else:
        FAILURES.append(f"{label} {detail}".strip())
        print(f"FAIL {label} {detail}")


N = types.SimpleNamespace


# ---------------------------------------------------------------------------
# Generic homeassistant.* stub generation - copied technique from
# tests/_hase_import_smoke.py (see its own module docstring), so importing
# the real custom_components.hase package tree never needs a real Home
# Assistant install.
# ---------------------------------------------------------------------------


class SurfaceMeta(type):
    def __getattr__(cls, name):
        value = 1 << len(cls.__dict__) if cls.__name__.endswith("Feature") else name.lower()
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


# Module-level (not function-local) so run_checks() below can assert
# directly on repair-issue create/clear behaviour without reaching back
# into homeassistant.helpers.issue_registry's fake internals.
ISSUE_STORE: dict = {}


def _module(name):
    if name in sys.modules:
        return sys.modules[name]
    result = types.ModuleType(name)
    result.__path__ = []
    sys.modules[name] = result
    if "." in name:
        parent, child = name.rsplit(".", 1)
        setattr(_module(parent), child, result)
    return result


def install_generic_ha_stubs() -> None:
    imports = [
        n
        for path in COMPONENT.rglob("*.py")
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("homeassistant")
    ]
    for node in imports:
        _module(node.module)
    for node in imports:
        target = _module(node.module)
        for alias in node.names:
            if hasattr(target, alias.name):
                continue
            if alias.name.isupper():
                value = alias.name.removeprefix("CONF_").removeprefix("ATTR_").lower()
            elif alias.name[0].isupper():
                base = Exception if alias.name.endswith(("Error", "Failed")) else Surface
                value = type(alias.name, (base,), {})
            elif alias.name in {
                "selector", "intent", "config_validation", "conversation",
                "area_registry", "device_registry", "entity_registry",
                "issue_registry", "websocket_api", "panel_custom",
            }:
                value = _module(node.module + "." + alias.name)
            else:
                value = lambda *a, **k: None
            setattr(target, alias.name, value)

    _module("homeassistant.components.websocket_api").websocket_command = lambda schema: (lambda fn: fn)
    _module("homeassistant.components.websocket_api").require_admin = lambda fn: fn
    _module("homeassistant.components.websocket_api").async_response = lambda fn: fn
    _module("homeassistant.core").callback = lambda fn: fn
    _module("homeassistant.helpers.config_validation").ensure_list = (
        lambda value: value if isinstance(value, list) else [value]
    )
    selector = _module("homeassistant.helpers.selector")
    for path in COMPONENT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "selector":
                setattr(selector, node.attr, type(node.attr, (Surface,), {}))

    # Working (not inert) issue_registry fake - providers.mcp.repairs uses
    # `from homeassistant.helpers import issue_registry as ir` then plain
    # attribute access (ir.async_get/async_create_issue/async_delete_issue),
    # which the generic ImportFrom-scanning loop above never sees to stub
    # individually. Backed by the module-level ISSUE_STORE so run_checks()
    # can assert directly on the "invalid area creates a repair issue" /
    # "restored area clears it" behaviour.
    class _FakeIssueEntry:
        def __init__(self, domain, issue_id):
            self.domain = domain
            self.issue_id = issue_id

    class _FakeIssueRegistry:
        issues = ISSUE_STORE

    def _fake_async_create_issue(hass, domain, issue_id, **kwargs):
        ISSUE_STORE[(domain, issue_id)] = _FakeIssueEntry(domain, issue_id)

    def _fake_async_delete_issue(hass, domain, issue_id):
        ISSUE_STORE.pop((domain, issue_id), None)

    issue_registry = _module("homeassistant.helpers.issue_registry")
    issue_registry.async_get = lambda hass: _FakeIssueRegistry()
    issue_registry.async_create_issue = _fake_async_create_issue
    issue_registry.async_delete_issue = _fake_async_delete_issue
    issue_registry.IssueSeverity = N(WARNING="warning", ERROR="error", CRITICAL="critical")


# ---------------------------------------------------------------------------
# The handful of symbols that must be FUNCTIONALLY real for this check -
# installed AFTER the generic pass above, overriding its inert stand-ins.
# ---------------------------------------------------------------------------


class FakeArea:
    def __init__(self, id, name, aliases=()):
        self.id = id
        self.name = name
        self.aliases = list(aliases)


class MutableAreaRegistry:
    """Mutable on purpose - simulates an admin deleting an area from the
    real HA area registry WHILE Bridges are already configured against it,
    without reloading anything."""

    def __init__(self, areas):
        self.areas = {a.id: a for a in areas}

    def remove(self, area_id):
        self.areas.pop(area_id, None)


class FakeEntityEntry:
    def __init__(self, entity_id, area_id, device_id=None, disabled_by=None, hidden_by=None, name=None):
        self.entity_id = entity_id
        self.area_id = area_id
        self.device_id = device_id
        self.disabled_by = disabled_by
        self.hidden_by = hidden_by
        self.name = name
        self.original_name = name


class FakeEntityRegistry:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}

    def async_get(self, entity_id):
        return self.entities.get(entity_id)


class FakeDeviceRegistry:
    def __init__(self):
        self.devices: dict = {}


class FakeStates:
    def __init__(self, states):
        self._states = states

    def get(self, entity_id):
        return self._states.get(entity_id)


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, *, target=None, blocking=True, context=None):
        self.calls.append((domain, service, dict(data), target))


NOT_EXPOSED_ENTITIES = {"light.secret"}

# Every command.assist_redirect=True Spell's rendered text that was
# actually forwarded through the fake conversation agent below - lets
# run_checks() assert the real forward happened (not just that some
# success/denial was reported) without needing a real Home Assistant
# conversation agent.
ASSIST_FORWARD_CALLS: list[str] = []
# Prefix that makes the fake agent answer with an ERROR response_type
# instead of a normal reply - used to prove core.mcp_execution reports a
# FAILED (never SUCCESS) outcome for a real Assist error reply.
ASSIST_ERROR_TRIGGER = "__assist_error__"


def install_working_registry_fakes(area_registry: MutableAreaRegistry, entity_registry: FakeEntityRegistry) -> None:
    _module("homeassistant.helpers.area_registry").async_get = lambda hass: area_registry
    _module("homeassistant.helpers.device_registry").async_get = lambda hass: FakeDeviceRegistry()
    _module("homeassistant.helpers.entity_registry").async_get = lambda hass: entity_registry
    _module("homeassistant.components.homeassistant.exposed_entities").async_should_expose = (
        lambda hass, category, entity_id: entity_id not in NOT_EXPOSED_ENTITIES
    )

    # Working (not inert) conversation.async_converse fake - the real
    # HaAssistForwarder (custom_components/hase/spellbooker_ha.py) calls
    # this for real, over the real network round-trip, whenever a
    # tools/call resolves to an assist_redirect=True Spell. Never touches
    # hass.services - Home Assistant's own conversation agent deciding
    # what to do with the text is out of scope for this check (and for
    # HASE itself, since that decision genuinely belongs to whichever real
    # agent an operator configures); this check only proves HASE forwards
    # the right text through the right, permission-gated path and reports
    # the real outcome back, never fabricating one.
    class _FakeIntentResponse:
        def __init__(self, response_type, speech):
            self.response_type = response_type
            self.speech = {"plain": {"speech": speech}} if speech else {}

    class _FakeConversationResult:
        def __init__(self, response):
            self.response = response
            self.continue_conversation = False

    intent_mod = _module("homeassistant.helpers.intent")
    intent_mod.IntentResponseType = N(ERROR="error", ACTION_DONE="action_done", QUERY_ANSWER="query_answer")

    async def fake_async_converse(hass, *, text, conversation_id, context, agent_id, language):
        ASSIST_FORWARD_CALLS.append(text)
        if ASSIST_ERROR_TRIGGER in text:
            return _FakeConversationResult(_FakeIntentResponse(intent_mod.IntentResponseType.ERROR, "Das habe ich nicht verstanden."))
        return _FakeConversationResult(
            _FakeIntentResponse(intent_mod.IntentResponseType.ACTION_DONE, f"Assist erledigt: {text}")
        )

    _module("homeassistant.components.conversation").async_converse = fake_async_converse


AREAS = [
    FakeArea("jans_zimmer", "Jans Zimmer"),
    FakeArea("wohnzimmer", "Wohnzimmer"),
    FakeArea("kinderzimmer", "Kinderzimmer Jan"),
]
ENTITIES = [
    FakeEntityEntry("light.jan", "jans_zimmer"),
    FakeEntityEntry("light.wohnzimmer", "wohnzimmer"),
    FakeEntityEntry("light.secret", "wohnzimmer"),  # NOT exposed - must never be reachable via MCP
    FakeEntityEntry("sensor.wohnzimmer_temp", "wohnzimmer"),
]
STATES = {
    "light.jan": N(state="off", attributes={}),
    "light.wohnzimmer": N(state="on", attributes={}),
    "light.secret": N(state="on", attributes={}),
    "sensor.wohnzimmer_temp": N(state="21.5", attributes={"unit_of_measurement": "°C", "device_class": "temperature"}),
}


# ---------------------------------------------------------------------------
# Spellbook (real core.spellbook_engine, in-memory - Action, Query, Combined)
# ---------------------------------------------------------------------------

TEST_BOOK_ID = "mcp_protocol_test_book"
LUMOS = {
    "id": "lumos", "trigger": "lumos {area}",
    "operation": "light.set_power",
    "target": {"kind": "area_name", "capture": "area"},
    "arguments": {"on": True},
    "catchphrase": "Lumos {area}.",
}
AVG_TEMP = {
    "id": "avg_temp", "trigger": "average temperature in {area}",
    "sources": [
        {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
         "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
    ],
    "output": "{t} degrees",
}
COMBINED = {
    "id": "evening_routine", "trigger": "evening routine {area}",
    "steps": [
        {"alias": "light", "operation": "light.turn_on", "target": {"kind": "area_name", "capture": "area"}},
        {"alias": "temp", "sources": [
            {"kind": "avg", "target": {"kind": "area_name", "capture": "area"},
             "domain_filter": "sensor", "device_class": "temperature", "variable": "t"},
        ]},
    ],
    "output": "{light.target_count} lights, {temp.t} degrees",
}
SECRET_ACCESS = {
    # Deliberately targets the entity that is never exposed to Assist -
    # proves MCP cannot reach it either, and needs no argument at all
    # (fixed entity_id target - a genuine user capture is not the point
    # of this particular check).
    "id": "reveal_secret", "trigger": "reveal secret",
    "operation": "light.set_power",
    "target": {"kind": "entity_id", "value": "light.secret"},
    "arguments": {"on": True},
}
LORE_ANSWER = {
    # A genuine text-only Spell with assist_redirect explicitly False -
    # the confirmed-safe-to-speak-directly case. Real middle_earth.yaml
    # content currently has NONE of these yet (every one of its 32
    # text-only Spells still carries the pending-review default,
    # assist_redirect=True - see models.DEFAULT_ASSIST_REDIRECT), so the
    # "a real text-only Spell is actually called and its output checked"
    # proof needs its own explicit fixture here rather than real content.
    "id": "lore_answer", "trigger": "tell me about {area}",
    "output": "{area} is a fine place to visit.",
    "assist_redirect": False,
}
ASSIST_REDIRECT_SPELL = {
    # Mirrors real middle_earth.yaml's own still-pending-review shape
    # (command-phrased output, no explicit assist_redirect - defaults to
    # True). Proves MCP actually forwards it to Home Assistant's own
    # configured conversation agent (the fake install_working_registry_
    # fakes wires up) and returns Assist's real reply - never the raw
    # command phrase, never a fabricated success.
    "id": "sunrise", "trigger": "sunrise over {area}",
    "output": "Set light {area} to 100 percent.",
}


def build_test_book():
    import importlib

    engine = importlib.import_module("custom_components.hase.core.spellbook_engine")
    raw = {
        "id": TEST_BOOK_ID, "name": "Protocol Test",
        "commands": [LUMOS, AVG_TEMP, COMBINED, SECRET_ACCESS, LORE_ANSWER, ASSIST_REDIRECT_SPELL],
    }
    book = engine.load_spellbook_from_dict(TEST_BOOK_ID, raw)
    assert book is not None, "test fixture spellbook failed to load"
    return book


def load_real_middle_earth_book():
    """The REAL, bundled custom_components/hase/spellbooks/middle_earth.yaml
    - 42 commands, only 10 with a structured operation/sources/steps, the
    other 32 pure text/lore (output only). Loaded exactly like production
    does (load_spellbook_from_path, no synthetic fixture), so the "all 42
    appear in tools/list" check below proves the real book against the
    real projection code, not a hand-picked stand-in."""
    import importlib
    from pathlib import Path

    engine = importlib.import_module("custom_components.hase.core.spellbook_engine")
    path = Path(__file__).resolve().parents[1] / "custom_components" / "hase" / "spellbooks" / "middle_earth.yaml"
    book = engine.load_spellbook_from_path(path)
    assert book is not None, "real middle_earth.yaml failed to load"
    return book


MIDDLE_EARTH_BOOK_ID = "middle_earth"


# ---------------------------------------------------------------------------
# Fake config entry / subentries (Bridges)
# ---------------------------------------------------------------------------


class FakeSubentry:
    def __init__(self, subentry_id, data, title=""):
        self.subentry_id = subentry_id
        self.subentry_type = "mcp_bridge"
        self.data = data
        self.title = title


class FakeConfigEntry:
    def __init__(self, entry_id, subentries):
        self.entry_id = entry_id
        self.subentries = subentries


def bridge_data(area_id, allowed_areas, unrestricted, issued, *, book_id=TEST_BOOK_ID):
    return {
        "spellbooker_book": book_id,
        "mcp_bridge_area_id": area_id or "",
        "spellbooker_allowed_areas": allowed_areas,
        "spellbooker_unrestricted": unrestricted,
        "spellbooker_queries_allowed_areas": allowed_areas,
        "spellbooker_queries_unrestricted": unrestricted,
        "mcp_bridge_language": "",
        "mcp_bridge_credential_verifier": issued.verifier,
        "mcp_bridge_credential_suffix": issued.suffix,
    }


async def main() -> None:
    import importlib

    install_generic_ha_stubs()
    area_registry = MutableAreaRegistry(AREAS)
    entity_registry = FakeEntityRegistry(ENTITIES)
    install_working_registry_fakes(area_registry, entity_registry)

    services = FakeServices()
    hass = N(
        data={},
        config=N(language="de"),
        states=FakeStates(STATES),
        services=services,
        config_entries=None,  # set below, once ENTRY exists
    )

    credentials = importlib.import_module("custom_components.hase.providers.mcp.credentials")
    secret = b"mcp-protocol-check-fixed-secret-000000!"

    jan = credentials.issue_credential(secret)
    wz = credentials.issue_credential(secret)
    master = credentials.issue_credential(secret)
    # The "known, unrestricted test Bridge configuration" the 42-tool
    # export check runs under - same unrestricted shape as "master", just
    # pointed at the real middle_earth book instead of the synthetic one.
    middle_earth = credentials.issue_credential(secret)

    entry = FakeConfigEntry(
        entry_id="mcp-server-1",
        subentries={
            "bridge-jan": FakeSubentry(
                "bridge-jan", bridge_data("jans_zimmer", ["jans_zimmer"], False, jan), title="Bridge Jan",
            ),
            "bridge-wz": FakeSubentry(
                "bridge-wz", bridge_data("wohnzimmer", ["wohnzimmer", "kinderzimmer"], False, wz),
                title="Bridge Wohnzimmer",
            ),
            "bridge-master": FakeSubentry(
                "bridge-master", bridge_data(None, [], True, master), title="Bridge Master",
            ),
            "bridge-middle-earth": FakeSubentry(
                "bridge-middle-earth",
                bridge_data(None, [], True, middle_earth, book_id=MIDDLE_EARTH_BOOK_ID),
                title="Bridge Middle-earth",
            ),
        },
    )
    hass.config_entries = N(async_get_entry=lambda entry_id: entry if entry_id == entry.entry_id else None)

    provider_pkg = importlib.import_module("custom_components.hase.providers.mcp")

    class FixedSecretStore:
        def __init__(self, hass):
            pass

        async def async_get_secret(self):
            return secret

    with patch.object(provider_pkg, "McpSecretStore", FixedSecretStore):
        await provider_pkg.async_rebuild_credential_index(hass, entry)

    book = build_test_book()
    middle_earth_book = load_real_middle_earth_book()
    transport = importlib.import_module("custom_components.hase.providers.mcp.transport")

    async def fake_get_spellbook(hass, book_id):
        if book_id == MIDDLE_EARTH_BOOK_ID:
            return middle_earth_book
        return book

    with patch.object(transport, "async_get_spellbook", side_effect=fake_get_spellbook):
        await run_checks(
            hass, transport, area_registry, provider_pkg, credentials, secret, entry,
            jan, wz, master, middle_earth, middle_earth_book,
        )


async def run_checks(
    hass, transport, area_registry, provider_pkg, credentials, secret, entry,
    jan, wz, master, middle_earth, middle_earth_book,
) -> None:
    import uvicorn
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import (
        create_mcp_http_client,
        streamable_http_client,
    )

    _server, app = transport.build_streamable_http_app(hass, streamable_http_path="/mcp")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uv_server = uvicorn.Server(config)
    serve_task = asyncio.create_task(uv_server.serve())
    for _ in range(200):
        if getattr(uv_server, "started", False):
            break
        await asyncio.sleep(0.02)
    else:
        check("server startup", False, "uvicorn did not report started")
        return
    port = uv_server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    check("server startup", True)

    async def call(token, tool_name=None, args=None, *, expect_ok=True):
        client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            async with streamable_http_client(url, http_client=client) as streams:
                read_stream, write_stream, *_ = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = None
                    if tool_name is not None:
                        result = await session.call_tool(tool_name, args or {})
                    return tools.tools, result
        except Exception as exc:
            if expect_ok:
                raise
            return None, exc
        finally:
            await client.aclose()

    # --- invalid/missing token rejected ---
    _, exc = await call("not-a-real-token", expect_ok=False)
    check("invalid token rejected", exc is not None)
    _, exc = await call("", expect_ok=False)
    check("missing token rejected", exc is not None)

    # --- per-bridge identity, same tool name, different projections, CASE A/B/C ---
    jan_tools, _ = await call(jan.token)
    wz_tools, _ = await call(wz.token)
    master_tools, _ = await call(master.token)
    jan_lumos = next((t for t in jan_tools if t.name == "lumos"), None)
    wz_lumos = next((t for t in wz_tools if t.name == "lumos"), None)
    master_lumos = next((t for t in master_tools if t.name == "lumos"), None)
    check("same tool name 'lumos' exposed by every bridge", all([jan_lumos, wz_lumos, master_lumos]))
    check("CASE A (jan, room-bound): no area argument", jan_lumos is not None and jan_lumos.input_schema.get("properties") == {})
    check("CASE B (master, no area): area required", master_lumos is not None and "area" in master_lumos.input_schema.get("required", []))
    check(
        "CASE C (wohnzimmer, own area + wider scope): area optional",
        wz_lumos is not None
        and "area" in wz_lumos.input_schema.get("properties", {})
        and "area" not in wz_lumos.input_schema.get("required", []),
    )
    check(
        "different bridges project different descriptions for the same tool name",
        jan_lumos is not None and master_lumos is not None and jan_lumos.description != master_lumos.description,
    )

    # --- concurrent requests from different bridges stay isolated ---
    (concurrent_jan_tools, _), (concurrent_master_tools, _) = await asyncio.gather(call(jan.token), call(master.token))
    concurrent_jan_lumos = next((t for t in concurrent_jan_tools if t.name == "lumos"), None)
    concurrent_master_lumos = next((t for t in concurrent_master_tools if t.name == "lumos"), None)
    check(
        "concurrent requests from different bridges never cross-contaminate",
        concurrent_jan_lumos is not None and concurrent_master_lumos is not None
        and concurrent_jan_lumos.input_schema != concurrent_master_lumos.input_schema,
    )

    # --- Action execution ---
    _, result = await call(jan.token, "lumos", {})
    check(
        "action execution (CASE A implicit area) renders HASE's own catchphrase as TextContent",
        result is not None and not result.is_error and result.content[0].text == "Lumos Jans Zimmer.",
    )
    check("action dispatched a real service call", any(c[0] == "light" and c[1] == "turn_on" for c in hass.services.calls))

    # --- Query execution ---
    _, result = await call(wz.token, "avg_temp", {})
    check(
        "query execution renders HASE's own response template as TextContent",
        result is not None and not result.is_error and result.content[0].text == "21.5 degrees",
    )

    # --- Combined execution ---
    _, result = await call(wz.token, "evening_routine", {})
    check(
        "combined spell execution renders both steps' variables as TextContent",
        result is not None and not result.is_error and "lights" in result.content[0].text and "degrees" in result.content[0].text,
    )

    # --- DENIED: wz's action scope is Wohnzimmer+Kinderzimmer only ---
    _, result = await call(wz.token, "lumos", {"area": "Jans Zimmer"})
    check(
        "action AreaPolicy DENIED for an explicit out-of-scope area, mapped cleanly with HASE's own reason",
        result is not None and result.is_error and "DENIED" in result.content[0].text and "area_not_permitted" in result.content[0].text,
    )

    # --- Assist exposure cannot be bypassed via MCP ---
    _, result = await call(jan.token, "reveal_secret", {})
    check("Assist exposure cannot be bypassed via MCP (non-exposed entity)", result is not None and result.is_error)

    # --- credential regeneration immediately rejects old token, new one works immediately ---
    new_jan = credentials.issue_credential(secret)
    entry.subentries["bridge-jan"].data["mcp_bridge_credential_verifier"] = new_jan.verifier
    entry.subentries["bridge-jan"].data["mcp_bridge_credential_suffix"] = new_jan.suffix
    await provider_pkg.async_rebuild_credential_index(hass, entry)
    _, exc = await call(jan.token, expect_ok=False)
    check("old credential rejected immediately after regeneration", exc is not None)
    new_jan_tools, _ = await call(new_jan.token)
    check("new credential works immediately after regeneration", any(t.name == "lumos" for t in new_jan_tools))

    # --- Bridge deletion immediately rejects its token ---
    del entry.subentries["bridge-wz"]
    await provider_pkg.async_rebuild_credential_index(hass, entry)
    _, exc = await call(wz.token, expect_ok=False)
    check("deleted bridge's credential rejected immediately", exc is not None)

    # --- Spellbook change reflected in subsequent tools/list, no reload ---
    entry.subentries["bridge-master"].data["spellbooker_book"] = ""
    master_tools_after, _ = await call(master.token)
    check("spellbook change (cleared) reflected in the very next tools/list", master_tools_after == [])
    entry.subentries["bridge-master"].data["spellbooker_book"] = TEST_BOOK_ID  # restore

    # --- invalid configured area never silently becomes MASTER ---
    area_registry.remove("jans_zimmer")
    tools_after_area_removed, _ = await call(new_jan.token)
    check(
        "bridge with a deleted configured area exposes no tools (never silently MASTER)",
        tools_after_area_removed == [],
    )
    _, call_result = await call(new_jan.token, "lumos", {})
    check(
        "tools/call also fails safe for a bridge with a deleted area, without a reload",
        call_result is not None and call_result.is_error and "bridge_misconfigured" in call_result.content[0].text,
    )
    check(
        "a repair issue is raised for the bridge whose configured area no longer exists",
        any(entry.issue_id.endswith("bridge-jan") for entry in ISSUE_STORE.values()),
    )

    # --- restoring the area clears the repair issue again (not raised-once-forever) ---
    area_registry.areas["jans_zimmer"] = FakeArea("jans_zimmer", "Jans Zimmer")
    await call(new_jan.token)
    check(
        "the repair issue clears once the bridge's configured area exists again",
        not any(entry.issue_id.endswith("bridge-jan") for entry in ISSUE_STORE.values()),
    )

    # --- real middle_earth.yaml under the known, unrestricted test Bridge
    # configuration: ALL 42 spells now appear in a real tools/list - the
    # 10 structured (operation/sources/steps) ones AND the 32 text-only
    # ones (every one of them still carrying assist_redirect's
    # pending-review default, True - see models.DEFAULT_ASSIST_REDIRECT),
    # which now genuinely forward to Home Assistant's own configured
    # conversation agent for real execution instead of being skipped. ---
    import importlib

    mcp_projection = importlib.import_module("custom_components.hase.core.mcp_projection")
    models_mod = importlib.import_module("custom_components.hase.core.models")
    unrestricted = models_mod.AreaPolicy(allowed_areas=None, unrestricted=True)
    real_bridge_context = mcp_projection.BridgeContext(
        area=None, policy=models_mod.ScopedAreaPolicy(actions=unrestricted, queries=unrestricted), language="de",
    )
    direct_projections, direct_skips = mcp_projection.project_spellbook(middle_earth_book, real_bridge_context)
    check(
        "real middle_earth.yaml: all 42 spells project as MCP tools, none skipped",
        len(direct_projections) == len(middle_earth_book.commands) and direct_skips == (),
        f"projected={len(direct_projections)} (want {len(middle_earth_book.commands)}) "
        f"skips={[s.reason for s in direct_skips]}",
    )

    me_tools, _ = await call(middle_earth.token)
    check(
        "real middle_earth.yaml via a real tools/list: all 42 spells are present",
        len(me_tools) == len(middle_earth_book.commands)
        and {t.name for t in me_tools} == {c.spell_id for c in middle_earth_book.commands},
        f"got {len(me_tools)} (want {len(middle_earth_book.commands)})",
    )

    # --- an assist_redirect=True real spell (middle_earth_07) actually
    # executes: the unrestricted "middle_earth" Bridge genuinely forwards
    # its rendered command text to the fake Assist agent (installed by
    # install_working_registry_fakes, reached through the real
    # HaAssistForwarder over the real network round-trip) and returns
    # Assist's real reply - never the raw command phrase, never a
    # fabricated success. ---
    ASSIST_FORWARD_CALLS.clear()
    _, redirect_call = await call(middle_earth.token, "middle_earth_07", {"area": "Gondor"})
    check(
        "an assist_redirect real spell actually forwards its rendered command text to Assist",
        ASSIST_FORWARD_CALLS == ["Licht Gondor auf 100 Prozent stellen"],
        f"forwarded={ASSIST_FORWARD_CALLS}",
    )
    check(
        "and returns Assist's real reply as the tool's response, never the raw command phrase",
        redirect_call is not None and not redirect_call.is_error
        # middle_earth_07 declares its own catchphrase ("Der neue Tag
        # beginnt.") - the fake agent's ACTION_DONE reply confirms
        # completion, so core.mcp_execution's own substitution rule
        # (mirrors providers.code27.bridge._forward_to_assist exactly)
        # returns the Spell's own catchphrase, never Assist's generic
        # reply nor the raw command phrase.
        and redirect_call.content[0].text == "Der neue Tag beginnt.",
    )

    # --- a representative structured action spell from the real book
    # still executes through the real dispatch path (mocked HA service
    # call, never a real device action) - proves action spells keep
    # working unchanged alongside assist_redirect spells, not just that
    # the new execution path itself works. ---
    _, action_result = await call(middle_earth.token, "light_all_middle_earth", {})
    check(
        "representative structured action spell still executes for real (mocked HA call)",
        action_result is not None and not action_result.is_error,
    )

    # --- a genuine, confirmed-safe (assist_redirect=False) text/lore
    # Spell actually called, real output checked - proves the fixed-text
    # mechanism itself still works end to end for a Spell that has
    # explicitly opted OUT of Assist-forwarding, using the synthetic test
    # book (real middle_earth.yaml currently has none confirmed yet). ---
    _, lore_result = await call(master.token, "lore_answer", {"area": "Bruchtal"})
    check(
        "a confirmed text-only spell renders its real output with the supplied placeholder",
        lore_result is not None and not lore_result.is_error
        and lore_result.content[0].text == "Bruchtal is a fine place to visit.",
    )

    # --- an assist_redirect spell also actually executes under the
    # synthetic test book's own unrestricted Bridge (master) - the
    # pending-review default (True) is a real, functioning execution
    # kind, never a permanently-unsupported one. ---
    ASSIST_FORWARD_CALLS.clear()
    _, sunrise_call = await call(master.token, "sunrise", {"area": "Gondor"})
    check(
        "a synthetic pending-review (assist_redirect default) spell also actually executes",
        sunrise_call is not None and not sunrise_call.is_error
        and sunrise_call.content[0].text == "Assist erledigt: Set light Gondor to 100 percent."
        and ASSIST_FORWARD_CALLS == ["Set light Gondor to 100 percent."],
    )

    # --- a restricted Bridge must never be able to reach Assist via MCP
    # either - its own AreaPolicy is not unrestricted, so its
    # assist_redirect call is DENIED before Assist is ever asked (proven
    # by ASSIST_FORWARD_CALLS staying untouched across this one call).
    # Uses new_jan (still valid and still area-restricted at this point in
    # the script - the original jan/wz credentials above were already
    # regenerated/deleted by earlier checks). ---
    calls_before = len(ASSIST_FORWARD_CALLS)
    _, restricted_redirect_call = await call(new_jan.token, "sunrise", {"area": "Jans Zimmer"})
    check(
        "a restricted Bridge's assist_redirect call is denied, never bypassing its own AreaPolicy via Assist",
        restricted_redirect_call is not None and restricted_redirect_call.is_error
        and "assist_redirect_requires_unrestricted_policy" in restricted_redirect_call.content[0].text
        and len(ASSIST_FORWARD_CALLS) == calls_before,
    )

    # --- an ERROR reply from Assist is reported as FAILED, never
    # fabricated as a success - proves the real HaAssistForwarder's own
    # response_type check (homeassistant.helpers.intent.
    # IntentResponseType.ERROR) actually works end-to-end over the real
    # network round-trip, not just against a hand-built outcome. ---
    _, error_call = await call(master.token, "sunrise", {"area": ASSIST_ERROR_TRIGGER})
    check(
        "an ERROR reply from Assist is reported as FAILED, never fabricated as a success",
        error_call is not None and error_call.is_error and "FAILED" in error_call.content[0].text,
    )

    # --- GET (standalone SSE stream) does not crash the server; clean disconnect ---
    # A plain buffered GET would hang forever reading an SSE stream's body
    # (it never ends on its own) - stream instead, read only the response
    # headers/status, then close early without consuming the body. That
    # early close IS the "clean disconnect" being checked: the server must
    # tolerate it (proven by the shutdown check right after still
    # succeeding) rather than hang or crash.
    import httpx2

    try:
        async with httpx2.AsyncClient() as raw_client, raw_client.stream(
            "GET", url, headers={"Authorization": f"Bearer {master.token}"}, timeout=3.0,
        ) as resp:
            check("GET (standalone SSE stream) opens without crashing the server", resp.status_code in (200, 405, 406))
    except Exception as exc:  # noqa: BLE001 - a clean rejection is an acceptable outcome, a hang/crash is not
        check("GET (standalone SSE stream) opens without crashing the server", False, str(exc))

    uv_server.should_exit = True
    try:
        await asyncio.wait_for(serve_task, timeout=10)
        check("server shutdown", True)
    except TimeoutError:
        check("server shutdown", False, "did not stop within timeout")


if __name__ == "__main__":
    asyncio.run(main())
    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED:")
        for f in FAILURES:
            print(f" - {f}")
        sys.exit(1)
    print("\nAll protocol checks passed.")
    sys.exit(0)
