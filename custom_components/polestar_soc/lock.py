"""Lock platform for Polestar — vehicle door lock control."""

from __future__ import annotations

from typing import Any

import grpc
from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PolestarCoordinator
from .pccs import PccsError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar lock entities from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[LockEntity] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarLock(coordinator, vehicle, vin))

    async_add_entities(entities)


class PolestarLock(CoordinatorEntity[PolestarCoordinator], LockEntity):
    """Door lock — shows lock state and provides lock/unlock control."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_lock"

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_lock"

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
    def is_locked(self) -> bool | None:
        """Return true if the vehicle is locked."""
        data = self.coordinator.data
        if not data:
            return None
        exterior = data.get("exterior", {}).get(self._vin)
        if exterior is None:
            return None
        val = exterior.get("central_lock")
        if val is None or val == 0:
            return None
        return val == 2  # LOCKED

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the vehicle."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.lock,
                self._vin,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(f"Failed to lock: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the vehicle."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.pccs.unlock,
                self._vin,
            )
        except (grpc.RpcError, PccsError) as err:
            raise HomeAssistantError(f"Failed to unlock: {err}") from err
        await self.coordinator.async_request_refresh()
