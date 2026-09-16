"""HASE MCP architecture, Phase 3: MCP Server/Bridge config flow tests.

Loaded via test_bridge_delivery's load_source (AST-stripped of
homeassistant/relative imports) with small fakes standing in for the
ConfigFlow/ConfigSubentryFlow base classes and the selector helpers -
mirrors test_satellite_subentry_flow.py's own harness closely, since
McpBridgeSubentryFlow is deliberately modeled on SatelliteSubentryFlow.
providers.mcp.credentials/const are pure (zero Home Assistant imports), so
this file uses the REAL implementations for those rather than fakes -
genuine crypto behavior under test, not a stand-in for it.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from itertools import pairwise
from pathlib import Path

import test_bridge_delivery as support

N = support.N
constants = support.constants

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"


def _load_pure_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mcp_credentials = _load_pure_module("hase_mcp_credentials_flow", ROOT / "providers/mcp/credentials.py")
mcp_const = _load_pure_module("hase_mcp_const_flow", ROOT / "providers/mcp/const.py")


class FakeSubentryFlowBase:
    """Stands in for homeassistant.config_entries.ConfigSubentryFlow -
    only the handful of methods/attributes McpBridgeSubentryFlow actually
    touches, same convention as test_satellite_subentry_flow.py's own."""

    def __init_subclass__(cls, **kwargs):
        pass

    def __init__(self):
        self.hass = N(config=N(language="de"))
        self.source = "user"
        self._entry = None
        self._subentry = None
        self.update_call = None

    def _get_entry(self):
        return self._entry

    def _get_reconfigure_subentry(self):
        return self._subentry

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_update_and_abort(self, entry, subentry, **kwargs):
        self.update_call = (entry, subentry, kwargs)
        return {"type": "abort", "reason": kwargs.get("reason", "reconfigure_successful")}


class FakeConfigFlowBase:
    """Stands in for homeassistant.config_entries.ConfigFlow/OptionsFlow -
    this file instantiates HaseConfigFlow itself (for the menu/mcp_server
    step), unlike test_satellite_subentry_flow.py."""

    def __init_subclass__(cls, **kwargs):
        pass

    def __init__(self):
        self.hass = None

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


FAKE_SELECTOR = N(
    SelectSelector=lambda cfg: cfg,
    SelectSelectorConfig=lambda **kw: kw,
    SelectOptionDict=lambda **kw: kw,
    SelectSelectorMode=N(DROPDOWN="dropdown"),
    AreaSelector=lambda cfg: cfg,
    AreaSelectorConfig=lambda **kw: kw,
)

VOL = N(
    Schema=lambda value: value, Required=lambda key, **kw: key,
    Optional=lambda key, **kw: key,
)


class FakeSecretStore:
    """Deterministic stand-in for providers.mcp.secret_store.McpSecretStore
    - a fixed, known secret, so a test can independently recompute the
    expected verifier via the REAL providers.mcp.credentials module rather
    than trusting the flow's own output blindly."""

    SECRET = b"fixed-test-secret-for-verifier-checks!!"

    def __init__(self, hass):
        pass

    async def async_get_secret(self) -> bytes:
        return self.SECRET


