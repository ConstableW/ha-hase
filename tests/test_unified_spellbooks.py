"""Etappe 8: real canonical books, shared matcher and real HA adapter boundaries."""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_harness_support as support
import yaml

engine = support.engine
models = support.models
executor = importlib.import_module('hase_spellbook_core.executor')
N = types.SimpleNamespace
ROOT = support.ROOT


# demo.yaml is the single source of truth for generic tests (see its
# "Engine test contract" section); thematic books are never loaded here.
DEMO_PATH = ROOT / 'spellbooks/demo.yaml'


def enabled_demo():
    """Exercise Demo commands while its shipped book remains disabled."""
    raw = yaml.safe_load(DEMO_PATH.read_text(encoding='utf-8'))
    raw['disabled'] = False
    return engine.load_spellbook_from_dict('demo', raw)


class InventoryTests(unittest.TestCase):
    def test_shipped_demo_is_administrable_but_not_runnable(self):
        book = engine.load_spellbook_from_path(DEMO_PATH)
        self.assertTrue(book.disabled)
        self.assertIsNone(engine.find_command(book, 'alle lichter an', 'de'))
        self.assertIsInstance(engine.match(book, 'alle lichter an', 'de'), models.EngineNoMatch)
        self.assertIsNone(engine.find_command_by_id(book, 'light_on'))

    def test_canonical_spellbooks_are_self_consistent(self):
        """Current spellbooks are validated as current data, not as a legacy snapshot.

        Historical Etappe-1 migration coverage belongs to LegacyMigrationTests and
        docs/spellbook-engine/migration-inventory.json.  A deliberate rename,
        removal or wording change in today's spellbooks must not require rewriting
        that historical snapshot.
        """
        spellbook_dir = ROOT / "spellbooks"
        paths = sorted(spellbook_dir.glob("*.yaml"))
        self.assertTrue(paths, "No canonical spellbooks found")

        allowed_keys = {
            'id', 'trigger', 'triggers', 'output', 'operation', 'target',
            'arguments', 'catchphrase', 'output_mode', 'description', 'enabled',
            'name', 'examples', 'tags', 'sources', 'steps', 'assist_redirect',
            'user',
        }

        for path in paths:
            raw = yaml.safe_load(path.read_text(encoding='utf-8'))
            self.assertIsInstance(raw, dict, path.name)
            commands = raw.get('commands')
            self.assertIsInstance(commands, list, path.name)

            book = engine.load_spellbook_from_dict(path.stem, raw)
            self.assertIsNotNone(book, path.name)
            self.assertEqual(len(book.commands), len(commands), path.name)

            # Canonical primary triggers must remain unique within a book.
            keys = [json.dumps(c['trigger'], sort_keys=True) for c in commands]
            self.assertEqual(len(keys), len(set(keys)), path.name)

            # Legacy representation must not leak back into canonical books.
            for command in commands:
                self.assertNotIn('action', command, (path.name, command.get('id')))
                unknown = set(command) - allowed_keys
                self.assertFalse(
                    unknown,
                    (path.name, command.get('id'), sorted(unknown)),
                )

    def test_localized_canonical_typed_and_output(self):
        book = enabled_demo()
        for lang, text in [('de', 'licht Gondor an'), ('en', 'turn on the light in Gondor')]:
            neutral = engine.find_command(book, text, lang)
            typed = engine.match(book, text, lang)
            self.assertEqual(neutral.action_draft, typed.action_draft)
            self.assertEqual(neutral.captures, {'area': 'Gondor'})
        checked = 0
        for command in book.commands:
            if command.operation is None and not command.steps and not command.sources and isinstance(command.output, dict):
                checked += 1
                for lang in ['de', 'en']:
                    trigger = support.matching.resolve_localized(command.trigger, lang)
                    captures = {name: 'Gondor' for kind, name in command.compiled[trigger] if kind == 'ph'}
                    found = engine.find_command(book, trigger.format(**captures), lang)
                    self.assertIsNotNone(found)
                    self.assertEqual(found.output, command.output[lang].format(**captures))
                    # match() now ranks a text-only trigger too (the real
                    # gap docs/HASE-MCP.md's "Text-only Spells and
                    # assist_redirect" section documents as fixed) -
                    # a real EngineMatch, action_draft/query_plan/
                    # step_plan all None, command/captures carrying what
                    # core.router.SpellbookRouter needs to dispatch it.
                    text_only = engine.match(book, trigger.format(**captures), lang)
                    self.assertIsInstance(text_only, models.EngineMatch)
                    self.assertIsNone(text_only.action_draft)
                    self.assertIsNone(text_only.query_plan)
                    self.assertIsNone(text_only.step_plan)
                    self.assertEqual(text_only.command.spell_id, command.spell_id)
                    self.assertEqual(text_only.captures, captures)
        self.assertGreaterEqual(checked, 1, 'demo.yaml must contain a localized text-only spell')


class RealTargetExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        entries = {}
        states = {}
        for name, area in [('a','kitchen'),('b','kitchen'),('c','office'),('disabled','kitchen'),('private','kitchen'),('group','kitchen'),('hidden','kitchen'),('unavailable','kitchen')]:
            eid = 'light.' + name
            entries[eid] = N(entity_id=eid, area_id=area, device_id=None,
                             disabled_by='user' if name == 'disabled' else None,
                             name=name, original_name=name, hidden_by='user' if name=='hidden' else None)
            states[eid] = N(state='unavailable' if name=='unavailable' else 'off', attributes={'entity_id':['light.a','light.c']} if name == 'group' else {})
        # Exercise device-derived area assignment as well.
        entries['light.b'].area_id = None
        entries['light.b'].device_id = 'device-b'
        self.hass = N(states=N(get=states.get), services=N(async_call=AsyncMock()))
        mods = {
            'homeassistant': types.ModuleType('homeassistant'),
            'homeassistant.helpers': types.ModuleType('homeassistant.helpers'),
            'homeassistant.components': types.ModuleType('homeassistant.components'),
            'homeassistant.components.homeassistant': types.ModuleType('homeassistant.components.homeassistant'),
            'homeassistant.helpers.entity_registry': N(async_get=lambda h:N(entities=entries)),
            'homeassistant.helpers.device_registry': N(async_get=lambda h:N(devices={'device-b':N(area_id='kitchen')})),
            'homeassistant.helpers.area_registry': N(async_get=lambda h:N(areas={})),
            'homeassistant.components.homeassistant.exposed_entities': N(async_should_expose=lambda h,a,e:e!='light.private'),
        }
        self.mods = patch.dict(sys.modules, mods)
        self.mods.start()
        self.addCleanup(self.mods.stop)
        self.ha = support.load_source('spellbooker_ha', ResolvedEntity=executor.ResolvedEntity,
            ResolvedTargets=executor.ResolvedTargets, TargetNotFound=executor.TargetNotFound,
            VacuumCleanOutcome=executor.VacuumCleanOutcome,
            ServiceCallOutcome=executor.ServiceCallOutcome)

    @staticmethod
    def _service_targets(call):
        # Generic services pass the targets as `target=`; the legacy
        # set_light_power path put them into the service data.
        target = call.kwargs.get('target') or {}
        return target.get('entity_id') or call.args[2]['entity_id']


    async def test_named_forbidden_target_is_denied(self):
        restricted = models.AreaPolicy(frozenset({'kitchen'}))
        result = await executor.ActionExecutor(self.ha.HaTargetResolver(self.hass),self.ha.HaServiceCaller(self.hass)).execute(
            models.ActionDraft('light.set_power',models.ActionTarget('entity_id','light.c'),{'on':True}),
            models.ScopedAreaPolicy(restricted,restricted))
        self.assertEqual(result.status,models.ExecutionStatus.DENIED)
        self.hass.services.async_call.assert_not_awaited()



