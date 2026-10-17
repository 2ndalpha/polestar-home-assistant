"""Tests for switch platform — charging timer toggle."""

from unittest.mock import AsyncMock, MagicMock, create_autospec

import grpc
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.polestar_soc.cep import CepClient, CepError
from custom_components.polestar_soc.const import DOMAIN
from custom_components.polestar_soc.pccs import PccsClient, PccsError
from custom_components.polestar_soc.switch import PolestarChargeTimerSwitch, PolestarClimateSwitch

VIN = "YSMYKEAE1RB000001"


def _make_switch(
    coordinator_data: dict | None,
    vehicle: dict,
    vin: str = VIN,
) -> PolestarChargeTimerSwitch:
    """Create a PolestarChargeTimerSwitch with a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    coordinator.async_request_refresh = AsyncMock()
    return PolestarChargeTimerSwitch(coordinator, vehicle, vin)


class TestIsOn:
    def test_active(self, sample_coordinator_data, sample_vehicle, sample_charge_timer):
        sample_coordinator_data["charge_timer"][VIN] = sample_charge_timer
        switch = _make_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is True

    def test_inactive(self, sample_coordinator_data, sample_vehicle, sample_charge_timer):
        sample_charge_timer["is_departure_active"] = False
        sample_coordinator_data["charge_timer"][VIN] = sample_charge_timer
        switch = _make_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is False

    def test_none_when_no_coordinator_data(self, sample_vehicle):
        switch = _make_switch(None, sample_vehicle)
        assert switch.is_on is None

    def test_none_when_no_timer_data(self, sample_coordinator_data, sample_vehicle):
        switch = _make_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is None


class TestEntity:
    def test_unique_id(self, sample_coordinator_data, sample_vehicle):
        switch = _make_switch(sample_coordinator_data, sample_vehicle)
        assert switch.unique_id == f"{VIN}_charging_timer"

    def test_translation_key(self, sample_coordinator_data, sample_vehicle):
        switch = _make_switch(sample_coordinator_data, sample_vehicle)
        assert switch.translation_key == "charging_timer"

    def test_device_info(self, sample_coordinator_data, sample_vehicle):
        switch = _make_switch(sample_coordinator_data, sample_vehicle)
        assert switch.device_info["identifiers"] == {(DOMAIN, VIN)}
        assert switch.device_info["manufacturer"] == "Polestar"


class TestTurnOn:
    @pytest.mark.asyncio
    async def test_calls_set_with_activated_true(
        self, sample_coordinator_data, sample_vehicle, sample_charge_timer
    ):
        sample_charge_timer["is_departure_active"] = False
        sample_coordinator_data["charge_timer"][VIN] = sample_charge_timer
        switch = _make_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        switch.hass = hass

        await switch.async_turn_on()

        switch.coordinator.pccs.set_global_charge_timer.assert_called_once_with(
            VIN, 22, 0, 6, 30, True
        )
        switch.coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_grpc_error_raises_ha_error(
        self, sample_coordinator_data, sample_vehicle, sample_charge_timer
    ):
        sample_coordinator_data["charge_timer"][VIN] = sample_charge_timer
        switch = _make_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=grpc.RpcError())
        switch.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to set charging timer"):
            await switch.async_turn_on()

    @pytest.mark.asyncio
    async def test_pccs_error_raises_ha_error(
        self, sample_coordinator_data, sample_vehicle, sample_charge_timer
    ):
        sample_coordinator_data["charge_timer"][VIN] = sample_charge_timer
        switch = _make_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=PccsError("timer failed"))
        switch.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to set charging timer"):
            await switch.async_turn_on()


class TestTurnOff:
    @pytest.mark.asyncio
    async def test_calls_set_with_activated_false(
        self, sample_coordinator_data, sample_vehicle, sample_charge_timer
    ):
        sample_coordinator_data["charge_timer"][VIN] = sample_charge_timer
        switch = _make_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        switch.hass = hass

        await switch.async_turn_off()

        switch.coordinator.pccs.set_global_charge_timer.assert_called_once_with(
            VIN, 22, 0, 6, 30, False
        )
        switch.coordinator.async_request_refresh.assert_called_once()


class TestMissingTimerData:
    @pytest.mark.asyncio
    async def test_defaults_times_to_zero(self, sample_coordinator_data, sample_vehicle):
        """When no timer data exists, times default to 0."""
        switch = _make_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        switch.hass = hass

        await switch.async_turn_on()

        switch.coordinator.pccs.set_global_charge_timer.assert_called_once_with(
            VIN, 0, 0, 0, 0, True
        )


# ===========================================================================
# PolestarClimateSwitch tests
# ===========================================================================


def _make_climate_switch(
    coordinator_data: dict | None,
    vehicle: dict,
    vin: str = VIN,
) -> PolestarClimateSwitch:
    """Create a PolestarClimateSwitch with a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    coordinator.async_request_refresh = AsyncMock()
    coordinator.cep = create_autospec(CepClient, instance=True)
    coordinator.pccs = create_autospec(PccsClient, instance=True)
    return PolestarClimateSwitch(coordinator, vehicle, vin)


