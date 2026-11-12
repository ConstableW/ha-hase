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
    CONF_BRIDGE_AI_TASK_ENTITY_ID,
    CONF_CODE27_EXTERNAL_ENTRY_ID,
    CONF_ENTRY_KIND,
    CONF_SPELLBOOKER_ALLOWED_AREAS,
    CONF_SPELLBOOKER_BOOK,
    CONF_SPELLBOOKER_MODE,
    CONF_SPELLBOOKER_NO_MATCH_RESPONSE,
    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
    CONF_SPELLBOOKER_UNRESTRICTED,
    DOMAIN,
    ENTRY_KIND_CODE27_EXTERNAL_HUB,
    ENTRY_KIND_SPELLBOOKER,
    SPELLBOOKER_MODE_SPELLBOOK_ONLY,
)
from .providers.registry import get_provider_registry
from .spellbook_loader import async_discover_spellbooks

_PROVIDER_FLOW_MIXINS = tuple(
    provider.config_flow.flow_mixin
    for provider in get_provider_registry().providers
    if provider.config_flow is not None
)


class HaseConfigFlow(*_PROVIDER_FLOW_MIXINS, ConfigFlow, domain=DOMAIN):
    """Configure HASE agents and companion providers."""

    VERSION = 1

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rebind an external hub while leaving CODE27 ownership untouched."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry.data.get(CONF_ENTRY_KIND) != ENTRY_KIND_CODE27_EXTERNAL_HUB:
            return await super().async_step_reconfigure(user_input)

        choices = [
            selector.SelectOptionDict(value=target.entry_id, label=target.title)
            for target in self.hass.config_entries.async_entries("code27")
        ]
        if not choices:
            return self.async_abort(reason="no_code27_entry")

        errors: dict[str, str] = {}
        if user_input is not None:
            target_id = user_input[CONF_CODE27_EXTERNAL_ENTRY_ID]
            target = self.hass.config_entries.async_get_entry(target_id)
            if target is None or target.domain != "code27":
                errors[CONF_CODE27_EXTERNAL_ENTRY_ID] = "no_code27_entry"
            elif any(
                other.entry_id != entry.entry_id
                and other.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_CODE27_EXTERNAL_HUB
                and other.data.get(CONF_CODE27_EXTERNAL_ENTRY_ID) == target_id
                for other in self.hass.config_entries.async_entries(DOMAIN)
            ):
                errors[CONF_CODE27_EXTERNAL_ENTRY_ID] = "external_already_bound"
            else:
                return self.async_update_and_abort(
                    entry,
                    data_updates={CONF_CODE27_EXTERNAL_ENTRY_ID: target_id},
                )

        return self.async_show_form(
            step_id="reconfigure_external_hub",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_CODE27_EXTERNAL_ENTRY_ID,
                    default=(
                        entry.data[CONF_CODE27_EXTERNAL_ENTRY_ID]
                        if any(
                            choice["value"] == entry.data[CONF_CODE27_EXTERNAL_ENTRY_ID]
                            for choice in choices
                        )
                        else choices[0]["value"]
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=choices, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the core Spellbooker and each currently available provider."""
        registry = get_provider_registry(self.hass)
        menu_options = ["spellbooker"]
        if self.hass.config_entries.async_entries("code27"):
            menu_options.append("code27_external_hub")
        menu_options.extend(
            provider.config_flow.step_id
            for provider in registry.providers
            if provider.config_flow is not None
            and provider.config_flow.is_available(self.hass)
        )
        return self.async_show_menu(step_id="user", menu_options=menu_options)

    async def async_step_code27_external_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind a standalone CODE27 entry without owning its device."""
        entries = self.hass.config_entries.async_entries("code27")
        choices = [
            selector.SelectOptionDict(value=entry.entry_id, label=entry.title)
            for entry in entries
        ]
        if not choices:
            return self.async_abort(reason="no_code27_entry")
        if user_input is not None:
            target_id = user_input[CONF_CODE27_EXTERNAL_ENTRY_ID]
            target = self.hass.config_entries.async_get_entry(target_id)
            if target is None or target.domain != "code27":
                return self.async_abort(reason="no_code27_entry")
            for existing in self.hass.config_entries.async_entries(DOMAIN):
                if (
                    existing.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_CODE27_EXTERNAL_HUB
                    and existing.data.get(CONF_CODE27_EXTERNAL_ENTRY_ID) == target_id
                ):
                    return self.async_abort(reason="external_already_bound")
            return self.async_create_entry(
                title=f"HASE → {target.title}",
                data={
                    CONF_ENTRY_KIND: ENTRY_KIND_CODE27_EXTERNAL_HUB,
                    CONF_CODE27_EXTERNAL_ENTRY_ID: target_id,
                },
            )
        return self.async_show_form(
            step_id="code27_external_hub",
            data_schema=vol.Schema({
                vol.Required(CONF_CODE27_EXTERNAL_ENTRY_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=choices, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }),
        )

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
        if config_entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_SPELLBOOKER:
            return SpellbookerOptionsFlow(config_entry)
        if config_entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_CODE27_EXTERNAL_HUB:
            from .providers.code27.config_flow import Code27OptionsFlow
            return Code27OptionsFlow(config_entry)
        provider = get_provider_registry().resolve_entry(config_entry)
        if (
            provider is not None
            and provider.config_flow is not None
            and provider.config_flow.options_flow_factory is not None
        ):
            return provider.config_flow.options_flow_factory(config_entry)
        return UnavailableProviderOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry flow types contributed by the owning provider."""
        if config_entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_CODE27_EXTERNAL_HUB:
            from .const import SUBENTRY_TYPE_SATELLITE
            from .providers.code27.config_flow import SatelliteSubentryFlow
            return {SUBENTRY_TYPE_SATELLITE: SatelliteSubentryFlow}
        provider = get_provider_registry().resolve_entry(config_entry)
        if provider is None or provider.config_flow is None:
            return {}
        return dict(provider.config_flow.subentry_flows)


class UnavailableProviderOptionsFlow(OptionsFlow):
    """Report that an entry no longer has an installed options provider."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="provider_not_available")


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
