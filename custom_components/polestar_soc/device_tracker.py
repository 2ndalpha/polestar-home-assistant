"""Device tracker platform for Polestar — vehicle GPS location."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PolestarCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar device tracker from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[PolestarDeviceTracker] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarDeviceTracker(coordinator, vehicle, vin))

    async_add_entities(entities)


class PolestarDeviceTracker(CoordinatorEntity[PolestarCoordinator], TrackerEntity):
    """Representation of a Polestar vehicle location."""

    _attr_has_entity_name = True
    _attr_translation_key = "location"
    _attr_source_type = SourceType.GPS

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_location"

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

    def _location_data(self) -> dict | None:
        """Return location dict from coordinator data, or None."""
        data = self.coordinator.data
        if not data:
            return None
        return data.get("location", {}).get(self._vin)

    @property
    def latitude(self) -> float | None:
        """Return latitude."""
        loc = self._location_data()
        if loc is None:
            return None
        return loc.get("latitude")

    @property
    def longitude(self) -> float | None:
        """Return longitude."""
        loc = self._location_data()
        if loc is None:
            return None
        return loc.get("longitude")

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return extra state attributes."""
        loc = self._location_data()
        if loc is None:
            return None
        timestamp_ms = loc.get("timestamp_ms")
        if timestamp_ms is None:
            return None
        return {
            "location_timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat(),
        }
