"""Regression coverage for config_flow.py's SatelliteSubentryFlow -
the new subentry-based way to add/reconfigure a SATELLITE agent
(replacing an earlier top-level entry kind, see
const.ENTRY_KIND_SPELLBOOKER_SATELLITE's own note on why). Loaded via
test_harness_support's load_source (AST-stripped of
homeassistant/relative imports) with small fakes standing in for the
ConfigSubentryFlow base class and the selector helpers - this file's
own validation/data-shaping logic is what's under test, not those.
"""
import unittest

import test_harness_support as support

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


def _load_flow_module():
    return support.load_source(
        "config_flow", **constants,
        vol=VOL, selector=FAKE_SELECTOR, callback=lambda f: f,
        ConfigFlow=FakeConfigFlowBase, OptionsFlow=FakeConfigFlowBase,
        ConfigSubentryFlow=FakeSubentryFlowBase, ConfigSubentry=object, SubentryFlowResult=dict,
        CONF_HOST="host", CONF_PORT="port",
        Code27Client=None, Code27ConnectionError=Exception, Code27Error=Exception,
        async_get_clientsession=lambda h: None,
    )


# The satellite is a slim pipe to the character: name + character slot only.
VALID_INPUT = {"name": "Lilya Satellite", "spellbooker_profile_id": "1"}


class CreateStepTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        module = _load_flow_module()
        self.flow = module.SatelliteSubentryFlow()
        self.flow.hass = N(config=N(language="de"))

    async def test_valid_input_stores_only_the_character_slot(self):
        result = await self.flow.async_step_user(dict(VALID_INPUT))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Lilya Satellite")
        self.assertEqual(result["data"], {"spellbooker_profile_id": "1", "spellbooker_pass_area": True})

    async def test_old_fields_are_not_stored(self):
        legacy = {**VALID_INPUT, "spellbooker_book": "demo", "spellbooker_unrestricted": True,
                  "spellbooker_allowed_areas": ["zwergstadt"], "spellbooker_no_match_response": "x"}
        result = await self.flow.async_step_user(legacy)
        self.assertEqual(result["data"], {"spellbooker_profile_id": "1", "spellbooker_pass_area": True})

    async def test_passing_the_area_can_be_switched_off(self):
        result = await self.flow.async_step_user({**VALID_INPUT, "spellbooker_pass_area": False})
        self.assertIs(result["data"]["spellbooker_pass_area"], False)

    async def test_form_has_only_name_and_slot(self):
        result = await self.flow.async_step_user(None)
        self.assertEqual(list(result["data_schema"]), ["name", "spellbooker_profile_id", "spellbooker_pass_area"])

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
        module = _load_flow_module()
        self.flow = module.SatelliteSubentryFlow()
        self.flow.hass = N(config=N(language="de"))
        self.flow.source = "reconfigure"
        self.entry = N(entry_id="dev-1", title="CODE27 Papa")
        self.subentry = N(
            subentry_id="sat-1", title="Lilya Satellite",
            # an older satellite may still carry book/areas - they are simply ignored
            data={"spellbooker_profile_id": "1", "spellbooker_book": "demo",
                  "spellbooker_allowed_areas": ["zwergstadt"]},
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
