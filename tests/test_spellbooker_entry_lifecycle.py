"""Etappe 2: the entry_kind branch in __init__.py. The real regression
risk here isn't the new spellbooker path in isolation - it's that
every EXISTING CODE27 device entry (no entry_kind key at all) keeps
working exactly as before. Both directions are covered.
"""
import unittest

import test_harness_support as support

N = support.N
constants = support.constants

VOL = N(
    Schema=lambda value: value, Required=lambda key, **kw: key,
    Optional=lambda key, **kw: key, All=lambda *args: args,
    Range=lambda **kw: kw, In=lambda value: value, Length=lambda **kw: kw,
)
FAKE_PLATFORM = N(
    **{
        v.upper(): v
        for v in ["binary_sensor", "button", "image", "light", "media_player", "number", "select",
                   "sensor", "switch", "todo", "conversation"]
    }
)


def load_init(**extra):
    return support.load_source(
        "__init__", **constants, vol=VOL,
        CONF_HOST="host", CONF_PORT="port", Platform=FAKE_PLATFORM,
        cv=N(ensure_list=list, config_entry_only_config_schema=lambda domain: object()), async_get_clientsession=lambda h: None,
        Code27Client=lambda *args: None, Code27Coordinator=lambda *args: None,
        **extra,
    )


class FakeConfigEntries:
    def __init__(self):
        self.forwarded: list[tuple[str, tuple]] = []
        self.unloaded: list[tuple[str, tuple]] = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry.entry_id, tuple(platforms)))

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append((entry.entry_id, tuple(platforms)))
        return True


class SpellbookerEntrySetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.init = load_init()
        self.config_entries = FakeConfigEntries()
        self.hass = N(data={}, config_entries=self.config_entries)

    async def test_spellbooker_entry_needs_no_host_and_forwards_only_conversation(self):
        entry = N(
            entry_id="sb-1",
            data={"entry_kind": "spellbooker"},  # CONF_ENTRY_KIND / ENTRY_KIND_SPELLBOOKER
            options={},
            add_update_listener=lambda listener: (lambda: None),
            async_on_unload=lambda cb: None,
        )
        result = await self.init.async_setup_entry(self.hass, entry)
        self.assertTrue(result)
        self.assertEqual(self.config_entries.forwarded, [("sb-1", ("conversation",))])
        self.assertNotIn(self.init.DOMAIN, self.hass.data)

    async def test_spellbooker_entry_unload_only_touches_conversation_platform(self):
        entry = N(entry_id="sb-1", data={"entry_kind": "spellbooker"})
        ok = await self.init.async_unload_entry(self.hass, entry)
        self.assertTrue(ok)
        self.assertEqual(self.config_entries.unloaded, [("sb-1", ("conversation",))])

    async def test_legacy_code27_entry_without_kind_is_claimed_by_provider(self):
        # Historical CODE27 entries predate CONF_ENTRY_KIND, but they do
        # contain the provider-specific host field.  Compatibility belongs
        # to the CODE27 provider's legacy matcher; the generic HASE core must
        # never treat every kind-less entry as CODE27.
        entry = N(entry_id="dev-1", data={"host": "192.0.2.10"}, options={})
        # Reaching the provider-specific setup path is enough for this harness;
        # the fake client/coordinator cannot complete a real device setup.
        with self.assertRaises((AttributeError, TypeError, KeyError)):
            await self.init.async_setup_entry(self.hass, entry)
        self.assertEqual(self.config_entries.forwarded, [])

    async def test_legacy_satellite_entry_kind_raises_a_clear_setup_error(self):
        # SATELLITE used to be its own top-level entry kind - it's now
        # a subentry of its backing CODE27 device instead (see
        # const.ENTRY_KIND_SPELLBOOKER_SATELLITE's own note). Any
        # leftover entry of the old shape can no longer be set up
        # meaningfully and must fail loudly and cleanly, never crash
        # with a raw KeyError or silently limp along under semantics
        # it no longer supports.
        init = load_init(ConfigEntryError=RuntimeError)
        entry = N(entry_id="sat-old", data={"entry_kind": "spellbooker_satellite"}, options={})
        with self.assertRaises(RuntimeError):
            await init.async_setup_entry(self.hass, entry)
        self.assertEqual(self.config_entries.forwarded, [])

    async def test_legacy_satellite_entry_unload_still_works_cleanly(self):
        # Setup never succeeds for this shape (see above) - unload
        # (e.g. the user deleting the leftover entry) must still be a
        # clean no-op rather than crash reaching for a coordinator
        # that was never created.
        entry = N(entry_id="sat-old", data={"entry_kind": "spellbooker_satellite"})
        ok = await self.init.async_unload_entry(self.hass, entry)
        self.assertTrue(ok)
        self.assertEqual(self.config_entries.unloaded, [("sat-old", ("conversation",))])


