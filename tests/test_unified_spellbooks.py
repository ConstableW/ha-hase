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
N = types.SimpleNamespace
ROOT = support.ROOT


class InventoryTests(unittest.TestCase):
    def test_every_source_trigger_and_representation_survives(self):
        inventory = json.loads((ROOT.parents[1] / 'docs/spellbook-engine/migration-inventory.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(len(s['legacy']) for s in inventory['sources'].values()), 86)
        counts = {}
        for theme, sources in inventory['sources'].items():
            raw = yaml.safe_load((ROOT / 'spellbooks' / f'{theme}.yaml').read_text(encoding='utf-8'))
            book = engine.load_spellbook_from_dict(theme, raw)
            self.assertEqual(len(book.commands), len(raw['commands']))
            counts[theme] = len(book.commands)
            keys = [json.dumps(c['trigger'], sort_keys=True) for c in raw['commands']]
            self.assertEqual(len(keys), len(set(keys)))
            for source_kind, entries in sources.items():
                for old in entries:
                    matches = [c for c in raw['commands'] if c['trigger'] == old['trigger']]
                    self.assertEqual(len(matches), 1, (theme, old['trigger']))
                    new = matches[0]
                    for key in ['output', 'operation', 'target', 'arguments']:
                        if key in old:
                            self.assertEqual(new[key], old[key])
                    if 'catchphrase' in old and not (source_kind == 'typed' and old.get('id') == 'light_beacons_named_area'):
                        self.assertEqual(new['catchphrase'], old['catchphrase'])
                    self.assertNotIn('action', new)
                    self.assertLessEqual(set(new), {'id','trigger','output','operation','target','arguments','catchphrase'})
                    if old.get('action'):
                        self.assertEqual(new['operation'], 'vacuum.clean_area' if old['action'] == 'vacuum_clean' else 'light.set_power')
        self.assertEqual(counts, {'middle_earth': 42, 'potter_verse': 45, 'demo': 10})

    def test_localized_canonical_typed_and_output(self):
        book = engine.load_spellbook_from_path(ROOT / 'spellbooks/middle_earth.yaml')
        for lang, text in [('de', 'licht von eärendil Gondor'), ('en', 'light of eärendil Gondor')]:
            neutral = engine.find_command(book, text, lang)
            typed = engine.match(book, text, lang)
            self.assertEqual(neutral.action_draft, typed.action_draft)
            self.assertIsNone(neutral.output)
            self.assertEqual(neutral.captures, {'area': 'Gondor'})
        for command in book.commands:
            if command.operation is None and isinstance(command.output, dict):
                for lang in ['de', 'en']:
                    trigger = support.matching.resolve_localized(command.trigger, lang)
                    captures = {name: 'Gondor' for kind, name in command.compiled[trigger] if kind == 'ph'}
                    found = engine.find_command(book, trigger.format(**captures), lang)
                    self.assertIsNotNone(found)
                    self.assertEqual(found.output, command.output[lang].format(**captures))
                    self.assertIsInstance(engine.match(book, trigger.format(**captures), lang), models.EngineNoMatch)
                return
        self.fail('No localized output command')


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
            VacuumCleanOutcome=executor.VacuumCleanOutcome)
        self.frontend = support.load_source('frontend', ActionExecutor=executor.ActionExecutor,
            ActionDraft=models.ActionDraft, AreaPolicy=models.AreaPolicy,
            ScopedAreaPolicy=models.ScopedAreaPolicy, ExecutionResult=models.ExecutionResult,
            HaTargetResolver=self.ha.HaTargetResolver, HaServiceCaller=self.ha.HaServiceCaller)

    async def test_real_adapter_all_allowed_on_off_and_scoped_satellite(self):
        book = engine.load_spellbook_from_path(ROOT / 'spellbooks/potter_verse.yaml')
        for text, on in [('lumos maxima',True),('nox maxima',False)]:
            neutral = engine.find_command(book,text,'de')
            typed = engine.match(book,text,'de')
            self.assertEqual(neutral.action_draft,typed.action_draft)
            result = await self.frontend.Code27FrontendAdapter(self.hass).execute(neutral.action_draft)
            self.assertEqual(result.status, models.ExecutionStatus.SUCCESS)
            call = self.hass.services.async_call.call_args
            self.assertEqual(call.args[:2], ('light', 'turn_on' if on else 'turn_off'))
            self.assertEqual(set(call.args[2]['entity_id']), {'light.a','light.b','light.c'})
            restricted = models.AreaPolicy(frozenset({'kitchen'}))
            result = await executor.ActionExecutor(self.ha.HaTargetResolver(self.hass),self.ha.HaServiceCaller(self.hass)).execute(
                typed.action_draft, models.ScopedAreaPolicy(restricted,restricted))
            self.assertEqual(result.status,models.ExecutionStatus.SUCCESS)
            self.assertEqual(set(self.hass.services.async_call.call_args.args[2]['entity_id']),{'light.a','light.b'})

    async def test_named_forbidden_target_is_denied(self):
        restricted = models.AreaPolicy(frozenset({'kitchen'}))
        result = await executor.ActionExecutor(self.ha.HaTargetResolver(self.hass),self.ha.HaServiceCaller(self.hass)).execute(
            models.ActionDraft('light.set_power',models.ActionTarget('entity_id','light.c'),{'on':True}),
            models.ScopedAreaPolicy(restricted,restricted))
        self.assertEqual(result.status,models.ExecutionStatus.DENIED)
        self.hass.services.async_call.assert_not_awaited()


class UnifiedBridgeTests(unittest.IsolatedAsyncioTestCase):
    setUp = support.DeliveryTests.setUp
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

    async def test_output_only_reaches_assist_but_not_satellite(self):
        book = self.book([{'trigger':'evening','output':'Activate evening mood'}])
        adapter,assist = await self.run_spell(book,'evening',None)
        adapter.execute.assert_not_awaited()
        self.assertEqual(assist.call_args.args[3],'Activate evening mood')
        self.assertIsInstance(engine.match(book,'evening'),models.EngineNoMatch)

    async def test_typed_plus_output_success_failure_and_unsupported_never_forward(self):
        book = self.book([{'trigger':'lumos','operation':'light.set_power','target':{'kind':'all_allowed'},
                           'arguments':{'on':True},'output':'Never send me','catchphrase':'Lumos.'}])
        for status in [models.ExecutionStatus.SUCCESS,models.ExecutionStatus.FAILED,models.ExecutionStatus.UNSUPPORTED]:
            adapter,assist = await self.run_spell(book,'lumos',models.ExecutionResult(status,'test'))
            adapter.execute.assert_awaited_once_with(engine.match(book,'lumos').action_draft)
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
        book = engine.load_spellbook_from_path(ROOT / 'spellbooks/middle_earth.yaml')
        self.entry.options['bridge_vacuum_enabled'] = True
        adapter,assist = await self.run_spell(book,'reinigt die hallen von Gondor',
                                             models.ExecutionResult(models.ExecutionStatus.SUCCESS,'ok'))
        adapter.execute.assert_awaited_once_with(engine.match(book,'reinigt die hallen von Gondor','de').action_draft)
        assist.assert_not_awaited()
        self.entry.options['bridge_vacuum_enabled'] = False
        adapter,assist = await self.run_spell(book,'reinigt die hallen von Gondor',None)
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
        book = engine.load_spellbook_from_path(ROOT / 'spellbooks/middle_earth.yaml')
        adapter, assist = await self.run_spell(book, 'die sonne geht auf über Gondor', None)
        adapter.execute.assert_not_awaited()
        self.assertEqual(assist.call_args.args[3], 'Licht Gondor auf 100 Prozent stellen')
        self.assertEqual(assist.call_args.kwargs['catchphrase'], 'Der neue Tag beginnt.')
        self.assertIsInstance(engine.match(book,'die sonne geht auf über Gondor','de'),models.EngineNoMatch)
