"""Configure HASE standalone agents and companion providers."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ENTRY_KIND,
    CONF_SPELLBOOKER_ALLOWED_AREAS,
    CONF_SPELLBOOKER_BOOK,
    CONF_SPELLBOOKER_MODE,
    CONF_SPELLBOOKER_NO_MATCH_RESPONSE,
    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
    CONF_SPELLBOOKER_UNRESTRICTED,
    DOMAIN,
    ENTRY_KIND_CODE27_DEVICE,
    ENTRY_KIND_MCP_SERVER,
    ENTRY_KIND_SPELLBOOKER,
    SPELLBOOKER_MODE_SPELLBOOK_ONLY,
    SUBENTRY_TYPE_MCP_BRIDGE,
    SUBENTRY_TYPE_SATELLITE,
)
from .providers.code27.config_flow import (
    Code27FlowMixin,
    Code27OptionsFlow,
    SatelliteSubentryFlow,
)
from .providers.code27.const import CONF_BRIDGE_AI_TASK_ENTITY_ID
from .providers.mcp.config_flow import (
    McpBridgeSubentryFlow,
    McpFlowMixin,
    McpServerOptionsFlow,
    async_mcp_server_exists,
)
from .spellbook_loader import async_discover_spellbooks


class HaseConfigFlow(Code27FlowMixin, McpFlowMixin, ConfigFlow, domain=DOMAIN):
    """Configure HASE agents and companion providers."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Three independent things live behind "Add Integration -> HASE":
        pairing an actual CODE27 device, a standalone SPELLBOOK_ONLY
        Spellbooker agent that needs no device at all, or the singleton
        MCP Server. A plain spellbooker entry must never require
        CONF_HOST - see __init__.py's entry_kind branch and stage-1/
        README.md's Policy-und-Entry-Lifecycle section. Neither a
        SATELLITE agent nor an MCP Bridge is created here - both are
        subentries added from an existing parent entry's own page
        (SatelliteSubentryFlow/McpBridgeSubentryFlow below), so each
        renders nested under its parent hub instead of as its own
        top-level entry. Once an MCP Server already exists, its menu
        option is omitted entirely - see async_mcp_server_exists."""
        menu_options = ["code27_device", "spellbooker"]
        if not async_mcp_server_exists(self.hass):
            menu_options.append("mcp_server")
        return self.async_show_menu(step_id="user", menu_options=menu_options)

    async def async_step_spellbooker(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A standalone SPELLBOOK_ONLY Spellbooker conversation agent -
        no device, no backend, always its own top-level entry. Satellites are configured as subentries on the CODE27 provider entry."""
        errors: dict[str, str] = {}
        book_choices = await async_discover_spellbooks(self.hass)
        if user_input is not None:
            if not book_choices:
                errors["base"] = "no_spellbook_available"
            elif not user_input.get(
                CONF_SPELLBOOKER_UNRESTRICTED
            ) and not user_input.get(CONF_SPELLBOOKER_ALLOWED_AREAS):
                errors["base"] = "areas_or_unrestricted_required"
            elif not user_input.get(
                CONF_SPELLBOOKER_QUERIES_UNRESTRICTED
            ) and not user_input.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS):
                errors["base"] = "queries_areas_or_unrestricted_required"
            else:
                return self.async_create_entry(
                    title=user_input["name"],
                    data={CONF_ENTRY_KIND: ENTRY_KIND_SPELLBOOKER},
                    options={
                        CONF_SPELLBOOKER_MODE: SPELLBOOKER_MODE_SPELLBOOK_ONLY,
                        CONF_SPELLBOOKER_BOOK: user_input[CONF_SPELLBOOKER_BOOK],
                        CONF_SPELLBOOKER_ALLOWED_AREAS: user_input.get(
                            CONF_SPELLBOOKER_ALLOWED_AREAS, []
                        ),
                        CONF_SPELLBOOKER_UNRESTRICTED: user_input.get(
                            CONF_SPELLBOOKER_UNRESTRICTED, False
                        ),
                        CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS: user_input.get(
                            CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, []
                        ),
                        CONF_SPELLBOOKER_QUERIES_UNRESTRICTED: user_input.get(
                            CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, False
                        ),
                        CONF_SPELLBOOKER_NO_MATCH_RESPONSE: user_input.get(
                            CONF_SPELLBOOKER_NO_MATCH_RESPONSE, ""
                        ),
                        CONF_BRIDGE_AI_TASK_ENTITY_ID: user_input.get(
                            CONF_BRIDGE_AI_TASK_ENTITY_ID
                        ),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required("name", default="HASE-Agent"): str,
                vol.Required(CONF_SPELLBOOKER_BOOK): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=book_id, label=name)
                            for book_id, name in book_choices
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SPELLBOOKER_ALLOWED_AREAS, default=[]
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(CONF_SPELLBOOKER_UNRESTRICTED, default=False): bool,
                vol.Optional(
                    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, default=[]
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, default=False
                ): bool,
                vol.Optional(CONF_SPELLBOOKER_NO_MATCH_RESPONSE, default=""): str,
                vol.Optional(CONF_BRIDGE_AI_TASK_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="ai_task")
                ),
            }
        )
        return self.async_show_form(
            step_id="spellbooker", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        entry_kind = config_entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_CODE27_DEVICE)
        if entry_kind == ENTRY_KIND_SPELLBOOKER:
            return SpellbookerOptionsFlow(config_entry)
        if entry_kind == ENTRY_KIND_MCP_SERVER:
            return McpServerOptionsFlow(config_entry)
        return Code27OptionsFlow(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """A SATELLITE agent is only offered as a subentry of a CODE27
        DEVICE entry - it needs one to bind to, and this is what makes
        it render nested under that device's own hub instead of as a
        separate top-level entry (see SatelliteSubentryFlow, and
        ENTRY_KIND_SPELLBOOKER_SATELLITE's own note on why this
        replaced a second top-level entry kind). An MCP Bridge is offered
        the identical way, but only on the singleton MCP Server entry -
        see McpBridgeSubentryFlow's own docstring."""
        entry_kind = config_entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_CODE27_DEVICE)
        if entry_kind == ENTRY_KIND_CODE27_DEVICE:
            return {SUBENTRY_TYPE_SATELLITE: SatelliteSubentryFlow}
        if entry_kind == ENTRY_KIND_MCP_SERVER:
            return {SUBENTRY_TYPE_MCP_BRIDGE: McpBridgeSubentryFlow}
        return {}


