"""Etappe 2: the pure Spellbook Engine core. No HA needed - core/ has
zero homeassistant imports, so these are plain imports, not the
AST-stripping load_source() trick the legacy-module tests use.
"""
import asyncio
import importlib
import importlib.util
import itertools
import sys
import unittest
from pathlib import Path

_request_ids = itertools.count()


def _scoped(policy: "AreaPolicy") -> "ScopedAreaPolicy":
    """Most of this file's tests predate #11's action/query policy
    split and aren't testing that split - mirroring the same AreaPolicy
    into both scopes reproduces the exact pre-#11 single-policy
    behavior they were written against."""
    return ScopedAreaPolicy(actions=policy, queries=policy)


def _next_request_id() -> str:
    # Router.handle() now dedups by request_id (a retried delivery
    # must not re-execute) - tests that fire multiple DISTINCT turns
    # need distinct ids, same as any real caller, or the dedup cache
    # would collapse them into "the same request answered once".
    return f"test-req-{next(_request_ids)}"

HASE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hase"
DEMO_BOOK_PATH = HASE_ROOT / "spellbooks" / "demo.yaml"


def _load_core_package():
    """Load custom_components/hase/core/ as its own top-level package,
    under a collision-free name. NOT sys.path.insert(HASE_ROOT) -
    that directory also contains select.py (an HA entity platform),
    which would shadow the stdlib `select` module the moment anything
    (asyncio, in this file's own IsolatedAsyncioTestCase) imports it."""
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
matching = importlib.import_module("hase_spellbook_core.matching")
spellbook_engine = importlib.import_module("hase_spellbook_core.spellbook_engine")
_executor_mod = importlib.import_module("hase_spellbook_core.executor")
_models_mod = importlib.import_module("hase_spellbook_core.models")
_router_mod = importlib.import_module("hase_spellbook_core.router")
_session_mod = importlib.import_module("hase_spellbook_core.session")
_interaction_state_mod = importlib.import_module("hase_spellbook_core.interaction_state")

ActionExecutor = _executor_mod.ActionExecutor
ResolvedEntity = _executor_mod.ResolvedEntity
ResolvedTargets = _executor_mod.ResolvedTargets
TargetNotFound = _executor_mod.TargetNotFound
VacuumCleanOutcome = _executor_mod.VacuumCleanOutcome
BackendResult = _models_mod.BackendResult

_legacy_migration_mod = importlib.import_module("hase_spellbook_core.legacy_migration")
migrate_legacy_spellbook = _legacy_migration_mod.migrate_legacy_spellbook

_text_only_execution_mod = importlib.import_module("hase_spellbook_core.text_only_execution")
AssistForwardOutcome = _text_only_execution_mod.AssistForwardOutcome

ActionDraft = _models_mod.ActionDraft
ActionTarget = _models_mod.ActionTarget
AmbiguousOption = _models_mod.AmbiguousOption
AreaPolicy = _models_mod.AreaPolicy
ScopedAreaPolicy = _models_mod.ScopedAreaPolicy
ConversationRequest = _models_mod.ConversationRequest
ConversationStatus = _models_mod.ConversationStatus
EngineAmbiguous = _models_mod.EngineAmbiguous
EngineMatch = _models_mod.EngineMatch
EngineNoMatch = _models_mod.EngineNoMatch
ExecutionStatus = _models_mod.ExecutionStatus
FrontendMode = _models_mod.FrontendMode

AgentConfig = _router_mod.AgentConfig
SpellbookRouter = _router_mod.SpellbookRouter

compute_interaction_session = _session_mod.compute_interaction_session
local_scope = _session_mod.local_scope
backend_scope = _session_mod.backend_scope

InteractionStateStore = _interaction_state_mod.InteractionStateStore
DialogCapacityError = _interaction_state_mod.DialogCapacityError
IDLE_TTL_SECONDS = _interaction_state_mod.IDLE_TTL_SECONDS
MAX_OPEN_DIALOGS = _interaction_state_mod.MAX_OPEN_DIALOGS
SessionLockRegistry = _interaction_state_mod.SessionLockRegistry


class MatchingTests(unittest.TestCase):
    def test_tokenize_with_spans_strips_punctuation_keeps_offsets(self):
        tokens = matching.tokenize_with_spans("Licht Buero, bitte!")
        words = [w for w, _, _ in tokens]
        self.assertEqual(words, ["Licht", "Buero", "bitte"])
        word, start, end = tokens[1]
        self.assertEqual("Licht Buero, bitte!"[start:end], word)

    def test_compile_trigger_rejects_adjacent_placeholders(self):
        self.assertIsNone(matching.compile_trigger("{area} {state}"))

    def test_compile_trigger_splits_literal_and_placeholder(self):
        segments = matching.compile_trigger("licht {area} an")
        self.assertEqual(segments, [("lit", "licht"), ("ph", "area"), ("lit", "an")])

    def test_match_segments_captures_placeholder_between_literals(self):
        segments = matching.compile_trigger("licht {area} an")
        text = "licht Buero an"
        tokens = matching.tokenize_with_spans(text)
        result = matching.match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        self.assertEqual(result.captures, {"area": "Buero"})
        self.assertTrue(result.is_exact)

    def test_match_segments_fuzzy_typo_is_not_exact(self):
        segments = matching.compile_trigger("licht {area} an")
        text = "lich Buero an"  # missing a "t"
        tokens = matching.tokenize_with_spans(text)
        result = matching.match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        self.assertFalse(result.is_exact)

    def test_match_segments_rejects_unrelated_text(self):
        segments = matching.compile_trigger("licht {area} an")
        text = "komplett unrelated text"
        tokens = matching.tokenize_with_spans(text)
        self.assertIsNone(matching.match_segments(segments, tokens, text))

    def test_multiword_literal_does_not_fuzzy_match_on_shared_prefix_alone(self):
        # Found live while converting real spellbooks: a purely-literal
        # multi-word trigger ("...von mittelerde") used to be scored as
        # ONE blob against the whole phrase, so a long shared prefix
        # ("löscht die feuer von ") diluted a completely different
        # final word ("mittelerde" vs "gondor") into a passing ratio
        # (0.759, above the 0.75 threshold) even though no fuzzy
        # tolerance for THAT word was ever intended.
        segments = matching.compile_trigger("löscht die feuer von mittelerde")
        text = "löscht die feuer von Gondor"
        tokens = matching.tokenize_with_spans(text)
        self.assertIsNone(matching.match_segments(segments, tokens, text))

    def test_multiword_literal_still_tolerates_a_single_word_typo(self):
        # The fix must not regress ordinary typo tolerance - each word
        # is still fuzzy-matched, just independently instead of as one
        # blob with its neighbors.
        segments = matching.compile_trigger("löscht die feuer von mittelerde")
        text = "löscht die feur von mittelerde"  # "feuer" -> "feur"
        tokens = matching.tokenize_with_spans(text)
        result = matching.match_segments(segments, tokens, text)
        self.assertIsNotNone(result)
        self.assertFalse(result.is_exact)


DEMO_YAML = """
name: Demo
commands:
  - id: light_on
    trigger: "licht {area} an"
    operation: light.set_power
    target: {kind: area_name, capture: area}
    arguments: {"on": true}
    catchphrase: "Mach ich, {area}."
  - id: light_off
    trigger: "licht {area} aus"
    operation: light.set_power
    target: {kind: area_name, capture: area}
    arguments: {"on": false}
  - id: get_state
    trigger: "wie ist der zustand von {entity}"
    operation: entity.get_state
    target: {kind: entity_name, capture: entity}
"""