def _load_flow_module(book_choices=(("potter_verse", "Potterverse"),), book_languages=None):
    async def fake_discover_books(hass):
        return list(book_choices)

    # Mirrors async_get_spellbook(hass, book_id) -> EngineSpellbook | None -
    # only the one attribute (.languages) _relevant_book_languages actually
    # reads. Default: every known book_choices entry has de/en, matching
    # every real bundled spellbook.
    languages_by_book = book_languages or {book_id: ("de", "en") for book_id, _ in book_choices}

    async def fake_get_spellbook(hass, book_id):
        if book_id not in languages_by_book:
            return None
        return N(languages=languages_by_book[book_id])

    return support.load_source(
        "config_flow", **constants,
        vol=VOL, selector=FAKE_SELECTOR, callback=lambda f: f,
        ConfigFlow=FakeConfigFlowBase, OptionsFlow=FakeConfigFlowBase,
        ConfigSubentryFlow=FakeSubentryFlowBase, ConfigSubentry=object, SubentryFlowResult=dict,
        CONF_HOST="host", CONF_PORT="port",
        Code27Client=None, Code27ConnectionError=Exception, Code27Error=Exception,
        async_get_clientsession=lambda h: None,
        async_discover_spellbooks=fake_discover_books,
        async_get_spellbook=fake_get_spellbook,
        McpSecretStore=FakeSecretStore,
        issue_credential=mcp_credentials.issue_credential,
        CONF_MCP_BRIDGE_AREA_ID=mcp_const.CONF_MCP_BRIDGE_AREA_ID,
        CONF_MCP_BRIDGE_LANGUAGE=mcp_const.CONF_MCP_BRIDGE_LANGUAGE,
        CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER=mcp_const.CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER,
        CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX=mcp_const.CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX,
        CONF_MCP_SERVER_PORT=mcp_const.CONF_MCP_SERVER_PORT,
        DEFAULT_MCP_SERVER_PORT=mcp_const.DEFAULT_MCP_SERVER_PORT,
        MCP_SERVER_TITLE=mcp_const.MCP_SERVER_TITLE,
    )


VALID_BRIDGE_INPUT = {
    "name": "Bridge JAN",
    "spellbooker_book": "potter_verse",
    "mcp_bridge_area_id": "jans_zimmer",
    "spellbooker_allowed_areas": ["jans_zimmer"],
    "spellbooker_unrestricted": False,
    "spellbooker_queries_allowed_areas": ["jans_zimmer"],
    "spellbooker_queries_unrestricted": False,
    "mcp_bridge_language": "",
}


class McpServerSingletonTests(unittest.IsolatedAsyncioTestCase):
    """"Only one MCP Server can be configured" - both halves: the menu
    hides the option once one exists, and the step itself refuses to
    create a second even if reached directly."""

    def setUp(self):
        self.module = _load_flow_module()

    async def test_menu_offers_mcp_server_when_none_exists(self):
        flow = self.module.HaseConfigFlow()
        flow.hass = N(config_entries=N(async_entries=lambda domain: []))
        result = await flow.async_step_user()
        self.assertIn("mcp_server", result["menu_options"])

    async def test_menu_hides_mcp_server_once_one_exists(self):
        flow = self.module.HaseConfigFlow()
        existing = N(data={"entry_kind": "mcp_server"})
        flow.hass = N(config_entries=N(async_entries=lambda domain: [existing]))
        result = await flow.async_step_user()
        self.assertNotIn("mcp_server", result["menu_options"])

    async def test_mcp_server_step_creates_the_singleton_entry(self):
        flow = self.module.HaseConfigFlow()
        flow.hass = N(config_entries=N(async_entries=lambda domain: []))
        result = await flow.async_step_mcp_server({"mcp_server_port": 9000})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["entry_kind"], "mcp_server")
        # Infrastructure only - no spellbook/area/scope/persona field
        # anywhere in what gets persisted.
        self.assertEqual(set(result["data"]), {"entry_kind"})
        # The title is fixed - never taken from user_input - since the
        # server is a singleton and "which MCP Server" is never a real
        # question anywhere, including the generic HA "pick a config
        # entry" picker every "Add MCP Bridge" action goes through.
        self.assertEqual(result["title"], "HASE MCP Server")
        # The port asked up front here is the SAME field
        # McpServerOptionsFlow later edits - it lands in .options, not
        # .data, from the very first start.
        self.assertEqual(result["options"], {"mcp_server_port": 9000})

    async def test_mcp_server_step_offers_a_port_field_and_no_name_field(self):
        flow = self.module.HaseConfigFlow()
        flow.hass = N(config_entries=N(async_entries=lambda domain: []))
        result = await flow.async_step_mcp_server(None)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "mcp_server")
        schema = result["data_schema"]
        self.assertIn("mcp_server_port", schema)
        # No "name" field anywhere - the title is fixed, never user input.
        self.assertNotIn("name", schema)

    async def test_mcp_server_step_refuses_a_second_server_even_if_reached_directly(self):
        flow = self.module.HaseConfigFlow()
        existing = N(data={"entry_kind": "mcp_server"})
        flow.hass = N(config_entries=N(async_entries=lambda domain: [existing]))
        result = await flow.async_step_mcp_server(None)
        self.assertEqual(result["type"], "abort")


class McpServerOptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    """The MCP Server entry's own options: exactly the listener port -
    infrastructure/transport, never Spellbook/area/scope."""

    def setUp(self):
        self.module = _load_flow_module()

    async def test_default_port_is_offered_when_unset(self):
        entry = N(options={})
        flow = self.module.McpServerOptionsFlow(entry)
        result = await flow.async_step_init(None)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "mcp_server")

    async def test_submitting_a_port_creates_the_options_entry(self):
        entry = N(options={"mcp_server_port": 8815})
        flow = self.module.McpServerOptionsFlow(entry)
        result = await flow.async_step_init({"mcp_server_port": 9001})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["mcp_server_port"], 9001)


class BridgeCreateStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = _load_flow_module([("potter_verse", "Potterverse")])
        self.flow = self.module.McpBridgeSubentryFlow()

    async def test_valid_input_issues_a_credential_and_reveals_it_once(self):
        result = await self.flow.async_step_user(dict(VALID_BRIDGE_INPUT))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "reveal_credential")
        token = result["description_placeholders"]["token"]
        self.assertTrue(token)

        # Confirming the reveal actually creates the subentry, WITHOUT the
        # plaintext token anywhere in the persisted data.
        finish = await self.flow.async_step_reveal_credential({})
        self.assertEqual(finish["type"], "create_entry")
        data = finish["data"]
        self.assertEqual(data["spellbooker_book"], "potter_verse")
        self.assertEqual(data["mcp_bridge_area_id"], "jans_zimmer")
        self.assertNotIn(token, str(data))
        verifier = data["mcp_bridge_credential_verifier"]
        suffix = data["mcp_bridge_credential_suffix"]
        self.assertTrue(verifier)
        self.assertNotIn(token, verifier)
        self.assertTrue(token.endswith(suffix))

        # Independently recompute the expected verifier via the REAL
        # credentials module - proves the flow used deterministic
        # HMAC verification, not something ad hoc.
        expected_verifier = mcp_credentials.compute_verifier(FakeSecretStore.SECRET, token)
        self.assertEqual(verifier, expected_verifier)

    async def test_area_less_bridge_is_valid(self):
        user_input = {**VALID_BRIDGE_INPUT, "mcp_bridge_area_id": ""}
        await self.flow.async_step_user(user_input)
        finish = await self.flow.async_step_reveal_credential({})
        self.assertEqual(finish["data"]["mcp_bridge_area_id"], "")

    async def test_no_book_choice_is_stored_as_empty_string(self):
        user_input = {**VALID_BRIDGE_INPUT, "spellbooker_book": ""}
        await self.flow.async_step_user(user_input)
        finish = await self.flow.async_step_reveal_credential({})
        self.assertEqual(finish["data"]["spellbooker_book"], "")

    async def test_missing_actions_areas_and_unrestricted_is_rejected(self):
        user_input = {**VALID_BRIDGE_INPUT, "spellbooker_allowed_areas": [], "spellbooker_unrestricted": False}
        result = await self.flow.async_step_user(user_input)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "areas_or_unrestricted_required")

    async def test_missing_queries_areas_and_unrestricted_is_rejected(self):
        user_input = {
            **VALID_BRIDGE_INPUT,
            "spellbooker_queries_allowed_areas": [], "spellbooker_queries_unrestricted": False,
        }
        result = await self.flow.async_step_user(user_input)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "queries_areas_or_unrestricted_required")

    async def test_first_render_shows_a_form_not_an_immediate_entry(self):
        result = await self.flow.async_step_user(None)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")

    async def test_no_satellite_only_fields_in_the_persisted_data(self):
        await self.flow.async_step_user(dict(VALID_BRIDGE_INPUT))
        finish = await self.flow.async_step_reveal_credential({})
        # Unrelated Satellite concepts (character slot, no-match response,
        # AI Task cleanup, ...) must never leak into a Bridge's own config.
        for satellite_only_key in ("spellbooker_profile_id", "spellbooker_no_match_response", "bridge_ai_task_entity_id"):
            self.assertNotIn(satellite_only_key, finish["data"])


