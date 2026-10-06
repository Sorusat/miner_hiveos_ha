"""Single switch for starting and pausing the miner."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_NAME,
    DOMAIN,
    STATE_MINING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_SUSPENDED,
)
from .coordinator import HiveosMinerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HiveosMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME) or "Miner"
    uid = entry.unique_id or entry.entry_id
    async_add_entities([HiveosMinerSwitch(coordinator, name, uid)])


class HiveosMinerSwitch(CoordinatorEntity[HiveosMinerCoordinator], SwitchEntity):
    """A miner exposed as a switch, like a lamp in the dashboard.

    Turning it off pauses the miner; turning it on starts it or resumes it.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: HiveosMinerCoordinator, name: str, uid: str) -> None:
        super().__init__(coordinator)
        # With has_entity_name set, _attr_name must stay None so Home Assistant
        # composes "<device name> <translated key>" itself. Assigning the device
        # name here makes the label meaningless and hides it in pickers.
        self._attr_name = None
        self._attr_translation_key = "mining"
        self._attr_unique_id = f"{uid}_mining_switch"
        self._attr_icon = "mdi:pickaxe"

    @property
    def is_on(self) -> bool:
        state = (self.coordinator.data or {}).get("state")
        return state in (STATE_MINING, STATE_STARTING)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_turn_on(self, **kwargs) -> None:
        data = self.coordinator.data or {}
        state = data.get("state")
        if state == STATE_SUSPENDED:
            # Resume keeps the running configuration; a full start is needed
            # when the miner process is not running at all.
            await self.coordinator.async_resume()
        elif state == STATE_STOPPED:
            await self.coordinator.async_start_mining()
        elif state == STATE_STARTING:
            # Already booting from an earlier command: do not stack another one.
            return
        else:
            await self.coordinator.async_start_mining()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_stop_mining()
        await self.coordinator.async_request_refresh()