class SpellbookEngineTests(unittest.TestCase):
    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_text("demo", DEMO_YAML)

    def test_book_loads_all_valid_commands(self):
        self.assertEqual(len(self.book.commands), 3)

    def test_match_action_fills_target_and_arguments(self):
        result = spellbook_engine.match(self.book, "licht Buero an")
        self.assertIsInstance(result, EngineMatch)
        self.assertEqual(result.action_draft.operation, "light.set_power")
        self.assertEqual(result.action_draft.target, ActionTarget("area_name", "Buero"))
        self.assertEqual(result.action_draft.arguments, {"on": True})
        self.assertEqual(result.action_draft.catchphrase, "Mach ich, Buero.")

    def test_match_query_has_no_arguments(self):
        result = spellbook_engine.match(self.book, "wie ist der zustand von Kuechenlicht")
        self.assertIsInstance(result, EngineMatch)
        self.assertEqual(result.action_draft.operation, "entity.get_state")
        self.assertEqual(result.action_draft.target, ActionTarget("entity_name", "Kuechenlicht"))

    def test_no_match_for_unrelated_text(self):
        result = spellbook_engine.match(self.book, "das hat nichts damit zu tun")
        self.assertIsInstance(result, EngineNoMatch)

    def test_adjacent_placeholders_command_is_skipped_not_fatal(self):
        # Skipping the one bad command must not crash the parse itself
        # (that's the "not fatal" this test name refers to) - but a
        # book where EVERY command was rejected is not a legitimate
        # empty book either (see the fix's own note on
        # load_spellbook_from_dict) - it's a load failure, same as
        # unparseable YAML, and must propagate as None so callers can
        # tell "no commands were ever declared" apart from "every
        # declared command was invalid".
        book = spellbook_engine.load_spellbook_from_text(
            "bad",
            "name: Bad\ncommands:\n  - id: x\n    trigger: \"{a} {b}\"\n    "
            "operation: light.set_power\n    target: {kind: all_allowed}\n",
        )
        self.assertIsNone(book)

    def test_unknown_target_kind_command_is_skipped(self):
        book = spellbook_engine.load_spellbook_from_text(
            "bad",
            "name: Bad\ncommands:\n  - id: x\n    trigger: \"hallo\"\n    "
            "operation: light.set_power\n    target: {kind: nonsense}\n",
        )
        self.assertIsNone(book)

    def test_malformed_yaml_returns_none_not_an_exception(self):
        self.assertIsNone(spellbook_engine.load_spellbook_from_text("bad", "not: valid: yaml: :"))

    def test_a_book_with_at_least_one_valid_command_still_loads(self):
        # The "all commands invalid -> None" fix must not overreach:
        # a book with one bad command and one good one still loads,
        # with only the bad one skipped - that's the actual "skipped,
        # not fatal" behavior the tests above are named for.
        book = spellbook_engine.load_spellbook_from_text(
            "mixed",
            "name: Mixed\ncommands:\n"
            "  - id: bad\n    trigger: \"{a} {b}\"\n    operation: light.set_power\n"
            "    target: {kind: all_allowed}\n"
            "  - id: good\n    trigger: \"hallo\"\n    operation: light.set_power\n"
            "    target: {kind: all_allowed}\n    arguments: {on: true}\n",
        )
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 1)
        self.assertEqual(book.commands[0].spell_id, "good")

    def test_declared_empty_commands_list_is_a_legitimate_empty_book(self):
        # commands: [] from the start (no commands ever declared) is a
        # deliberate empty stub, not a load failure - must not be
        # confused with "commands were declared but all rejected".
        book = spellbook_engine.load_spellbook_from_text("empty", "name: Empty\ncommands: []\n")
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 0)

    def test_dangling_target_capture_reference_is_skipped_not_silently_dead(self):
        # "area" isn't a placeholder in this trigger - without load-time
        # validation this command would load fine and then silently
        # never match anything (its target always resolves to None).
        # The single-bad-command book this constructs now fails to
        # load entirely instead (see the fix's own note above) -
        # still never a silently-dead command either way.
        book = spellbook_engine.load_spellbook_from_text(
            "bad",
            "name: Bad\ncommands:\n  - id: x\n    trigger: \"mach {ort} an\"\n    "
            "operation: light.set_power\n    target: {kind: area_name, capture: area}\n    "
            "arguments: {on: true}\n",
        )
        self.assertIsNone(book)

    def test_target_with_neither_capture_nor_value_is_skipped_not_silently_dead(self):
        # Second-round audit: {kind: area_name} with NEITHER a capture
        # NOR a value used to load fine and then silently never match
        # anything, same class of problem as the dangling-capture case
        # above but for a target missing entirely instead of pointing
        # at a typo'd placeholder name.
        book = spellbook_engine.load_spellbook_from_text(
            "bad",
            "name: Bad\ncommands:\n  - id: x\n    trigger: \"hallo {area}\"\n    "
            "operation: light.set_power\n    target: {kind: area_name}\n    "
            "arguments: {on: true}\n",
        )
        self.assertIsNone(book)

    def test_malformed_boolean_true_values_is_skipped_not_a_crash(self):
        # YAML parses bare `true`/`false` as Python bools, not strings -
        # an author writing true_values: [true] instead of a spoken
        # word list must be rejected at load time, not crash
        # _resolve_arguments the first time this trigger is spoken.
        book = spellbook_engine.load_spellbook_from_text(
            "bad",
            "name: Bad\ncommands:\n  - id: x\n    trigger: \"schalte {state}\"\n    "
            "operation: light.set_power\n    target: {kind: all_allowed}\n    "
            "arguments: {on: {capture: state, true_values: [true], false_values: [aus]}}\n",
        )
        self.assertIsNone(book)

    def test_shipped_demo_book_loads_and_matches(self):
        book = spellbook_engine.load_spellbook_from_path(DEMO_BOOK_PATH)
        self.assertIsNotNone(book)
        self.assertGreaterEqual(len(book.commands), 2)
        result = spellbook_engine.match(book, "licht Buero an")
        self.assertIsInstance(result, EngineMatch)

    def test_middle_earth_area_scoped_off_does_not_hit_the_whole_house_spell(self):
        # Regression for the multi-word-literal fuzzy-match bug found
        # while converting this exact book: "löscht die feuer von
        # {area}" (area-scoped) and "löscht die feuer von mittelerde"
        # (whole-house, higher specificity tier) share a long literal
        # prefix - before the matching.py fix, an area name here could
        # fuzzy-match against "mittelerde" and silently turn off every
        # light in the house instead of just the named area.
        book = spellbook_engine.load_spellbook_from_path(
            HASE_ROOT / "spellbooks" / "middle_earth.yaml"
        )
        self.assertIsNotNone(book)
        result = spellbook_engine.match(book, "löscht die feuer von Gondor", "de")
        self.assertIsInstance(result, EngineMatch)
        self.assertEqual(result.action_draft.target, ActionTarget("area_name", "Gondor"))
        self.assertEqual(result.action_draft.arguments, {"on": False})

    def test_extra_trailing_word_never_falls_back_to_the_whole_house_spell(self):
        # Second, distinct regression found via a second-round audit:
        # match_segments used to accept a match that only covered a
        # PREFIX of the utterance, silently dropping trailing tokens
        # nothing in the trigger accounted for. "lumos maxima Küche"
        # matched the area-less "lumos maxima" (whole-house) trigger on
        # its first two words alone, dropping "Küche" on the floor -
        # turning on every light in the house instead of failing safely
        # on what should have been recognized as an unsupported
        # per-area brightness command.
        book = spellbook_engine.load_spellbook_from_path(
            HASE_ROOT / "spellbooks" / "potter_verse.yaml"
        )
        self.assertIsNotNone(book)
        result = spellbook_engine.match(book, "lumos maxima Küche", "de")
        if isinstance(result, EngineMatch) and result.action_draft is not None:
            # Falling through to a looser tier (e.g. "lumos {area}"
            # capturing "maxima Küche" as a garbage area name, or - since
            # match() now also ranks text-only commands, see this
            # module's own OutputOnlyCommandTests - a text-only Spell
            # like potter_verse.yaml's "lumos maxima {area}" winning
            # instead) is acceptable - either fails safely (an unresolvable
            # target, or a garbage-area phrase forwarded to Assist)
            # without ever touching a device. What must never happen is
            # the whole-house action winning.
            self.assertNotEqual(result.action_draft.target, ActionTarget("all_allowed", None))


class OutputOnlyCommandTests(unittest.TestCase):
    """Etappe 7: a command with `output` instead of/alongside `operation`
    - the unified-schema field that lets a single spellbook file also
    serve CODE27's bridge (free text forwarded to Assist), which this
    engine's own build_draft/build_query_plan/build_step_plan never wrap
    into an ActionDraft/QueryPlan/StepPlan. See EngineCommand's own
    docstring."""

    def test_output_only_command_loads_successfully(self):
        book = spellbook_engine.load_spellbook_from_text(
            "b",
            "name: B\ncommands:\n  - id: x\n    trigger: \"mach das licht aus\"\n    "
            "output: \"Licht aus\"\n",
        )
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 1)
        self.assertIsNone(book.commands[0].operation)
        self.assertEqual(book.commands[0].output, "Licht aus")

    def test_output_only_command_produces_a_text_only_match_never_an_action_draft(self):
        # Was: "an output-only command must be invisible to match()".
        # That was the real gap this session's live voice trace
        # (docs/HASE-MCP.md's own "Text-only Spells and assist_redirect"
        # section) empirically proved and fixed: match() now ranks
        # EVERY execution kind, exactly like find_command() already does
        # for every other frontend - a text-only trigger reaches
        # core.router.SpellbookRouter as a real EngineMatch, dispatched
        # via command/captures (core.text_only_execution.
        # execute_text_only_command, the same neutral function MCP also
        # calls directly), never NO_MATCH and never silently wrapped
        # into a fake ActionDraft/QueryPlan/StepPlan either.
        book = spellbook_engine.load_spellbook_from_text(
            "b",
            "name: B\ncommands:\n  - id: x\n    trigger: \"mach das licht aus\"\n    "
            "output: \"Licht aus\"\n",
        )
        result = spellbook_engine.match(book, "mach das licht aus", "de")
        self.assertIsInstance(result, EngineMatch)
        self.assertIsNone(result.action_draft)
        self.assertIsNone(result.query_plan)
        self.assertIsNone(result.step_plan)
        self.assertEqual(result.spell_id, "x")
        self.assertIsNotNone(result.command)
        self.assertEqual(result.command.spell_id, "x")
        self.assertEqual(result.captures, {})

    def test_command_with_neither_operation_nor_output_is_skipped(self):
        book = spellbook_engine.load_spellbook_from_text(
            "mixed",
            "name: Mixed\ncommands:\n"
            "  - id: bad\n    trigger: \"hallo\"\n"
            "  - id: good\n    trigger: \"hallo wieder\"\n    operation: light.set_power\n"
            "    target: {kind: all_allowed}\n    arguments: {on: true}\n",
        )
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 1)
        self.assertEqual(book.commands[0].spell_id, "good")

    def test_book_of_only_output_commands_still_loads(self):
        # The "every command invalid -> load failure" rule must not
        # misfire here: every command in this book loaded successfully
        # (as output-only), so this is a legitimate book, not a broken
        # one - unlike test_adjacent_placeholders_command_is_skipped_not_fatal's
        # single bad command, nothing here was actually rejected.
        book = spellbook_engine.load_spellbook_from_text(
            "b",
            "name: B\ncommands:\n"
            "  - id: a\n    trigger: \"eins\"\n    output: \"one\"\n"
            "  - id: b\n    trigger: \"zwei\"\n    output: \"two\"\n",
        )
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 2)

    def test_invalid_output_field_is_skipped(self):
        book = spellbook_engine.load_spellbook_from_text(
            "mixed",
            "name: Mixed\ncommands:\n"
            "  - id: bad\n    trigger: \"hallo\"\n    output: 5\n"
            "  - id: good\n    trigger: \"hallo wieder\"\n    output: \"hi\"\n",
        )
        self.assertIsNotNone(book)
        self.assertEqual(len(book.commands), 1)
        self.assertEqual(book.commands[0].spell_id, "good")

    def test_operation_and_output_can_coexist_on_one_command(self):
        # Not required by the schema, but not forbidden either - a book
        # author documenting a human-readable fallback description
        # alongside a typed action must not be rejected.
        book = spellbook_engine.load_spellbook_from_text(
            "b",
            "name: B\ncommands:\n  - id: x\n    trigger: \"licht an\"\n    "
            "operation: light.set_power\n    target: {kind: all_allowed}\n    "
            "arguments: {on: true}\n    output: \"Alle Lichter an\"\n",
        )
        self.assertIsNotNone(book)
        command = book.commands[0]
        self.assertEqual(command.operation, "light.set_power")
        self.assertEqual(command.output, "Alle Lichter an")
        result = spellbook_engine.match(book, "licht an", "de")
        self.assertIsInstance(result, EngineMatch)


class FakeResolver:
    def __init__(self, table: dict[str, ResolvedTargets], areas: dict[str, str] | None = None):
        self._table = table
        self._areas = areas or {}

    async def resolve(self, target, *, domain_filter):
        return self._table.get(target.value, TargetNotFound())

    async def resolve_area(self, target):
        return self._areas.get(target.value, TargetNotFound())


