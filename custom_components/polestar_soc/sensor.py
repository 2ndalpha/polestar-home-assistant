"""Sensor platform for Polestar State of Charge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PolestarCoordinator


@dataclass(frozen=True, kw_only=True)
class PolestarSensorDescription(SensorEntityDescription):
    """Describe a Polestar sensor."""

    value_fn: Callable[[dict, dict | None, dict | None], object]


def _battery_soc(vehicle: dict, battery: dict | None, odometer: dict | None) -> int | None:
    if battery is None:
        return None
    return battery.get("batteryChargeLevelPercentage")


def _charging_status(vehicle: dict, battery: dict | None, odometer: dict | None) -> str:
    if battery is None:
        return "Unknown"
    return PolestarCoordinator.format_charging_status(battery.get("chargingStatus"))


def _charging_time_remaining(
    vehicle: dict, battery: dict | None, odometer: dict | None
) -> int | None:
    if battery is None:
        return None
    return battery.get("estimatedChargingTimeToFullMinutes")


def _odometer_km(vehicle: dict, battery: dict | None, odometer: dict | None) -> float | None:
    if odometer is None:
        return None
    meters = odometer.get("odometerMeters")
    if meters is None:
        return None
    return round(meters / 1000, 1)


SENSOR_DESCRIPTIONS: tuple[PolestarSensorDescription, ...] = (
    PolestarSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_battery_soc,
    ),
    PolestarSensorDescription(
        key="charging_status",
        translation_key="charging_status",
        value_fn=_charging_status,
    ),
    PolestarSensorDescription(
        key="charging_time_remaining",
        translation_key="charging_time_remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_charging_time_remaining,
    ),
    PolestarSensorDescription(
        key="odometer",
        translation_key="odometer",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_odometer_km,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar sensors from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[PolestarSensor] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        for description in SENSOR_DESCRIPTIONS:
            entities.append(PolestarSensor(coordinator, description, vehicle, vin))

    async_add_entities(entities)


class PolestarSensor(CoordinatorEntity[PolestarCoordinator], SensorEntity):
    """Representation of a Polestar sensor."""

    entity_description: PolestarSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        description: PolestarSensorDescription,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._vin = vin
        self._attr_unique_id = f"{vin}_{description.key}"

        model_name = "Polestar"
        content = vehicle.get("content")
        if content and content.get("model"):
            model_name = content["model"].get("name", model_name)
        year = vehicle.get("modelYear", "")
        device_name = f"{model_name} ({year})" if year else model_name

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vin)},
            name=device_name,
            manufacturer="Polestar",
            model=model_name,
            sw_version=str(year) if year else None,
        )

    @property
    def native_value(self) -> object:
        """Return the sensor value."""
        data = self.coordinator.data
        if not data:
            return None
        battery = data.get("battery", {}).get(self._vin)
        odometer = data.get("odometer", {}).get(self._vin)
        # Find current vehicle info
        vehicle = {}
        for v in data.get("vehicles", []):
            if v["vin"] == self._vin:
                vehicle = v
                break
        return self.entity_description.value_fn(vehicle, battery, odometer)
