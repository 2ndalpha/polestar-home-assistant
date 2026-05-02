"""Binary sensor platform for Polestar — vehicle exterior state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ALARM_STATUS_MAP,
    CHARGER_CONNECTION_STATUS_MAP,
    DOMAIN,
    EXTERIOR_LIGHT_WARNING_MAP,
    FLUID_WARNING_MAP,
    LOW_VOLTAGE_BATTERY_WARNING_MAP,
    OIL_LEVEL_WARNING_MAP,
    OPEN_STATUS_MAP,
    TYRE_PRESSURE_WARNING_MAP,
    UNAVAILABLE_REASON_MAP,
)
from .coordinator import PolestarCoordinator


@dataclass(frozen=True, kw_only=True)
class PolestarBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Polestar binary sensor."""

    is_on_fn: Callable[[dict, str], bool | None]
    extra_attrs_fn: Callable[[dict, str], dict | None] | None = None


# ---------------------------------------------------------------------------
# is_on helpers
# ---------------------------------------------------------------------------


def _open_status_is_on(key: str) -> Callable[[dict, str], bool | None]:
    """Create an is_on_fn for an OpenStatus field (doors, windows, openings)."""

    def _fn(data: dict, vin: str) -> bool | None:
        exterior = data.get("exterior", {}).get(vin)
        if exterior is None:
            return None
        val = exterior.get(key)
        if val is None or val == 0:
            return None
        return val in (1, 3)  # OPEN(1) or AJAR(3)

    return _fn


def _alarm_is_on(data: dict, vin: str) -> bool | None:
    """Alarm: True=Triggered, False=Idle, None=Unknown."""
    exterior = data.get("exterior", {}).get(vin)
    if exterior is None:
        return None
    val = exterior.get("alarm")
    if val is None or val == 0:
        return None
    return val == 2  # TRIGGERED(2)


def _availability_is_on(data: dict, vin: str) -> bool | None:
    """Vehicle available: True=Connected, False=Disconnected, None=Unknown."""
    availability = data.get("availability", {}).get(vin)
    if availability is None:
        return None
    status = availability.get("availability_status")
    if status == 1:
        return True  # AVAILABLE
    if status == 2:
        return False  # UNAVAILABLE
    return None  # UNSPECIFIED or missing


def _availability_extra_attrs(data: dict, vin: str) -> dict | None:
    """Return unavailable_reason as an extra attribute."""
    availability = data.get("availability", {}).get(vin)
    if availability is None:
        return None
    reason_val = availability.get("unavailable_reason")
    reason = UNAVAILABLE_REASON_MAP.get(reason_val) if reason_val else None
    return {"unavailable_reason": reason}


# ---------------------------------------------------------------------------
# Charger connected helpers
# ---------------------------------------------------------------------------


def _charger_connected_is_on(data: dict, vin: str) -> bool | None:
    """Charger connected: True=Plugged in, False=Unplugged, None=Unknown."""
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    val = cep_battery.get("charger_connection_status")
    if val is None:
        return None
    return val != 2  # True for CONNECTED(1) and FAULT(3), False for DISCONNECTED(2)


def _charger_connected_extra_attrs(data: dict, vin: str) -> dict | None:
    """Return charger connection status as an extra attribute."""
    cep_battery = data.get("cep_battery", {}).get(vin)
    if cep_battery is None:
        return None
    val = cep_battery.get("charger_connection_status")
    if val is None:
        return None
    label = CHARGER_CONNECTION_STATUS_MAP.get(val, f"Unknown ({val})")
    return {"raw_state": label}


# ---------------------------------------------------------------------------
# Health warning helpers
# ---------------------------------------------------------------------------


def _health_warning_is_on(
    warning_key: str, *, threshold: int = 2
) -> Callable[[dict, str], bool | None]:
    """Create an is_on_fn for a health warning field.

    Returns True when warning value >= threshold (default 2, meaning any
    non-OK warning state). Returns None when data is missing or UNSPECIFIED(0).
    """

    def _fn(data: dict, vin: str) -> bool | None:
        health = data.get("health", {}).get(vin)
        if health is None:
            return None
        val = health.get(warning_key)
        if val is None:
            return None
        return val >= threshold

    return _fn


