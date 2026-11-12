"""Etappe 8: real canonical books, shared matcher and real HA adapter boundaries."""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_bridge_delivery as support
import yaml

engine = support.engine
models = support.models
executor = importlib.import_module('hase_spellbook_core.executor')
support.bridge.response_text_for_failure = executor.response_text_for_failure
support.bridge.response_text_for_success = executor.response_text_for_success
support.bridge.record_execution = lambda *args, **kwargs: None
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
        self.frontend = support.load_source('frontend', ActionExecutor=executor.ActionExecutor,
            ActionDraft=models.ActionDraft, AreaPolicy=models.AreaPolicy,
            ScopedAreaPolicy=models.ScopedAreaPolicy, ExecutionResult=models.ExecutionResult,
            HaTargetResolver=self.ha.HaTargetResolver, HaServiceCaller=self.ha.HaServiceCaller)

    @staticmethod
    def _service_targets(call):
        # Generic services pass the targets as `target=`; the legacy
        # set_light_power path put them into the service data.
        target = call.kwargs.get('target') or {}
        return target.get('entity_id') or call.args[2]['entity_id']

    async def test_real_adapter_all_allowed_on_off_and_scoped_satellite(self):
        book = enabled_demo()
        for text, on in [('alle lichter an',True),('alle lichter aus',False)]:
            neutral = engine.find_command(book,text,'de')
            typed = engine.match(book,text,'de')
            self.assertEqual(neutral.action_draft,typed.action_draft)
            result = await self.frontend.Code27FrontendAdapter(self.hass).execute(neutral.action_draft)
            self.assertEqual(result.status, models.ExecutionStatus.SUCCESS)
            call = self.hass.services.async_call.call_args
            self.assertEqual(call.args[:2], ('light', 'turn_on' if on else 'turn_off'))
            self.assertEqual(set(self._service_targets(call)), {'light.a','light.b','light.c'})
            restricted = models.AreaPolicy(frozenset({'kitchen'}))
            result = await executor.ActionExecutor(self.ha.HaTargetResolver(self.hass),self.ha.HaServiceCaller(self.hass)).execute(
                typed.action_draft, models.ScopedAreaPolicy(restricted,restricted))
            self.assertEqual(result.status,models.ExecutionStatus.SUCCESS)
            self.assertEqual(set(self._service_targets(self.hass.services.async_call.call_args)),{'light.a','light.b'})

    async def test_named_forbidden_target_is_denied(self):
        restricted = models.AreaPolicy(frozenset({'kitchen'}))
        result = await executor.ActionExecutor(self.ha.HaTargetResolver(self.hass),self.ha.HaServiceCaller(self.hass)).execute(
            models.ActionDraft('light.set_power',models.ActionTarget('entity_id','light.c'),{'on':True}),
            models.ScopedAreaPolicy(restricted,restricted))
        self.assertEqual(result.status,models.ExecutionStatus.DENIED)
        self.hass.services.async_call.assert_not_awaited()

    async def test_query_plan_respects_the_same_exposure_and_area_policy_as_actions(self):
        # CODE27's new query_plan dispatch (providers/code27/bridge.py's
        # _handle_typed_query, providers/code27/frontend.py's
        # Code27FrontendAdapter.execute_query_plan) reuses the exact same
        # ActionExecutor.execute_query_plan/TargetResolver/AreaPolicy
        # machinery real actions already go through above - proven here
        # against the SAME real entity/area registries and the SAME
        # already-established {light.a, light.b, light.c} exposed/
        # resolvable set (light.private/disabled/hidden/unavailable/group
        # never counted, exactly as test_real_adapter_all_allowed_on_off_
        # and_scoped_satellite already proves for actions).
        plan = models.QueryPlan(
            sources=(models.QuerySourceSpec(kind='count', target=models.ActionTarget('all_allowed'), domain_filter='light', variable='n'),),
            response_template='{n}',
        )
        adapter = self.frontend.Code27FrontendAdapter(self.hass)
        result = await adapter.execute_query_plan(plan)
        self.assertEqual(result.status, models.ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data['response_text'], '3')

        # Same query, but through a RESTRICTED policy (mirroring what a
        # real Bridge/Satellite would enforce, even though CODE27 itself
        # always uses the unrestricted owner policy) - kitchen-only
        # excludes light.c (office), proving AreaPolicy is genuinely
        # consulted for this path, not bypassed.
        restricted = models.AreaPolicy(frozenset({'kitchen'}))
        restricted_result = await executor.ActionExecutor(
            self.ha.HaTargetResolver(self.hass), self.ha.HaServiceCaller(self.hass),
        ).execute_query_plan(plan, models.ScopedAreaPolicy(restricted, restricted))
        self.assertEqual(restricted_result.status, models.ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(restricted_result.data['response_text'], '2')


class UnifiedBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        support.DeliveryTests.setUp(self)
        # A real Home Assistant ConfigEntry always exposes .data.
        # DeliveryTests' lightweight SimpleNamespace fixture predates that contract.
        self.entry.data = {}
    def book(self, commands):
        return engine.load_spellbook_from_dict('test',{'name':'Test','commands':commands})

    async def run_spell(self, book, text, result):
        self.entry.options['bridge_spellbook'] = 'test'
        adapter = N(execute=AsyncMock(return_value=result))
        with patch.object(support.loader,'async_get_spellbook',AsyncMock(return_value=book)), \
             patch.object(support.bridge,'Code27FrontendAdapter',return_value=adapter,create=True), \
             patch.object(support.bridge,'_forward_to_assist',AsyncMock(return_value=(None,None,''))) as assist, \
             patch.object(support.bridge,'_output_speech',AsyncMock()), \
             patch.object(support.bridge,'_log_activity'):
            await support.bridge._handle_bridge_message(self.hass,self.entry,self.c,text,None)
        return adapter, assist

    async def test_output_only_reaches_assist_via_code27_bridge_and_satellite_alike(self):
        # Was: "...but not Satellite" - the real gap this session's live
        # voice trace found and fixed (docs/HASE-MCP.md's own "Text-only
        # Spells and assist_redirect" section). CODE27's own chat Bridge
        # (asserted below, unchanged) and core.router.SpellbookRouter's
        # native Satellite/SPELLBOOK_ONLY matching now both reach this
        # execution kind - see test_router.py's own
        # VoiceReachesEveryExecutionKindTests for the router-side proof.
        book = self.book([{'trigger':'evening','output':'Activate evening mood'}])
        adapter,assist = await self.run_spell(book,'evening',None)
        adapter.execute.assert_not_awaited()
        self.assertEqual(assist.call_args.args[3],'Activate evening mood')
        satellite_match = engine.match(book,'evening')
        self.assertIsInstance(satellite_match, models.EngineMatch)
        self.assertIsNone(satellite_match.action_draft)
        self.assertEqual(satellite_match.command.output, 'Activate evening mood')

    async def test_typed_plus_output_success_failure_and_unsupported_never_forward(self):
        book = self.book([{'trigger':'lumos','operation':'light.set_power','target':{'kind':'all_allowed'},
                           'arguments':{'on':True},'output':'Never send me','catchphrase':'Lumos.'}])
        for status in [models.ExecutionStatus.SUCCESS,models.ExecutionStatus.FAILED,models.ExecutionStatus.UNSUPPORTED]:
            adapter,assist = await self.run_spell(book,'lumos',models.ExecutionResult(status,'test'))
            adapter.execute.assert_awaited_once_with(engine.match(book,'lumos').action_draft, source_area_id=None)
            assist.assert_not_awaited()

    async def test_typed_ambiguity_choice_keeps_typed_intent(self):
        commands = [{'trigger':trigger,'operation':'light.set_power','target':{'kind':'all_allowed'},
                     'arguments':{'on':on},'output':output} for trigger,on,output in
                    [('activate lamps',True,'lights on'),('deactivate lamps',False,'lights off')]]
        book = self.book(commands)
        a = engine.find_command(book,'activate lamps')
        b = engine.find_command(book,'deactivate lamps')
        with patch.object(support.bridge,'_output_speech',AsyncMock()), patch.object(support.bridge,'_log_activity'):
            await support.bridge._begin_spell_choice_correction(self.hass,self.entry,self.c,engine.CommandAmbiguity(a,b),None)
        self.assertIsInstance(self.c.pending_correction.spell_option_a[0],models.ActionDraft)
        with patch.object(support.bridge,'_handle_typed_action',AsyncMock()) as typed, \
             patch.object(support.bridge,'_forward_to_assist',AsyncMock()) as assist, \
             patch.object(support.bridge,'_log_activity'):
            await support.bridge._consume_spell_choice_reply(self.hass,self.entry,self.c,self.c.pending_correction,'lights on',None)
        typed.assert_awaited_once()
        assist.assert_not_awaited()


    async def test_typed_entity_correction_retries_draft_without_assist(self):
        self.entry.options['bridge_entity_correction_enabled'] = True
        draft = models.ActionDraft('light.set_power', models.ActionTarget('area_name','Kithen'), {'on':True}, 'Lumos.')
        adapter = N(execute=AsyncMock(side_effect=[
            models.ExecutionResult(models.ExecutionStatus.INVALID,'target_not_found'),
            models.ExecutionResult(models.ExecutionStatus.SUCCESS,'ok'),
        ]))
        with patch.object(support.bridge,'Code27FrontendAdapter',return_value=adapter,create=True), \
             patch.object(support.spellbook,'find_correction_candidate',return_value=('Kitchen',0.9)), \
             patch.object(support.spellbook,'verify_named_candidate',return_value=('Kitchen',1.0)), \
             patch.object(support.bridge,'_output_speech',AsyncMock()), \
             patch.object(support.bridge,'_log_activity'), \
             patch.object(support.bridge,'_forward_to_assist',AsyncMock()) as assist:
            await support.bridge._handle_typed_action(self.hass,self.entry,self.c,draft,None)
            pending = self.c.pending_correction
            self.assertEqual(pending.typed_draft,draft)
            await support.bridge._retry_with_candidate(self.hass,self.entry,self.c,pending,'Kitchen',None)
        self.assertIsNone(self.c.pending_correction)
        self.assertEqual(adapter.execute.await_args_list[1].args[0],
                         models.ActionDraft('light.set_power',models.ActionTarget('area_name','Kitchen'),{'on':True},'Lumos.'))
        assist.assert_not_awaited()

    async def test_light_all_percentage_dim_reports_on_not_off(self):
        # Regression: light.set_power's percentage variant (the Wizard's
        # "Licht dimmen" action) targeting all_allowed used to always
        # speak "aus" (off), because draft.arguments.get("on") is None
        # for a percentage-based draft - see _handle_typed_action's
        # light_all branch. The real HA call was always correct; only
        # the spoken confirmation lied.
        draft = models.ActionDraft('light.set_power', models.ActionTarget('all_allowed'), {'percentage': 30})
        adapter = N(execute=AsyncMock(return_value=models.ExecutionResult(models.ExecutionStatus.SUCCESS, 'ok')))
        with patch.object(support.bridge, 'Code27FrontendAdapter', return_value=adapter, create=True), \
             patch.object(support.bridge, '_output_speech', AsyncMock()) as speak, \
             patch.object(support.bridge, '_log_activity'):
            await support.bridge._handle_typed_action(self.hass, self.entry, self.c, draft, None)
        self.assertIn('an', speak.call_args.args[3])
        self.assertNotIn('aus', speak.call_args.args[3])

    async def test_light_all_percentage_zero_reports_off(self):
        draft = models.ActionDraft('light.set_power', models.ActionTarget('all_allowed'), {'percentage': 0})
        adapter = N(execute=AsyncMock(return_value=models.ExecutionResult(models.ExecutionStatus.SUCCESS, 'ok')))
        with patch.object(support.bridge, 'Code27FrontendAdapter', return_value=adapter, create=True), \
             patch.object(support.bridge, '_output_speech', AsyncMock()) as speak, \
             patch.object(support.bridge, '_log_activity'):
            await support.bridge._handle_typed_action(self.hass, self.entry, self.c, draft, None)
        self.assertIn('aus', speak.call_args.args[3])

    async def test_canonical_vacuum_uses_generic_execution_and_frontend_toggle(self):
        book = enabled_demo()
        for lang, text in (('de', 'reinige Gondor'), ('en', 'clean Gondor')):
            self.hass.config.language = lang
            self.entry.options['bridge_vacuum_enabled'] = True
            adapter,assist = await self.run_spell(book,text,
                                                 models.ExecutionResult(models.ExecutionStatus.SUCCESS,'ok'))
            adapter.execute.assert_awaited_once_with(engine.match(book,text,lang).action_draft, source_area_id=None)
            assist.assert_not_awaited()
            self.entry.options['bridge_vacuum_enabled'] = False
            adapter,assist = await self.run_spell(book,text,None)
            adapter.execute.assert_not_awaited()
            assist.assert_not_awaited()

    def test_neutral_ambiguity_includes_output_and_mixed_commands(self):
        for typed in [False, True]:
            commands = [
                {'trigger':'aktiviere licht {area}','output':'Licht {area} an'},
                {'trigger':'deaktiviere licht {area}','output':'Licht {area} aus'},
            ]
            if typed:
                commands[0].update(operation='light.set_power', target={'kind':'area_name','capture':'area'}, arguments={'on':True})
            found = engine.find_command(self.book(commands),'daktiviere licht Buero','de')
            self.assertIsInstance(found,engine.CommandAmbiguity)
            self.assertNotEqual(found.option_a.spoken_trigger,found.option_b.spoken_trigger)


    async def test_canonical_output_only_bridge_localization(self):
        book = enabled_demo()
        for lang, text, forwarded, catchphrase in [
            ('de', 'die sonne geht auf über Gondor', 'Licht Gondor auf 100 Prozent stellen', 'Der neue Tag beginnt.'),
            ('en', 'the sun rises over Gondor', 'Set the light in Gondor to 100 percent', 'A new day begins.'),
        ]:
            self.hass.config.language = lang
            adapter, assist = await self.run_spell(book, text, None)
            adapter.execute.assert_not_awaited()
            self.assertEqual(assist.call_args.args[3], forwarded, lang)
            self.assertEqual(assist.call_args.kwargs['catchphrase'], catchphrase, lang)
            # core.router.SpellbookRouter reaches this same Spell via native
            # Satellite/SPELLBOOK_ONLY matching too.
            satellite_match = engine.match(book, text, lang)
            self.assertIsInstance(satellite_match, models.EngineMatch)
            self.assertIsNone(satellite_match.action_draft)
            self.assertEqual(satellite_match.command.spell_id, 'sunrise')