class FakeCaller:
    def __init__(self, *, accept=True, state=None, vacuum_outcome=None):
        self._accept = accept
        self._state = state
        self._vacuum_outcome = vacuum_outcome
        self.calls: list[tuple] = []
        self.vacuum_calls: list[str] = []
        self.contexts: list = []

    async def set_light_power(self, entity_ids, on, *, context=None):
        self.calls.append((entity_ids, on))
        self.contexts.append(context)
        return self._accept

    async def get_entity_state(self, entity_id):
        return self._state

    async def clean_vacuum_area(self, area_id, *, context=None):
        self.vacuum_calls.append(area_id)
        self.contexts.append(context)
        return self._vacuum_outcome

    async def skip_track(self, entity_ids, forward, *, context=None):
        self.calls.append((entity_ids, forward))
        self.contexts.append(context)
        return self._accept


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolver = FakeResolver(
            {
                "Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),)),
                "Garten": ResolvedTargets((ResolvedEntity("light.garten", "garten"),)),
                "Flur": ResolvedTargets((ResolvedEntity("light.flur", None),)),
            }
        )
        self.caller = FakeCaller()
        self.executor = ActionExecutor(self.resolver, self.caller)

    async def test_allowed_area_executes_and_calls_service(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.caller.calls, [(("light.buero",), True)])

    async def test_media_player_skip_track_forward_and_back(self):
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("media_player.buero", "buero"),))})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))

        forward = ActionDraft(
            "media_player.skip_track", ActionTarget("area_name", "Buero"), {"forward": True}
        )
        result = await executor.execute(forward, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

        back = ActionDraft(
            "media_player.skip_track", ActionTarget("area_name", "Buero"), {"forward": False}
        )
        result = await executor.execute(back, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

        self.assertEqual(caller.calls, [(("media_player.buero",), True), (("media_player.buero",), False)])

    async def test_media_player_skip_track_respects_area_policy(self):
        resolver = FakeResolver({"Garten": ResolvedTargets((ResolvedEntity("media_player.garten", "garten"),))})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))  # garten NOT allowed
        draft = ActionDraft(
            "media_player.skip_track", ActionTarget("area_name", "Garten"), {"forward": True}
        )
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(caller.calls, [])

    async def test_forbidden_area_is_denied_and_never_calls_service(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Garten"), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(self.caller.calls, [])

    async def test_area_less_entity_denied_under_restricted_policy(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Flur"), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.DENIED)

    async def test_unrestricted_policy_permits_any_area(self):
        policy = AreaPolicy(allowed_areas=None, unrestricted=True)
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Garten"), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

    async def test_unknown_operation_is_unsupported_not_a_crash(self):
        policy = AreaPolicy(allowed_areas=frozenset())
        draft = ActionDraft("vacuum.clean", ActionTarget("area_name", "Buero"), {})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.UNSUPPORTED)

    async def test_target_kind_not_allowed_for_operation_is_invalid(self):
        policy = AreaPolicy(allowed_areas=frozenset(), unrestricted=True)
        draft = ActionDraft("entity.get_state", ActionTarget("area_name", "Buero"), {})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "target_kind_not_allowed_for_operation")

    async def test_wrong_argument_type_is_invalid(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": 1})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")

    async def test_missing_argument_is_invalid(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)

    async def test_unknown_target_name_is_invalid_not_denied(self):
        policy = AreaPolicy(allowed_areas=frozenset(), unrestricted=True)
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Nirgendwo"), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "target_not_found")

    async def test_non_string_target_value_is_rejected_not_a_crash(self):
        policy = AreaPolicy(allowed_areas=frozenset(), unrestricted=True)
        draft = ActionDraft("light.set_power", ActionTarget("area_name", ["not", "a", "string"]), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "malformed_target")
        self.assertEqual(self.caller.calls, [])

    async def test_empty_string_target_value_is_rejected_at_validate_shape(self):
        draft = ActionDraft("light.set_power", ActionTarget("area_name", ""), {"on": True})
        self.assertFalse(self.executor.validate_shape(draft))

    async def test_valid_target_shape_passes(self):
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        self.assertTrue(self.executor.validate_shape(draft))
        all_allowed_draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), {"on": True})
        self.assertTrue(self.executor.validate_shape(all_allowed_draft))

    async def test_non_dict_arguments_are_rejected_not_a_crash(self):
        # Found live (external audit): a backend proposal's ActionDraft
        # is built with no validation of its own - arguments=None
        # crashed set(arguments) with TypeError, arguments=["on"]
        # crashed arguments[name] (a string key into a list) with a
        # different TypeError. Both must fail closed at validate_shape,
        # the whole reason it exists for a batch of untrusted proposals.
        none_args_draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), None)
        self.assertFalse(self.executor.validate_shape(none_args_draft))
        list_args_draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), ["on"])
        self.assertFalse(self.executor.validate_shape(list_args_draft))

        result = await self.executor.execute(none_args_draft, _scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True)))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")
        result = await self.executor.execute(list_args_draft, _scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True)))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "bad_arguments")
        self.assertEqual(self.caller.calls, [])

    async def test_none_proposal_is_rejected_at_validate_shape_not_a_crash(self):
        # Found live (external audit): validate_shape(None) crashed
        # with AttributeError on draft.operation - the proposal object
        # itself was never checked, only its fields. A batch check
        # (`all(validate_shape(d) for d in proposals)` in router.py)
        # must be able to reject a None entry as cleanly as any other
        # malformed one, never raise out of the generator expression.
        self.assertFalse(self.executor.validate_shape(None))
        self.assertFalse(self.executor.validate_shape("not a draft either"))

    async def test_query_returns_query_answer_status(self):
        caller = FakeCaller(state={"state": "on", "unit": None})
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset(), unrestricted=True)
        draft = ActionDraft("entity.get_state", ActionTarget("entity_name", "Buero"), {})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(result.data, {"state": "on", "unit": None})

    async def test_query_response_template_uses_live_sensor_and_area_values_in_dry_run(self):
        caller = FakeCaller(state={
            "state": "22",
            "unit": "°C",
            "entity_name": "Wohnzimmer Temperatur",
            "entity_id": "sensor.wohnzimmer_temperatur",
            "area": "Wohnzimmer",
        })
        executor = ActionExecutor(self.resolver, caller)
        draft = ActionDraft(
            "entity.get_state",
            ActionTarget("entity_name", "Buero"),
            {},
            response_template="Im {area} sind es aktuell {state} {unit}.",
        )
        result = await executor.execute(
            draft,
            _scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True)),
            dry_run=True,
        )
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(
            result.data["response_text"],
            "Im Wohnzimmer sind es aktuell 22 °C.",
        )

    async def test_query_response_template_supports_light_values(self):
        caller = FakeCaller(state={
            "state": "on",
            "entity_name": "Stehlampe",
            "entity_id": "light.stehlampe",
            "area": "Wohnzimmer",
            "brightness": 128,
            "brightness_percent": 50,
            "color_temp_kelvin": 3000,
            "rgb_color": "255, 180, 90",
            "effect": "fireplace",
        })
        executor = ActionExecutor(self.resolver, caller)
        draft = ActionDraft(
            "entity.get_state",
            ActionTarget("entity_name", "Buero"),
            {},
            response_template=(
                "{entity_name} im {area}: {state}, {brightness_percent} Prozent, "
                "{color_temp_kelvin} Kelvin, RGB {rgb_color}, Effekt {effect}."
            ),
        )
        result = await executor.execute(
            draft,
            _scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True)),
        )
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)
        self.assertEqual(
            result.data["response_text"],
            "Stehlampe im Wohnzimmer: on, 50 Prozent, 3000 Kelvin, "
            "RGB 255, 180, 90, Effekt fireplace.",
        )

    async def test_rejected_service_call_is_failed_not_success(self):
        caller = FakeCaller(accept=False)
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.FAILED)

    async def test_context_is_forwarded_to_the_service_caller(self):
        # Whatever HA Context the frontend attached to this turn must
        # reach the actual service call, so Logbook/History/automation
        # traces attribute it correctly instead of showing "no context".
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        sentinel_context = object()
        await self.executor.execute(draft, _scoped(policy), context=sentinel_context)
        self.assertEqual(self.caller.contexts, [sentinel_context])


class AreaPolicyTests(unittest.TestCase):
    def test_missing_allowed_areas_without_unrestricted_is_rejected(self):
        with self.assertRaises(ValueError):
            AreaPolicy(allowed_areas=None)

    def test_empty_allowed_areas_means_no_areas(self):
        policy = AreaPolicy(allowed_areas=frozenset())
        self.assertFalse(policy.permits_area("buero"))

    def test_unrestricted_true_permits_without_a_list(self):
        policy = AreaPolicy(allowed_areas=None, unrestricted=True)
        self.assertTrue(policy.permits_area("anything"))


