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

from .const import (
    CLIMATE_RUNNING_STATUS_MAP,
    DOMAIN,
    HEATING_INTENSITY_MAP,
    UNAVAILABLE_REASON_MAP,
    USAGE_MODE_MAP,
)
from .coordinator import PolestarCoordinator


@dataclass(frozen=True, kw_only=True)
class PolestarSensorDescription(SensorEntityDescription):
    """Describe a Polestar sensor."""

    value_fn: Callable[[dict, str], object]


# ---------------------------------------------------------------------------
# Value functions — each takes (coordinator_data, vin)
# ---------------------------------------------------------------------------


def _battery_soc(data: dict, vin: str) -> int | None:
    battery = data.get("battery", {}).get(vin)
    if battery is None:
        return None
    return battery.get("batteryChargeLevelPercentage")


def _charging_status(data: dict, vin: str) -> str:
    battery = data.get("battery", {}).get(vin)
    if battery is None:
        return "Unknown"
    return PolestarCoordinator.format_charging_status(battery.get("chargingStatus"))


def _charging_time_remaining(data: dict, vin: str) -> int | None:
    battery = data.get("battery", {}).get(vin)
    if battery is None:
        return None
    return battery.get("estimatedChargingTimeToFullMinutes")


def _odometer_km(data: dict, vin: str) -> float | None:
    odometer = data.get("odometer", {}).get(vin)
    if odometer is None:
        return None
    meters = odometer.get("odometerMeters")
    if meters is None:
        return None
    return round(meters / 1000, 1)


def _climate_status(data: dict, vin: str) -> str | None:
    climate = data.get("climate", {}).get(vin)
    if climate is None:
        return None
    return climate.get("status")


def _climate_heating(key: str) -> Callable[[dict, str], str | None]:
    """Create a value_fn for a heating intensity sensor."""

    def _value_fn(data: dict, vin: str) -> str | None:
        climate = data.get("climate", {}).get(vin)
        if climate is None:
            return None
        return climate.get(key)

    return _value_fn


def _usage_mode(data: dict, vin: str) -> str | None:
    availability = data.get("availability", {}).get(vin)
    if availability is None:
        return None
    val = availability.get("usage_mode")
    if val is None:
        return None
    return USAGE_MODE_MAP.get(val)


def _unavailable_reason(data: dict, vin: str) -> str | None:
    availability = data.get("availability", {}).get(vin)
    if availability is None:
        return None
    val = availability.get("unavailable_reason")
    if val is None:
        return None
    return UNAVAILABLE_REASON_MAP.get(val)


def _estimated_range(data: dict, vin: str) -> int | None:
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    return cep_battery.get("estimated_range_km")


# Options lists for ENUM sensors
_CLIMATE_STATUS_OPTIONS = list(CLIMATE_RUNNING_STATUS_MAP.values())
_HEATING_INTENSITY_OPTIONS = list(HEATING_INTENSITY_MAP.values())
_USAGE_MODE_OPTIONS = list(USAGE_MODE_MAP.values())
_UNAVAILABLE_REASON_OPTIONS = list(UNAVAILABLE_REASON_MAP.values())

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
    PolestarSensorDescription(
        key="climate_status",
        translation_key="climate_status",
        device_class=SensorDeviceClass.ENUM,
        options=_CLIMATE_STATUS_OPTIONS,
        value_fn=_climate_status,
    ),
    PolestarSensorDescription(
        key="driver_seat_heating",
        translation_key="driver_seat_heating",
        device_class=SensorDeviceClass.ENUM,
        options=_HEATING_INTENSITY_OPTIONS,
        value_fn=_climate_heating("driver_seat_heating"),
    ),
    PolestarSensorDescription(
        key="passenger_seat_heating",
        translation_key="passenger_seat_heating",
        device_class=SensorDeviceClass.ENUM,
        options=_HEATING_INTENSITY_OPTIONS,
        value_fn=_climate_heating("passenger_seat_heating"),
    ),
    PolestarSensorDescription(
        key="rear_left_seat_heating",
        translation_key="rear_left_seat_heating",
        device_class=SensorDeviceClass.ENUM,
        options=_HEATING_INTENSITY_OPTIONS,
        value_fn=_climate_heating("rear_left_seat_heating"),
    ),
    PolestarSensorDescription(
        key="rear_right_seat_heating",
        translation_key="rear_right_seat_heating",
        device_class=SensorDeviceClass.ENUM,
        options=_HEATING_INTENSITY_OPTIONS,
        value_fn=_climate_heating("rear_right_seat_heating"),
    ),
    PolestarSensorDescription(
        key="steering_wheel_heating",
        translation_key="steering_wheel_heating",
        device_class=SensorDeviceClass.ENUM,
        options=_HEATING_INTENSITY_OPTIONS,
        value_fn=_climate_heating("steering_wheel_heating"),
    ),
    PolestarSensorDescription(
        key="estimated_range",
        translation_key="estimated_range",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_estimated_range,
    ),
    PolestarSensorDescription(
        key="usage_mode",
        translation_key="usage_mode",
        device_class=SensorDeviceClass.ENUM,
        options=_USAGE_MODE_OPTIONS,
        value_fn=_usage_mode,
    ),
    PolestarSensorDescription(
        key="unavailable_reason",
        translation_key="unavailable_reason",
        device_class=SensorDeviceClass.ENUM,
        options=_UNAVAILABLE_REASON_OPTIONS,
        entity_registry_enabled_default=False,
        value_fn=_unavailable_reason,
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
        return self.entity_description.value_fn(data, self._vin)