class SpellbookerOptionsFlow(OptionsFlow):
    """Options for a standalone spellbooker entry: which book, which
    areas, no-match phrasing. No mode selector yet - SPELLBOOK_ONLY
    only until Etappe 6."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Required entry-point method name for any OptionsFlow - but it
        just hands off to async_step_spellbooker immediately, so the
        actual form renders under its own step_id/translation key
        instead of colliding with Code27OptionsFlow's "init" step
        (same domain, same options.step.init.* strings otherwise)."""
        return await self.async_step_spellbooker(user_input)

    async def async_step_spellbooker(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self._config_entry.options
        book_choices = await async_discover_spellbooks(self.hass)

        if user_input is not None:
            if not user_input.get(CONF_SPELLBOOKER_UNRESTRICTED) and not user_input.get(
                CONF_SPELLBOOKER_ALLOWED_AREAS
            ):
                errors["base"] = "areas_or_unrestricted_required"
            elif not user_input.get(
                CONF_SPELLBOOKER_QUERIES_UNRESTRICTED
            ) and not user_input.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS):
                errors["base"] = "queries_areas_or_unrestricted_required"
            else:
                # async_create_entry(data=...) here REPLACES the whole
                # options dict with just this form's fields - the mode
                # flag has no field on this form, so it must be re-set
                # explicitly or it would quietly vanish from an
                # existing entry's options the first time someone
                # saves its options.
                return self.async_create_entry(
                    data={
                        **user_input,
                        CONF_SPELLBOOKER_MODE: SPELLBOOKER_MODE_SPELLBOOK_ONLY,
                    }
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SPELLBOOKER_BOOK,
                    default=options.get(CONF_SPELLBOOKER_BOOK),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=book_id, label=name)
                            for book_id, name in book_choices
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SPELLBOOKER_ALLOWED_AREAS,
                    default=options.get(CONF_SPELLBOOKER_ALLOWED_AREAS, []),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_SPELLBOOKER_UNRESTRICTED,
                    default=options.get(CONF_SPELLBOOKER_UNRESTRICTED, False),
                ): bool,
                # Defaults mirror the actions fields above whenever
                # this entry predates the separate queries policy
                # (#11) - re-saving this form unchanged on an old
                # entry must reproduce the exact same effective
                # behavior it already had, not silently narrow query
                # access to nothing. See conversation.py's matching
                # mirror-fallback on the read side.
                vol.Optional(
                    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
                    default=options.get(
                        CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
                        options.get(CONF_SPELLBOOKER_ALLOWED_AREAS, []),
                    ),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
                    default=options.get(
                        CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
                        options.get(CONF_SPELLBOOKER_UNRESTRICTED, False),
                    ),
                ): bool,
                vol.Optional(
                    CONF_SPELLBOOKER_NO_MATCH_RESPONSE,
                    default=options.get(CONF_SPELLBOOKER_NO_MATCH_RESPONSE, ""),
                ): str,
                vol.Optional(
                    CONF_BRIDGE_AI_TASK_ENTITY_ID,
                    description={
                        "suggested_value": options.get(CONF_BRIDGE_AI_TASK_ENTITY_ID)
                    },
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="ai_task")
                ),
            }
        )
        return self.async_show_form(
            step_id="spellbooker", data_schema=schema, errors=errors
        )
