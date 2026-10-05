"""Binary sensors: reachability and active mining."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN, STATE_MINING
from .coordinator import HiveosMinerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HiveosMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME) or "Miner"
    uid = entry.unique_id or entry.entry_id
    async_add_entities(
        [
            HiveosMinerBinarySensor(coordinator, name, uid, "connectivity"),
            HiveosMinerBinarySensor(coordinator, name, uid, "is_powered"),
            # is_mining duplicates the state sensor; kept but off by default so
            # the dashboard is not cluttered with two ways of saying the same.
            HiveosMinerBinarySensor(coordinator, name, uid, "is_mining"),
        ]
    )


class HiveosMinerBinarySensor(CoordinatorEntity[HiveosMinerCoordinator], BinarySensorEntity):
    """Reachability and mining state as on/off."""

    _attr_should_poll = False

    def __init__(
        self, coordinator: HiveosMinerCoordinator, name: str, uid: str, kind: str
    ) -> None:
        super().__init__(coordinator)
        self._kind = kind
        if kind == "connectivity":
            self._attr_name = f"{name} Доступен"
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
            self._attr_icon = "mdi:lan-connect"
            self._attr_entity_registry_enabled_default = True
        elif kind == "is_powered":
            self._attr_name = f"{name} Под питанием"
            self._attr_device_class = BinarySensorDeviceClass.POWER
            self._attr_icon = "mdi:power-plug"
            self._attr_entity_registry_enabled_default = True
        else:
            self._attr_name = f"{name} Майнит"
            self._attr_device_class = BinarySensorDeviceClass.RUNNING
            self._attr_icon = "mdi:pickaxe"
            # Same information as the state sensor, so it stays out of the way.
            self._attr_entity_registry_enabled_default = False
        self._attr_unique_id = f"{uid}_{kind}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=name,
            manufacturer="HiveOS",
            model=coordinator.model,
            configuration_url=f"http://{coordinator.host}",
        )

    @property
    def is_on(self) -> bool:
        if self._kind == "connectivity":
            return self.coordinator.last_update_success
        if self._kind == "is_powered":
            # A powered-off miner answers nothing, so its absence from the
            # network is the only proof it is off.
            return self.coordinator.last_update_success
        return (self.coordinator.data or {}).get("state") == STATE_MINING

    @property
    def available(self) -> bool:
        # Connectivity and power must survive a failed poll, or the reason for
        # the outage disappears from the dashboard.
        if self._kind in ("connectivity", "is_powered"):
            return True
        return self.coordinator.last_update_success