class BridgeFormAreaPickerAdjacencyTests(unittest.IsolatedAsyncioTestCase):
    """Regression test for a real, live-observed Home Assistant frontend
    defect: two AreaSelector (ha-area-picker) fields placed immediately
    adjacent to each other in this dynamically-generated form mis-targeted
    a selection into the WRONG one of the pair - confirmed live against a
    real running Home Assistant instance (picking an area meant for the
    multi-select "allowed areas" field kept landing in the single-select
    "Bereich"/CONF_MCP_BRIDGE_AREA_ID field instead, silently, with no
    error). Backend/schema validation was never the problem - security-
    relevant since a scoped Bridge's policy could end up with the WRONG
    areas selected without any indication. The fix is simply "never place
    two AreaSelector fields back-to-back"; this test pins that layout
    invariant so it can never silently regress."""

    async def test_no_two_area_selector_fields_are_adjacent(self):
        module = _load_flow_module([("potter_verse", "Potterverse")])
        flow = module.McpBridgeSubentryFlow()
        result = await flow.async_step_user(None)
        # This test's own fake selector.AreaSelector (see FAKE_SELECTOR
        # above) collapses to a plain passthrough, so an AreaSelector's
        # position can't be told apart from any other field's by TYPE here
        # - identified by field NAME instead, which is exactly what
        # determines real ha-area-picker placement in the real form.
        area_field_names = {
            mcp_const.CONF_MCP_BRIDGE_AREA_ID,
            constants["CONF_SPELLBOOKER_ALLOWED_AREAS"],
            constants["CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS"],
        }
        keys = list(result["data_schema"].keys())
        area_selector_positions = [i for i, key in enumerate(keys) if key in area_field_names]
        self.assertEqual(len(area_selector_positions), 3)
        for earlier, later in pairwise(area_selector_positions):
            self.assertGreater(
                later - earlier, 1,
                f"AreaSelector fields at schema positions {earlier} and {later} are adjacent",
            )


class TranslationFieldOrderMatchesSchemaTests(unittest.TestCase):
    """The REAL bug mechanism, discovered only after the schema-only fix
    above was deployed and did NOT change the live form at all: Home
    Assistant's dynamically-generated config-flow form orders its fields
    by strings.json's/translations/*.json's own "data" dict key order for
    that step - NOT by the vol.Schema dict's own key order in
    config_flow.py. Reordering only the Python schema (as
    BridgeFormAreaPickerAdjacencyTests above checks) is a complete no-op
    for the real rendered form unless every translation file's "data" (and
    "data_description", which must list the exact same keys) is reordered
    to match. This test reads the real JSON files directly - the only way
    to guard the mechanism that actually controls live field order."""

    def _data_keys(self, path, step):
        import json
        raw = json.loads(path.read_text(encoding="utf-8"))
        node = raw["config_subentries"]["mcp_bridge"]["step"][step]
        # "data" (every field's label, drives real field ORDER) vs
        # "data_description" (per-field help text, optional per key - "name"
        # has none) - data_description's own keys must still be a SUBSET,
        # never naming a field "data" doesn't have.
        self.assertTrue(
            set(node["data_description"].keys()) <= set(node["data"].keys()),
            f"{path.name}[{step}]: data_description names a field missing from data",
        )
        return list(node["data"].keys())

    def _assert_no_adjacent_area_fields(self, keys, *, label):
        area_field_names = {
            mcp_const.CONF_MCP_BRIDGE_AREA_ID,
            constants["CONF_SPELLBOOKER_ALLOWED_AREAS"],
            constants["CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS"],
        }
        positions = [i for i, key in enumerate(keys) if key in area_field_names]
        self.assertEqual(len(positions), 3, f"{label}: expected 3 AreaSelector fields, found {len(positions)}")
        for earlier, later in pairwise(positions):
            self.assertGreater(
                later - earlier, 1,
                f"{label}: AreaSelector fields at positions {earlier} and {later} are adjacent",
            )

    def test_strings_json_and_every_translation_keep_area_fields_apart(self):
        for filename in ("strings.json", "translations/de.json", "translations/en.json"):
            path = ROOT / filename
            for step in ("user", "reconfigure"):
                keys = self._data_keys(path, step)
                self._assert_no_adjacent_area_fields(keys, label=f"{filename}[{step}]")