class ScopedAreaPolicyExecutorTests(unittest.IsolatedAsyncioTestCase):
    """#11: actions and queries each get their own independently
    configured AreaPolicy - the executor must pick whichever applies
    based on the operation catalog's own action/query classification,
    never a single shared policy for both."""

    def setUp(self):
        self.resolver = FakeResolver(
            {
                "Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),)),
                "Zwergstadt": ResolvedTargets((ResolvedEntity("sensor.zwergstadt", "zwergstadt"),)),
            }
        )
        self.caller = FakeCaller(state={"state": "on", "unit": None})
        self.executor = ActionExecutor(self.resolver, self.caller)

    async def test_action_denied_in_an_area_the_query_policy_still_allows(self):
        policies = ScopedAreaPolicy(
            actions=AreaPolicy(allowed_areas=frozenset()),  # no areas for actions
            queries=AreaPolicy(allowed_areas=frozenset({"buero"})),
        )
        draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        result = await self.executor.execute(draft, policies)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(self.caller.calls, [])

    async def test_query_denied_in_an_area_the_action_policy_still_allows(self):
        policies = ScopedAreaPolicy(
            actions=AreaPolicy(allowed_areas=frozenset({"buero"})),
            queries=AreaPolicy(allowed_areas=frozenset()),  # no areas for queries
        )
        draft = ActionDraft("entity.get_state", ActionTarget("entity_name", "Buero"), {})
        result = await self.executor.execute(draft, policies)
        self.assertEqual(result.status, ExecutionStatus.DENIED)

    async def test_query_permitted_by_its_own_policy_even_when_actions_are_unrestricted_elsewhere(self):
        policies = ScopedAreaPolicy(
            actions=AreaPolicy(allowed_areas=frozenset()),
            queries=AreaPolicy(allowed_areas=frozenset({"buero"})),
        )
        draft = ActionDraft("entity.get_state", ActionTarget("entity_name", "Buero"), {})
        result = await self.executor.execute(draft, policies)
        self.assertEqual(result.status, ExecutionStatus.QUERY_ANSWER)

    async def test_vacuum_clean_area_uses_the_actions_policy_not_the_queries_policy(self):
        resolver = FakeResolver({}, areas={"Zwergstadt": "zwergstadt"})
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome(status="started", vacuum_entity_id="vacuum.x"))
        executor = ActionExecutor(resolver, caller)
        policies = ScopedAreaPolicy(
            actions=AreaPolicy(allowed_areas=frozenset({"zwergstadt"})),
            queries=AreaPolicy(allowed_areas=frozenset()),  # would deny if wrongly used
        )
        draft = ActionDraft("vacuum.clean_area", ActionTarget("area_name", "Zwergstadt"), {})
        result = await executor.execute(draft, policies)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_text("demo", DEMO_YAML)
        self.resolver = FakeResolver(
            {"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))}
        )
        self.router = SpellbookRouter(ActionExecutor(self.resolver, FakeCaller()))

    def _request(self, text):
        return ConversationRequest(
            request_id=_next_request_id(),
            agent_entry_id="e1",
            text=text,
            language="de",
            source_namespace="test",
        )

    async def test_match_and_allowed_completes_with_catchphrase(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
        )
        result = await self.router.handle(self._request("licht Buero an"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Mach ich, Buero.")
        self.assertEqual(len(result.execution_results), 1)

    async def test_no_match_returns_configured_deterministic_response(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset())),
            no_match_response="Das kenne ich nicht.",
        )
        result = await self.router.handle(self._request("random gibberish"), config)
        self.assertEqual(result.status, ConversationStatus.NO_MATCH)
        self.assertEqual(result.response_text, "Das kenne ich nicht.")
        self.assertEqual(result.execution_results, ())

    async def test_policy_denial_is_rejected_status_deterministic_text(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset())),  # Buero not permitted
        )
        result = await self.router.handle(self._request("licht Buero an"), config)
        self.assertEqual(result.status, ConversationStatus.REJECTED)

    async def test_satellite_mode_is_refused_not_silently_handled(self):
        config = AgentConfig(
            mode=FrontendMode.SATELLITE,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
        )
        result = await self.router.handle(self._request("licht Buero an"), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)

    async def test_query_answer_speaks_the_state_not_a_catchphrase(self):
        # A catchphrase is "what to say after DOING something" - a
        # query has nothing to do, so a catchphrase configured on it
        # (however unusual) must never swallow the actual answer.
        book = spellbook_engine.load_spellbook_from_text(
            "q",
            "name: Q\ncommands:\n  - id: temp\n    trigger: \"wie warm ist es in {area}\"\n    "
            "operation: entity.get_state\n    target: {kind: entity_name, capture: area}\n    "
            "catchphrase: \"Hier ist die Antwort.\"\n",
        )
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("sensor.buero_temp", "buero"),))})
        caller = FakeCaller(state={"state": "21.5", "unit": "°C"})
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"})))
        )
        result = await router.handle(self._request("wie warm ist es in Buero"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "21.5°C")

    async def test_query_answer_supports_configured_live_return_values(self):
        book = spellbook_engine.load_spellbook_from_text(
            "q",
            "name: Q\ncommands:\n  - id: temp\n    trigger: \"wie warm ist es in {area}\"\n    "
            "operation: entity.get_state\n    target: {kind: entity_id, value: sensor.wohnzimmer}\n    "
            "catchphrase: \"Im {area} sind es aktuell {state} {unit}.\"\n",
        )
        self.assertIsNotNone(book)
        resolver = FakeResolver({
            "sensor.wohnzimmer": ResolvedTargets((ResolvedEntity("sensor.wohnzimmer", "wohnzimmer"),))
        })
        caller = FakeCaller(state={
            "state": "22", "unit": "°C", "entity_name": "Temperatur",
            "entity_id": "sensor.wohnzimmer", "area": "Wohnzimmer",
        })
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"wohnzimmer"}))),
        )
        result = await router.handle(self._request("wie warm ist es in Wohnzimmer"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Im Wohnzimmer sind es aktuell 22 °C.")

    async def test_duplicate_request_id_is_not_executed_twice(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
        )
        request = ConversationRequest(
            request_id="retry-1", agent_entry_id="e1", text="licht Buero an",
            language="de", source_namespace="test",
        )
        r1 = await self.router.handle(request, config)
        r2 = await self.router.handle(request, config)
        self.assertEqual(r1, r2)
        caller = self.router._executor._caller
        # Only one real service call happened, even though handle()
        # was invoked twice with the same request_id - a retried
        # delivery must not toggle the light twice.
        self.assertEqual(len(caller.calls), 1)

    async def test_truly_concurrent_duplicate_request_id_still_executes_once(self):
        # The sequential test above (await one call, then the other)
        # never exercises the actual race: a plain check-then-store
        # cache has a real gap between "look up" and "record the
        # result" that two calls in flight AT THE SAME TIME can both
        # slip through, provided the callee actually awaits something
        # (any real HA service call does) - confirmed live. A fresh
        # caller with a genuine suspension point in it is required to
        # reproduce that; the shared self.caller's calls list from
        # setUp is reset here so this test's count isn't polluted by
        # anything the class fixture already did with it.
        class SuspendingCaller(FakeCaller):
            async def set_light_power(self, entity_ids, on, *, context=None):
                await asyncio.sleep(0)
                return await super().set_light_power(entity_ids, on, context=context)

        caller = SuspendingCaller()
        router = SpellbookRouter(ActionExecutor(self.resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
        )
        request = ConversationRequest(
            request_id="retry-concurrent", agent_entry_id="e1", text="licht Buero an",
            language="de", source_namespace="test",
        )
        r1, r2 = await asyncio.gather(router.handle(request, config), router.handle(request, config))
        self.assertEqual(r1, r2)
        self.assertEqual(len(caller.calls), 1)

    async def test_cancelling_a_waiting_duplicate_does_not_corrupt_the_original(self):
        # Found live (external audit), no physical concurrency needed
        # to reproduce: a duplicate request_id awaiting the ORIGINAL's
        # shared future must never be able to cancel that future out
        # from under the original just by having ITS OWN caller cancel
        # it (a timeout, an upstream request being torn down, ...).
        # A bare `await existing` does exactly that - asyncio.Task
        # cancellation cancels whatever the task is currently awaiting,
        # and a Future being awaited by two different tasks is the
        # SAME object either way. The original, still mid-execution,
        # would then crash with InvalidStateError calling
        # future.set_result() on an already-cancelled future - after
        # the underlying action (the real service call) already ran.
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowCaller(FakeCaller):
            async def set_light_power(self, entity_ids, on, *, context=None):
                entered.set()
                await release.wait()
                return await super().set_light_power(entity_ids, on, context=context)

        caller = SlowCaller()
        router = SpellbookRouter(ActionExecutor(self.resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
        )
        request = ConversationRequest(
            request_id="cancel-dup", agent_entry_id="e1", text="licht Buero an",
            language="de", source_namespace="test",
        )

        original_task = asyncio.ensure_future(router.handle(request, config))
        await entered.wait()  # original holds the shared future, is mid-service-call

        duplicate_task = asyncio.ensure_future(router.handle(request, config))
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # let the duplicate actually reach the shared-future await

        duplicate_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await duplicate_task

        release.set()  # let the original's service call finish
        result = await original_task  # must not raise InvalidStateError
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(len(caller.calls), 1)


AMBIGUOUS_YAML = """
name: Ambiguity Test
commands:
  - id: activate
    trigger: "aktiviere licht {area}"
    operation: light.set_power
    target: {kind: area_name, capture: area}
    arguments: {"on": true}
    catchphrase: "Aktiviert."
  - id: deactivate
    trigger: "deaktiviere licht {area}"
    operation: light.set_power
    target: {kind: area_name, capture: area}
    arguments: {"on": false}
    catchphrase: "Deaktiviert."
"""


class AmbiguityDetectionTests(unittest.TestCase):
    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_text("t", AMBIGUOUS_YAML)

    def test_near_tied_opposite_candidates_are_ambiguous(self):
        # "daktiviere" sits almost exactly between "aktiviere" and
        # "deaktiviere" in fuzzy-ratio terms (verified empirically).
        result = spellbook_engine.match(self.book, "daktiviere licht Buero")
        self.assertIsInstance(result, EngineAmbiguous)

    def test_ambiguous_options_show_distinct_spoken_triggers(self):
        result = spellbook_engine.match(self.book, "daktiviere licht Buero")
        self.assertNotEqual(result.option_a.spoken_trigger, result.option_b.spoken_trigger)
        triggers = {result.option_a.spoken_trigger, result.option_b.spoken_trigger}
        self.assertEqual(triggers, {"aktiviere licht Buero", "deaktiviere licht Buero"})

    def test_clear_winner_is_not_ambiguous(self):
        result = spellbook_engine.match(self.book, "aktiviere licht Buero")
        self.assertIsInstance(result, EngineMatch)
        self.assertEqual(result.action_draft.arguments, {"on": True})

    def test_near_tie_without_opposite_outcome_is_not_ambiguous(self):
        # Same wording collision, but arguments agree - not a real
        # "which one did you mean" situation.
        book = spellbook_engine.load_spellbook_from_text(
            "t2",
            'name: T\ncommands:\n'
            '  - id: a\n    trigger: "aktiviere licht {area}"\n    operation: light.set_power\n'
            '    target: {kind: area_name, capture: area}\n    arguments: {"on": true}\n'
            '  - id: b\n    trigger: "aktiviere lampe {area}"\n    operation: light.set_power\n'
            '    target: {kind: area_name, capture: area}\n    arguments: {"on": true}\n',
        )
        result = spellbook_engine.match(book, "aktiviere licht Buero")
        self.assertIsInstance(result, EngineMatch)


class SessionTests(unittest.TestCase):
    def test_local_scope_is_keyed_by_agent_entry_not_shared(self):
        a = local_scope("entry-1")
        b = local_scope("entry-2")
        self.assertNotEqual(a, b)

    def test_two_backend_instances_with_same_profile_dont_collide(self):
        a = backend_scope("code27-device-A", "lilya")
        b = backend_scope("code27-device-B", "lilya")
        self.assertNotEqual(a, b)

    def test_same_scope_and_token_is_the_same_session(self):
        scope = local_scope("entry-1")
        s1 = compute_interaction_session(
            agent_entry_id="entry-1", scope=scope, source_namespace="ha", conversation_token="abc"
        )
        s2 = compute_interaction_session(
            agent_entry_id="entry-1", scope=scope, source_namespace="ha", conversation_token="abc"
        )
        self.assertEqual(s1, s2)
        self.assertEqual(hash(s1), hash(s2))

    def test_different_conversation_tokens_are_different_sessions(self):
        scope = local_scope("entry-1")
        s1 = compute_interaction_session(
            agent_entry_id="entry-1", scope=scope, source_namespace="ha", conversation_token="abc"
        )
        s2 = compute_interaction_session(
            agent_entry_id="entry-1", scope=scope, source_namespace="ha", conversation_token="xyz"
        )
        self.assertNotEqual(s1, s2)

    def test_missing_token_still_produces_a_usable_session(self):
        scope = local_scope("entry-1")
        session = compute_interaction_session(
            agent_entry_id="entry-1", scope=scope, source_namespace="ha", conversation_token=None
        )
        self.assertIsInstance(hash(session), int)


def _fake_option(spell_id="x", spoken="hallo", on=True):
    return AmbiguousOption(
        action_draft=ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": on}),
        spell_id=spell_id,
        matched_text="hallo",
        spoken_trigger=spoken,
    )


class InteractionStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.store = InteractionStateStore(clock=lambda: self.now)
        self.session = compute_interaction_session(
            agent_entry_id="e1", scope=local_scope("e1"), source_namespace="ha", conversation_token="t1"
        )

    def test_begin_then_get_returns_the_dialog(self):
        self.store.begin(self.session, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")
        self.assertIsNotNone(self.store.get(self.session))

    def test_discard_removes_it(self):
        self.store.begin(self.session, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")
        self.store.discard(self.session)
        self.assertIsNone(self.store.get(self.session))

    def test_idle_ttl_expires_without_activity(self):
        self.store.begin(self.session, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")
        self.now += IDLE_TTL_SECONDS + 1
        self.assertIsNone(self.store.get(self.session))

    def test_touch_renews_the_ttl(self):
        self.store.begin(self.session, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")
        self.now += IDLE_TTL_SECONDS - 1
        self.store.touch(self.session)
        self.now += IDLE_TTL_SECONDS - 1
        self.assertIsNotNone(self.store.get(self.session))

    def test_capacity_cap_rejects_new_but_not_existing(self):
        for i in range(MAX_OPEN_DIALOGS):
            session = compute_interaction_session(
                agent_entry_id="e1", scope=local_scope("e1"), source_namespace="ha",
                conversation_token=f"t{i}",
            )
            self.store.begin(session, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")
        with self.assertRaises(DialogCapacityError):
            overflow_session = compute_interaction_session(
                agent_entry_id="e1", scope=local_scope("e1"), source_namespace="ha",
                conversation_token="overflow",
            )
            self.store.begin(overflow_session, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")
        # An existing dialog can still be refreshed even while full.
        existing = compute_interaction_session(
            agent_entry_id="e1", scope=local_scope("e1"), source_namespace="ha", conversation_token="t0"
        )
        self.store.begin(existing, _fake_option("a"), _fake_option("b"), language="de", reply_route_ref="")


class ClarificationFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_text("t", AMBIGUOUS_YAML)
        self.resolver = FakeResolver(
            {"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))}
        )
        self.caller = FakeCaller()
        self.router = SpellbookRouter(ActionExecutor(self.resolver, self.caller))
        self.config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY,
            spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
        )

    def _request(self, text, token):
        return ConversationRequest(
            request_id=_next_request_id(), agent_entry_id="e1", text=text, language="de",
            source_namespace="test", conversation_token=token,
        )

    async def test_ambiguous_match_asks_then_reply_executes_the_right_one(self):
        r1 = await self.router.handle(self._request("daktiviere licht Buero", "conv-1"), self.config)
        self.assertEqual(r1.status, ConversationStatus.CONFIRMATION_REQUIRED)

        r2 = await self.router.handle(self._request("deaktiviere", "conv-1"), self.config)
        self.assertEqual(r2.status, ConversationStatus.COMPLETED)
        self.assertEqual(r2.response_text, "Deaktiviert.")
        self.assertEqual(self.caller.calls, [(("light.buero",), False)])

    async def test_result_echoes_back_a_generated_token_for_a_tokenless_caller(self):
        # Second-round audit: a caller that gives no conversation_token
        # at all still gets a session (session.py generates a random
        # one internally, exactly to avoid pooling unrelated
        # token-less callers into one shared session) - but that value
        # used to be invisible outside the store. A caller who wants
        # to continue this exchange needs it echoed back so it can be
        # sent as next turn's conversation_token.
        raw_request = ConversationRequest(
            request_id=_next_request_id(), agent_entry_id="e1", text="daktiviere licht Buero",
            language="de", source_namespace="test", conversation_token=None,
        )
        r1 = await self.router.handle(raw_request, self.config)
        self.assertEqual(r1.status, ConversationStatus.CONFIRMATION_REQUIRED)
        self.assertIsNotNone(r1.conversation_token)

        follow_up = ConversationRequest(
            request_id=_next_request_id(), agent_entry_id="e1", text="deaktiviere",
            language="de", source_namespace="test", conversation_token=r1.conversation_token,
        )
        r2 = await self.router.handle(follow_up, self.config)
        self.assertEqual(r2.status, ConversationStatus.COMPLETED)
        self.assertEqual(r2.response_text, "Deaktiviert.")

    async def test_negative_reply_cancels_without_executing(self):
        await self.router.handle(self._request("daktiviere licht Buero", "conv-2"), self.config)
        r2 = await self.router.handle(self._request("nein", "conv-2"), self.config)
        self.assertEqual(r2.status, ConversationStatus.COMPLETED)
        self.assertEqual(r2.response_text, "Abgebrochen.")
        self.assertEqual(self.caller.calls, [])

    async def test_unrelated_short_reply_never_authorizes_an_action(self):
        # Found live: "dein name" scored 0.38 against "Deaktiviert."
        # under the old 0.35 floor - purely coincidental character
        # overlap (SequenceMatcher on short strings), not real
        # resemblance - and would have turned the light off in
        # response to a completely unrelated utterance. Must re-ask
        # instead, same as any other unrecognized reply.
        await self.router.handle(self._request("daktiviere licht Buero", "conv-name"), self.config)
        r2 = await self.router.handle(self._request("dein name", "conv-name"), self.config)
        self.assertEqual(r2.status, ConversationStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(self.caller.calls, [])

    async def test_unrecognized_reply_does_not_renew_the_idle_ttl(self):
        # A reply that neither picks an option nor starts a new command
        # is noise, not engagement - it must not keep a stale dialog
        # alive past its normal idle TTL indefinitely.
        self.now = 1000.0
        store = InteractionStateStore(clock=lambda: self.now)
        router = SpellbookRouter(ActionExecutor(self.resolver, self.caller), store)
        await router.handle(self._request("daktiviere licht Buero", "conv-ttl"), self.config)

        self.now += IDLE_TTL_SECONDS - 1
        r = await router.handle(self._request("dein name", "conv-ttl"), self.config)
        self.assertEqual(r.status, ConversationStatus.CONFIRMATION_REQUIRED)

        self.now += 2  # past the ORIGINAL ttl, had it not been renewed
        r = await router.handle(self._request("deaktiviere", "conv-ttl"), self.config)
        # The dialog is gone - "deaktiviere" alone matches nothing on
        # its own, so this is NO_MATCH, not a resolved clarification.
        self.assertEqual(r.status, ConversationStatus.NO_MATCH)

    async def test_unrelated_new_command_discards_the_open_question(self):
        await self.router.handle(self._request("daktiviere licht Buero", "conv-3"), self.config)
        r2 = await self.router.handle(self._request("aktiviere licht Buero", "conv-3"), self.config)
        # A confident, unambiguous new command executes directly...
        self.assertEqual(r2.status, ConversationStatus.COMPLETED)
        # ...and the old question is gone, not still answerable: on
        # its own "deaktiviere" matches nothing in the spellbook (it
        # only resolved earlier as a reply to the now-discarded pair).
        r3 = await self.router.handle(self._request("deaktiviere", "conv-3"), self.config)
        self.assertEqual(r3.status, ConversationStatus.NO_MATCH)
        self.assertEqual(r3.response_text, self.config.no_match_response)

    async def test_two_concurrent_sessions_dont_cross_answer(self):
        await self.router.handle(self._request("daktiviere licht Buero", "conv-a"), self.config)
        await self.router.handle(self._request("daktiviere licht Buero", "conv-b"), self.config)
        # Answering session b must not be affected by session a still
        # being open, and vice versa - each resolves independently.
        # Full phrases, not the bare "aktiviere"/"deaktiviere" words -
        # "aktiviere" is a literal substring of "deaktiviere", so on
        # its own it's genuinely, correctly too close to call (see
        # AmbiguityDetectionTests) and would just re-prompt; that's
        # not what this test is checking.
        r_b = await self.router.handle(self._request("aktiviere licht Buero", "conv-b"), self.config)
        self.assertEqual(r_b.status, ConversationStatus.COMPLETED)
        self.assertEqual(r_b.response_text, "Aktiviert.")
        r_a = await self.router.handle(self._request("deaktiviere licht Buero", "conv-a"), self.config)
        self.assertEqual(r_a.status, ConversationStatus.COMPLETED)
        self.assertEqual(r_a.response_text, "Deaktiviert.")

    async def test_policy_is_rechecked_at_reply_time_not_cached(self):
        # Ambiguity raised while Buero was allowed...
        await self.router.handle(self._request("daktiviere licht Buero", "conv-4"), self.config)
        # ...but the policy tightens before the reply arrives.
        locked_down = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset())),
        )
        r2 = await self.router.handle(self._request("deaktiviere", "conv-4"), locked_down)
        self.assertEqual(r2.status, ConversationStatus.REJECTED)
        self.assertEqual(self.caller.calls, [])

    async def test_reply_resembling_neither_option_reasks_instead_of_guessing(self):
        # Only the margin between the two options used to gate a pick -
        # a reply that resembles NEITHER option (background noise, a
        # totally unrelated fragment the engine also didn't match)
        # could still "win" against whichever scored marginally higher.
        # An absolute floor must reject that and re-ask instead.
        r1 = await self.router.handle(self._request("daktiviere licht Buero", "conv-5"), self.config)
        self.assertEqual(r1.status, ConversationStatus.CONFIRMATION_REQUIRED)
        r2 = await self.router.handle(self._request("was ist das wetter morgen", "conv-5"), self.config)
        self.assertEqual(r2.status, ConversationStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(self.caller.calls, [])
        # The question is still open and answerable normally afterwards.
        r3 = await self.router.handle(self._request("deaktiviere", "conv-5"), self.config)
        self.assertEqual(r3.status, ConversationStatus.COMPLETED)
        self.assertEqual(r3.response_text, "Deaktiviert.")

    async def test_unrelated_reply_sharing_a_word_stem_never_authorizes_an_action(self):
        # Found live (external audit): "deine Aktivität" scored 0.667
        # against the FULL "deaktiviere licht Buero" trigger under the
        # old whole-phrase resemblance check - comfortably above the
        # 0.55 floor that was only ever tuned against "dein name"
        # (0.38) - and turned the light off in response to a sentence
        # that never said anything resembling a deliberate pick. The
        # fix scores only against each option's DISTINGUISHING word
        # ("aktiviere"/"deaktiviere" here, never the shared "licht
        # Buero" filler) - "Aktivität" shares a stem with "aktiviere"
        # but stays below the match floor either way.
        await self.router.handle(self._request("daktiviere licht Buero", "conv-6"), self.config)
        r2 = await self.router.handle(self._request("deine Aktivität", "conv-6"), self.config)
        self.assertEqual(r2.status, ConversationStatus.CONFIRMATION_REQUIRED)
        self.assertEqual(self.caller.calls, [])
        # The question is still open and answerable normally afterwards.
        r3 = await self.router.handle(self._request("aktiviere", "conv-6"), self.config)
        self.assertEqual(r3.status, ConversationStatus.COMPLETED)
        self.assertEqual(r3.response_text, "Aktiviert.")
        self.assertEqual(self.caller.calls, [(("light.buero",), True)])

    async def test_router_serializes_same_session_through_one_lock_per_session(self):
        # The router acquires SessionLockRegistry for the computed
        # InteractionSession before touching the store - this is a
        # direct test of that primitive's mutual exclusion (which is
        # what actually prevents two overlapping turns for the same
        # session from racing on the store or double-executing a
        # clarification's answer).
        registry = SessionLockRegistry()
        order: list[str] = []
        first_acquired = asyncio.Event()
        second_attempted = asyncio.Event()
        release_first = asyncio.Event()

        async def first():
            async with registry.acquire("shared-session"):
                order.append("first-acquired")
                first_acquired.set()
                await release_first.wait()
            order.append("first-released")

        async def second():
            await first_acquired.wait()
            second_attempted.set()
            async with registry.acquire("shared-session"):
                order.append("second-acquired")

        task1 = asyncio.ensure_future(first())
        task2 = asyncio.ensure_future(second())
        await first_acquired.wait()
        await second_attempted.wait()
        await asyncio.sleep(0)  # let `second` actually block on the held lock
        self.assertEqual(order, ["first-acquired"])
        release_first.set()
        await asyncio.gather(task1, task2)
        self.assertEqual(order, ["first-acquired", "first-released", "second-acquired"])

    async def test_session_lock_registry_drops_entries_once_unused(self):
        # A long-running conversation entity sees a fresh conversation
        # token per dialogue - an entry per session that's never
        # cleaned up would be a slow, unbounded memory leak over the
        # entity's uptime.
        registry = SessionLockRegistry()
        async with registry.acquire("s1"):
            self.assertEqual(len(registry._locks), 1)
        self.assertEqual(len(registry._locks), 0)

        # Two overlapping holders of the SAME session (a second caller
        # queued up behind the first, both still "interested"): the
        # entry must survive until the LAST one releases, not the
        # first, or a caller queued behind an about-to-be-deleted entry
        # could end up on an unrelated new lock for the same session.
        first_holds = asyncio.Event()
        release_first = asyncio.Event()
        second_done = asyncio.Event()

        async def first():
            async with registry.acquire("s2"):
                first_holds.set()
                await release_first.wait()

        async def second():
            await first_holds.wait()
            async with registry.acquire("s2"):
                pass
            second_done.set()

        t1 = asyncio.ensure_future(first())
        t2 = asyncio.ensure_future(second())
        await first_holds.wait()
        # Let `second` actually run far enough to register itself
        # (refcount 2) and block on the held lock, however many loop
        # iterations that takes - don't guess an exact tick count.
        for _ in range(50):
            if registry._locks.get("s2", (None, 0))[1] == 2:
                break
            await asyncio.sleep(0)
        else:
            self.fail("`second` never registered interest in the held lock")
        # While both are interested, the entry must exist with
        # refcount 2 - proven above by the poll loop reaching it
        # without the registry ever mistaking "first released" for
        # "nobody wants this session anymore" and deleting it out from
        # under `second`. Releasing now and waiting for both to finish
        # (order between them is an implementation detail, not part of
        # the contract) must leave no trace behind.
        release_first.set()
        await asyncio.gather(t1, t2)
        self.assertTrue(second_done.is_set())
        self.assertEqual(len(registry._locks), 0)


class AllAllowedPolicyTests(unittest.IsolatedAsyncioTestCase):
    """light_all_on/off migrate to light.set_power + target all_allowed
    (Etappe 4) - unlike a named target, all_allowed must silently drop
    forbidden members instead of denying the whole batch."""

    def setUp(self):
        self.resolver = FakeResolver({
            None: ResolvedTargets((
                ResolvedEntity("light.buero", "buero"),
                ResolvedEntity("light.garten", "garten"),
                ResolvedEntity("light.flur", None),
            )),
        })
        self.caller = FakeCaller()
        self.executor = ActionExecutor(self.resolver, self.caller)

    async def test_all_allowed_only_touches_permitted_entities(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero"}))
        draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.caller.calls, [(("light.buero",), True)])

    async def test_all_allowed_with_nothing_permitted_is_invalid_not_denied(self):
        policy = AreaPolicy(allowed_areas=frozenset())
        draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), {"on": True})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(self.caller.calls, [])

    async def test_all_allowed_unrestricted_touches_truly_everything_area_or_not(self):
        # Second-round audit fix: light.flur has no area at all. An
        # unrestricted policy means "no area restriction applies" -
        # excluding an entity purely because it happens to have no
        # area to check contradicts that, and used to happen here
        # because area_id is None was checked before unrestricted
        # ever got a say. Under a RESTRICTED policy an area-less
        # entity is still correctly excluded (see the sibling test
        # below) - only unrestricted changes this.
        policy = AreaPolicy(allowed_areas=None, unrestricted=True)
        draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), {"on": False})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        called_entities = set(self.caller.calls[0][0])
        self.assertEqual(called_entities, {"light.buero", "light.garten", "light.flur"})

    async def test_all_allowed_restricted_still_excludes_area_less_entities(self):
        policy = AreaPolicy(allowed_areas=frozenset({"buero", "garten"}))
        draft = ActionDraft("light.set_power", ActionTarget("all_allowed", None), {"on": False})
        result = await self.executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        called_entities = set(self.caller.calls[0][0])
        self.assertEqual(called_entities, {"light.buero", "light.garten"})

    async def test_named_target_area_less_entity_permitted_only_when_unrestricted(self):
        resolver = FakeResolver({"Flur": ResolvedTargets((ResolvedEntity("light.flur", None),))})
        caller = FakeCaller()
        executor = ActionExecutor(resolver, caller)
        draft = ActionDraft("light.set_power", ActionTarget("entity_name", "Flur"), {"on": True})

        restricted = AreaPolicy(allowed_areas=frozenset({"buero"}))
        result = await executor.execute(draft, _scoped(restricted))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(caller.calls, [])

        unrestricted = AreaPolicy(allowed_areas=None, unrestricted=True)
        result = await executor.execute(draft, _scoped(unrestricted))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.calls, [(("light.flur",), True)])


class VacuumExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolver = FakeResolver({}, areas={"Zwergstadt": "zwergstadt", "Garten": "garten"})

    async def test_permitted_area_started_reports_success(self):
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome("started", "vacuum.kreacher", "Zwergstadt"))
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"zwergstadt"}))
        draft = ActionDraft("vacuum.clean_area", ActionTarget("area_name", "Zwergstadt"), {})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.vacuum_calls, ["zwergstadt"])

    async def test_forbidden_area_is_denied_before_any_vacuum_call(self):
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome("started", "vacuum.kreacher", "Garten"))
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"zwergstadt"}))
        draft = ActionDraft("vacuum.clean_area", ActionTarget("area_name", "Garten"), {})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertEqual(caller.vacuum_calls, [])  # never even asked to clean

    async def test_ambiguous_room_match_is_reported_not_guessed(self):
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome("ambiguous"))
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"zwergstadt"}))
        draft = ActionDraft("vacuum.clean_area", ActionTarget("area_name", "Zwergstadt"), {})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.reason_code, "ambiguous_vacuum_match")

    async def test_no_match_is_invalid(self):
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome("no_match"))
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"zwergstadt"}))
        draft = ActionDraft("vacuum.clean_area", ActionTarget("area_name", "Zwergstadt"), {})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)

    async def test_entity_target_kind_rejected_for_vacuum(self):
        # vacuum.clean_area is area-only - "which vacuum, which room"
        # is a discovery problem, not something a spoken entity_name
        # could name directly.
        caller = FakeCaller()
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset(), unrestricted=True)
        draft = ActionDraft("vacuum.clean_area", ActionTarget("entity_name", "Kreacher"), {})
        result = await executor.execute(draft, _scoped(policy))
        self.assertEqual(result.status, ExecutionStatus.INVALID)
        self.assertEqual(result.reason_code, "target_kind_not_allowed_for_operation")
        self.assertEqual(caller.vacuum_calls, [])

    async def test_source_area_uses_generic_area_resolution_for_vacuum(self):
        caller = FakeCaller(vacuum_outcome=VacuumCleanOutcome("started", "vacuum.kreacher", "Zwergstadt"))
        self.resolver._areas["zwergstadt"] = "zwergstadt"
        executor = ActionExecutor(self.resolver, caller)
        policy = AreaPolicy(allowed_areas=frozenset({"zwergstadt"}))
        draft = ActionDraft("vacuum.clean_area", ActionTarget("source_area"), {})
        result = await executor.execute(draft, _scoped(policy), source_area_id="zwergstadt")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(caller.vacuum_calls, ["zwergstadt"])


