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
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfPower, UnitOfPressure, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CHARGING_STATUS_MAP,
    CHARGING_TYPE_MAP,
    CLIMATE_RUNNING_STATUS_MAP,
    DOMAIN,
    HEATING_INTENSITY_MAP,
    SERVICE_WARNING_MAP,
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


def _charging_status(data: dict, vin: str) -> str | None:
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    val = cep_battery.get("charging_status")
    # The key is always present in a CEP battery payload, carrying None when the
    # field is absent or zero — so this guard, not the map's default, is what
    # keeps "no data" out of the UI.
    if val is None:
        return None
    return CHARGING_STATUS_MAP.get(val, f"Unknown ({val})")


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


def _charging_power(data: dict, vin: str) -> int | None:
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    return cep_battery.get("charging_power_watts")


def _charging_type(data: dict, vin: str) -> str | None:
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    val = cep_battery.get("charging_type")
    if val is None:
        return None
    return CHARGING_TYPE_MAP.get(val)


def _estimated_range_miles(data: dict, vin: str) -> int | None:
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    return cep_battery.get("estimated_range_miles")


def _health_pressure(key: str) -> Callable[[dict, str], float | None]:
    """Create a value_fn for a tyre pressure sensor (kPa)."""

    def _value_fn(data: dict, vin: str) -> float | None:
        health = data.get("health", {}).get(vin)
        if health is None:
            return None
        return health.get(key)

    return _value_fn


def _health_int(key: str) -> Callable[[dict, str], int | None]:
    """Create a value_fn for a health integer sensor (days/distance to service)."""

    def _value_fn(data: dict, vin: str) -> int | None:
        health = data.get("health", {}).get(vin)
        if health is None:
            return None
        return health.get(key)

    return _value_fn


def _service_warning(data: dict, vin: str) -> str | None:
    health = data.get("health", {}).get(vin)
    if health is None:
        return None
    val = health.get("service_warning")
    if val is None:
        return None
    return SERVICE_WARNING_MAP.get(val)


# Options lists for ENUM sensors
_CLIMATE_STATUS_OPTIONS = list(CLIMATE_RUNNING_STATUS_MAP.values())
_HEATING_INTENSITY_OPTIONS = list(HEATING_INTENSITY_MAP.values())
_USAGE_MODE_OPTIONS = list(USAGE_MODE_MAP.values())
_UNAVAILABLE_REASON_OPTIONS = list(UNAVAILABLE_REASON_MAP.values())
_SERVICE_WARNING_OPTIONS = list(SERVICE_WARNING_MAP.values())
_CHARGING_TYPE_OPTIONS = list(CHARGING_TYPE_MAP.values())

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
        key="charging_power",
        translation_key="charging_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_charging_power,
    ),
    PolestarSensorDescription(
        key="charging_type",
        translation_key="charging_type",
        device_class=SensorDeviceClass.ENUM,
        options=_CHARGING_TYPE_OPTIONS,
        value_fn=_charging_type,
    ),
    PolestarSensorDescription(
        key="estimated_range_miles",
        translation_key="estimated_range_miles",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_estimated_range_miles,
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
    # -- Health: Tyre pressure sensors --
    PolestarSensorDescription(
        key="front_left_tyre_pressure",
        translation_key="front_left_tyre_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_health_pressure("front_left_tyre_pressure_kpa"),
    ),
    PolestarSensorDescription(
        key="front_right_tyre_pressure",
        translation_key="front_right_tyre_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_health_pressure("front_right_tyre_pressure_kpa"),
    ),
    PolestarSensorDescription(
        key="rear_left_tyre_pressure",
        translation_key="rear_left_tyre_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_health_pressure("rear_left_tyre_pressure_kpa"),
    ),
    PolestarSensorDescription(
        key="rear_right_tyre_pressure",
        translation_key="rear_right_tyre_pressure",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_health_pressure("rear_right_tyre_pressure_kpa"),
    ),
    # -- Health: Service info --
    PolestarSensorDescription(
        key="days_to_service",
        translation_key="days_to_service",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_health_int("days_to_service"),
    ),
    PolestarSensorDescription(
        key="distance_to_service",
        translation_key="distance_to_service",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_health_int("distance_to_service_km"),
    ),
    PolestarSensorDescription(
        key="service_warning",
        translation_key="service_warning",
        device_class=SensorDeviceClass.ENUM,
        options=_SERVICE_WARNING_OPTIONS,
        entity_registry_enabled_default=False,
        value_fn=_service_warning,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar sensors from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        for description in SENSOR_DESCRIPTIONS:
            entities.append(PolestarSensor(coordinator, description, vehicle, vin))

    # One diagnostic sensor per config entry (not per VIN) exposing the
    # health of the PCCS / CEP / GraphQL API layers.
    entities.append(PolestarApiHealthSensor(coordinator, entry))

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

        model_name = vehicle.get("modelName") or "Polestar"
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


_API_HEALTH_OPTIONS = ["ok", "degraded", "down"]


class PolestarApiHealthSensor(CoordinatorEntity[PolestarCoordinator], SensorEntity):
    """Diagnostic sensor reporting the health of each Polestar API layer.

    State is the worst per-layer ``status`` reported by
    ``_LayerHealth.to_dict()``, ranked ``ok`` < ``degraded`` < ``down``.  The
    ranking lives there rather than here so that conditions the raw
    ``consecutive_failures`` counter cannot express — notably a sticky schema
    break, which holds a layer at ``degraded`` with a counter of 0 — reach the
    entity state.

    Attributes mirror the per-layer dict from ``_LayerHealth.to_dict()``
    so users can drill into specific failing endpoints from the More
    Info panel without checking logs.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "api_health"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _API_HEALTH_OPTIONS

    def __init__(self, coordinator: PolestarCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_api_health"

    @property
    def native_value(self) -> str:
        """Return the worst per-layer status as the overall sensor state."""
        api_health = (self.coordinator.data or {}).get("api_health") or {}
        worst = 0
        for layer in api_health.values():
            status = layer.get("status")
            # Ignore anything not in the known ranking rather than letting a
            # future status string raise from .index().
            if status in _API_HEALTH_OPTIONS:
                worst = max(worst, _API_HEALTH_OPTIONS.index(status))
        return _API_HEALTH_OPTIONS[worst]

    @property
    def extra_state_attributes(self) -> dict:
        """Expose per-layer health for drill-down."""
        api_health = (self.coordinator.data or {}).get("api_health") or {}
        result: dict = {}
        for layer, state in api_health.items():
            result[f"{layer}_status"] = state.get("status")
            result[f"last_{layer}_code"] = state.get("last_code")
            result[f"last_{layer}_success_at"] = state.get("last_success_at")
            result[f"{layer}_failing_endpoints"] = state.get("failing_endpoints", [])
            result[f"{layer}_schema_endpoints"] = state.get("schema_endpoints", [])
            result[f"{layer}_consecutive_failures"] = state.get("consecutive_failures", 0)
        return result
