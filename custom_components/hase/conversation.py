"""The HA-Voice-Frontlayer: one ConversationEntity per spellbooker
config entry, registered as a selectable Conversation Agent (shows up
in the per-device "Assistent" pipeline dropdown, same list as "Home
Assistant"/any other agent - see docs/spellbook-engine's
"HA-technische Anbindung" section for how that was verified against a
real device).

Deliberately thin: _async_handle_message only builds a request, calls
the router, and maps the result back. The exact HA-side types used
here (ConversationInput/ConversationResult/ChatLog/IntentResponse
error codes) are written against documented HA behavior but have not
been exercised against a running Home Assistant instance - that live
check is tracked separately from these adapter-level tests, same
principle as everywhere else in this feature.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from homeassistant.components.conversation import (
    ConversationEntity,
    ConversationInput,
)
from homeassistant.components.conversation import (
    ConversationResult as HAConversationResult,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CODIE_AREA_LIMITS_KEY,
    CONF_ENTRY_KIND,
    CONF_SPELLBOOKER_ALLOWED_AREAS,
    CONF_SPELLBOOKER_BOOK,
    CONF_SPELLBOOKER_NO_MATCH_RESPONSE,
    CONF_SPELLBOOKER_PASS_AREA,
    CONF_SPELLBOOKER_PROFILE_ID,
    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
    CONF_SPELLBOOKER_UNRESTRICTED,
    DOMAIN,
    ENTRY_KIND_SPELLBOOKER,
    SUBENTRY_TYPE_SATELLITE,
)
from .core.executor import ActionExecutor
from .core.models import (
    AreaPolicy,
    ConversationRequest,
    ConversationStatus,
    ExecutionStatus,
    FrontendMode,
    ScopedAreaPolicy,
)
from .core.router import DEFAULT_NO_MATCH_RESPONSE, AgentConfig, SpellbookRouter
from .core.spellbook_engine import EngineSpellbook
from .execution_trace import record_execution
from .spellbook_loader import async_get_spellbook
from .spellbooker_ha import (
    HaAssistForwarder,
    HaServiceCaller,
    HaTargetResolver,
    resolve_source_area_id,
)

_LOGGER = logging.getLogger(__name__)

# A SATELLITE entry may run with no local spellbook at all - every
# utterance then falls straight through engine_match's own
# zero-commands EngineNoMatch path to the CODE27 backend, never a
# hard "Kein Spellbook konfiguriert" error. A SPELLBOOK_ONLY entry has
# no backend to fall through to, so it keeps that hard error - see
# _async_handle_message.
_EMPTY_SATELLITE_BOOK = EngineSpellbook(id="__satellite_no_local_book__", name="", commands=())


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """A plain ENTRY_KIND_SPELLBOOKER entry gets exactly one entity for
    itself (unchanged from before subentries existed). Any other entry
    (a code27_device) gets zero or more entities - one per
    SUBENTRY_TYPE_SATELLITE subentry it owns - each registered with
    config_subentry_id so the entity_platform associates its device
    with that subentry, which is what makes it render nested under
    this entry's own hub in the HA UI instead of as a separate
    top-level entry (see const.ENTRY_KIND_SPELLBOOKER_SATELLITE's own
    note on why a second top-level entry kind couldn't do that)."""
    if config_entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_SPELLBOOKER:
        async_add_entities([SpellbookerConversationEntity(hass, config_entry)])
        return

    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_SATELLITE:
            continue
        async_add_entities(
            [SpellbookerConversationEntity(hass, config_entry, subentry=subentry)],
            config_subentry_id=subentry.subentry_id,
        )


def intent_response_for(language: str, result) -> intent.IntentResponse:
    """Map our ConversationResult onto HA's IntentResponse. Kept as a
    free function - independently testable without a real
    ConversationEntity/hass instance.

    Left as the framework default (ACTION_DONE) only for the cases
    that genuinely are an action having been done, or a follow-up
    question (continue_conversation, set separately by the caller,
    is the actual mechanism for that - HA has no distinct
    IntentResponseType for "assistant is asking something back").

    Output mode (item 8): verified against HA's own intent.py
    (IntentResponse/IntentResponseType) that there is NO native
    TEXT-vs-SPEECH split at this layer - only whether async_set_speech was
    called at all, and IntentResponseType itself has no "silent"/
    "display-only" variant either. Whether spoken text is actually spoken
    aloud is a pipeline/frontend decision (a voice satellite vs the Assist
    text chat), not something a conversation agent's response can flag -
    so AUTO/TEXT/SPEECH/TEXT_AND_SPEECH are indistinguishable here and all
    keep exactly today's behavior. Only NONE is representable at all: an
    empty speech string (not skipping the call) keeps the response shape
    HA's own frontend expects, while being as close to "no output" as this
    layer supports.
    """
    response = intent.IntentResponse(language=language)
    if result.status is ConversationStatus.ERROR:
        response.async_set_error(
            intent.IntentResponseErrorCode.FAILED_TO_HANDLE, result.response_text
        )
    elif result.status is ConversationStatus.REJECTED:
        response.async_set_error(
            intent.IntentResponseErrorCode.NO_VALID_TARGETS, result.response_text
        )
    elif result.status is ConversationStatus.NO_MATCH:
        response.async_set_error(
            intent.IntentResponseErrorCode.NO_INTENT_MATCH, result.response_text
        )
    elif result.status is ConversationStatus.BACKEND_UNAVAILABLE:
        response.async_set_error(
            intent.IntentResponseErrorCode.FAILED_TO_HANDLE, result.response_text
        )
    else:
        response.async_set_speech("" if result.output_mode == "NONE" else result.response_text)
        if any(er.status is ExecutionStatus.QUERY_ANSWER for er in result.execution_results):
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
    return response


class SpellbookerConversationEntity(ConversationEntity):
    """One entity class serves both shapes a spellbooker agent comes
    in - a standalone SPELLBOOK_ONLY entry (const.ENTRY_KIND_SPELLBOOKER,
    `subentry=None`) and a SATELLITE agent (`subentry` set, a
    ConfigSubentry of type const.SUBENTRY_TYPE_SATELLITE living on its
    backing CODE27 device's own entry) - since the actual per-message
    work (build a request, call the router, map the result back) is
    identical either way; only where settings are read from and
    whether a device is registered differs.

    A SATELLITE entity's device is registered under its subentry
    (config_subentry_id, passed to async_add_entities in
    async_setup_entry) - HA's own native mechanism for a device-scoped
    child configuration that renders nested under its parent hub in
    the UI (e.g. "CODE27 Papa" -> "Lilya Satellite"), replacing an
    earlier attempt with a second top-level entry kind linked only via
    via_device/via_device_id, which HA's integrations page still shows
    as its own separate hub regardless (config entries, not device
    links, are what that page groups by) - see
    const.ENTRY_KIND_SPELLBOOKER_SATELLITE's own note. A plain
    SPELLBOOK_ONLY entry gets no device_info and stays a flat
    top-level entry, exactly as before.

    Experimental for SATELLITE: stage-1/README.md's own required live
    capability test for CODE27 backend reply correlation was never
    run - see providers/code27/backend.py's docstring. The underlying wiring is
    real, not a stub; "experimental" describes the unproven
    correlation guarantee under concurrent physical device use, not
    missing functionality."""

    _attr_has_entity_name = True

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, *, subentry: ConfigSubentry | None = None
    ) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._subentry = subentry

        if subentry is not None:
            # Satellite entities are a legacy provider-specific surface. Keep
            # its imports lazy so a missing optional provider cannot prevent a
            # standalone Spellbooker from loading.
            from .providers.code27.const import MANUFACTURER

            self._attr_unique_id = subentry.subentry_id
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, subentry.subentry_id)},
                manufacturer=MANUFACTURER,
                model="HASE-Agent Satellite",
                name=subentry.title,
            )
            # has_entity_name=True + a device present + _attr_name=None
            # shows just the device's own name for this entity, not
            # "<device> <entity>" - this entity IS the device, there's
            # nothing else under it to disambiguate from.
            self._attr_name = None
        else:
            self._attr_unique_id = config_entry.entry_id
            # No device_info - this entity IS the whole config entry,
            # not a feature of some other device - so with
            # has_entity_name=True and no device to inherit a name
            # from, _attr_name has to be set explicitly or the entity
            # ends up unnamed everywhere that shows friendly_name
            # (entity list, Logbook, history), even though the entry
            # itself has a name the user chose.
            self._attr_name = config_entry.title

        # One Router (and the InteractionStateStore it owns) for this
        # entity's whole lifetime, not per message - a fresh store on
        # every turn would silently drop every open clarification the
        # moment it was created. Any options/subentry change reloads
        # the whole owning entry (_async_spellbooker_update_listener in
        # __init__.py for SPELLBOOK_ONLY; async_update_reload_and_abort
        # in config_flow.py's SatelliteSubentryFlow for SATELLITE),
        # which recreates this entity and so also discards any dialog
        # left open under the old book/policy - never answered against
        # a stale configuration.
        self._router = SpellbookRouter(
            ActionExecutor(
                HaTargetResolver(hass), HaServiceCaller(hass),
                expert_allowed=lambda: getattr(hass, "data", {}).get("hase_studio_permissions", {}).get(
                    (self._subentry.data if self._subentry is not None else self._config_entry.options).get(CONF_SPELLBOOKER_BOOK),
                    False,
                ) is True,
            ),
            # Reused verbatim from providers/mcp/transport.py's own wiring -
            # the SAME real AssistForwarder implementation, not a second
            # one, lets a text-only Spell's assist_redirect=True execution
            # kind reach Home Assistant's own configured conversation agent
            # through native voice/Satellite too (core.router.SpellbookRouter
            # ._execute_text_only_and_respond), closing the gap where this
            # execution kind was previously invisible to SATELLITE/
            # SPELLBOOK_ONLY matching entirely (spellbook_engine.match()).
            assist_forwarder=HaAssistForwarder(hass),
        )

    @property
    def supported_languages(self) -> list[str] | str:
        return "*"

    async def _async_handle_message(
        self, user_input: ConversationInput, chat_log: Any
    ) -> HAConversationResult:
        language = user_input.language or self.hass.config.language or "de"
        # chat_log.conversation_id is the framework's OWN already-
        # resolved id for this turn (it creates/persists the chat
        # session before this handler ever runs) - using it, rather
        # than minting a second independent id from
        # user_input.conversation_id, keeps our session identity
        # (core/session.py) and HA's own chat history pointed at the
        # same conversation. Falling back to user_input.conversation_id
        # only covers a chat_log that (in tests, or an older HA) has no
        # conversation_id of its own yet.
        conversation_token = (
            getattr(chat_log, "conversation_id", None) or user_input.conversation_id or str(uuid.uuid4())
        )

        if self._subentry is not None:
            # A satellite's mode is always SATELLITE - nothing else
            # could ever be true for something that only exists as a
            # SUBENTRY_TYPE_SATELLITE subentry, so unlike a
            # SPELLBOOK_ONLY entry's options, there's no stored mode
            # flag to read here at all.
            settings = self._subentry.data
            mode = FrontendMode.SATELLITE
        else:
            settings = self._config_entry.options
            mode = FrontendMode.SPELLBOOK_ONLY

        # A satellite has no local spellbook (older satellites may still carry
        # one in their data - ignored): everything goes to the CODE27 character.
        book_id = None if mode is FrontendMode.SATELLITE else settings.get(CONF_SPELLBOOKER_BOOK)
        if not book_id:
            if mode is not FrontendMode.SATELLITE:
                # A SPELLBOOK_ONLY entry has no backend to fall
                # through to - a missing book here is a genuine
                # misconfiguration, not a valid "do nothing locally"
                # state.
                response = intent.IntentResponse(language=language)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    "Kein Spellbook konfiguriert.",
                )
                return HAConversationResult(response=response, conversation_id=conversation_token)
            # SATELLITE with no local book AT ALL (never configured) -
            # every utterance falls straight through to the CODE27
            # backend below via EngineNoMatch, never a hard error. This
            # is deliberately NOT the same branch as "a book WAS
            # configured but failed to load" below - see that
            # branch's own note on why those two must never be
            # conflated.
            book = _EMPTY_SATELLITE_BOOK
        else:
            book = await async_get_spellbook(self.hass, book_id)
            if book is None:
                # A book was explicitly configured (book_id is set)
                # but couldn't load - missing file, unparseable YAML,
                # or every one of its commands failed validation (see
                # spellbook_engine.load_spellbook_from_dict's own
                # note). This must fail closed with a real error,
                # for BOTH modes - falling through to "no local
                # book"/backend or to NO_MATCH here would silently
                # hide a real misconfiguration behind behavior that
                # looks like a deliberate choice. Found live (external
                # audit): a SATELLITE with a deleted book file was
                # reaching the backend as if no book had ever been
                # configured at all.
                response = intent.IntentResponse(language=language)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Spellbook „{book_id}“ konnte nicht geladen werden.",
                )
                return HAConversationResult(response=response, conversation_id=conversation_token)

        actions_policy = AreaPolicy(
            allowed_areas=frozenset(settings.get(CONF_SPELLBOOKER_ALLOWED_AREAS, [])),
            unrestricted=bool(settings.get(CONF_SPELLBOOKER_UNRESTRICTED, False)),
        )
        if (
            CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS in settings
            or CONF_SPELLBOOKER_QUERIES_UNRESTRICTED in settings
        ):
            queries_policy = AreaPolicy(
                allowed_areas=frozenset(settings.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, [])),
                unrestricted=bool(settings.get(CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, False)),
            )
        else:
            # This entry/subentry predates the separate queries policy
            # (#11) - mirror the actions policy exactly, so a config
            # saved before this existed keeps its old single-policy
            # behavior unchanged rather than crashing or silently
            # becoming unrestricted for queries. Only a re-save
            # through the updated options/subentry form ever sets
            # these keys.
            queries_policy = actions_policy
        # A CODE27 device may limit its own sentences further (options
        # "Allowed areas"): the stricter of both rules wins.
        limit = (self.hass.data.get(CODIE_AREA_LIMITS_KEY) or {}).get(user_input.device_id or "")
        if limit is not None:  # (actions limit, queries limit), each None = no extra limit
            actions_policy = actions_policy.narrowed_to(limit[0])
            queries_policy = queries_policy.narrowed_to(limit[1])
        policy = ScopedAreaPolicy(actions=actions_policy, queries=queries_policy)
        no_match_response = (
            settings.get(CONF_SPELLBOOKER_NO_MATCH_RESPONSE) or DEFAULT_NO_MATCH_RESPONSE
        )
        backend = None
        backend_instance_id = None
        profile_id = None
        if mode is FrontendMode.SATELLITE:
            # The backing CODE27 device is simply whichever entry this
            # satellite's subentry lives on - never a separately
            # stored/selected field, see SUBENTRY_TYPE_SATELLITE's own
            # note.
            backend_instance_id = self._config_entry.entry_id
            profile_id = settings.get(CONF_SPELLBOOKER_PROFILE_ID)
            coordinator = self.hass.data.get(DOMAIN, {}).get(backend_instance_id)
            if coordinator is not None:
                from .providers.code27.backend import Code27ConversationBackend

                # The speaking device's HA area travels with the sentence, so
                # "Licht an" from a kitchen satellite means the kitchen.
                from homeassistant.helpers import area_registry as ar

                area_id = (
                    resolve_source_area_id(self.hass, user_input.device_id)
                    if settings.get(CONF_SPELLBOOKER_PASS_AREA, True) else None
                )
                area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
                backend = Code27ConversationBackend(
                    self.hass, coordinator, source_area=area.name if area is not None else None
                )
            # coordinator is None here whenever the backend CODE27
            # entry was removed, isn't loaded (yet), or the option
            # never resolved - backend stays None and
            # core.router.SpellbookRouter itself treats a SATELLITE
            # AgentConfig with no backend as a hard configuration
            # error, never a silent fall-back to SPELLBOOK_ONLY.
        config = AgentConfig(
            mode=mode,
            spellbook=book,
            policy=policy,
            no_match_response=no_match_response,
            backend=backend,
            backend_instance_id=backend_instance_id,
            profile_id=profile_id,
        )

        request = ConversationRequest(
            request_id=str(uuid.uuid4()),
            # A SATELLITE's own subentry_id, not the parent CODE27
            # device entry it lives on - two satellites sharing that
            # parent (and, if misconfigured, even the same profile_id)
            # must never end up with the same session identity purely
            # because they share a parent entry. The backend/coordinator
            # lookup is unaffected: that stays keyed by
            # config.backend_instance_id (self._config_entry.entry_id,
            # the parent), set separately below - only the SESSION
            # identity changes here. Found live (external audit).
            agent_entry_id=self._subentry.subentry_id if self._subentry is not None else self._config_entry.entry_id,
            text=user_input.text,
            language=language,
            source_namespace="ha_conversation",
            source_id=user_input.device_id,
            conversation_token=conversation_token,
            context=user_input.context,
            source_area_id=resolve_source_area_id(self.hass, user_input.device_id),
        )

        started_at = time.monotonic()
        result = await self._router.handle(request, config)
        for execution in result.execution_results:
            record_execution(
                self.hass, channel="conversation", book_id=book_id,
                spell_id=result.spell_id, result=execution,
                started_at=started_at, correlation_id=request.request_id,
            )

        add_assistant_content = getattr(chat_log, "async_add_assistant_content_without_tools", None)
        if add_assistant_content is not None:
            from homeassistant.components import conversation as ha_conversation

            add_assistant_content(
                ha_conversation.AssistantContent(agent_id=user_input.agent_id, content=result.response_text)
            )

        return HAConversationResult(
            response=intent_response_for(language, result),
            conversation_id=conversation_token,
            continue_conversation=result.status is ConversationStatus.CONFIRMATION_REQUIRED,
        )