LEGACY_YAML = """
id: legacy_test
name: Legacy Test
commands:
  - id: lights_on
    trigger:
      de: "entzuende die feuer von mittelerde"
      en: "light the fires of middle-earth"
    action: light_all_on
    catchphrase: "Erleuchtet."
  - id: lights_off
    trigger: "loesche die feuer"
    action: light_all_off
  - id: clean_room
    trigger: "reinige die hallen von {area}"
    action: vacuum_clean
    catchphrase: "Wird gereinigt."
  - id: broken_vacuum
    trigger: "sauge {room}"
    action: vacuum_clean
  - id: free_text
    trigger: "lumos {area}"
    output: "Licht {area} einschalten"
    catchphrase: "Mach ich."
  - id: weird_action
    trigger: "irgendwas"
    action: teleport
"""


SOURCE_AREA_YAML = """
name: SourceAreaDemo
commands:
  - id: light_on_named_area
    trigger: "licht {area} an"
    operation: light.set_power
    target: {kind: area_name, capture: area}
    arguments: {"on": true}
    catchphrase: "Explizit."

  - id: light_on_default_area
    trigger: "licht an"
    operation: light.set_power
    target: {kind: source_area}
    arguments: {"on": true}
    catchphrase: "Hier."

  - id: light_on_everywhere
    trigger: "alle lichter an"
    operation: light.set_power
    target: {kind: all_allowed}
    arguments: {"on": true}
    catchphrase: "Ueberall."
"""


