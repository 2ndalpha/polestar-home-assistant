"""Number platform for Polestar State of Charge — charge limit control."""

from __future__ import annotations

import logging

import grpc
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PolestarCoordinator
from .pccs import PccsError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar number entities from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarChargeLimitNumber(coordinator, vehicle, vin))
        entities.append(PolestarClimateTimerTemperatureNumber(coordinator, vehicle, vin))

    async_add_entities(entities)


class PolestarChargeLimitNumber(CoordinatorEntity[PolestarCoordinator], NumberEntity):
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
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.set_target_soc, self._vin, int(value)
            )
        except grpc.RpcError as err:
            raise HomeAssistantError(f"Failed to set charge limit: {err}") from err
        await self.coordinator.async_request_refresh()


class PolestarClimateTimerTemperatureNumber(CoordinatorEntity[PolestarCoordinator], NumberEntity):
    """Climate timer temperature — sets the target cabin temperature for scheduled climate."""

    _attr_has_entity_name = True
    _attr_translation_key = "climate_timer_temperature"
    _attr_native_min_value = 15
    _attr_native_max_value = 28
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the climate timer temperature entity."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_climate_timer_temperature"

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
        """Return the current climate timer temperature."""
        data = self.coordinator.data
        if not data:
            return None
        settings = data.get("climate_timer_settings", {}).get(self._vin)
        if settings is None:
            return None
        return settings.get("temperature")

    async def async_set_native_value(self, value: float) -> None:
        """Set the climate timer temperature via PCCS."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.set_parking_climate_timer_settings,
                self._vin,
                value,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(f"Failed to set climate timer temperature: {err}") from err
        await self.coordinator.async_request_refresh()
