"""Polling coordinator shared by every entity of one config entry."""

from __future__ import annotations

from datetime import timedelta
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    HiveosMinerApi,
    HiveosMinerAuthError,
    HiveosMinerConnectionError,
    HiveosMinerError,
    parse_miner_status,
)
from .const import (
    CONF_HOST,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_MODEL,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    ENDPOINT_STATUS,
    STATE_MINING,
    STATE_STARTING,
    STATE_SUSPENDED,
)

_LOGGER = logging.getLogger(__name__)

# A resume/start command is followed by a fully zeroed status payload, which is
# byte-identical to a powered-off miner. The command itself is the only signal
# available in that window, so it is latched for at most this long.
PENDING_START_TIMEOUT = timedelta(minutes=5)


async def async_validate_input(hass: HomeAssistant, data: dict) -> dict[str, str]:
    """Check the credentials and return normalised input, for the config flow."""
    api = HiveosMinerApi(
        data[CONF_HOST], data.get(CONF_USERNAME) or "root", data.get(CONF_PASSWORD) or "root"
    )
    try:
        await api.async_validate()
    finally:
        await api.close()
    return data


class HiveosMinerCoordinator(DataUpdateCoordinator):
    """Polls one miner over the local network."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.host = entry.data[CONF_HOST]
        self.model = entry.data.get(CONF_MODEL) or DEFAULT_MODEL
        self.api = HiveosMinerApi(
            self.host,
            entry.data.get(CONF_USERNAME) or DEFAULT_USERNAME,
            entry.data.get(CONF_PASSWORD) or DEFAULT_PASSWORD,
            port=entry.data.get("port") or DEFAULT_PORT,
        )
        self.available = False
        self._command_lock = asyncio.Lock()
        self._paused_by_us = False
        # Latched while a start/resume command is still settling, because the
        # status payload during that window is indistinguishable from power-off.
        self._pending_start: dt_util.datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.host}",
            update_interval=timedelta(
                seconds=max(5, min(3600, entry.data.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL))
            ),
        )

    @property
    def pending_start(self) -> bool:
        """True while a start/resume command is still settling.

        Expires on its own so a command that never took effect cannot leave
        the miner advertising itself as starting forever.
        """
        if self._pending_start is None:
            return False
        if dt_util.utcnow() - self._pending_start > PENDING_START_TIMEOUT:
            self._pending_start = None
            return False
        return True

    def _clear_pending_start(self) -> None:
        if self._pending_start is not None:
            _LOGGER.debug("%s: start confirmed, clearing latch", self.host)
        self._pending_start = None

    async def _async_update_data(self) -> dict:
        try:
            raw = await self.api.async_get(ENDPOINT_STATUS)
        except HiveosMinerAuthError as err:
            self._clear_pending_start()
            raise ConfigEntryAuthFailed(str(err)) from err
        except HiveosMinerConnectionError:
            # An unresponsive miner is intentionally represented as a valid
            # offline payload. This keeps the status sensor actionable as
            # "Выключен" and lets the start switch remain available. Numeric
            # entities get None values and therefore display as unavailable.
            self.available = False
            self._paused_by_us = False
            self._clear_pending_start()
            return parse_miner_status({"summary": {}, "devs": [], "pools": []})
        except HiveosMinerError as err:
            self._clear_pending_start()
            raise UpdateFailed(str(err)) from err

        log = await self.api.async_log_tail()
        self.available = True
        try:
            data = parse_miner_status(
                raw, log or ("SUSPENDED" if self._paused_by_us else ""), self.pending_start
            )
        except HiveosMinerError as err:
            raise UpdateFailed(str(err)) from err
        if data["state"] == STATE_MINING:
            self._paused_by_us = False
        if data["state"] in (STATE_MINING, STATE_SUSPENDED):
            self._clear_pending_start()
        elif data["state"] == STATE_STARTING and not self._pending_start:
            # The log reported a boot we did not ask for: a watchdog or the
            # web UI started it.
            self._pending_start = None
        return data

    async def async_shutdown(self) -> None:
        await self.api.close()

    async def async_stop(self) -> None:
        await self.api.close()

    async def async_resume(self) -> None:
        await self.api.async_resume()
        self._paused_by_us = False
        self._pending_start = dt_util.utcnow()
        _LOGGER.info("%s: resume issued, holding state 'starting'", self.host)

    async def async_start_mining(self) -> None:
        await self.api.async_start()
        self._paused_by_us = False
        self._pending_start = dt_util.utcnow()
        _LOGGER.info("%s: start issued, holding state 'starting'", self.host)

    async def async_stop_mining(self) -> None:
        await self.api.async_stop()
        self._pending_start = None
        self._paused_by_us = True
        _LOGGER.info("%s: stop issued", self.host)

    async def async_set_mining(self, enabled: bool) -> None:
        """Serialize commands and recheck state after the previous refresh."""
        async with self._command_lock:
            state = (self.data or {}).get("state")
            try:
                if enabled:
                    if state in (STATE_MINING, STATE_STARTING) or self.pending_start:
                        return
                    if state == STATE_SUSPENDED:
                        await self.async_resume()
                    else:
                        await self.async_start_mining()
                else:
                    if state not in (STATE_MINING, STATE_STARTING):
                        return
                    await self.async_stop_mining()
            except HiveosMinerAuthError as err:
                self.entry.async_start_reauth(self.hass)
                raise ConfigEntryAuthFailed(str(err)) from err
            await self.async_request_refresh()