class SourceAreaTargetingTests(unittest.IsolatedAsyncioTestCase):
    """core/models.py's source_area_id / ActionTarget(kind="source_area")
    - a spellbook command opts in explicitly; the router substitutes
    whatever area the REQUEST says it came from, never anything wider,
    and only when the user didn't name an area themselves."""

    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_text("t", SOURCE_AREA_YAML)
        self.resolver = FakeResolver({
            "Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),)),
            "buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),)),
            "zwergstadt": ResolvedTargets((ResolvedEntity("light.zwergstadt", "zwergstadt"),)),
            "kueche": ResolvedTargets((ResolvedEntity("light.kueche", "kueche"),)),
            None: ResolvedTargets((
                ResolvedEntity("light.buero", "buero"),
                ResolvedEntity("light.zwergstadt", "zwergstadt"),
            )),
        })
        self.caller = FakeCaller()
        self.router = SpellbookRouter(ActionExecutor(self.resolver, self.caller))

    def _request(self, text, source_area_id=None):
        return ConversationRequest(
            request_id=_next_request_id(), agent_entry_id="e1", text=text, language="de",
            source_namespace="test", source_area_id=source_area_id,
        )

    async def test_no_area_in_text_uses_the_source_area(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"zwergstadt"}))),
        )
        result = await self.router.handle(self._request("licht an", source_area_id="zwergstadt"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(self.caller.calls, [(("light.zwergstadt",), True)])

    async def test_explicit_area_wins_over_source_area(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero", "zwergstadt"}))),
        )
        # Same device (source_area_id="zwergstadt") but the user names
        # a DIFFERENT area explicitly - the explicit one must win.
        result = await self.router.handle(
            self._request("licht Buero an", source_area_id="zwergstadt"), config
        )
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(self.caller.calls, [(("light.buero",), True)])

    async def test_no_area_and_no_source_area_is_a_controlled_rejection(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True)),
        )
        # No device behind this request at all (e.g. a text-only
        # frontend) - must NEVER fall back to all_allowed.
        result = await self.router.handle(self._request("licht an", source_area_id=None), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)
        self.assertEqual(self.caller.calls, [])

    async def test_real_global_command_stays_global_regardless_of_source_area(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=None, unrestricted=True)),
        )
        result = await self.router.handle(
            self._request("alle lichter an", source_area_id="zwergstadt"), config
        )
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        called_entities = set(self.caller.calls[0][0])
        self.assertEqual(called_entities, {"light.buero", "light.zwergstadt"})

    async def test_extra_unparsed_target_text_never_triggers_the_global_command(self):
        # This book's only all_allowed-capable trigger is "alle lichter
        # an" - unrelated to the default-area command's own trigger, so
        # this mainly re-confirms core/matching.py's full-consumption
        # fix in a source-area book specifically: trailing text the
        # engine can't place must reject the match outright, not fall
        # through to source_area OR all_allowed by accident.
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=None, unrestricted=True)),
        )
        result = await self.router.handle(
            self._request("licht an Kueche", source_area_id="zwergstadt"), config
        )
        self.assertEqual(result.status, ConversationStatus.NO_MATCH)
        self.assertEqual(self.caller.calls, [])

    async def test_two_satellites_same_book_each_use_their_own_area(self):
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero", "zwergstadt"}))),
        )
        r_a = await self.router.handle(self._request("licht an", source_area_id="buero"), config)
        r_b = await self.router.handle(self._request("licht an", source_area_id="zwergstadt"), config)
        self.assertEqual(r_a.status, ConversationStatus.COMPLETED)
        self.assertEqual(r_b.status, ConversationStatus.COMPLETED)
        self.assertEqual(
            sorted(c[0][0] for c in self.caller.calls), ["light.buero", "light.zwergstadt"],
        )

    async def test_source_area_still_goes_through_the_normal_area_policy(self):
        # The substituted area is checked by the SAME AreaPolicy as any
        # explicit area - it grants no extra authorization by itself.
        config = AgentConfig(
            mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),  # zwergstadt NOT allowed
        )
        result = await self.router.handle(self._request("licht an", source_area_id="zwergstadt"), config)
        self.assertEqual(result.status, ConversationStatus.REJECTED)
        self.assertEqual(self.caller.calls, [])


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        import yaml as _yaml
        self.raw = _yaml.safe_load(LEGACY_YAML)
        self.result = migrate_legacy_spellbook("legacy_test", self.raw)

    def test_action_bearing_entries_are_migrated(self):
        self.assertIsNotNone(self.result.book)
        migrated_ids = {c.spell_id for c in self.result.book.commands}
        self.assertEqual(migrated_ids, {"lights_on", "lights_off", "clean_room"})

    def test_light_all_on_becomes_typed_all_allowed(self):
        command = next(c for c in self.result.book.commands if c.spell_id == "lights_on")
        self.assertEqual(command.operation, "light.set_power")
        self.assertEqual(command.target_spec, {"kind": "all_allowed"})
        self.assertEqual(command.arguments_spec, {"on": True})

    def test_vacuum_clean_becomes_typed_area_capture(self):
        command = next(c for c in self.result.book.commands if c.spell_id == "clean_room")
        self.assertEqual(command.operation, "vacuum.clean_area")
        self.assertEqual(command.target_spec, {"kind": "area_name", "capture": "area"})

    def test_localized_trigger_survives_migration(self):
        result = migrate_legacy_spellbook("t", self.raw)
        matched = spellbook_engine.match(result.book, "entzuende die feuer von mittelerde", "de")
        self.assertIsInstance(matched, EngineMatch)

    def test_free_text_output_is_reported_not_silently_wrapped(self):
        reasons = {u.spell_id: u.reason for u in self.result.unsupported}
        self.assertEqual(reasons["free_text"], "free_text_output")

    def test_unknown_action_is_reported(self):
        reasons = {u.spell_id: u.reason for u in self.result.unsupported}
        self.assertEqual(reasons["weird_action"], "unknown_action")

    def test_vacuum_clean_without_area_placeholder_is_reported_not_silently_broken(self):
        reasons = {u.spell_id: u.reason for u in self.result.unsupported}
        self.assertEqual(reasons["broken_vacuum"], "vacuum_clean_missing_area_placeholder")

    def test_real_production_spellbooks_match_the_etappe_1_inventory(self):
        total_supported = 0
        total_unsupported = 0
        import json
        inventory = json.loads((HASE_ROOT.parents[1] / "docs/spellbook-engine/migration-inventory.json").read_text(encoding="utf-8"))
        for theme, entries in inventory["sources"].items():
            raw = {"name": theme, "commands": entries["legacy"]}
            result = migrate_legacy_spellbook(theme, raw)
            total_supported += len(result.book.commands) if result.book else 0
            total_unsupported += len(result.unsupported)
        # Updated 2026-09-10: star_trek.yaml (2 direct actions, 3
        # free-text - see docs/spellbook-engine/stage-1/inventory.md's
        # own update note) was removed as unused, dropping the
        # original 91/7/84 inventory to 86/5/81. If this ever changes
        # again, it means the real spellbook files changed - update
        # the inventory doc too, don't just bump this number.
        self.assertEqual(total_supported, 5)
        self.assertEqual(total_unsupported, 81)


