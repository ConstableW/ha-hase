"""Standalone CODE27 pairing and options flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Code27Client, Code27ConnectionError, Code27Error
from .const import (
    CONF_LOCAL_SECRET,
    CONF_MACHINE_ID,
    CONF_ALARM_SWITCHES,
    CONF_REMOTE_RECOVERY_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .duplicate import find_existing_device_entry

_LOGGER = logging.getLogger(__name__)
DEVICE_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str, vol.Required(CONF_PORT, default=DEFAULT_PORT): int})
PAIR_SCHEMA = vol.Schema({vol.Required("pairing_code"): str})


class Code27ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one standalone CODE27 device entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port = DEFAULT_PORT
        self._client: Code27Client | None = None
        self._challenge_id: str | None = None
        self._credentials: tuple[str, str] | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            self._host, self._port = user_input[CONF_HOST], user_input[CONF_PORT]
            self._client = Code27Client(async_get_clientsession(self.hass), self._host, self._port)
            try:
                await self._client.bootstrap()
            except Code27ConnectionError:
                errors["base"] = "cannot_connect"
            except Code27Error:
                errors["base"] = "unknown"
            else:
                return await self._request_pairing_code()
        return self.async_show_form(step_id="user", data_schema=DEVICE_SCHEMA, errors=errors)

    async def _request_pairing_code(self) -> ConfigFlowResult:
        assert self._client is not None
        self._credentials = None
        try:
            self._challenge_id = await self._client.pair_request()
        except Code27Error:
            return self.async_show_form(step_id="user", data_schema=DEVICE_SCHEMA, errors={"base": "pairing_request_failed"})
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            assert self._client is not None
            try:
                self._credentials = await self._client.pair_confirm(
                    user_input["pairing_code"].strip(), self._challenge_id or ""
                )
            except Code27Error as err:
                code = getattr(err, "code", None) or str(err)
                if code in ("PAIRING_CODE_EXPIRED", "PAIRING_CODE_USED"):
                    return await self._request_pairing_code()
                errors["base"] = "invalid_pairing_code"
            else:
                return await self.async_step_verify()
        return self.async_show_form(
            step_id="pair", data_schema=PAIR_SCHEMA, errors=errors,
            description_placeholders={"host": self._host or ""},
        )

    async def async_step_verify(self, user_input=None) -> ConfigFlowResult:
        assert self._client is not None and self._credentials is not None
        try:
            system = await self._client.get_system()
        except Code27ConnectionError:
            error = "cannot_connect"
        except Code27Error:
            error = "unknown"
        else:
            return await self._finish_pairing(system)
        return self.async_show_form(step_id="verify", data_schema=vol.Schema({}), errors={"base": error})

    async def _finish_pairing(self, system: dict[str, Any]) -> ConfigFlowResult:
        assert self._credentials is not None
        user_id, secret = self._credentials
        machine_id = system.get("machine_id") or user_id
        if not isinstance(machine_id, str) or not machine_id:
            return self.async_abort(reason="invalid_identity")
        if self._reauth_entry is not None:
            expected = self._reauth_entry.data.get(CONF_MACHINE_ID) or self._reauth_entry.unique_id
            if not expected or machine_id != expected:
                return self.async_abort(reason="wrong_device")
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data_updates={CONF_USER_ID: user_id, CONF_LOCAL_SECRET: secret},
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
                CONF_HOST: self._host, CONF_PORT: self._port,
                CONF_USER_ID: user_id, CONF_LOCAL_SECRET: secret,
                CONF_MACHINE_ID: machine_id,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._host = entry_data[CONF_HOST]
        self._port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        self._client = Code27Client(async_get_clientsession(self.hass), self._host, self._port)
        return await self._request_pairing_code()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors = {}
        if user_input is not None:
            client = Code27Client(
                async_get_clientsession(self.hass), user_input[CONF_HOST], user_input[CONF_PORT],
                entry.data.get(CONF_USER_ID), entry.data.get(CONF_LOCAL_SECRET),
                entry.data.get(CONF_MACHINE_ID),
            )
            try:
                system = await client.get_system()
            except Code27ConnectionError:
                errors["base"] = "cannot_connect"
            except Code27Error:
                errors["base"] = "unknown"
            else:
                machine_id = system.get("machine_id") or entry.data.get(CONF_USER_ID)
                if not machine_id or machine_id != (entry.data.get(CONF_MACHINE_ID) or entry.unique_id):
                    return self.async_abort(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)): int,
            }), errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Code27OptionsFlow(config_entry)


class Code27OptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self._entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                    vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                vol.Required(CONF_REMOTE_RECOVERY_ENABLED, default=options.get(CONF_REMOTE_RECOVERY_ENABLED, False)): bool,
                vol.Required(CONF_ALARM_SWITCHES, default=options.get(CONF_ALARM_SWITCHES, True)): bool,
            }),
        )
