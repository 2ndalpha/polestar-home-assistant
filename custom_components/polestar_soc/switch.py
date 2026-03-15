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

# Climate statuses that indicate pre-conditioning is NOT active.
# Any new status added to CLIMATE_RUNNING_STATUS_MAP will default to "active"
# (conservative — switch shows as on, prompting the user to turn it off).
_CLIMATE_INACTIVE_STATUSES = {"Unknown", "Off"}


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
        for slot in range(5):
            entities.append(PolestarClimateTimerSwitch(coordinator, vehicle, vin, slot))

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
        return status not in _CLIMATE_INACTIVE_STATUSES

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


class PolestarClimateTimerSwitch(CoordinatorEntity[PolestarCoordinator], SwitchEntity):
    """Parking climate timer switch — enables or disables a scheduled climate timer."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:radiator"

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
        slot: int,
    ) -> None:
        """Initialize the climate timer switch entity.

        Args:
            slot: Timer slot index (0-4, matching the API's 0-based index field).
        """
        super().__init__(coordinator)
        self._vin = vin
        self._slot = slot
        display_num = slot + 1  # 1-based for user display
        self._attr_translation_key = f"climate_timer_{display_num}"
        self._attr_unique_id = f"{vin}_climate_timer_{display_num}"

        if display_num >= 3:
            self._attr_entity_registry_enabled_default = False

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

    def _get_timer(self) -> dict | None:
        """Get the timer dict for this slot from coordinator data."""
        data = self.coordinator.data
        if not data:
            return None
        timers = data.get("climate_timers", {}).get(self._vin) or []
        for timer in timers:
            if timer.get("index") == self._slot:
                return timer
        return None

    @property
    def available(self) -> bool:
        """Return False when the timer slot is empty."""
        return super().available and self._get_timer() is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether this climate timer is active."""
        timer = self._get_timer()
        if timer is None:
            return None
        return timer.get("activated")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this climate timer."""
        await self._set_activated(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this climate timer."""
        await self._set_activated(False)

    async def _set_activated(self, activated: bool) -> None:
        """Set the timer activation state, preserving all other fields."""
        all_timers = list(self.coordinator.data.get("climate_timers", {}).get(self._vin) or [])

        # Modify the target timer's activated field
        found = False
        for i, timer in enumerate(all_timers):
            if timer.get("index") == self._slot:
                all_timers[i] = {**timer, "activated": activated}
                found = True
                break

        if not found:
            raise HomeAssistantError(f"Climate timer {self._slot + 1} not found")

        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.set_parking_climate_timers,
                self._vin,
                all_timers,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(
                f"Failed to set climate timer {self._slot + 1}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