def _health_warning_exact_is_on(
    warning_key: str, on_value: int
) -> Callable[[dict, str], bool | None]:
    """Create an is_on_fn that triggers on a specific warning value."""

    def _fn(data: dict, vin: str) -> bool | None:
        health = data.get("health", {}).get(vin)
        if health is None:
            return None
        val = health.get(warning_key)
        if val is None:
            return None
        return val == on_value

    return _fn


def _health_warning_attrs(
    warning_key: str, label_map: dict[int, str | None]
) -> Callable[[dict, str], dict | None]:
    """Create an extra_attrs_fn for a health warning binary sensor."""

    def _fn(data: dict, vin: str) -> dict | None:
        health = data.get("health", {}).get(vin)
        if health is None:
            return None
        val = health.get(warning_key)
        if val is None:
            return None
        label = label_map.get(val, f"Unknown ({val})")
        if label is None:
            return None
        return {"raw_state": label}

    return _fn


def _tyre_warning_attrs(warning_key: str, pressure_key: str) -> Callable[[dict, str], dict | None]:
    """Create an extra_attrs_fn for tyre pressure warning (includes kPa value)."""

    def _fn(data: dict, vin: str) -> dict | None:
        health = data.get("health", {}).get(vin)
        if health is None:
            return None
        val = health.get(warning_key)
        if val is None:
            return None
        label = TYRE_PRESSURE_WARNING_MAP.get(val, f"Unknown ({val})")
        if label is None:
            return None
        attrs: dict = {"raw_state": label}
        pressure = health.get(pressure_key)
        if pressure is not None:
            attrs["pressure_kpa"] = pressure
        return attrs

    return _fn


# ---------------------------------------------------------------------------
# Entity descriptions
# ---------------------------------------------------------------------------

