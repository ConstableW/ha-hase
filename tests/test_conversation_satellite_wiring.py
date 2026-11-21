"""Regression coverage for conversation.py's SATELLITE wiring:
mode-based AgentConfig construction, backend resolution from the
config_entry a SATELLITE subentry lives on, and how async_setup_entry
turns each SUBENTRY_TYPE_SATELLITE subentry into its own entity with
config_subentry_id set (the mechanism that makes it render nested
under its owning entry's hub in the HA UI). Loaded via
test_harness_support's load_source (AST-stripped of
homeassistant/relative imports) with real core/ classes as adapters
plus small fakes for the HA-registry-touching pieces (spellbooker_ha.py,
providers/code27/backend.py) - this file's own logic is what's under test, not
those.
"""
import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

import test_harness_support as support

HASE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"


def _load_core_package():
    if "hase_spellbook_core" in sys.modules:
        return sys.modules["hase_spellbook_core"]
    spec = importlib.util.spec_from_file_location(
        "hase_spellbook_core",
        HASE_ROOT / "core" / "__init__.py",
        submodule_search_locations=[str(HASE_ROOT / "core")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load_core_package()
_models_mod = importlib.import_module("hase_spellbook_core.models")
_router_mod = importlib.import_module("hase_spellbook_core.router")
_executor_mod = importlib.import_module("hase_spellbook_core.executor")
_engine_mod = importlib.import_module("hase_spellbook_core.spellbook_engine")
_mcp_execution_mod = importlib.import_module("hase_spellbook_core.mcp_execution")

ConversationRequest = _models_mod.ConversationRequest
ConversationResult = _models_mod.ConversationResult
ConversationStatus = _models_mod.ConversationStatus
ExecutionStatus = _models_mod.ExecutionStatus
ExecutionResult = _models_mod.ExecutionResult
FrontendMode = _models_mod.FrontendMode
AreaPolicy = _models_mod.AreaPolicy
ScopedAreaPolicy = _models_mod.ScopedAreaPolicy
BackendResult = _models_mod.BackendResult
SpellbookRouter = _router_mod.SpellbookRouter
AgentConfig = _router_mod.AgentConfig
ActionExecutor = _executor_mod.ActionExecutor
EngineSpellbook = _engine_mod.EngineSpellbook


class FakeConversationEntity:
    """Stands in for homeassistant.components.conversation.ConversationEntity -
    only __init__ compatibility matters here, this module never calls
    any of the real base class's own methods."""


class N(types.SimpleNamespace):
    pass


def _fake_subentry(subentry_id="sat-1", title="Lilya Satellite", data=None):
    """A ConfigSubentry duck-type - conversation.py only ever reads
    .subentry_id, .title and .data off the real thing, never calls any
    of its methods, so a plain namespace is a faithful enough stand-in."""
    return N(subentry_id=subentry_id, subentry_type="satellite", title=title, data=data or {}, unique_id=None)


class FakeIntentResponse:
    def __init__(self, language):
        self.language = language
        self.response_type = "action_done"
        self.speech = {}
        self.error_code = None

    def async_set_error(self, code, message):
        self.error_code = code
        self.speech["plain"] = {"speech": message}

    def async_set_speech(self, speech):
        self.speech["plain"] = {"speech": speech}


class FakeTargetResolver:
    async def resolve(self, target, *, domain_filter):
        return _executor_mod.TargetNotFound()

    async def resolve_area(self, target):
        return _executor_mod.TargetNotFound()


class FakeServiceCaller:
    async def set_light_power(self, entity_ids, on, *, context=None):
        return True

    async def get_entity_state(self, entity_id):
        return None

    async def clean_vacuum_area(self, area_id, *, context=None):
        return None

    async def skip_track(self, entity_ids, forward, *, context=None):
        return True


class FakeBackend:
    def __init__(self, hass, coordinator):
        self.hass = hass
        self.coordinator = coordinator

    async def converse(self, text, language, scope):
        return BackendResult(status="reply", response_text=f"backend saw: {text}")


class FakeAssistForwarder:
    """Stands in for spellbooker_ha.HaAssistForwarder - real Home
    Assistant conversation.async_converse is never touched by this test
    module, only the core.mcp_execution.AssistForwarder Protocol shape."""

    def __init__(self, hass):
        self.hass = hass
        self.calls = []

    async def forward(self, command_text, *, language, context):
        self.calls.append(command_text)
        return _mcp_execution_mod.AssistForwardOutcome(
            speech=None, is_error=False, is_query_answer=False, continue_conversation=False,
        )


def _load_conversation_module(**extra_adapters):
    async def passthrough_cleanup(_hass, command, _task_entity_id):
        return command, None, None

    adapters = dict(  # noqa: C408 - keyword mapping keeps the injected symbols readable.
        ConversationEntity=FakeConversationEntity,
        ConversationInput=object,
        HAConversationResult=lambda **kw: N(**kw),
        DeviceInfo=lambda **kw: N(**kw),
        AddEntitiesCallback=object,
        intent=N(
            IntentResponse=FakeIntentResponse,
            IntentResponseErrorCode=N(
                FAILED_TO_HANDLE="failed_to_handle", NO_VALID_TARGETS="no_valid_targets",
                NO_INTENT_MATCH="no_intent_match",
            ),
            IntentResponseType=N(ACTION_DONE="action_done", QUERY_ANSWER="query_answer"),
        ),
        ActionExecutor=ActionExecutor,
        EngineSpellbook=EngineSpellbook,
        AreaPolicy=AreaPolicy,
        ScopedAreaPolicy=ScopedAreaPolicy,
        ConversationRequest=ConversationRequest,
        ConversationStatus=ConversationStatus,
        ExecutionStatus=ExecutionStatus,
        FrontendMode=FrontendMode,
        DEFAULT_NO_MATCH_RESPONSE="Das kann ich mit diesem Spellbook nicht.",
        AgentConfig=AgentConfig,
        SpellbookRouter=SpellbookRouter,
        HaServiceCaller=lambda hass: FakeServiceCaller(),
        HaTargetResolver=lambda hass: FakeTargetResolver(),
        HaAssistForwarder=FakeAssistForwarder,
        record_execution=lambda *args, **kwargs: None,
        async_get_spellbook=None,  # overridden per-test below
        resolve_source_area_id=lambda hass, device_id: None,
        Code27ConversationBackend=FakeBackend,
        async_cleanup_command=passthrough_cleanup,
        # const.py values, verbatim - a relative import, stripped by
        # load_source just like everything else from this package.
        CONF_ENTRY_KIND="entry_kind",
        CONF_SPELLBOOKER_ALLOWED_AREAS="spellbooker_allowed_areas",
        CONF_SPELLBOOKER_BOOK="spellbooker_book",
        CONF_SPELLBOOKER_NO_MATCH_RESPONSE="spellbooker_no_match_response",
        CONF_SPELLBOOKER_PROFILE_ID="spellbooker_profile_id",
        CONF_SPELLBOOKER_UNRESTRICTED="spellbooker_unrestricted",
        CODIE_AREA_LIMITS_KEY="hase_codie_area_limits",
        CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS="spellbooker_queries_allowed_areas",
        CONF_SPELLBOOKER_QUERIES_UNRESTRICTED="spellbooker_queries_unrestricted",
        CONF_BRIDGE_AI_TASK_ENTITY_ID="bridge_ai_task_entity_id",
        DOMAIN="hase",
        ENTRY_KIND_SPELLBOOKER="spellbooker",
        SUBENTRY_TYPE_SATELLITE="satellite",
        MANUFACTURER="CODE27",
    )
    adapters.update(extra_adapters)
    return support.load_source("conversation", **adapters)


class OutputModeTests(unittest.TestCase):
    """Item 8: intent_response_for's output_mode mapping. Verified against
    HA's own intent.py source (no native TEXT-vs-SPEECH split, only
    whether async_set_speech was called) - AUTO/TEXT/SPEECH/TEXT_AND_SPEECH
    are indistinguishable at this layer and all keep today's behavior;
    NONE is the one representable case (empty speech, not skipped)."""

    def setUp(self):
        self.conversation = _load_conversation_module()

    def _completed(self, output_mode, execution_results=()):
        return ConversationResult(
            request_id="r1", response_text="Es sind 2 Lichter an.",
            status=ConversationStatus.COMPLETED, execution_results=execution_results,
            output_mode=output_mode,
        )

    def test_auto_speaks_the_response_text(self):
        response = self.conversation.intent_response_for("de", self._completed("AUTO"))
        self.assertEqual(response.speech["plain"]["speech"], "Es sind 2 Lichter an.")

    def test_text_and_speech_and_text_and_speech_all_speak_identically(self):
        for mode in ("TEXT", "SPEECH", "TEXT_AND_SPEECH"):
            with self.subTest(mode=mode):
                response = self.conversation.intent_response_for("de", self._completed(mode))
                self.assertEqual(response.speech["plain"]["speech"], "Es sind 2 Lichter an.")

    def test_none_sets_empty_speech_not_the_real_text(self):
        response = self.conversation.intent_response_for("de", self._completed("NONE"))
        self.assertEqual(response.speech["plain"]["speech"], "")

    def test_none_does_not_affect_error_status_text(self):
        result = ConversationResult(
            request_id="r1", response_text="Das ist mir hier nicht erlaubt.",
            status=ConversationStatus.REJECTED, output_mode="NONE",
        )
        response = self.conversation.intent_response_for("de", result)
        self.assertEqual(response.speech["plain"]["speech"], "Das ist mir hier nicht erlaubt.")

    def test_query_answer_response_type_still_set_regardless_of_output_mode(self):
        execution = ExecutionResult(ExecutionStatus.QUERY_ANSWER, "ok", {"response_text": "2"})
        response = self.conversation.intent_response_for("de", self._completed("NONE", (execution,)))
        self.assertEqual(response.response_type, "query_answer")


class SpellbookOnlyModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_spellbook_only_entry_gets_no_device_info_and_a_name(self):
        conversation = _load_conversation_module()
        entry = N(entry_id="e1", title="Gondor", data={"entry_kind": "spellbooker"}, options={})
        hass = N(data={}, config=N(language="de"))
        entity = conversation.SpellbookerConversationEntity(hass, entry)
        self.assertEqual(entity._attr_name, "Gondor")
        self.assertFalse(hasattr(entity, "_attr_device_info"))


class SetupEntryTests(unittest.IsolatedAsyncioTestCase):
    """async_setup_entry is what actually decides whether an entity
    gets built the SPELLBOOK_ONLY way (one entity for the entry
    itself) or once per SATELLITE subentry - and, for the latter, it's
    the config_subentry_id passed to async_add_entities that makes the
    entity's device render nested under this entry's own hub in the
    HA UI, not anything set on the entity itself."""

    async def test_spellbooker_entry_gets_exactly_one_entity_no_subentry_id(self):
        conversation = _load_conversation_module()
        entry = N(entry_id="e1", title="Gondor", data={"entry_kind": "spellbooker"}, options={}, subentries={})

        captured = []

        def add_entities(entities, config_subentry_id=None):
            captured.append((entities, config_subentry_id))

        await conversation.async_setup_entry(N(), entry, add_entities)
        self.assertEqual(len(captured), 1)
        entities, subentry_id = captured[0]
        self.assertEqual(len(entities), 1)
        self.assertIsNone(subentry_id)

    async def test_device_entry_with_no_satellite_subentries_adds_nothing(self):
        conversation = _load_conversation_module()
        entry = N(entry_id="dev-1", title="CODE27 Papa", data={"entry_kind": "code27_device"}, subentries={})

        captured = []
        await conversation.async_setup_entry(N(), entry, lambda *a, **kw: captured.append((a, kw)))
        self.assertEqual(captured, [])

    async def test_device_entry_gets_one_entity_per_satellite_subentry_with_its_own_subentry_id(self):
        conversation = _load_conversation_module()
        sub_a = _fake_subentry("sat-a", "Lilya Satellite")
        sub_b = _fake_subentry("sat-b", "Second Satellite")
        # A non-satellite subentry type must never turn into an entity.
        other = N(subentry_id="other-1", subentry_type="something_else", title="Ignore me", data={})
        entry = N(
            entry_id="dev-1", title="CODE27 Papa", data={"entry_kind": "code27_device"},
            subentries={sub_a.subentry_id: sub_a, sub_b.subentry_id: sub_b, other.subentry_id: other},
        )

        captured = []

        def add_entities(entities, config_subentry_id=None):
            captured.append((entities, config_subentry_id))

        await conversation.async_setup_entry(N(data={}), entry, add_entities)

        self.assertEqual(len(captured), 2)
        subentry_ids_used = {call[1] for call in captured}
        self.assertEqual(subentry_ids_used, {"sat-a", "sat-b"})
        for entities, _subentry_id in captured:
            self.assertEqual(len(entities), 1)


class ScopedPolicyMigrationTests(unittest.IsolatedAsyncioTestCase):
    """#11: an entry saved before the separate queries policy existed
    has no spellbooker_queries_allowed_areas/spellbooker_queries_
    unrestricted keys in its options at all - conversation.py must
    mirror the actions policy into queries for exactly that entry,
    never crash and never silently default queries to unrestricted or
    to nothing. An entry that HAS saved explicit queries-scope values
    must use them as-is, not mirror actions."""

    def _make_entity_and_capture_policy(self, options):
        conversation = _load_conversation_module(
            async_get_spellbook=AsyncStub(returns=_fake_book()),
        )
        hass = N(data={}, config=N(language="de"))
        entry = N(entry_id="e-migrate", title="Gondor", data={"entry_kind": "spellbooker"}, options=options)
        entity = conversation.SpellbookerConversationEntity(hass, entry)

        captured = {}
        real_handle = entity._router.handle

        async def spy_handle(request, config):
            captured["config"] = config
            return await real_handle(request, config)

        entity._router.handle = spy_handle
        return entity, captured

    async def test_missing_queries_keys_mirrors_the_actions_policy(self):
        entity, captured = self._make_entity_and_capture_policy({
            "spellbooker_book": "demo",
            "spellbooker_allowed_areas": ["buero"],
            "spellbooker_unrestricted": False,
            # no spellbooker_queries_* keys at all - pre-#11 entry.
        })
        user_input = N(
            text="nichts passt hier", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.gondor",
        )
        await entity._async_handle_message(user_input, N(conversation_id="c1"))

        policy = captured["config"].policy
        self.assertEqual(policy.actions.allowed_areas, frozenset({"buero"}))
        self.assertEqual(policy.queries.allowed_areas, frozenset({"buero"}))
        self.assertFalse(policy.queries.unrestricted)

    async def test_explicit_queries_keys_are_used_as_is_not_mirrored(self):
        entity, captured = self._make_entity_and_capture_policy({
            "spellbooker_book": "demo",
            "spellbooker_allowed_areas": ["buero"],
            "spellbooker_unrestricted": False,
            "spellbooker_queries_allowed_areas": [],
            "spellbooker_queries_unrestricted": True,
        })
        user_input = N(
            text="nichts passt hier", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.gondor",
        )
        await entity._async_handle_message(user_input, N(conversation_id="c2"))

        policy = captured["config"].policy
        self.assertEqual(policy.actions.allowed_areas, frozenset({"buero"}))
        self.assertFalse(policy.actions.unrestricted)
        self.assertTrue(policy.queries.unrestricted)




class SatelliteSessionIdentityTests(unittest.IsolatedAsyncioTestCase):
    """New finding (external audit): two SATELLITE subentries on the
    SAME parent CODE27 device entry, configured with the SAME
    profile_id, must still get distinct session identities -
    agent_entry_id used to be the shared PARENT entry_id for every
    satellite of that device, which is also what backend_scope's own
    backend_instance_id already is, making agent_entry_id carry zero
    per-satellite distinguishing power. The fix threads each
    satellite's own stable subentry_id into agent_entry_id instead,
    leaving backend_instance_id (still the parent, needed to reach the
    right coordinator) untouched."""

    def _make_entity(self, conversation, subentry_id, coordinator):
        hass = N(data={"hase": {"code27-1": coordinator}}, config=N(language="de"))
        backend_entry = N(entry_id="code27-1", title="CODE27 Papa", data={"entry_kind": "code27_device"})
        subentry = _fake_subentry(
            subentry_id, f"Satellite {subentry_id}",
            data={
                "spellbooker_profile_id": "1",  # same profile on purpose - the collision case
                "spellbooker_book": "demo",
                "spellbooker_unrestricted": True,
                "spellbooker_queries_unrestricted": True,
            },
        )
        return conversation.SpellbookerConversationEntity(hass, backend_entry, subentry=subentry)

    async def test_two_satellites_same_parent_same_profile_get_different_agent_entry_ids(self):
        conversation = _load_conversation_module(
            async_get_spellbook=AsyncStub(returns=_fake_book()),
        )
        coordinator = N(entry=N(entry_id="code27-1"))
        entity_a = self._make_entity(conversation, "sat-a", coordinator)
        entity_b = self._make_entity(conversation, "sat-b", coordinator)

        captured = {}

        async def make_spy(entity, key):
            real_handle = entity._router.handle

            async def spy_handle(request, config):
                captured[key] = request
                return await real_handle(request, config)

            entity._router.handle = spy_handle

        await make_spy(entity_a, "a")
        await make_spy(entity_b, "b")

        user_input = N(
            text="etwas unbekanntes", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.satx",
        )
        await entity_a._async_handle_message(user_input, N(conversation_id="shared-token"))
        await entity_b._async_handle_message(user_input, N(conversation_id="shared-token"))

        request_a, request_b = captured["a"], captured["b"]
        # Same backend instance (the shared parent) and same profile -
        # exactly the collision setup - but distinct agent identities.
        self.assertEqual(request_a.conversation_token, request_b.conversation_token)
        self.assertNotEqual(request_a.agent_entry_id, request_b.agent_entry_id)
        self.assertEqual(request_a.agent_entry_id, "sat-a")
        self.assertEqual(request_b.agent_entry_id, "sat-b")


class SatelliteModeWiringTests(unittest.IsolatedAsyncioTestCase):
    """A SATELLITE entity is constructed with the CODE27 device's own
    config_entry (never a separate top-level entry) plus its own
    ConfigSubentry - "which backend" is simply "whichever entry this
    was constructed with", never a separately stored/selected field."""

    def _make_entity(self, conversation, backend_entry_id="code27-1", coordinator=N(), subentry_data=None):  # noqa: B008 - shared read-only default stub, explicit None tests absence.
        hass = N(
            data={"hase": {backend_entry_id: coordinator}} if coordinator is not None else {"hase": {}},
            config=N(language="de"),
        )
        backend_entry = N(entry_id=backend_entry_id, title="CODE27 Papa", data={"entry_kind": "code27_device"})
        subentry = _fake_subentry(
            "sat-1", "Lilya Satellite",
            data=subentry_data or {
                "spellbooker_profile_id": "1",
                "spellbooker_book": "demo",
                "spellbooker_unrestricted": True,
                "spellbooker_queries_unrestricted": True,
            },
        )
        entity = conversation.SpellbookerConversationEntity(hass, backend_entry, subentry=subentry)
        return entity, hass, backend_entry

    async def test_satellite_entity_gets_a_device_identified_by_its_own_subentry_id(self):
        conversation = _load_conversation_module()
        entity, _hass, _entry = self._make_entity(conversation)
        self.assertIsNone(entity._attr_name)
        self.assertEqual(entity._attr_unique_id, "sat-1")
        self.assertEqual(entity._attr_device_info.identifiers, {("hase", "sat-1")})

    async def test_satellite_config_resolves_a_real_backend_when_coordinator_present(self):
        conversation = _load_conversation_module(
            async_get_spellbook=AsyncStub(returns=_fake_book()),
        )
        coordinator = N(entry=N(entry_id="code27-1"))
        entity, _hass, _entry = self._make_entity(conversation, coordinator=coordinator)

        captured = {}
        real_handle = entity._router.handle

        async def spy_handle(request, config):
            captured["config"] = config
            return await real_handle(request, config)

        entity._router.handle = spy_handle
        user_input = N(
            text="etwas unbekanntes", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.sat1",
        )
        chat_log = N(conversation_id="conv-1")
        await entity._async_handle_message(user_input, chat_log)

        config = captured["config"]
        self.assertEqual(config.mode, FrontendMode.SATELLITE)
        self.assertIsInstance(config.backend, FakeBackend)
        self.assertEqual(config.backend_instance_id, "code27-1")
        self.assertEqual(config.profile_id, "1")

    async def test_missing_backend_coordinator_leaves_backend_none_not_a_silent_downgrade(self):
        conversation = _load_conversation_module(
            async_get_spellbook=AsyncStub(returns=_fake_book()),
        )
        entity, _hass, _entry = self._make_entity(conversation, coordinator=None)

        captured = {}
        real_handle = entity._router.handle

        async def spy_handle(request, config):
            captured["config"] = config
            return await real_handle(request, config)

        entity._router.handle = spy_handle
        user_input = N(
            text="etwas", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.sat1",
        )
        chat_log = N(conversation_id="conv-2")
        await entity._async_handle_message(user_input, chat_log)

        config = captured["config"]
        self.assertEqual(config.mode, FrontendMode.SATELLITE)
        self.assertIsNone(config.backend)
        # The router treats this as a hard config error, not
        # SPELLBOOK_ONLY behavior - mode stayed SATELLITE above, and
        # the missing backend is caught downstream by the router
        # itself (ConversationStatus.ERROR), never silently ignored.


class SatelliteNoLocalBookTests(unittest.IsolatedAsyncioTestCase):
    """A SATELLITE subentry may be configured with no local spellbook
    at all (CONF_SPELLBOOKER_BOOK = "" / the "Keins" choice) - every
    utterance must then fall straight through to the CODE27 backend,
    never a hard "Kein Spellbook konfiguriert" error, since that error
    only makes sense for a SPELLBOOK_ONLY entry with nothing to fall
    back to."""

    def _make_entity(self, conversation, coordinator):
        hass = N(data={"hase": {"code27-1": coordinator}}, config=N(language="de"))
        backend_entry = N(entry_id="code27-1", title="CODE27 Papa", data={"entry_kind": "code27_device"})
        subentry = _fake_subentry(
            "sat-nobook", "Book-less Satellite",
            data={
                "spellbooker_profile_id": "1",
                "spellbooker_book": "",
                "spellbooker_unrestricted": True,
                "spellbooker_queries_unrestricted": True,
            },
        )
        entity = conversation.SpellbookerConversationEntity(hass, backend_entry, subentry=subentry)
        return entity, hass, backend_entry

    async def test_no_local_book_never_hard_errors_and_reaches_the_backend(self):
        conversation = _load_conversation_module(async_get_spellbook=None)
        coordinator = N(entry=N(entry_id="code27-1"))
        entity, _hass, _entry = self._make_entity(conversation, coordinator)

        user_input = N(
            text="irgendwas", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.sat_nobook",
        )
        chat_log = N(conversation_id="conv-nobook")
        result = await entity._async_handle_message(user_input, chat_log)

        # A hard "no spellbook" error would have response.error_code
        # set and never touch the backend at all - instead the empty
        # local book produces EngineNoMatch, which the router forwards
        # to FakeBackend.converse(), whose canned reply comes back as
        # the spoken response.
        self.assertIsNone(result.response.error_code)
        self.assertIn("backend saw: irgendwas", result.response.speech["plain"]["speech"])


class SatelliteBrokenBookFailsClosedTests(unittest.IsolatedAsyncioTestCase):
    """#13 (external audit): a SATELLITE with a CONCRETELY configured
    book that fails to load (deleted file, parse error, every command
    invalid - anything async_get_spellbook returns None for)
    must fail closed with a real conversation error - never silently
    treated the same as "no book configured", which would forward the
    turn to the CODE27 backend as if the misconfiguration didn't
    exist. This is a different case from SatelliteNoLocalBookTests
    above (book_id itself empty, a deliberate and valid choice)."""

    def _make_entity(self, conversation, coordinator):
        hass = N(data={"hase": {"code27-1": coordinator}}, config=N(language="de"))
        backend_entry = N(entry_id="code27-1", title="CODE27 Papa", data={"entry_kind": "code27_device"})
        subentry = _fake_subentry(
            "sat-brokenbook", "Broken-book Satellite",
            data={
                "spellbooker_profile_id": "1",
                "spellbooker_book": "deleted_book",
                "spellbooker_unrestricted": True,
                "spellbooker_queries_unrestricted": True,
            },
        )
        entity = conversation.SpellbookerConversationEntity(hass, backend_entry, subentry=subentry)
        return entity, hass, backend_entry

    async def test_configured_but_unloadable_book_fails_closed_never_reaches_backend(self):
        conversation = _load_conversation_module(
            # Simulates a deleted/broken book file: async_get_spellbook
            # returns None for a book_id that IS set, as spellbooker_ha.py's
            # real function does when the file no longer exists or fails to load.
            async_get_spellbook=AsyncStub(returns=None),
        )
        coordinator = N(entry=N(entry_id="code27-1"))
        entity, _hass, _entry = self._make_entity(conversation, coordinator)

        user_input = N(
            text="irgendwas", language="de", conversation_id=None, device_id=None,
            context=None, agent_id="conversation.sat_brokenbook",
        )
        chat_log = N(conversation_id="conv-brokenbook")
        result = await entity._async_handle_message(user_input, chat_log)

        self.assertEqual(result.response.error_code, "failed_to_handle")
        self.assertIn("deleted_book", result.response.speech["plain"]["speech"])


class AsyncStub:
    """Simple async-callable stub - patch.object(AsyncMock) territory,
    but load_source's exec()'d module namespace doesn't play nicely
    with unittest.mock patching of module-level names after the fact,
    so this is injected directly as the adapter instead."""

    def __init__(self, returns):
        self._returns = returns

    async def __call__(self, hass, book_id):
        return self._returns


def _fake_book():
    from hase_spellbook_core import spellbook_engine

    return spellbook_engine.load_spellbook_from_text(
        "demo",
        'name: Demo\ncommands:\n  - id: x\n    trigger: "nichts passt hier"\n    '
        'operation: light.set_power\n    target: {kind: all_allowed}\n    arguments: {"on": true}\n',
    )


if __name__ == "__main__":
    unittest.main()
