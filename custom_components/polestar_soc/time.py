"""Time platform for Polestar State of Charge — charge timer controls."""

from __future__ import annotations

import logging
from datetime import time

import grpc
from homeassistant.components.time import TimeEntity
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar time entities from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[TimeEntity] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarChargeTimeEntity(coordinator, vehicle, vin, time_key="start"))
        entities.append(PolestarChargeTimeEntity(coordinator, vehicle, vin, time_key="end"))
        for slot in range(5):
            entities.append(PolestarClimateTimerTimeEntity(coordinator, vehicle, vin, slot))

    async_add_entities(entities)


class PolestarChargeTimeEntity(CoordinatorEntity[PolestarCoordinator], TimeEntity):
    """Charge timer time entity — sets charging start or end time."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
        time_key: str,
    ) -> None:
        """Initialize the charge timer time entity.

        Args:
            time_key: "start" or "end" — determines which timer endpoint this controls.
        """
        super().__init__(coordinator)
        self._vin = vin
        self._time_key = time_key

        if time_key == "start":
            self._attr_translation_key = "charging_start_time"
            self._attr_unique_id = f"{vin}_charging_start_time"
        else:
            self._attr_translation_key = "charging_end_time"
            self._attr_unique_id = f"{vin}_charging_end_time"

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
    def native_value(self) -> time | None:
        """Return the current timer time."""
        data = self.coordinator.data
        if not data:
            return None
        timer_data = data.get("charge_timer", {}).get(self._vin)
        if timer_data is None:
            return None

        hour = timer_data.get(f"{self._time_key}_hour")
        minute = timer_data.get(f"{self._time_key}_min")
        if hour is None or minute is None:
            return None
        return time(hour=hour, minute=minute)

    async def async_set_value(self, value: time) -> None:
        """Set the charge timer time via PCCS.

        Both start and end times are sent together in a single gRPC call,
        so we read the current opposite value from coordinator data.
        """
        timer_data = self.coordinator.data.get("charge_timer", {}).get(self._vin) or {}
        activated = timer_data.get("is_departure_active", True)

        if self._time_key == "start":
            start_h, start_m = value.hour, value.minute
            end_h = timer_data.get("end_hour") or 0
            end_m = timer_data.get("end_min") or 0
        else:
            start_h = timer_data.get("start_hour") or 0
            start_m = timer_data.get("start_min") or 0
            end_h, end_m = value.hour, value.minute

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
            raise HomeAssistantError(f"Failed to set charge timer: {err}") from err
        await self.coordinator.async_request_refresh()


class PolestarClimateTimerTimeEntity(CoordinatorEntity[PolestarCoordinator], TimeEntity):
    """Climate timer departure time entity — sets when the car should be ready."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
        slot: int,
    ) -> None:
        """Initialize the climate timer time entity.

        Args:
            slot: Timer slot index (0-4, matching the API's 0-based index field).
        """
        super().__init__(coordinator)
        self._vin = vin
        self._slot = slot
        display_num = slot + 1  # 1-based for user display
        self._attr_translation_key = f"climate_timer_{display_num}_time"
        self._attr_unique_id = f"{vin}_climate_timer_{display_num}_time"

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
    def native_value(self) -> time | None:
        """Return the departure time for this timer."""
        timer = self._get_timer()
        if timer is None:
            return None
        hour = timer.get("hour")
        minute = timer.get("minute")
        if hour is None or minute is None:
            return None
        return time(hour=hour, minute=minute)

    async def async_set_value(self, value: time) -> None:
        """Set the climate timer departure time via PCCS.

        Sends the full timer list (replace-all semantics).
        """
        all_timers = list(self.coordinator.data.get("climate_timers", {}).get(self._vin) or [])

        # Modify the target timer's departure time
        found = False
        for i, timer in enumerate(all_timers):
            if timer.get("index") == self._slot:
                all_timers[i] = {**timer, "hour": value.hour, "minute": value.minute}
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
                f"Failed to set climate timer {self._slot + 1} time: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