class TestClimateIsOn:
    def test_active_preconditioning(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["climate"][VIN]["status"] = "Pre-conditioning"
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is True

    def test_active_starting(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["climate"][VIN]["status"] = "Starting"
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is True

    def test_off(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["climate"][VIN]["status"] = "Off"
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is False

    def test_unknown(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["climate"][VIN]["status"] = "Unknown"
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is False

    def test_none_when_no_coordinator_data(self, sample_vehicle):
        switch = _make_climate_switch(None, sample_vehicle)
        assert switch.is_on is None

    def test_none_when_no_climate_data(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["climate"] = {}
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is None

    def test_none_when_status_is_none(self, sample_coordinator_data, sample_vehicle):
        sample_coordinator_data["climate"][VIN]["status"] = None
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.is_on is None


class TestClimateEntity:
    def test_unique_id(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.unique_id == f"{VIN}_climate"

    def test_translation_key(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.translation_key == "climate"

    def test_device_info(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        assert switch.device_info["identifiers"] == {(DOMAIN, VIN)}
        assert switch.device_info["manufacturer"] == "Polestar"


class TestClimateTurnOn:
    @pytest.mark.asyncio
    async def test_calls_cep_climatization_start(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        switch.hass = hass

        await switch.async_turn_on()

        switch.coordinator.cep.climatization_start.assert_called_once_with(VIN)
        switch.coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_grpc_error_raises_ha_error(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=grpc.RpcError())
        switch.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to start climate"):
            await switch.async_turn_on()

    @pytest.mark.asyncio
    async def test_cep_error_raises_ha_error(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)
        switch.coordinator.cep.climatization_start.side_effect = CepError(
            "Climatization failed: CAR_OFFLINE"
        )

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        switch.hass = hass

        with pytest.raises(
            HomeAssistantError,
            match=r"^Failed to start climate: Climatization failed: CAR_OFFLINE$",
        ):
            await switch.async_turn_on()
        switch.coordinator.async_request_refresh.assert_not_called()


class TestClimateTurnOff:
    @pytest.mark.asyncio
    async def test_calls_cep_climatization_stop(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
        switch.hass = hass

        await switch.async_turn_off()

        switch.coordinator.cep.climatization_stop.assert_called_once_with(VIN)
        switch.coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_grpc_error_raises_ha_error(self, sample_coordinator_data, sample_vehicle):
        switch = _make_climate_switch(sample_coordinator_data, sample_vehicle)

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(side_effect=grpc.RpcError())
        switch.hass = hass

        with pytest.raises(HomeAssistantError, match="Failed to stop climate"):
            await switch.async_turn_off()
