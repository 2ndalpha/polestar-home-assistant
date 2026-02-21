"""Number platform for Polestar State of Charge — charge limit control."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PolestarCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar number entities from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[PolestarChargeLimitNumber] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarChargeLimitNumber(coordinator, vehicle, vin))

    async_add_entities(entities)


class PolestarChargeLimitNumber(
    CoordinatorEntity[PolestarCoordinator], NumberEntity
):
    """Charge limit number entity — sets the target SOC percentage."""

    _attr_has_entity_name = True
    _attr_translation_key = "charge_limit"
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the charge limit number entity."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_charge_limit"

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
    def native_value(self) -> float | None:
        """Return the current charge limit percentage."""
        data = self.coordinator.data
        if not data:
            return None
        soc_data = data.get("target_soc", {}).get(self._vin)
        if soc_data is None:
            return None
        return soc_data.get("target_soc")

    async def async_set_native_value(self, value: float) -> None:
        """Set the charge limit percentage via PCCS."""
        await self.hass.async_add_executor_job(
            self.coordinator.pccs.set_target_soc, self._vin, int(value)
        )
        await self.coordinator.async_request_refresh()