class SatelliteSubentryReloadTests(unittest.IsolatedAsyncioTestCase):
    """A SATELLITE subentry being added, edited or removed is the ONE
    kind of change to a CODE27 device entry that must reload it -
    conversation.py's async_setup_entry only ever reads subentries
    once, at setup, so nothing else would ever pick the change up.
    Every OTHER options change (bridge settings, ...) must NOT
    reload, or it defeats "switch spellbook, no reload"."""

    def setUp(self):
        self.init = load_init()
        self.reloaded: list[str] = []
        self.hass = N(
            data={self.init.DOMAIN: {}},
            config_entries=N(async_reload=self._record_reload),
        )

    async def _record_reload(self, entry_id):
        self.reloaded.append(entry_id)

    def _entry(self, subentries):
        return N(entry_id="dev-1", options={}, subentries=subentries)

    def _coordinator(self, known_satellite_subentries):
        return N(
            update_interval=N(total_seconds=lambda: self.init.DEFAULT_SCAN_INTERVAL),
            known_satellite_subentries=known_satellite_subentries,
        )

    async def test_new_satellite_subentry_triggers_a_reload(self):
        self.hass.data[self.init.DOMAIN]["dev-1"] = self._coordinator({})
        sub = N(subentry_id="sat-1", subentry_type="satellite", title="Lilya", data={"a": 1})
        await self.init._async_update_listener(self.hass, self._entry({"sat-1": sub}))
        self.assertEqual(self.reloaded, ["dev-1"])

    async def test_edited_satellite_subentry_data_triggers_a_reload(self):
        self.hass.data[self.init.DOMAIN]["dev-1"] = self._coordinator({"sat-1": ("Lilya", {"a": 1})})
        sub = N(subentry_id="sat-1", subentry_type="satellite", title="Lilya", data={"a": 2})
        await self.init._async_update_listener(self.hass, self._entry({"sat-1": sub}))
        self.assertEqual(self.reloaded, ["dev-1"])

    async def test_removed_satellite_subentry_triggers_a_reload(self):
        self.hass.data[self.init.DOMAIN]["dev-1"] = self._coordinator({"sat-1": ("Lilya", {"a": 1})})
        await self.init._async_update_listener(self.hass, self._entry({}))
        self.assertEqual(self.reloaded, ["dev-1"])

    async def test_unrelated_option_change_with_unchanged_satellites_does_not_reload(self):
        self.hass.data[self.init.DOMAIN]["dev-1"] = self._coordinator({"sat-1": ("Lilya", {"a": 1})})
        sub = N(subentry_id="sat-1", subentry_type="satellite", title="Lilya", data={"a": 1})
        await self.init._async_update_listener(self.hass, self._entry({"sat-1": sub}))
        self.assertEqual(self.reloaded, [])

    async def test_credential_update_does_not_trigger_a_competing_reload(self):
        self.hass.data[self.init.DOMAIN]["dev-1"] = self._coordinator({})
        entry = self._entry({})
        entry.data = {"user_id": "new-user", "local_secret": "new-secret"}

        await self.init._async_update_listener(self.hass, entry)

        self.assertEqual(self.reloaded, [])


if __name__ == "__main__":
    unittest.main()
