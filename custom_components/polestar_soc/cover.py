"""Cover platform for Polestar — vehicle window control."""

from __future__ import annotations

from typing import Any

import grpc
from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cep import CepError
from .const import DOMAIN
from .coordinator import PolestarCoordinator

_WINDOW_KEYS = (
    "front_left_window",
    "front_right_window",
    "rear_left_window",
    "rear_right_window",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polestar cover entities from a config entry."""
    coordinator: PolestarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[CoverEntity] = []
    for vehicle in coordinator.data.get("vehicles", []):
        vin = vehicle["vin"]
        entities.append(PolestarWindowCover(coordinator, vehicle, vin))

    async_add_entities(entities)


class PolestarWindowCover(CoordinatorEntity[PolestarCoordinator], CoverEntity):
    """Window cover — shows window state and provides open/close control."""

    _attr_has_entity_name = True
    _attr_translation_key = "windows"
    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(
        self,
        coordinator: PolestarCoordinator,
        vehicle: dict,
        vin: str,
    ) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator)
        self._vin = vin
        self._attr_unique_id = f"{vin}_windows"

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
    def is_closed(self) -> bool | None:
        """Return true if all vehicle windows are closed."""
        data = self.coordinator.data
        if not data:
            return None
        exterior = data.get("exterior", {}).get(self._vin)
        if exterior is None:
            return None
        values = [exterior.get(k) for k in _WINDOW_KEYS]
        if any(v is None or v == 0 for v in values):
            return None
        return all(v == 2 for v in values)  # CLOSED

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open all vehicle windows."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.cep.window_open,
                self._vin,
            )
        except (grpc.RpcError, CepError) as err:
            raise HomeAssistantError(f"Failed to open windows: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close all vehicle windows."""
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.cep.window_close,
                self._vin,
            )
        except (grpc.RpcError, CepError) as err:
            raise HomeAssistantError(f"Failed to close windows: {err}") from err
        await self.coordinator.async_request_refresh()
