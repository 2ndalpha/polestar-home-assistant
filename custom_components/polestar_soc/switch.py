"""Switch platform for Polestar State of Charge — charging timer toggle."""

from __future__ import annotations

import logging
from typing import Any

import grpc
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PolestarCoordinator
from .pccs import PccsError

_LOGGER = logging.getLogger(__name__)

# Climate statuses that indicate pre-conditioning is active
_CLIMATE_ACTIVE_STATUSES = {
    "Starting",
    "Pre-conditioning",
    "Pre-conditioning (external power)",
    "Pre-cleaning",
    "Pre-conditioning and cleaning",
    "Residual heat",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar switch entities from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarChargeTimerSwitch(coordinator, vehicle, vin))
        entities.append(PolestarClimateSwitch(coordinator, vehicle, vin))

    async_add_entities(entities)


class PolestarChargeTimerSwitch(CoordinatorEntity[PolestarCoordinator], SwitchEntity):
    """Charging timer switch — enables or disables the scheduled charging window."""

    _attr_has_entity_name = True
    _attr_translation_key = "charging_timer"
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the charging timer switch entity."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_charging_timer"

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
        """Return whether the charging timer is active."""
        data = self.coordinator.data
        if not data:
            return None
        timer_data = data.get("charge_timer", {}).get(self._vin)
        if timer_data is None:
            return None
        return timer_data.get("is_departure_active")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the charging timer."""
        await self._set_activated(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the charging timer."""
        await self._set_activated(False)

    async def _set_activated(self, activated: bool) -> None:
        """Set the charging timer activated state, preserving current times."""
        timer_data = self.coordinator.data.get("charge_timer", {}).get(self._vin) or {}
        start_h = timer_data.get("start_hour") or 0
        start_m = timer_data.get("start_min") or 0
        end_h = timer_data.get("end_hour") or 0
        end_m = timer_data.get("end_min") or 0

        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.set_global_charge_timer,
                self._vin,
                start_h,
                start_m,
                end_h,
                end_m,
                activated,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(f"Failed to set charging timer: {err}") from err
        await self.coordinator.async_request_refresh()


class PolestarClimateSwitch(CoordinatorEntity[PolestarCoordinator], SwitchEntity):
    """Climate pre-conditioning switch — starts or stops cabin climate control."""

    _attr_has_entity_name = True
    _attr_translation_key = "climate"
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the climate switch entity."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_climate"

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
        """Return whether climate pre-conditioning is active."""
        data = self.coordinator.data
        if not data:
            return None
        climate = data.get("climate", {}).get(self._vin)
        if climate is None:
            return None
        status = climate.get("status")
        if status is None:
            return None
        return status in _CLIMATE_ACTIVE_STATUSES

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start climate pre-conditioning."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.climatization_start,
                self._vin,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(f"Failed to start climate: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop climate pre-conditioning."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.climatization_stop,
                self._vin,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(f"Failed to stop climate: {err}") from err
        await self.coordinator.async_request_refresh()