class BridgeLanguageFieldTests(unittest.IsolatedAsyncioTestCase):
    """The language field offers the ASSIGNED book's own languages
    (core.book_languages.languages(), via EngineSpellbook.languages) -
    never a second, hand-maintained list - and stays fully backward
    compatible with a stored value that predates this field being a
    selector at all, or that no longer matches the book's current
    languages."""

    def setUp(self):
        self.module = _load_flow_module(
            [("potter_verse", "Potterverse"), ("middle_earth", "Middle Earth")],
            book_languages={"potter_verse": ("de", "en"), "middle_earth": ("en",)},
        )

    def _language_options(self, schema):
        config = schema[self.module.CONF_MCP_BRIDGE_LANGUAGE]
        return [opt["value"] for opt in config["options"]]

    async def test_no_book_selected_yet_offers_only_the_use_ha_language_sentinel(self):
        flow = self.module.McpBridgeSubentryFlow()
        result = await flow.async_step_user(None)
        self.assertEqual(self._language_options(result["data_schema"]), [""])

    async def test_offers_the_currently_chosen_books_own_languages(self):
        flow = self.module.McpBridgeSubentryFlow()
        # An error re-render (missing area/unrestricted) still carries the
        # just-submitted book choice forward into the language options.
        user_input = {
            **VALID_BRIDGE_INPUT, "spellbooker_book": "middle_earth",
            "spellbooker_allowed_areas": [], "spellbooker_unrestricted": False,
        }
        result = await flow.async_step_user(user_input)
        self.assertEqual(result["errors"]["base"], "areas_or_unrestricted_required")
        self.assertEqual(self._language_options(result["data_schema"]), ["", "en"])

    async def test_reconfigure_first_render_uses_the_existing_bridges_stored_book(self):
        flow = self.module.McpBridgeSubentryFlow()
        flow.source = "reconfigure"
        flow._entry = N(entry_id="mcp-server-1", title="HASE MCP Server")
        flow._subentry = N(
            subentry_id="bridge-1", title="Bridge",
            data={**VALID_BRIDGE_INPUT, "spellbooker_book": "potter_verse"},
        )
        result = await flow.async_step_reconfigure(None)
        self.assertEqual(self._language_options(result["data_schema"]), ["", "de", "en"])

    async def test_a_language_value_outside_the_offered_list_still_persists(self):
        # custom_value=True on the selector, and the flow's own handling
        # never re-validates against the offered list - a stale value from
        # before the book changed, or a plain hand-typed code, must never
        # be rejected outright (core.book_languages.text()'s own fallback
        # chain is what makes an unresolvable language safe downstream).
        flow = self.module.McpBridgeSubentryFlow()
        user_input = {**VALID_BRIDGE_INPUT, "mcp_bridge_language": "fr"}
        await flow.async_step_user(user_input)
        finish = await flow.async_step_reveal_credential({})
        self.assertEqual(finish["data"]["mcp_bridge_language"], "fr")

    async def test_empty_language_still_means_use_home_assistant_language(self):
        flow = self.module.McpBridgeSubentryFlow()
        await flow.async_step_user(dict(VALID_BRIDGE_INPUT))
        finish = await flow.async_step_reveal_credential({})
        self.assertEqual(finish["data"]["mcp_bridge_language"], "")


