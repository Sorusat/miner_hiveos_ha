"""Config flow for the HiveOS Miner integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .api import HiveosMinerApi, HiveosMinerError
from .const import (
    CONF_HOST,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_MODEL,
    DEFAULT_NAME,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def normalise_host(value: str) -> str:
    """Keep an optional HTTP port; reject credentials and URL paths."""
    value = value.strip().rstrip("/")
    try:
        address = urlsplit(value if "://" in value else f"http://{value}")
        if (
            address.scheme != "http" or not address.hostname
            or address.username is not None or address.password is not None
            or address.path or address.query or address.fragment
        ):
            raise ValueError("Enter an IP/hostname or an HTTP URL without a path")
        port = address.port
        host = address.hostname.lower()
        if any(character.isspace() for character in host):
            raise ValueError("Address must not contain whitespace")
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{port}" if port else host
    except ValueError as err:
        raise vol.Invalid(str(err)) from err


SCAN_INTERVAL_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=5, max=3600))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): str,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): SCAN_INTERVAL_SCHEMA,
    }
)


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    """Accept a bare IP or a full URL and keep the stored values tidy."""
    host = normalise_host(str(data[CONF_HOST]))

    out = dict(data)
    out[CONF_HOST] = host
    out[CONF_NAME] = (data.get(CONF_NAME) or "").strip() or DEFAULT_NAME
    out[CONF_MODEL] = (data.get(CONF_MODEL) or "").strip() or DEFAULT_MODEL
    out[CONF_USERNAME] = data.get(CONF_USERNAME) or DEFAULT_USERNAME
    out[CONF_PASSWORD] = data.get(CONF_PASSWORD) or DEFAULT_PASSWORD
    return out


class HiveosMinerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a miner by IP address."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        from .options_flow import HiveosMinerOptionsFlow

        return HiveosMinerOptionsFlow()

    async def _async_try_connect(self, data: dict[str, Any]) -> str | None:
        """Probe the miner; return an error key or None on success."""
        api = HiveosMinerApi(
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            port=data.get("port") or DEFAULT_PORT,
        )
        try:
            await api.async_validate()
        except HiveosMinerError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001 - unknown failures surface as a form error
            _LOGGER.exception("Unexpected error validating %s", data[CONF_HOST])
            return "unknown"
        finally:
            await api.close()
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = _normalise(user_input)
            except vol.Invalid:
                return self.async_show_form(
                    step_id="user", data_schema=STEP_USER_SCHEMA,
                    errors={CONF_HOST: "invalid_host"},
                )
            await self.async_set_unique_id(data[CONF_HOST])
            self._abort_if_unique_id_configured()
            error = await self._async_try_connect(data)
            if error is None:
                return self.async_create_entry(title=data[CONF_NAME], data=data)
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Ask for the credentials again after a change on the miner itself."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = dict(entry.data)
            data[CONF_USERNAME] = user_input.get(CONF_USERNAME) or DEFAULT_USERNAME
            data[CONF_PASSWORD] = user_input.get(CONF_PASSWORD) or DEFAULT_PASSWORD
            error = await self._async_try_connect(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={CONF_HOST: entry.data.get(CONF_HOST, "")},
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Support YAML import for users who prefer configuration.yaml."""
        return await self.async_step_user(import_data)
