"""Options flow: change a miner's settings without removing it."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow

from .api import HiveosMinerApi, HiveosMinerError
from .const import (
    CONF_HOST,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_PORT,
)
from .config_flow import SCAN_INTERVAL_SCHEMA, _normalise


class HiveosMinerOptionsFlow(OptionsFlow):
    """Edit the connection details and polling interval of one miner."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self.handler)
            try:
                data = _normalise({**entry.data, **user_input})
            except vol.Invalid:
                return self.async_show_form(
                    step_id="init", data_schema=self._schema(entry.data),
                    errors={CONF_HOST: "invalid_host"},
                )
            host = data[CONF_HOST]
            # Keep the entry identity stable, but prevent two entries from
            # controlling the same endpoint after an address change.
            if any(
                other.entry_id != entry.entry_id
                and other.data.get(CONF_HOST) == host
                for other in self.hass.config_entries.async_entries(entry.domain)
            ):
                return self.async_show_form(
                    step_id="init", data_schema=self._schema(data),
                    errors={"base": "already_configured"},
                )

            # Refuse to save settings that do not work, so a typo cannot take
            # a working miner off the dashboard.
            api = HiveosMinerApi(
                host,
                data.get(CONF_USERNAME) or "root",
                data.get(CONF_PASSWORD) or "root",
                port=data.get("port") or DEFAULT_PORT,
            )
            try:
                await api.async_validate()
            except HiveosMinerError:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._schema(data),
                    errors={"base": "cannot_connect"},
                )
            finally:
                await api.close()

            self.hass.config_entries.async_update_entry(
                entry,
                data=data,
                title=data[CONF_NAME],
            )
            return self.async_create_entry(title="", data={})

        entry = self.hass.config_entries.async_get_entry(self.handler)
        return self.async_show_form(step_id="init", data_schema=self._schema(entry.data))

    def _schema(self, data: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=data.get(CONF_NAME, "Miner")): str,
                vol.Required(CONF_HOST, default=data.get(CONF_HOST, "")): str,
                vol.Optional(CONF_MODEL, default=data.get(CONF_MODEL, "Antminer")): str,
                vol.Optional(CONF_USERNAME, default=data.get(CONF_USERNAME, "root")): str,
                vol.Optional(CONF_PASSWORD, default=data.get(CONF_PASSWORD, "root")): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=int(data.get(CONF_SCAN_INTERVAL, 30))
                ): SCAN_INTERVAL_SCHEMA,
            }
        )