class FakeBackend:
    def __init__(self, result: BackendResult):
        self._result = result
        self.calls: list[tuple] = []

    async def converse(self, text, language, scope):
        self.calls.append((text, language, scope))
        return self._result


class RaisingBackend:
    async def converse(self, text, language, scope):
        raise RuntimeError("device unreachable")


class _FakeAssistForwarder:
    """core.text_only_execution.AssistForwarder Protocol - stands in for
    spellbooker_ha.HaAssistForwarder (real conversation.async_converse
    is never touched by this pure-core test file)."""

    def __init__(self, *, is_error=False, is_query_answer=False, speech=None, continue_conversation=False):
        self.calls: list[str] = []
        self._is_error = is_error
        self._is_query_answer = is_query_answer
        self._speech = speech
        self._continue_conversation = continue_conversation

    async def forward(self, command_text, *, language, context):
        self.calls.append(command_text)
        return AssistForwardOutcome(
            speech=self._speech, is_error=self._is_error,
            is_query_answer=self._is_query_answer, continue_conversation=self._continue_conversation,
        )


class SatelliteModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_text("t", DEMO_YAML)
        self.resolver = FakeResolver(
            {"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))}
        )
        self.executor = ActionExecutor(self.resolver, FakeCaller())

    def _request(self, text, token="conv-1"):
        return ConversationRequest(
            request_id=_next_request_id(), agent_entry_id="e1", text=text, language="de",
            source_namespace="test", conversation_token=token,
        )

    async def test_local_match_never_reaches_the_backend(self):
        backend = FakeBackend(BackendResult(status="reply", response_text="sollte nie kommen"))
        router = SpellbookRouter(self.executor)
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("licht Buero an"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(backend.calls, [])

    async def test_no_match_falls_through_to_backend_reply(self):
        backend = FakeBackend(BackendResult(status="reply", response_text="Ich finde Drucker spannend."))
        router = SpellbookRouter(self.executor)
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("was hältst du vom Drucker"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Ich finde Drucker spannend.")
        self.assertEqual(len(backend.calls), 1)

    async def test_backend_unavailable_status_is_reported_not_hidden(self):
        backend = FakeBackend(BackendResult(status="unavailable", response_text=None))
        router = SpellbookRouter(self.executor)
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("was hältst du vom Drucker"), config)
        self.assertEqual(result.status, ConversationStatus.BACKEND_UNAVAILABLE)

    async def test_crashing_backend_is_treated_as_unavailable_not_a_crash(self):
        router = SpellbookRouter(self.executor)
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=RaisingBackend(), backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("was hältst du vom Drucker"), config)
        self.assertEqual(result.status, ConversationStatus.BACKEND_UNAVAILABLE)

    async def test_satellite_without_backend_is_a_configuration_error(self):
        router = SpellbookRouter(self.executor)
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=None,
        )
        result = await router.handle(self._request("was hältst du vom Drucker"), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)

    async def test_backend_proposal_still_goes_through_policy(self):
        forbidden_draft = ActionDraft("light.set_power", ActionTarget("area_name", "Garten"), {"on": True})
        backend = FakeBackend(BackendResult(status="reply", response_text="Mach ich.", proposals=(forbidden_draft,)))
        resolver = FakeResolver({"Garten": ResolvedTargets((ResolvedEntity("light.garten", "garten"),))})
        router = SpellbookRouter(ActionExecutor(resolver, FakeCaller()))
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),  # garten NOT allowed
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("licht im Garten bitte"), config)
        # The engine has no match for this text, so it reaches the
        # backend; the backend's proposal targets a forbidden area -
        # the same DENIED policy check as a local match applies.
        self.assertEqual(result.status, ConversationStatus.REJECTED)
        # The backend's own optimistic reply text ("Mach ich.") must
        # never be spoken over a DENIED execution - that would tell
        # the user something happened that didn't.
        self.assertNotEqual(result.response_text, "Mach ich.")

    async def test_more_than_max_proposals_rejects_the_whole_batch(self):
        caller = FakeCaller()
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))})
        drafts = tuple(
            ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
            for _ in range(9)  # _MAX_PROPOSALS is 8
        )
        backend = FakeBackend(BackendResult(status="reply", response_text="Mach ich alles.", proposals=drafts))
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("mach ganz viel bitte"), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)
        # Not even the first one ran - a batch over the cap is
        # rejected outright, not silently truncated to the first 8.
        self.assertEqual(caller.calls, [])

    async def test_malformed_later_proposal_prevents_earlier_ones_from_running(self):
        # Proposal #1 is perfectly valid on its own - but #2 has an
        # unknown operation. Both must be validated BEFORE either
        # executes, so #1 never runs just because it happened to come
        # first in a batch that's invalid as a whole.
        caller = FakeCaller()
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))})
        good_draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        bad_draft = ActionDraft("nonsense.operation", ActionTarget("none", None), {})
        backend = FakeBackend(
            BackendResult(status="reply", response_text="Mach ich.", proposals=(good_draft, bad_draft))
        )
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("mach zwei sachen"), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)

    async def test_malformed_target_value_prevents_earlier_proposals_from_running(self):
        # Second-round audit: validate_shape checked target.kind but
        # never target.value - an empty-string area_name failed safely
        # later (TargetNotFound), but a non-string value (nothing stops
        # a backend proposal from setting one) would have reached
        # area_registry lookup code that calls .strip() on it and
        # crashed with AttributeError, discovered only after an
        # earlier, valid proposal in the same batch already ran.
        caller = FakeCaller()
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))})
        good_draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        malformed_draft = ActionDraft("light.set_power", ActionTarget("area_name", ["not", "a", "string"]), {"on": True})
        backend = FakeBackend(
            BackendResult(status="reply", response_text="Mach ich.", proposals=(good_draft, malformed_draft))
        )
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("mach zwei sachen wieder"), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)
        self.assertEqual(caller.calls, [])

    async def test_none_proposal_in_batch_is_rejected_not_a_crash(self):
        # Found live (external audit): validate_shape accessed
        # draft.operation directly - a proposal entry that's None (or
        # any other non-ActionDraft object), not just an ActionDraft
        # with a bad field, crashed the batch check in _consult_backend
        # (`all(validate_shape(d) for d in proposals)`) with an
        # AttributeError that propagated uncaught all the way to
        # router.handle()'s own caller - never a controlled rejection.
        caller = FakeCaller()
        resolver = FakeResolver({"Buero": ResolvedTargets((ResolvedEntity("light.buero", "buero"),))})
        good_draft = ActionDraft("light.set_power", ActionTarget("area_name", "Buero"), {"on": True})
        backend = FakeBackend(
            BackendResult(status="reply", response_text="Mach ich.", proposals=(good_draft, None))
        )
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"buero"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        # Must return a controlled ERROR result, never raise.
        result = await router.handle(self._request("mach zwei sachen, eins kaputt"), config)
        self.assertEqual(result.status, ConversationStatus.ERROR)
        self.assertEqual(caller.calls, [])

    async def test_successful_backend_query_proposal_speaks_the_real_answer(self):
        # Second-round audit: a successful QUERY_ANSWER proposal from
        # a backend used to still speak the backend's own response_text
        # (or "Erledigt.") - never the actual state the query resolved
        # to. The backend wrote that text before the proposal ever
        # ran; it cannot know the real answer.
        query_draft = ActionDraft("entity.get_state", ActionTarget("entity_name", "Kuechenlicht"), {})
        backend = FakeBackend(
            BackendResult(status="reply", response_text="Klar, sag ich dir.", proposals=(query_draft,))
        )
        resolver = FakeResolver({"Kuechenlicht": ResolvedTargets((ResolvedEntity("light.kueche", "kueche"),))})
        caller = FakeCaller(state={"state": "on", "unit": None})
        router = SpellbookRouter(ActionExecutor(resolver, caller))
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset({"kueche"}))),
            backend=backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("was macht das Kuechenlicht"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "on")

    async def test_conversation_scope_shared_across_two_frontends(self):
        # Two different agent_entry_ids, same backend_instance_id and
        # profile_id, must land on the same conversation_scope (and so
        # the same interaction_session for a given conversation_token)
        # - that's the whole point of Etappe 6 continuity.
        backend = FakeBackend(BackendResult(status="reply", response_text="Hallo."))
        router = SpellbookRouter(self.executor)
        config_office = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book,
            policy=_scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True)),
            backend=backend, backend_instance_id="code27-1", profile_id="lilya",
        )
        req = ConversationRequest(
            request_id="r1", agent_entry_id="gollum-bedroom", text="was hältst du vom Drucker",
            language="de", source_namespace="ha", conversation_token="shared-token",
        )
        await router.handle(req, config_office)
        self.assertEqual(len(backend.calls), 1)
        scope_used = backend.calls[0][2]
        self.assertEqual(scope_used, backend_scope("code27-1", "lilya"))


