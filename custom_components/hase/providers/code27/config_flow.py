"""Config flow for CODE27.

Pairing mirrors exactly what the device's own web UI does: request a
6-digit code (shown on the CODE27's physical screen), then confirm it to
receive a permanent local secret. Nothing here is invented beyond what the
device's own pairing screen already offers.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import (
    CONF_ENTRY_KIND,
    DOMAIN,
    CONF_SPELLBOOKER_PASS_AREA,
    CONF_SPELLBOOKER_PROFILE_ID,
    ENTRY_KIND_CODE27_DEVICE,
    ENTRY_KIND_CODE27_EXTERNAL_HUB,
    SUBENTRY_TYPE_MCP_BRIDGE,
)
from .api import Code27Client, Code27ConnectionError, Code27Error
from .const import (
    CONF_CODIE_AGENT,
    CONF_CODIE_ALLOWED_AREAS,
    CONF_CODIE_QUERIES_ALLOWED_AREAS,
    CONF_CODIE_QUERIES_UNRESTRICTED,
    CONF_CODIE_UNRESTRICTED,
    CONF_CODIE_TOOL_BRIDGE,
    CONF_LOCAL_SECRET,
    CONF_MACHINE_ID,
    CONF_ALARM_SWITCHES,
    CONF_REMOTE_RECOVERY_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .duplicate import find_existing_device_entry

_LOGGER = logging.getLogger(__name__)


STEP_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)

STEP_PAIR_SCHEMA = vol.Schema({vol.Required("pairing_code"): str})


class Code27FlowMixin:
    """Device pairing steps used by the HASE ConfigFlow."""

    def __init__(self) -> None:
        super().__init__()
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._client: Code27Client | None = None
        self._challenge_id: str | None = None
        self._reauth_entry: ConfigEntry | None = None
        self._paired_credentials: tuple[str, str] | None = None

    async def async_step_code27_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            session = async_get_clientsession(self.hass)
            self._client = Code27Client(session, self._host, self._port)
            try:
                await self._client.bootstrap()
            except Code27ConnectionError:
                errors["base"] = "cannot_connect"
            except Code27Error:
                errors["base"] = "unknown"
            else:
                return await self._async_request_pairing_code()

        return self.async_show_form(
            step_id="code27_device", data_schema=STEP_DEVICE_SCHEMA, errors=errors
        )

    async def _async_request_pairing_code(self) -> ConfigFlowResult:
        assert self._client is not None
        self._paired_credentials = None
        try:
            self._challenge_id = await self._client.pair_request()
        except Code27Error as err:
            _LOGGER.debug("Pairing request failed: %s", err)
            return self.async_show_form(
                step_id="code27_device",
                data_schema=STEP_DEVICE_SCHEMA,
                errors={"base": "pairing_request_failed"},
            )
        return await self.async_step_pair()

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._client is not None

        if user_input is not None:
            code = user_input["pairing_code"].strip()
            try:
                user_id, secret = await self._client.pair_confirm(
                    code, self._challenge_id or ""
                )
            except Code27Error as err:
                code_str = getattr(err, "code", None) or str(err)
                if code_str in ("PAIRING_CODE_EXPIRED", "PAIRING_CODE_USED"):
                    # The code is burned; ask the device to show a new one.
                    return await self._async_request_pairing_code()
                errors["base"] = "invalid_pairing_code"
            else:
                self._paired_credentials = (user_id, secret)
                return await self.async_step_verify()

        return self.async_show_form(
            step_id="pair",
            data_schema=STEP_PAIR_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._host or ""},
        )

    async def async_step_verify(self, user_input=None) -> ConfigFlowResult:
        """Retry the signed identity read without consuming the pairing code again."""
        assert self._client is not None and self._paired_credentials is not None
        try:
            system = await self._client.get_system()
        except Code27ConnectionError:
            error = "cannot_connect"
        except Code27Error:
            error = "unknown"
        else:
            return await self._async_finish_pairing(system)
        return self.async_show_form(
            step_id="verify", data_schema=vol.Schema({}), errors={"base": error}
        )

    async def _async_finish_pairing(self, system: dict[str, Any]) -> ConfigFlowResult:
        assert self._paired_credentials is not None
        user_id, secret = self._paired_credentials
        machine_id = system.get("machine_id") or user_id
        if not isinstance(machine_id, str) or not machine_id:
            return self.async_abort(reason="invalid_identity")

        if self._reauth_entry is not None:
            expected = (
                self._reauth_entry.data.get(CONF_MACHINE_ID)
                or self._reauth_entry.unique_id
            )
            if not expected or machine_id != expected:
                return self.async_abort(reason="wrong_device")
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data_updates={
                    CONF_USER_ID: user_id,
                    CONF_LOCAL_SECRET: secret,
                },
            )

        existing = find_existing_device_entry(self.hass, machine_id)
        if existing is not None:
            return self.async_abort(
                reason="already_configured",
                description_placeholders={"owner": existing.domain, "name": existing.title},
            )
        await self.async_set_unique_id(machine_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"CODE27 ({self._host})",
            data={
                CONF_ENTRY_KIND: ENTRY_KIND_CODE27_DEVICE,
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_USER_ID: user_id,
                CONF_LOCAL_SECRET: secret,
                CONF_MACHINE_ID: machine_id,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._host = entry_data[CONF_HOST]
        self._port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        session = async_get_clientsession(self.hass)
        self._client = Code27Client(session, self._host, self._port)
        return await self._async_request_pairing_code()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update the host/port without re-pairing.

        Verify the authenticated endpoint against the identity stored at
        pairing, including the historical user_id fallback. This is not a
        migration to a new, firmware-independent physical device identifier.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None or entry.data.get(CONF_ENTRY_KIND, ENTRY_KIND_CODE27_DEVICE) != ENTRY_KIND_CODE27_DEVICE:
            return self.async_abort(reason="not_supported")
        if CONF_HOST not in entry.data:
            return self.async_abort(reason="not_supported")
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            session = async_get_clientsession(self.hass)
            client = Code27Client(
                session,
                host,
                port,
                user_id=entry.data.get(CONF_USER_ID),
                local_secret=entry.data.get(CONF_LOCAL_SECRET),
                machine_id=entry.data.get(CONF_MACHINE_ID),
            )
            try:
                system = await client.get_system()
            except Code27ConnectionError:
                errors["base"] = "cannot_connect"
            except Code27Error:
                errors["base"] = "unknown"
            else:
                machine_id = system.get("machine_id") or entry.data.get(CONF_USER_ID)
                expected = entry.data.get(CONF_MACHINE_ID) or entry.unique_id
                if not machine_id or machine_id != expected:
                    return self.async_abort(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_PORT: port},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST, "")
                    ): str,
                    vol.Required(
                        CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                    ): int,
                }
            ),
            errors=errors,
        )


class SatelliteSubentryFlow(ConfigSubentryFlow):
    """Add/reconfigure a SATELLITE agent as a subentry of its backing
    CODE27 device's own entry - HA's native mechanism for "a device-
    scoped child configuration that still renders nested under its
    parent hub", which a second top-level entry kind could never
    achieve regardless of via_device_id (see
    ENTRY_KIND_SPELLBOOKER_SATELLITE's own note). "Which CODE27
    device" is therefore never a field on this form - it's simply
    whichever device entry this subentry was added from.

    Experimental: stage-1/README.md's own required live capability
    test for reply correlation (matching CODE27's chat reply back to
    THIS request, not a physically concurrent one) was never run -
    see providers/code27/backend.py's docstring and strings.json's own warning
    text on this step."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_satellite(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_satellite(user_input)

    async def _async_step_satellite(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        is_reconfigure = self.source == "reconfigure"
        existing = self._get_reconfigure_subentry() if is_reconfigure else None
        existing_data = existing.data if existing is not None else {}

        if user_input is not None:
            if not user_input.get(CONF_SPELLBOOKER_PROFILE_ID, "").strip().isdigit():
                errors["base"] = "invalid_profile_id"
            else:
                # Only the character slot: the satellite hands every sentence
                # to the character; switching is done by Codie's own tools
                # (the CODE27 options hold the agent and the area limits).
                data = {
                    CONF_SPELLBOOKER_PROFILE_ID: user_input[CONF_SPELLBOOKER_PROFILE_ID].strip(),
                    CONF_SPELLBOOKER_PASS_AREA: bool(user_input.get(CONF_SPELLBOOKER_PASS_AREA, True)),
                }
                if is_reconfigure:
                    # NOT async_update_reload_and_abort: it raises if the
                    # parent entry has ANY update_listener, and the CODE27
                    # device entry always has one - see __init__.py. That
                    # listener also reloads the entry when a satellite
                    # subentry changes. self._get_entry() needs HA 2025.4.0+
                    # (hacs.json's minimum).
                    return self.async_update_and_abort(
                        self._get_entry(), existing, title=user_input["name"], data=data
                    )
                return self.async_create_entry(title=user_input["name"], data=data)

        schema = vol.Schema(
            {
                vol.Required(
                    "name",
                    default=existing.title if existing is not None else "Satellite",
                ): str,
                vol.Required(
                    CONF_SPELLBOOKER_PROFILE_ID,
                    default=existing_data.get(CONF_SPELLBOOKER_PROFILE_ID, "1"),
                ): str,
                vol.Required(
                    CONF_SPELLBOOKER_PASS_AREA,
                    default=existing_data.get(CONF_SPELLBOOKER_PASS_AREA, True),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="reconfigure" if is_reconfigure else "user",
            data_schema=schema,
            errors=errors,
        )


def _mcp_bridge_choices(hass) -> list[selector.SelectOptionDict]:
    """Every configured MCP Bridge, value "<entry_id>:<subentry_id>"."""
    out: list[selector.SelectOptionDict] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        for sub_id, sub in entry.subentries.items():
            if sub.subentry_type == SUBENTRY_TYPE_MCP_BRIDGE:
                out.append(selector.SelectOptionDict(value=f"{entry.entry_id}:{sub_id}", label=sub.title))
    return out


class Code27OptionsFlow(OptionsFlow):
    """Options for CODE27: polling interval."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CODIE_UNRESTRICTED, True) and not user_input.get(CONF_CODIE_ALLOWED_AREAS):
                errors["base"] = "codie_areas_or_unrestricted_required"
            elif not user_input.get(CONF_CODIE_QUERIES_UNRESTRICTED, True) and not user_input.get(
                CONF_CODIE_QUERIES_ALLOWED_AREAS
            ):
                errors["base"] = "codie_queries_areas_or_unrestricted_required"
            else:
                return self.async_create_entry(data=user_input)

        options = self._config_entry.options
        bridge_none = "— Aus —" if (self.hass.config.language or "en").lower().startswith("de") else "— Off —"
        tool_bridge_options = [selector.SelectOptionDict(value="", label=bridge_none)] + _mcp_bridge_choices(self.hass)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CODIE_TOOL_BRIDGE,
                    default=options.get(CONF_CODIE_TOOL_BRIDGE, ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=tool_bridge_options, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                ),
                vol.Optional(
                    CONF_CODIE_AGENT,
                    description={"suggested_value": options.get(CONF_CODIE_AGENT)},
                ): selector.ConversationAgentSelector(
                    selector.ConversationAgentSelectorConfig()
                ),
                vol.Required(
                    CONF_CODIE_UNRESTRICTED,
                    default=options.get(CONF_CODIE_UNRESTRICTED, True),
                ): bool,
                vol.Optional(
                    CONF_CODIE_ALLOWED_AREAS,
                    default=options.get(CONF_CODIE_ALLOWED_AREAS, []),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_CODIE_QUERIES_UNRESTRICTED,
                    default=options.get(CONF_CODIE_QUERIES_UNRESTRICTED, True),
                ): bool,
                vol.Optional(
                    CONF_CODIE_QUERIES_ALLOWED_AREAS,
                    default=options.get(CONF_CODIE_QUERIES_ALLOWED_AREAS, []),
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Required(
                    CONF_REMOTE_RECOVERY_ENABLED,
                    default=options.get(CONF_REMOTE_RECOVERY_ENABLED, False),
                ): bool,
                vol.Required(
                    CONF_ALARM_SWITCHES,
                    default=options.get(CONF_ALARM_SWITCHES, True),
                ): bool,
            }
        )
        if self._config_entry.data.get(CONF_ENTRY_KIND) == ENTRY_KIND_CODE27_EXTERNAL_HUB:
            # The standalone owner controls its own polling and IP recovery.
            schema = vol.Schema({
                key: value for key, value in schema.schema.items()
                if getattr(key, "schema", key) not in (
                    CONF_SCAN_INTERVAL, CONF_REMOTE_RECOVERY_ENABLED
                )
            })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
