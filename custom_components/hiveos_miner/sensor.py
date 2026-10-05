"""Sensors: state, hashrate, temperature, power, uptime, shares."""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_NAME, DOMAIN, STATE_LABELS
from .coordinator import HiveosMinerCoordinator

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="state",
        translation_key="state",
        icon="mdi:state-machine",
    ),
    SensorEntityDescription(
        key="hashrate",
        translation_key="hashrate",
        native_unit_of_measurement="GH/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="hashrate_avg",
        translation_key="hashrate_avg",
        native_unit_of_measurement="GH/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="temp_max",
        translation_key="temp_max",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="power",
        translation_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="uptime_minutes",
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="accepted",
        translation_key="accepted_shares",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:check-circle-outline",
    ),
    SensorEntityDescription(
        key="rejected",
        translation_key="rejected_shares",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:close-circle-outline",
    ),
    SensorEntityDescription(
        key="boards_alive",
        translation_key="boards_alive",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
    ),
    SensorEntityDescription(
        key="pools",
        translation_key="pools",
        icon="mdi:lan",
        entity_registry_enabled_default=False,
    ),
)


def _sensor_value(data: dict[str, Any], key: str) -> Any:
    if key == "state":
        return data.get("state", "unknown")
    if key == "pools":
        return ", ".join(
            f"{p['status']}: {p['url']}" for p in data.get("pools", []) if p.get("url")
        ) or "нет пулов"
    return data.get(key)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: HiveosMinerCoordinator = hass.data[DOMAIN][entry.entry_id]
    name = entry.data.get(CONF_NAME) or "Miner"
    uid = entry.unique_id or entry.entry_id
    async_add_entities(
        HiveosMinerSensor(coordinator, desc, name, uid) for desc in SENSORS
    )


class HiveosMinerSensor(CoordinatorEntity[HiveosMinerCoordinator], SensorEntity):
    """One readable value from a miner."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: HiveosMinerCoordinator,
        description: SensorEntityDescription,
        name: str,
        uid: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{uid}_{description.key}"
        self._attr_translation_key = description.translation_key
        self._attr_name = f"{name} {description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=name,
            manufacturer="HiveOS",
            model="Antminer S19",
            sw_version=(self.coordinator.data or {}).get("miner_version"),
            configuration_url=f"http://{coordinator.host}",
        )

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        value = _sensor_value(data, self.entity_description.key)
        if self.entity_description.key == "state":
            return STATE_LABELS.get(value, "Неизвестно")
        return value

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
