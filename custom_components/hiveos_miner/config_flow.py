"""Config flow for the HiveOS Miner integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HiveosMinerApi, HiveosMinerError
from .const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_NAME,
    DEFAULT_PASSWORD,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
    }
)


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    host = str(data[CONF_HOST]).strip()
    if host.startswith(("http://", "https://")):
        host = host.split("://", 1)[1].rstrip("/")
    out = dict(data)
    out[CONF_HOST] = host
    out[CONF_NAME] = (data.get(CONF_NAME) or DEFAULT_NAME).strip()
    return out


class HiveosMinerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a miner by IP address."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise(user_input)
            await self.async_set_unique_id(data[CONF_HOST])
            self._abort_if_unique_id_configured()
            api = HiveosMinerApi(
                data[CONF_HOST],
                data.get(CONF_USERNAME) or DEFAULT_USERNAME,
                data.get(CONF_PASSWORD) or DEFAULT_PASSWORD,
                session=async_get_clientsession(self.hass),
            )
            try:
                await api.async_validate()
            except HiveosMinerError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surface unknown failures as auth issues
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Support YAML import for users who prefer configuration.yaml."""
        return await self.async_step_user(import_data)
