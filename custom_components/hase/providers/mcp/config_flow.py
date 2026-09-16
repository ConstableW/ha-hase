"""Config flow for the MCP Server/Bridge infrastructure - HASE MCP
architecture, Phase 3.

The MCP Server is infrastructure only - it owns no Spellbook, area, scope,
persona or CODE27 character slot, exactly one may exist, and its own form
has nothing to configure beyond a name. An MCP Bridge is a subentry of
that server, conceptually equivalent to a Satellite (see
core/mcp_projection.py's own architecture notes) - it reuses the EXACT
SAME CONF_SPELLBOOKER_BOOK/CONF_SPELLBOOKER_*_ALLOWED_AREAS/
CONF_SPELLBOOKER_*_UNRESTRICTED keys a Satellite subentry already uses for
Spellbook and action/query area scope, rather than redeclaring parallel
fields under different names. It does NOT reuse or reproduce anything
Satellite-specific that is unrelated to MCP - no character slot, no
no-match response, no CODE27 backend wiring.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.helpers import selector

from ...const import (
    CONF_ENTRY_KIND,
    CONF_SPELLBOOKER_ALLOWED_AREAS,
    CONF_SPELLBOOKER_BOOK,
    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
    CONF_SPELLBOOKER_UNRESTRICTED,
    DOMAIN,
    ENTRY_KIND_MCP_SERVER,
)
from ...spellbook_loader import async_discover_spellbooks, async_get_spellbook
from .const import (
    CONF_MCP_BRIDGE_AREA_ID,
    CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX,
    CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER,
    CONF_MCP_BRIDGE_LANGUAGE,
    CONF_MCP_SERVER_PORT,
    DEFAULT_MCP_SERVER_PORT,
    MCP_SERVER_TITLE,
)
from .credentials import issue_credential
from .secret_store import McpSecretStore


def async_mcp_server_exists(hass) -> bool:
    """Whether an MCP Server entry already exists - the singleton check
    both the root menu (hides the option) and this step itself (defensive
    abort if reached anyway, e.g. two concurrent flows) rely on."""
    return any(
        entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_MCP_SERVER
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


class McpFlowMixin:
    """The "Add MCP Server" step used by the HASE ConfigFlow's own menu -
    mirrors Code27FlowMixin's composition pattern exactly."""

    async def async_step_mcp_server(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if async_mcp_server_exists(self.hass):
            # Reachable only via a stale/second concurrent flow - the root
            # menu itself never offers this step once a server exists.
            return self.async_abort(reason="mcp_server_already_configured")
        if user_input is not None:
            return self.async_create_entry(
                title=MCP_SERVER_TITLE,
                data={CONF_ENTRY_KIND: ENTRY_KIND_MCP_SERVER},
                options={CONF_MCP_SERVER_PORT: user_input[CONF_MCP_SERVER_PORT]},
            )
        # No name field - MCP_SERVER_TITLE is fixed (see its own docstring).
        # The port is the one thing worth asking up front rather than only
        # via the options flow afterwards, since changing it later restarts
        # the listener - asking it here means the very first start already
        # uses the port the admin actually wants.
        schema = vol.Schema({vol.Required(CONF_MCP_SERVER_PORT, default=DEFAULT_MCP_SERVER_PORT): int})
        return self.async_show_form(step_id="mcp_server", data_schema=schema)


class McpServerOptionsFlow(OptionsFlow):
    """The MCP Server entry owns exactly one thing: its own dedicated
    listener port (Phase 4 - infrastructure/transport, not Spellbook/
    area/scope, which all live on its Bridge subentries instead, see this
    module's own docstring). Changing it reloads the entry (the same
    "any change reloads" convention as everywhere else in HASE), which
    stops the old listener and starts a new one on the new port - see
    providers/mcp/__init__.py's async_stop_transport/async_start_transport."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Required entry-point method name for any OptionsFlow - hands
        off immediately so the actual form renders under its own step_id/
        translation key instead of colliding with Code27OptionsFlow's own
        "init" step (same domain, same options.step.init.* strings
        otherwise) - the exact same reason SpellbookerOptionsFlow already
        does this."""
        return await self.async_step_mcp_server(user_input)

    async def async_step_mcp_server(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self._config_entry.options
        schema = vol.Schema({
            vol.Required(
                CONF_MCP_SERVER_PORT,
                default=options.get(CONF_MCP_SERVER_PORT, DEFAULT_MCP_SERVER_PORT),
            ): int,
        })
        return self.async_show_form(step_id="mcp_server", data_schema=schema)


def _no_local_book_label(hass) -> str:
    return "— Kein Spellbook —" if (hass.config.language or "en").lower().startswith("de") else "— No spellbook —"


def _use_ha_language_label(hass) -> str:
    return (
        "— Home-Assistant-Sprache verwenden —"
        if (hass.config.language or "en").lower().startswith("de")
        else "— Use Home Assistant language —"
    )


async def _relevant_book_languages(hass, book_id: str) -> tuple[str, ...]:
    """The language codes an MCP Bridge's language field should offer -
    the ASSIGNED book's own declared+detected languages
    (core.book_languages.languages(), computed once at load time as
    EngineSpellbook.languages - see core/spellbook_engine.py), the exact
    same set Spellbook Studio's own language management already reads.
    Never a second, hand-maintained language list. Empty (no book chosen,
    or the book failed to load) simply offers no language choices beyond
    "use Home Assistant's own language" - the selector's own
    custom_value=True still lets an admin type any code by hand, so this
    never blocks setting up a Bridge before its book is picked."""
    if not book_id:
        return ()
    book = await async_get_spellbook(hass, book_id)
    return book.languages if book is not None else ()


class McpBridgeSubentryFlow(ConfigSubentryFlow):
    """Add/reconfigure an MCP Bridge as a subentry of the singleton MCP
    Server entry - the same subentry-of-its-parent-hub pattern
    SatelliteSubentryFlow already established, so a Bridge renders nested
    under "MCP Server" in the HA UI exactly the way a Satellite renders
    nested under its CODE27 device.

    A brand-new Bridge always receives a freshly generated credential
    (shown exactly once, via async_step_reveal_credential). Reconfiguring
    an existing Bridge preserves its current credential unless the admin
    explicitly checks "Regenerate token" - the explicit regenerate
    operation the approved design requires.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending_data: dict[str, Any] | None = None
        self._pending_title: str | None = None
        self._pending_is_reconfigure: bool = False
        self._pending_token: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step_bridge(user_input, is_reconfigure=False)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step_bridge(user_input, is_reconfigure=True)

    async def _async_step_bridge(
        self, user_input: dict[str, Any] | None, *, is_reconfigure: bool,
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        book_choices = await async_discover_spellbooks(self.hass)
        existing = self._get_reconfigure_subentry() if is_reconfigure else None
        existing_data = existing.data if existing is not None else {}
        # Whichever book is currently relevant for the language field's
        # own options: what the admin just submitted (a validation-error
        # re-render still carries their book choice forward) if there is
        # one, otherwise the Bridge's already-stored book. HA's config
        # flow forms are not reactively cross-field aware within one
        # render, so a brand-new Bridge's very first render (no
        # user_input, no existing book) simply offers no book-specific
        # language yet - see _relevant_book_languages's own docstring.
        relevant_book_id = (user_input or existing_data).get(CONF_SPELLBOOKER_BOOK, "")

        if user_input is not None:
            if not user_input.get(CONF_SPELLBOOKER_UNRESTRICTED) and not user_input.get(CONF_SPELLBOOKER_ALLOWED_AREAS):
                errors["base"] = "areas_or_unrestricted_required"
            elif not user_input.get(CONF_SPELLBOOKER_QUERIES_UNRESTRICTED) and not user_input.get(
                CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS
            ):
                errors["base"] = "queries_areas_or_unrestricted_required"
            else:
                data = {
                    # "" (the "Keins" choice) means no assigned Spellbook -
                    # tools/list has nothing to project until one is set,
                    # same "valid but empty" convention as a Satellite's
                    # own book-less state.
                    CONF_SPELLBOOKER_BOOK: user_input.get(CONF_SPELLBOOKER_BOOK) or "",
                    # "" means deliberately area-less (MASTER-style) - see
                    # core/mcp_bridge_context.py's own None/error/BridgeArea
                    # distinction; an empty string here is intentionally
                    # equivalent to "no area_id configured", never a real id.
                    CONF_MCP_BRIDGE_AREA_ID: user_input.get(CONF_MCP_BRIDGE_AREA_ID) or "",
                    CONF_SPELLBOOKER_ALLOWED_AREAS: user_input.get(CONF_SPELLBOOKER_ALLOWED_AREAS, []),
                    CONF_SPELLBOOKER_UNRESTRICTED: user_input.get(CONF_SPELLBOOKER_UNRESTRICTED, False),
                    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS: user_input.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, []),
                    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED: user_input.get(CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, False),
                    CONF_MCP_BRIDGE_LANGUAGE: user_input.get(CONF_MCP_BRIDGE_LANGUAGE) or "",
                }
                # A new Bridge always gets a fresh credential; reconfigure
                # only replaces it on the explicit opt-in checkbox.
                regenerate = True if not is_reconfigure else bool(user_input.get("regenerate_credential"))
                if regenerate:
                    secret = await McpSecretStore(self.hass).async_get_secret()
                    issued = issue_credential(secret)
                    data[CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER] = issued.verifier
                    data[CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX] = issued.suffix
                    self._pending_token = issued.token
                else:
                    data[CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER] = existing_data.get(CONF_MCP_BRIDGE_CREDENTIAL_VERIFIER, "")
                    data[CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX] = existing_data.get(CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX, "")

                self._pending_data = data
                self._pending_title = user_input["name"]
                self._pending_is_reconfigure = is_reconfigure
                if regenerate:
                    return await self.async_step_reveal_credential()
                return self._async_finish()

        book_languages = await _relevant_book_languages(self.hass, relevant_book_id)
        schema = vol.Schema(
            {
                vol.Required("name", default=existing.title if existing is not None else "MCP Bridge"): str,
                vol.Optional(
                    CONF_SPELLBOOKER_BOOK,
                    default=existing_data.get(CONF_SPELLBOOKER_BOOK, ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="", label=_no_local_book_label(self.hass))
                        ]
                        + [
                            selector.SelectOptionDict(value=book_id, label=name)
                            for book_id, name in book_choices
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SPELLBOOKER_ALLOWED_AREAS,
                    default=existing_data.get(CONF_SPELLBOOKER_ALLOWED_AREAS, []),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_SPELLBOOKER_UNRESTRICTED,
                    default=existing_data.get(CONF_SPELLBOOKER_UNRESTRICTED, False),
                ): bool,
                vol.Optional(
                    CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS,
                    default=existing_data.get(CONF_SPELLBOOKER_QUERIES_ALLOWED_AREAS, []),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_SPELLBOOKER_QUERIES_UNRESTRICTED,
                    default=existing_data.get(CONF_SPELLBOOKER_QUERIES_UNRESTRICTED, False),
                ): bool,
                # Deliberately NOT adjacent to another AreaSelector field
                # (see this flow's own reordering note in docs/HASE-MCP.md's
                # "Known UI issues" section) - two ha-area-picker instances
                # placed back-to-back in this dynamically-generated HA form
                # were found to mis-target a selection into the WRONG one
                # of the pair (confirmed live, TWICE independently, under
                # two different browser viewport states, ruling out a
                # coordinate/scaling artifact - a real HA frontend
                # interaction defect, not a backend/schema validation bug).
                # IMPORTANT: the rendered field ORDER is governed by
                # strings.json's/translations/*.json's own "data" dict key
                # order for this step, NOT by this vol.Schema dict's own
                # key order (confirmed live: reordering only this schema,
                # without also reordering the matching translation files,
                # left the real form completely unchanged) - keep the
                # "data"/"data_description" key order in strings.json,
                # translations/de.json and translations/en.json's
                # config_subentries.mcp_bridge.step.user/reconfigure blocks
                # in sync with this Python order, or this fix is a no-op.
                vol.Optional(
                    CONF_MCP_BRIDGE_AREA_ID,
                    default=existing_data.get(CONF_MCP_BRIDGE_AREA_ID, ""),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=False)),
                vol.Optional(
                    CONF_MCP_BRIDGE_LANGUAGE,
                    default=existing_data.get(CONF_MCP_BRIDGE_LANGUAGE, ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value="", label=_use_ha_language_label(self.hass))]
                        + [selector.SelectOptionDict(value=lang, label=lang) for lang in book_languages],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        # A stored value from before this field became a
                        # selector, or from a book that has since changed/
                        # lost that language, must keep working exactly as
                        # it always has (core.book_languages.text()'s own
                        # fallback chain already handles a language that
                        # doesn't resolve) - never hard-reject it here.
                        custom_value=True,
                    )
                ),
                **(
                    {vol.Optional("regenerate_credential", default=False): bool}
                    if is_reconfigure
                    else {}
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure" if is_reconfigure else "user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "credential_suffix": existing_data.get(CONF_MCP_BRIDGE_CREDENTIAL_SUFFIX, "")
            }
            if is_reconfigure
            else None,
        )

    async def async_step_reveal_credential(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Shows the full bearer token exactly once - on initial creation,
        or right after an explicit regenerate. Never re-derivable from
        anything persisted (only the verifier/suffix are stored) - this is
        the one and only place the plaintext token is ever displayed."""
        if user_input is not None:
            return self._async_finish()
        return self.async_show_form(
            step_id="reveal_credential",
            data_schema=vol.Schema({}),
            description_placeholders={"token": self._pending_token or ""},
        )

    def _async_finish(self) -> SubentryFlowResult:
        assert self._pending_data is not None and self._pending_title is not None
        if self._pending_is_reconfigure:
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(),
                title=self._pending_title, data=self._pending_data,
            )
        return self.async_create_entry(title=self._pending_title, data=self._pending_data)
