"""Tests for lock platform — vehicle door lock control."""

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.polestar_soc.const import DOMAIN
from custom_components.polestar_soc.lock import PolestarLock
from custom_components.polestar_soc.pccs import PccsError

VIN = "YSMYKEAE1RB000001"


def _make_lock(
    coordinator_data: dict | None,
    vehicle: dict,
    vin: str = VIN,
) -> PolestarLock:
    """Create a PolestarLock with a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    coordinator.async_request_refresh = AsyncMock()
    return PolestarLock(coordinator, vehicle, vin)


class TestIsLocked:
    def test_locked(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["central_lock"] = 2  # LOCKED
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.is_locked is True

    def test_unlocked(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["central_lock"] = 1  # UNLOCKED
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.is_locked is False

    def test_unspecified(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["central_lock"] = 0  # UNSPECIFIED
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.is_locked is None

    def test_none_value(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"][VIN]["central_lock"] = None
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.is_locked is None

    def test_none_when_no_coordinator_data(self, sample_vehicle):
        lock = _make_lock(None, sample_vehicle)
        assert lock.is_locked is None

    def test_none_when_no_exterior_data(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["exterior"] = {}
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.is_locked is None


class TestEntity:
    def test_unique_id(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.unique_id == f"{VIN}_lock"

    def test_translation_key(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.translation_key == "door_lock"

    def test_has_entity_name(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.has_entity_name is True

    def test_device_info(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)
        assert lock.device_info["identifiers"] == {(DOMAIN, VIN)}
        assert lock.device_info["manufacturer"] == "Polestar"


class TestAsyncLock:
    @pytest.mark.asyncio
    async def test_calls_pccs_lock(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        lock.hass = hass

        await lock.async_lock()

        lock.coordinator.pccs.lock.assert_called_once_with(VIN)
        lock.coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_grpc_error_raises_ha_error(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=grpc.RpcError())
        lock.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to lock"):
            await lock.async_lock()

    @pytest.mark.asyncio
    async def test_pccs_error_raises_ha_error(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(
            side_effect=PccsError("Lock failed: INVOCATION_SPECIFIC_ERROR (a door is open)")
        )
        lock.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to lock"):
            await lock.async_lock()


class TestAsyncUnlock:
    @pytest.mark.asyncio
    async def test_calls_pccs_unlock(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        lock.hass = hass

        await lock.async_unlock()

        lock.coordinator.pccs.unlock.assert_called_once_with(VIN)
        lock.coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_grpc_error_raises_ha_error(self, sample_coordinator_data, sample_vehicle):
        lock = _make_lock(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=grpc.RpcError())
        lock.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to unlock"):
            await lock.async_unlock()