class BridgeIndependentConfigTests(unittest.IsolatedAsyncioTestCase):
    """Multiple Bridges, independently configured Spellbook/area/scopes/
    credentials - none of one Bridge's fields leak into another's."""

    def setUp(self):
        self.module = _load_flow_module([("potter_verse", "Potterverse"), ("middle_earth", "Middle Earth")])

    async def _create(self, **overrides):
        flow = self.module.McpBridgeSubentryFlow()
        await flow.async_step_user({**VALID_BRIDGE_INPUT, **overrides})
        return await flow.async_step_reveal_credential({})

    async def test_two_bridges_have_independent_spellbook_area_and_credentials(self):
        jan = await self._create(
            name="Bridge JAN", spellbooker_book="potter_verse", mcp_bridge_area_id="jans_zimmer",
            spellbooker_allowed_areas=["jans_zimmer"], spellbooker_queries_allowed_areas=["jans_zimmer"],
        )
        master = await self._create(
            name="Bridge MASTER", spellbooker_book="middle_earth", mcp_bridge_area_id="",
            spellbooker_unrestricted=True, spellbooker_queries_unrestricted=True,
        )
        self.assertEqual(jan["data"]["spellbooker_book"], "potter_verse")
        self.assertEqual(master["data"]["spellbooker_book"], "middle_earth")
        self.assertEqual(jan["data"]["mcp_bridge_area_id"], "jans_zimmer")
        self.assertEqual(master["data"]["mcp_bridge_area_id"], "")
        self.assertNotEqual(
            jan["data"]["mcp_bridge_credential_verifier"], master["data"]["mcp_bridge_credential_verifier"],
        )


class BridgeReconfigureStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = _load_flow_module([("potter_verse", "Potterverse")])
        self.flow = self.module.McpBridgeSubentryFlow()
        self.flow.source = "reconfigure"
        self.entry = N(entry_id="mcp-server-1", title="HASE MCP Server")
        secret = FakeSecretStore.SECRET
        issued = mcp_credentials.issue_credential(secret)
        self._original_token = issued.token
        self.subentry = N(
            subentry_id="bridge-jan", title="Bridge JAN",
            data={
                "spellbooker_book": "potter_verse", "mcp_bridge_area_id": "jans_zimmer",
                "spellbooker_allowed_areas": ["jans_zimmer"], "spellbooker_unrestricted": False,
                "spellbooker_queries_allowed_areas": ["jans_zimmer"], "spellbooker_queries_unrestricted": False,
                "mcp_bridge_language": "",
                "mcp_bridge_credential_verifier": issued.verifier,
                "mcp_bridge_credential_suffix": issued.suffix,
            },
        )
        self.flow._entry = self.entry
        self.flow._subentry = self.subentry

    async def test_first_render_prefills_from_the_existing_subentry(self):
        result = await self.flow.async_step_reconfigure(None)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "reconfigure")

    async def test_editing_without_regenerate_preserves_the_existing_credential(self):
        new_input = {**VALID_BRIDGE_INPUT, "mcp_bridge_area_id": "wohnzimmer", "regenerate_credential": False}
        result = await self.flow.async_step_reconfigure(new_input)
        # No reveal step - nothing new to show.
        self.assertEqual(result["type"], "abort")
        _entry, _subentry, kwargs = self.flow.update_call
        self.assertEqual(kwargs["data"]["mcp_bridge_credential_verifier"], self.subentry.data["mcp_bridge_credential_verifier"])
        self.assertEqual(kwargs["data"]["mcp_bridge_area_id"], "wohnzimmer")

    async def test_regenerate_checkbox_issues_a_new_credential_and_invalidates_the_old_one(self):
        new_input = {**VALID_BRIDGE_INPUT, "regenerate_credential": True}
        reveal = await self.flow.async_step_reconfigure(new_input)
        self.assertEqual(reveal["step_id"], "reveal_credential")
        new_token = reveal["description_placeholders"]["token"]
        self.assertNotEqual(new_token, self._original_token)

        finish = await self.flow.async_step_reveal_credential({})
        self.assertEqual(finish["type"], "abort")
        _, _, kwargs = self.flow.update_call
        new_verifier = kwargs["data"]["mcp_bridge_credential_verifier"]
        old_verifier = self.subentry.data["mcp_bridge_credential_verifier"]
        self.assertNotEqual(new_verifier, old_verifier)

        # The OLD token no longer verifies against the NEW stored
        # verifier - old credential fails immediately, new one works.
        self.assertFalse(mcp_credentials.verify(FakeSecretStore.SECRET, self._original_token, expected_verifier=new_verifier))
        self.assertTrue(mcp_credentials.verify(FakeSecretStore.SECRET, new_token, expected_verifier=new_verifier))


if __name__ == "__main__":
    unittest.main()