BINARY_SENSOR_DESCRIPTIONS: tuple[PolestarBinarySensorDescription, ...] = (
    PolestarBinarySensorDescription(
        key="vehicle_available",
        translation_key="vehicle_available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=_availability_is_on,
        extra_attrs_fn=_availability_extra_attrs,
    ),
    PolestarBinarySensorDescription(
        key="charger_connected",
        translation_key="charger_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=_charger_connected_is_on,
        extra_attrs_fn=_charger_connected_extra_attrs,
    ),
    PolestarBinarySensorDescription(
        key="front_left_door",
        translation_key="front_left_door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=_open_status_is_on("front_left_door"),
    ),
    PolestarBinarySensorDescription(
        key="front_right_door",
        translation_key="front_right_door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=_open_status_is_on("front_right_door"),
    ),
    PolestarBinarySensorDescription(
        key="rear_left_door",
        translation_key="rear_left_door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=_open_status_is_on("rear_left_door"),
    ),
    PolestarBinarySensorDescription(
        key="rear_right_door",
        translation_key="rear_right_door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=_open_status_is_on("rear_right_door"),
    ),
    # --- Disabled by default ---
    PolestarBinarySensorDescription(
        key="front_left_window",
        translation_key="front_left_window",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("front_left_window"),
    ),
    PolestarBinarySensorDescription(
        key="front_right_window",
        translation_key="front_right_window",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("front_right_window"),
    ),
    PolestarBinarySensorDescription(
        key="rear_left_window",
        translation_key="rear_left_window",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("rear_left_window"),
    ),
    PolestarBinarySensorDescription(
        key="rear_right_window",
        translation_key="rear_right_window",
        device_class=BinarySensorDeviceClass.WINDOW,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("rear_right_window"),
    ),
    PolestarBinarySensorDescription(
        key="hood",
        translation_key="hood",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("hood"),
    ),
    PolestarBinarySensorDescription(
        key="tailgate",
        translation_key="tailgate",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("tailgate"),
    ),
    PolestarBinarySensorDescription(
        key="tank_lid",
        translation_key="tank_lid",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("tank_lid"),
    ),
    PolestarBinarySensorDescription(
        key="sunroof",
        translation_key="sunroof",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_registry_enabled_default=False,
        is_on_fn=_open_status_is_on("sunroof"),
    ),
    PolestarBinarySensorDescription(
        key="alarm",
        translation_key="alarm",
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_registry_enabled_default=False,
        is_on_fn=_alarm_is_on,
    ),
    # -- Health: Tyre pressure warnings (enabled by default) --
    PolestarBinarySensorDescription(
        key="front_left_tyre_pressure_warning",
        translation_key="front_left_tyre_pressure_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_health_warning_is_on("front_left_tyre_pressure_warning"),
        extra_attrs_fn=_tyre_warning_attrs(
            "front_left_tyre_pressure_warning", "front_left_tyre_pressure_kpa"
        ),
    ),
    PolestarBinarySensorDescription(
        key="front_right_tyre_pressure_warning",
        translation_key="front_right_tyre_pressure_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_health_warning_is_on("front_right_tyre_pressure_warning"),
        extra_attrs_fn=_tyre_warning_attrs(
            "front_right_tyre_pressure_warning", "front_right_tyre_pressure_kpa"
        ),
    ),
    PolestarBinarySensorDescription(
        key="rear_left_tyre_pressure_warning",
        translation_key="rear_left_tyre_pressure_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_health_warning_is_on("rear_left_tyre_pressure_warning"),
        extra_attrs_fn=_tyre_warning_attrs(
            "rear_left_tyre_pressure_warning", "rear_left_tyre_pressure_kpa"
        ),
    ),
    PolestarBinarySensorDescription(
        key="rear_right_tyre_pressure_warning",
        translation_key="rear_right_tyre_pressure_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_health_warning_is_on("rear_right_tyre_pressure_warning"),
        extra_attrs_fn=_tyre_warning_attrs(
            "rear_right_tyre_pressure_warning", "rear_right_tyre_pressure_kpa"
        ),
    ),
    # -- Health: Fluid & battery warnings (enabled by default) --
    PolestarBinarySensorDescription(
        key="washer_fluid_level_warning",
        translation_key="washer_fluid_level_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_health_warning_exact_is_on("washer_fluid_level_warning", 2),
        extra_attrs_fn=_health_warning_attrs("washer_fluid_level_warning", FLUID_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="low_voltage_battery_warning",
        translation_key="low_voltage_battery_warning",
        device_class=BinarySensorDeviceClass.BATTERY,
        is_on_fn=_health_warning_exact_is_on("low_voltage_battery_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "low_voltage_battery_warning", LOW_VOLTAGE_BATTERY_WARNING_MAP
        ),
    ),
    # -- Health: Disabled by default --
    PolestarBinarySensorDescription(
        key="brake_fluid_level_warning",
        translation_key="brake_fluid_level_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_is_on("brake_fluid_level_warning"),
        extra_attrs_fn=_health_warning_attrs("brake_fluid_level_warning", FLUID_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="engine_coolant_level_warning",
        translation_key="engine_coolant_level_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_is_on("engine_coolant_level_warning"),
        extra_attrs_fn=_health_warning_attrs("engine_coolant_level_warning", FLUID_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="oil_level_warning",
        translation_key="oil_level_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_is_on("oil_level_warning"),
        extra_attrs_fn=_health_warning_attrs("oil_level_warning", OIL_LEVEL_WARNING_MAP),
    ),
    # -- Health: Light warnings (disabled by default) --
    PolestarBinarySensorDescription(
        key="brake_light_left_warning",
        translation_key="brake_light_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("brake_light_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "brake_light_left_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="brake_light_center_warning",
        translation_key="brake_light_center_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("brake_light_center_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "brake_light_center_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="brake_light_right_warning",
        translation_key="brake_light_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("brake_light_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "brake_light_right_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="fog_light_front_warning",
        translation_key="fog_light_front_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("fog_light_front_warning", 2),
        extra_attrs_fn=_health_warning_attrs("fog_light_front_warning", EXTERIOR_LIGHT_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="fog_light_rear_warning",
        translation_key="fog_light_rear_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("fog_light_rear_warning", 2),
        extra_attrs_fn=_health_warning_attrs("fog_light_rear_warning", EXTERIOR_LIGHT_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="position_light_front_left_warning",
        translation_key="position_light_front_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("position_light_front_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "position_light_front_left_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="position_light_front_right_warning",
        translation_key="position_light_front_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("position_light_front_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "position_light_front_right_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="position_light_rear_left_warning",
        translation_key="position_light_rear_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("position_light_rear_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "position_light_rear_left_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="position_light_rear_right_warning",
        translation_key="position_light_rear_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("position_light_rear_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "position_light_rear_right_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="high_beam_left_warning",
        translation_key="high_beam_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("high_beam_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs("high_beam_left_warning", EXTERIOR_LIGHT_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="high_beam_right_warning",
        translation_key="high_beam_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("high_beam_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs("high_beam_right_warning", EXTERIOR_LIGHT_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="low_beam_left_warning",
        translation_key="low_beam_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("low_beam_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs("low_beam_left_warning", EXTERIOR_LIGHT_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="low_beam_right_warning",
        translation_key="low_beam_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("low_beam_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs("low_beam_right_warning", EXTERIOR_LIGHT_WARNING_MAP),
    ),
    PolestarBinarySensorDescription(
        key="daytime_running_light_left_warning",
        translation_key="daytime_running_light_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("daytime_running_light_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "daytime_running_light_left_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="daytime_running_light_right_warning",
        translation_key="daytime_running_light_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("daytime_running_light_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "daytime_running_light_right_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="turn_indication_front_left_warning",
        translation_key="turn_indication_front_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("turn_indication_front_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "turn_indication_front_left_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="turn_indication_front_right_warning",
        translation_key="turn_indication_front_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("turn_indication_front_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "turn_indication_front_right_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="turn_indication_rear_left_warning",
        translation_key="turn_indication_rear_left_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("turn_indication_rear_left_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "turn_indication_rear_left_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="turn_indication_rear_right_warning",
        translation_key="turn_indication_rear_right_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("turn_indication_rear_right_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "turn_indication_rear_right_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="registration_plate_light_warning",
        translation_key="registration_plate_light_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("registration_plate_light_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "registration_plate_light_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
    PolestarBinarySensorDescription(
        key="side_mark_lights_warning",
        translation_key="side_mark_lights_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=False,
        is_on_fn=_health_warning_exact_is_on("side_mark_lights_warning", 2),
        extra_attrs_fn=_health_warning_attrs(
            "side_mark_lights_warning", EXTERIOR_LIGHT_WARNING_MAP
        ),
    ),
)

# Mapping from description key to the appropriate status map for raw_state labels
_STATUS_MAP_BY_KEY: dict[str, dict[int, str | None]] = {
    "alarm": ALARM_STATUS_MAP,
}


def _get_status_map(key: str) -> dict[int, str | None]:
    return _STATUS_MAP_BY_KEY.get(key, OPEN_STATUS_MAP)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar binary sensors from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[PolestarBinarySensor] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        for description in BINARY_SENSOR_DESCRIPTIONS:
            entities.append(PolestarBinarySensor(coordinator, description, vehicle, vin))

    async_add_entities(entities)


class PolestarBinarySensor(CoordinatorEntity[PolestarCoordinator], BinarySensorEntity):
    """Representation of a Polestar binary sensor."""

    entity_description: PolestarBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        description: PolestarBinarySensorDescription,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the binary sensor."""
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
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        data = self.coordinator.data
        if not data:
            return None
        return self.entity_description.is_on_fn(data, self._vin)

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return extra state attributes including raw enum label."""
        data = self.coordinator.data
        if not data:
            return None
        if self.entity_description.extra_attrs_fn is not None:
            return self.entity_description.extra_attrs_fn(data, self._vin)
        exterior = data.get("exterior", {}).get(self._vin)
        if exterior is None:
            return None
        raw_val = exterior.get(self.entity_description.key)
        if raw_val is None:
            return None
        status_map = _get_status_map(self.entity_description.key)
        label = status_map.get(raw_val, f"Unknown ({raw_val})")
        if label is None:
            return None
        return {"raw_state": label}
