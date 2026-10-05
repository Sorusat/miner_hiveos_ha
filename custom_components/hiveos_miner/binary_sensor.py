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

from .const import CONF_NAME, DOMAIN, STATE_MINING, STATE_STARTING
from .coordinator import HiveosMinerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HiveosMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME) or "Miner"
    uid = entry.unique_id or entry.entry_id
    entities = [
        HiveosMinerBinarySensor(coordinator, name, uid, "connectivity"),
        HiveosMinerBinarySensor(coordinator, name, uid, "is_powered"),
        HiveosMinerBinarySensor(coordinator, name, uid, "pools_ok"),
        # is_mining duplicates the state sensor; kept but off by default so
        # the dashboard is not cluttered with two ways of saying the same.
        HiveosMinerBinarySensor(coordinator, name, uid, "is_mining"),
    ]
    # A miner that is stopped reports no pools at all. Reporting "no pool is
    # alive" then would be noise, and would fire the all-pools-down alert.
    if (coordinator.data or {}).get("state") in (STATE_MINING, STATE_STARTING):
        entities.append(
            HiveosMinerBinarySensor(coordinator, name, uid, "pools_alive_now")
        )
    async_add_entities(entities)


class HiveosMinerBinarySensor(CoordinatorEntity[HiveosMinerCoordinator], BinarySensorEntity):
    """Reachability, power and pool state as on/off."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True

    def __init__(
        self, coordinator: HiveosMinerCoordinator, name: str, uid: str, kind: str
    ) -> None:
        super().__init__(coordinator)
        self._kind = kind
        # Name comes from the device plus the translation key, so None here is
        # deliberate: assigning the device name makes the label meaningless.
        self._attr_name = None
        self._attr_translation_key = kind
        if kind == "connectivity":
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
            self._attr_icon = "mdi:lan-connect"
        elif kind == "is_powered":
            self._attr_device_class = BinarySensorDeviceClass.POWER
            self._attr_icon = "mdi:power-plug"
        elif kind == "pools_ok":
            # Raw count, always present, usable as an automation trigger even
            # while the miner is stopped.
            self._attr_icon = "mdi:lan-connect"
        elif kind == "pools_alive_now":
            # Count-based view, only while actually mining.
            self._attr_device_class = BinarySensorDeviceClass.RUNNING
            self._attr_icon = "mdi:check-network"
        else:
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
        data = self.coordinator.data or {}
        if self._kind == "pools_ok":
            return (data.get("pools_alive") or 0) > 0
        if self._kind == "pools_alive_now":
            # False when every configured pool reports Dead: the case worth
            # alerting on, because the miner is hashing into nothing.
            return (data.get("pools_alive") or 0) > 0
        return data.get("state") == STATE_MINING

    @property
    def available(self) -> bool:
        # Connectivity, power and the pool count must survive a failed poll, or
        # the reason for the outage disappears from the dashboard.
        if self._kind in ("connectivity", "is_powered", "pools_ok", "pools_alive_now"):
            return True
        return self.coordinator.last_update_success
