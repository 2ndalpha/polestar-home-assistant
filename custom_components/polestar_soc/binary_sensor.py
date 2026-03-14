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

from .const import ALARM_STATUS_MAP, DOMAIN, OPEN_STATUS_MAP, UNAVAILABLE_REASON_MAP
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
