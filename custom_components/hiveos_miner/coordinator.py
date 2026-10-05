"""Polling coordinator shared by every entity of one config entry."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import HiveosMinerApi, HiveosMinerError, parse_miner_status
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
        # Latched while a start/resume command is still settling, because the
        # status payload during that window is indistinguishable from power-off.
        self._pending_start: dt_util.datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.host}",
            update_interval=timedelta(
                seconds=entry.data.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL
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
        except HiveosMinerError as err:
            # A miner that is powered off answers nothing at all. Keep the last
            # good payload so the dashboard holds its values, and let the
            # entities report unavailable through last_update_success.
            self.available = False
            self._clear_pending_start()
            raise UpdateFailed(str(err)) from err

        log = await self.api.async_log_tail()
        self.available = True
        data = parse_miner_status(raw, log, self.pending_start)
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
        self._pending_start = dt_util.utcnow()
        _LOGGER.info("%s: resume issued, holding state 'starting'", self.host)

    async def async_start_mining(self) -> None:
        await self.api.async_start()
        self._pending_start = dt_util.utcnow()
        _LOGGER.info("%s: start issued, holding state 'starting'", self.host)

    async def async_stop_mining(self) -> None:
        await self.api.async_stop()
        self._pending_start = None
        _LOGGER.info("%s: stop issued", self.host)
