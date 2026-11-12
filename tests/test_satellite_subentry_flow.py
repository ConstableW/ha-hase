"""Regression coverage for config_flow.py's SatelliteSubentryFlow -
the new subentry-based way to add/reconfigure a SATELLITE agent
(replacing an earlier top-level entry kind, see
const.ENTRY_KIND_SPELLBOOKER_SATELLITE's own note on why). Loaded via
test_bridge_delivery's load_source (AST-stripped of
homeassistant/relative imports) with small fakes standing in for the
ConfigSubentryFlow base class and the selector helpers - this file's
own validation/data-shaping logic is what's under test, not those.
"""
import unittest

import test_bridge_delivery as support

N = support.N
constants = support.constants


class FakeSubentryFlowBase:
    """Stands in for homeassistant.config_entries.ConfigSubentryFlow -
    only the handful of methods/attributes SatelliteSubentryFlow
    actually touches."""

    def __init_subclass__(cls, **kwargs):
        pass

    def __init__(self):
        self.hass = None
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
        # NOT async_update_reload_and_abort - see config_flow.py's own
        # note on why the subentry flow never reloads directly here;
        # the reload happens via the parent entry's own update
        # listener instead (__init__.py's _async_update_listener).
        self.update_call = (entry, subentry, kwargs)
        return {"type": "abort", "reason": kwargs.get("reason", "reconfigure_successful")}


class FakeConfigFlowBase:
    """Stands in for homeassistant.config_entries.ConfigFlow/OptionsFlow
    for the purposes of HaseConfigFlow's own class statement
    (`class HaseConfigFlow(ConfigFlow, domain=DOMAIN)`) - this file
    never instantiates HaseConfigFlow itself, only SatelliteSubentryFlow,
    so nothing beyond accepting the class-kwarg is needed."""

    def __init_subclass__(cls, **kwargs):
        pass


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


def _load_flow_module(book_choices):
    async def fake_discover_books(hass):
        return book_choices

    return support.load_source(
        "config_flow", **constants,
        vol=VOL, selector=FAKE_SELECTOR, callback=lambda f: f,
        ConfigFlow=FakeConfigFlowBase, OptionsFlow=FakeConfigFlowBase,
        ConfigSubentryFlow=FakeSubentryFlowBase, ConfigSubentry=object, SubentryFlowResult=dict,
        CONF_HOST="host", CONF_PORT="port",
        Code27Client=None, Code27ConnectionError=Exception, Code27Error=Exception,
        async_get_clientsession=lambda h: None,
        async_discover_spellbooks=fake_discover_books,
    )


VALID_INPUT = {
    "name": "Lilya Satellite",
    "spellbooker_profile_id": "1",
    "spellbooker_book": "demo",
    "spellbooker_allowed_areas": ["zwergstadt"],
    "spellbooker_unrestricted": False,
    "spellbooker_queries_allowed_areas": ["zwergstadt"],
    "spellbooker_queries_unrestricted": False,
    "spellbooker_no_match_response": "",
}


class CreateStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        module = _load_flow_module([("demo", "Demo")])
        self.flow = module.SatelliteSubentryFlow()
        self.flow.hass = N(config=N(language="de"))

    async def test_valid_input_creates_a_subentry_with_no_backend_field(self):
        result = await self.flow.async_step_user(dict(VALID_INPUT))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Lilya Satellite")
        data = result["data"]
        self.assertEqual(data["spellbooker_profile_id"], "1")
        self.assertEqual(data["spellbooker_book"], "demo")
        self.assertNotIn("spellbooker_backend_entry_id", data)
        self.assertNotIn("spellbooker_mode", data)

    async def test_no_book_choice_is_stored_as_empty_string(self):
        user_input = {**VALID_INPUT, "spellbooker_book": ""}
        result = await self.flow.async_step_user(user_input)
        self.assertEqual(result["data"]["spellbooker_book"], "")

    async def test_missing_actions_areas_and_unrestricted_is_rejected(self):
        user_input = {**VALID_INPUT, "spellbooker_allowed_areas": [], "spellbooker_unrestricted": False}
        result = await self.flow.async_step_user(user_input)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "areas_or_unrestricted_required")

    async def test_missing_queries_areas_and_unrestricted_is_rejected(self):
        user_input = {
            **VALID_INPUT,
            "spellbooker_queries_allowed_areas": [], "spellbooker_queries_unrestricted": False,
        }
        result = await self.flow.async_step_user(user_input)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "queries_areas_or_unrestricted_required")

    async def test_non_numeric_profile_id_is_rejected(self):
        user_input = {**VALID_INPUT, "spellbooker_profile_id": "Lilya"}
        result = await self.flow.async_step_user(user_input)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "invalid_profile_id")

    async def test_first_render_shows_a_form_not_an_immediate_entry(self):
        result = await self.flow.async_step_user(None)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")


class ReconfigureStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        module = _load_flow_module([("demo", "Demo")])
        self.flow = module.SatelliteSubentryFlow()
        self.flow.hass = N(config=N(language="de"))
        self.flow.source = "reconfigure"
        self.entry = N(entry_id="dev-1", title="CODE27 Papa")
        self.subentry = N(
            subentry_id="sat-1", title="Lilya Satellite",
            data={
                "spellbooker_profile_id": "1", "spellbooker_book": "demo",
                "spellbooker_allowed_areas": ["zwergstadt"], "spellbooker_unrestricted": False,
                "spellbooker_queries_allowed_areas": ["zwergstadt"], "spellbooker_queries_unrestricted": False,
                "spellbooker_no_match_response": "",
            },
        )
        self.flow._entry = self.entry
        self.flow._subentry = self.subentry

    async def test_first_render_prefills_from_the_existing_subentry(self):
        result = await self.flow.async_step_reconfigure(None)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "reconfigure")

    async def test_valid_change_updates_the_subentry_without_reloading_directly(self):
        new_input = {**VALID_INPUT, "spellbooker_profile_id": "2"}
        result = await self.flow.async_step_reconfigure(new_input)
        self.assertEqual(result["type"], "abort")
        entry, subentry, kwargs = self.flow.update_call
        self.assertIs(entry, self.entry)
        self.assertIs(subentry, self.subentry)
        self.assertEqual(kwargs["data"]["spellbooker_profile_id"], "2")
        self.assertEqual(kwargs["title"], "Lilya Satellite")


if __name__ == "__main__":
    unittest.main()