MIXED_AMBIGUOUS_YAML = """
name: Mixed Ambiguity Test
commands:
  - id: activate
    trigger: "aktiviere licht {area}"
    operation: light.set_power
    target: {kind: area_name, capture: area}
    arguments: {"on": true}
    catchphrase: "Aktiviert."
    output: "Licht {area} an"
  - id: deactivate_text_only
    trigger: "deaktiviere licht {area}"
    output: "Licht {area} aus"
    catchphrase: "Textantwort deaktiviert."
"""


class VoiceReachesEveryExecutionKindTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end regression for a real, empirically-found gap: native
    HASE-Agent matching (core.router.SpellbookRouter, exactly the code
    conversation.py's SpellbookerConversationEntity._async_handle_message
    calls for a real Voice Assist turn) used to call spellbook_engine.
    match() with typed_only=True, making EVERY text-only Spell invisible
    to it regardless of assist_redirect - confirmed live by tracing real,
    bundled custom_components/hase/spellbooks/middle_earth.yaml Spells
    through this exact router.handle() path for all three shapes
    (action/query/text-only) before and after the fix. See docs/HASE-MCP.
    md's own "Text-only Spells and assist_redirect" section.

    Also covers the follow-up correction: the fix first reused core.
    mcp_execution's own text-only dispatch directly, which had the
    dependency direction backwards (a HASE-Agent turn never touches MCP
    or the network at all). SpellbookRouter now calls core.
    text_only_execution.execute_text_only_command - a neutral module
    core.mcp_execution ALSO depends on, symmetrically - which these tests
    exercise via the real SpellbookRouter/AgentConfig wiring, never core.
    mcp_execution itself.

    Drives the SAME router.handle(request, config) conversation.py calls
    - never a lower-level shortcut - so this is a true end-to-end proof,
    not just a unit test of match()'s own return shape."""

    def setUp(self):
        self.book = spellbook_engine.load_spellbook_from_path(
            HASE_ROOT / "spellbooks" / "middle_earth.yaml"
        )
        self.assertIsNotNone(self.book)
        # middle_earth.yaml has no query Spell today - append the same
        # well-established query fixture the rest of this project's test
        # suite already uses (tests/_bridge_contract.py's CONTRACT_QUERY),
        # so all three execution kinds are covered from one real book.
        query_command = spellbook_engine._load_command("contract_query", {
            "id": "contract_query", "trigger": "wie viele lichter sind an in {area}",
            "sources": [{"kind": "count", "target": {"kind": "area_name", "capture": "area"},
                         "domain_filter": "light", "state_filter": "on", "variable": "n"}],
            "output": "{n} Lichter sind an.",
        })
        self.assertIsNotNone(query_command)
        from dataclasses import replace as _replace
        self.book = _replace(self.book, commands=(*self.book.commands, query_command))

        self.resolver = FakeResolver(
            {"Gondor": ResolvedTargets((ResolvedEntity("light.gondor_lamp", "gondor"),))}
        )
        self.caller = FakeCaller()
        self.executor = ActionExecutor(self.resolver, self.caller)
        self.assist_forwarder = _FakeAssistForwarder()
        self.backend = FakeBackend(BackendResult(status="reply", response_text="[backend echo]"))
        self.policy = _scoped(AreaPolicy(allowed_areas=frozenset(), unrestricted=True))

    def _router(self):
        return SpellbookRouter(self.executor, assist_forwarder=self.assist_forwarder)

    def _request(self, text, token="conv-1"):
        return ConversationRequest(
            request_id=_next_request_id(), agent_entry_id="e1", text=text, language="de",
            source_namespace="ha_conversation", conversation_token=token,
        )

    async def test_action_spell_executes_via_the_real_voice_request_path(self):
        router = self._router()
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book, policy=self.policy)
        result = await router.handle(self._request("entzündet die leuchtfeuer von Gondor"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Rohan wird antworten.")
        self.assertEqual(self.caller.calls, [(("light.gondor_lamp",), True)])

    async def test_query_spell_executes_via_the_real_voice_request_path(self):
        self.caller = FakeCaller(state={"state": "on", "unit": None})
        self.executor = ActionExecutor(self.resolver, self.caller)
        router = self._router()
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book, policy=self.policy)
        result = await router.handle(self._request("wie viele lichter sind an in Gondor"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "1 Lichter sind an.")

    async def test_assist_redirect_text_only_spell_executes_via_the_real_voice_request_path(self):
        # middle_earth_07 - "die sonne geht auf {area}" - the real Spell
        # this session's own live trace used to prove the gap. Must
        # actually forward the RENDERED command phrase to Assist (never
        # skip execution) and speak the Spell's OWN catchphrase on
        # confirmed completion, never Assist's raw reply or the command
        # text itself.
        router = self._router()
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=self.book, policy=self.policy)
        result = await router.handle(self._request("die sonne geht auf über Gondor"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Der neue Tag beginnt.")
        self.assertEqual(result.execution_results[0].status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.assist_forwarder.calls, ["Licht Gondor auf 100 Prozent stellen"])

    async def test_assist_redirect_text_only_spell_reaches_satellite_without_falling_back_to_code27(self):
        # SATELLITE mode, with a real backend present (so a fall-through
        # would silently succeed with the WRONG mechanism instead of
        # erroring loud) - the text-only Spell must still be matched and
        # dispatched through assist_redirect BEFORE _consult_backend is
        # ever reached, never falling through to CODE27's own chat
        # persona (which has no access to this Spell's own configured
        # output/catchphrase at all).
        router = self._router()
        config = AgentConfig(
            mode=FrontendMode.SATELLITE, spellbook=self.book, policy=self.policy,
            backend=self.backend, backend_instance_id="code27-1", profile_id="1",
        )
        result = await router.handle(self._request("die sonne geht auf über Gondor"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Der neue Tag beginnt.")
        self.assertEqual(self.assist_forwarder.calls, ["Licht Gondor auf 100 Prozent stellen"])
        self.assertEqual(self.backend.calls, [])

    async def test_fixed_text_only_spell_assist_redirect_false_never_touches_assist(self):
        # A genuine fixed answer (assist_redirect explicitly False) is
        # rendered directly - it must never be forwarded to Assist at
        # all, unlike middle_earth_07 above.
        book = spellbook_engine.load_spellbook_from_text(
            "fixed",
            "name: Fixed\ncommands:\n  - id: fact\n    trigger: \"erzähl mir etwas\"\n"
            "    output: \"Bruchtal ist ein schöner Ort.\"\n    assist_redirect: false\n",
        )
        router = self._router()
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=self.policy)
        result = await router.handle(self._request("erzähl mir etwas"), config)
        self.assertEqual(result.status, ConversationStatus.COMPLETED)
        self.assertEqual(result.response_text, "Bruchtal ist ein schöner Ort.")
        self.assertEqual(self.assist_forwarder.calls, [])

    async def test_ambiguous_typed_vs_text_only_pair_resolves_and_dispatches_the_text_only_option(self):
        # The ranking-interaction consequence of no longer filtering
        # text-only commands out before ambiguity detection: a typed
        # candidate and a text-only candidate can now be the two
        # near-tied, opposite-output options in one EngineAmbiguous, and
        # answering the resulting clarification must dispatch WHICHEVER
        # one was picked - including the text-only one - without
        # crashing on a None action_draft (see AmbiguousOption/
        # core.router._option_vocabulary's own fix for this).
        book = spellbook_engine.load_spellbook_from_text("mixed", MIXED_AMBIGUOUS_YAML)
        router = self._router()
        config = AgentConfig(mode=FrontendMode.SPELLBOOK_ONLY, spellbook=book, policy=self.policy)

        ambiguous = spellbook_engine.match(book, "daktiviere licht Gondor", "de")
        self.assertIsInstance(ambiguous, EngineAmbiguous)

        r1 = await router.handle(self._request("daktiviere licht Gondor", "conv-mix"), config)
        self.assertEqual(r1.status, ConversationStatus.CONFIRMATION_REQUIRED)

        r2 = await router.handle(self._request("deaktiviere", "conv-mix"), config)
        self.assertEqual(r2.status, ConversationStatus.COMPLETED)
        self.assertEqual(r2.response_text, "Textantwort deaktiviert.")
        self.assertEqual(self.assist_forwarder.calls, ["Licht Gondor aus"])
        self.caller.calls and self.fail("the typed (activate) option must not have been executed")


if __name__ == "__main__":
    unittest.main()
