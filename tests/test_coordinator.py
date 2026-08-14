"""Tests for coordinator utility functions and resilience behavior."""

from __future__ import annotations

import base64
import logging
from unittest.mock import MagicMock, patch

import grpc
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.polestar_soc.coordinator import (
    LAYER_PCCS,
    PolestarCoordinator,
    _b64urlencode,
    _GrpcAuthError,
)

from .conftest import make_rpc_error as _rpc_error

VIN = "YSMYKEAE1RB000001"

# The exact GraphQL validation error Polestar returned in issue #22.
_FIELD_UNDEFINED = (
    "GraphQL errors: Validation error of type FieldUndefined: Field 'chargingStatus' "
    "in type 'BatteryV2' is undefined @ 'carTelematicsV2/battery/chargingStatus'"
)


@pytest.fixture
def mock_entry() -> MagicMock:
    """Build a minimal ConfigEntry stand-in."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.data = {
        "email": "user@example.com",
        "password": "pw",
        "access_token": "web_tok",
        "refresh_token": "web_refresh",
        "pccs_access_token": "pccs_tok",
        "pccs_refresh_token": "pccs_refresh",
    }
    return entry


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_entry: MagicMock) -> PolestarCoordinator:
    """Build a PolestarCoordinator with mocked clients."""
    coord = PolestarCoordinator(hass, mock_entry)
    coord.api = MagicMock()
    coord.api.get_vehicles = MagicMock(return_value=[{"vin": VIN}])
    coord.api.get_telematics = MagicMock(return_value=({"battery": [], "odometer": []}, {}))
    coord.pccs = MagicMock()
    coord.cep = MagicMock()
    coord.pccs.get_target_soc = MagicMock(return_value={"target_soc": 80})
    coord.pccs.get_amp_limit = MagicMock(return_value={"amp_limit": 16})
    coord.pccs.get_global_charge_timer = MagicMock(return_value={})
    coord.pccs.get_parking_climate_timers = MagicMock(return_value=[])
    coord.pccs.get_parking_climate_timer_settings = MagicMock(return_value={})
    coord.cep.get_parking_climatization = MagicMock(return_value={})
    coord.cep.get_battery = MagicMock(return_value={"soc": 76.0})
    coord.cep.get_location = MagicMock(return_value={})
    coord.cep.get_exterior = MagicMock(return_value={})
    coord.cep.get_availability = MagicMock(return_value={})
    coord.cep.get_health = MagicMock(return_value={})
    return coord


class TestB64UrlEncode:
    def test_simple_bytes(self):
        result = _b64urlencode(b"hello")
        # URL-safe base64 of "hello" without padding
        expected = base64.urlsafe_b64encode(b"hello").rstrip(b"=").decode()
        assert result == expected

    def test_no_padding(self):
        # Ensure no '=' padding characters
        for length in range(1, 50):
            result = _b64urlencode(bytes(range(length)))
            assert "=" not in result

    def test_url_safe_chars(self):
        # Standard base64 uses + and /, URL-safe uses - and _
        # Use bytes that produce + and / in standard base64
        data = b"\xfb\xff\xfe"
        result = _b64urlencode(data)
        assert "+" not in result
        assert "/" not in result

    def test_empty_input(self):
        result = _b64urlencode(b"")
        assert result == ""


# ---------------------------------------------------------------------------
# _do_fetch — happy path + per-call error classification
# ---------------------------------------------------------------------------


class TestDoFetchHappyPath:
    def test_returns_data_with_api_health(self, coordinator: PolestarCoordinator):
        result = coordinator._do_fetch(auth_retry_used=False)

        assert result["vehicles"] == [{"vin": VIN}]
        assert result["target_soc"] == {VIN: {"target_soc": 80}}
        assert result["cep_battery"] == {VIN: {"soc": 76.0}}
        # api_health is present and all layers are ok.
        assert "api_health" in result
        for layer in ("pccs", "cep", "graphql"):
            assert result["api_health"][layer]["status"] == "ok"
            assert result["api_health"][layer]["consecutive_failures"] == 0


class TestDoFetchAuthRetrySignal:
    def test_first_permission_denied_raises_grpc_auth_error(self, coordinator: PolestarCoordinator):
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.PERMISSION_DENIED)
        )
        with pytest.raises(_GrpcAuthError) as exc_info:
            coordinator._do_fetch(auth_retry_used=False)
        assert exc_info.value.layer == LAYER_PCCS

    def test_unauthenticated_also_raises(self, coordinator: PolestarCoordinator):
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.UNAUTHENTICATED)
        )
        with pytest.raises(_GrpcAuthError):
            coordinator._do_fetch(auth_retry_used=False)

    def test_unavailable_does_not_raise(self, coordinator: PolestarCoordinator):
        """UNAVAILABLE is transient — must not trigger refresh-and-retry."""
        # Make every PCCS getter fail so the cycle is a full-failure for
        # the layer (partial success would reset consecutive_failures to 0
        # and hide the regression we want to catch).
        unavailable = _rpc_error(grpc.StatusCode.UNAVAILABLE)
        coordinator.pccs.get_target_soc = MagicMock(side_effect=unavailable)
        coordinator.pccs.get_amp_limit = MagicMock(side_effect=unavailable)
        coordinator.pccs.get_global_charge_timer = MagicMock(side_effect=unavailable)
        coordinator.pccs.get_parking_climate_timers = MagicMock(side_effect=unavailable)
        coordinator.pccs.get_parking_climate_timer_settings = MagicMock(side_effect=unavailable)
        result = coordinator._do_fetch(auth_retry_used=False)
        # Full-failure cycle → degraded after 1 cycle, last_code recorded.
        assert result["api_health"]["pccs"]["status"] == "degraded"
        assert result["api_health"]["pccs"]["last_code"] == "UNAVAILABLE"

    def test_auth_retry_used_suppresses_raise(self, coordinator: PolestarCoordinator):
        """When the caller has already retried, PERMISSION_DENIED is recorded."""
        # Make every PCCS getter fail so the cycle is full-failure.
        denied = _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
        for name in (
            "get_target_soc",
            "get_amp_limit",
            "get_global_charge_timer",
            "get_parking_climate_timers",
            "get_parking_climate_timer_settings",
        ):
            setattr(coordinator.pccs, name, MagicMock(side_effect=denied))
        result = coordinator._do_fetch(auth_retry_used=True)
        # No exception; failure is recorded via layer health tracker.
        assert result["api_health"]["pccs"]["status"] == "degraded"
        assert result["api_health"]["pccs"]["last_code"] == "PERMISSION_DENIED"

    def test_only_first_failure_raises_in_a_cycle(self, coordinator: PolestarCoordinator):
        """Once the auth-retry slot is used, later PERMISSION_DENIEDs do not re-raise."""
        # PCCS fails first → raises → cycle aborts before CEP runs.
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.PERMISSION_DENIED)
        )
        coordinator.cep.get_battery = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.PERMISSION_DENIED)
        )
        with pytest.raises(_GrpcAuthError) as exc_info:
            coordinator._do_fetch(auth_retry_used=False)
        assert exc_info.value.layer == LAYER_PCCS  # PCCS won the race


class TestDoFetchLastKnownGood:
    def test_preserves_previous_value_on_failure(self, coordinator: PolestarCoordinator):
        # Prime self.data with a previous-cycle target_soc value.
        coordinator.data = {
            "target_soc": {VIN: {"target_soc": 90}},
        }
        # Now PCCS fails for target_soc (with auth_retry_used=True so we
        # don't raise _GrpcAuthError).
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.UNAVAILABLE)
        )
        result = coordinator._do_fetch(auth_retry_used=True)
        # Previous value preserved.
        assert result["target_soc"] == {VIN: {"target_soc": 90}}

    def test_no_previous_value_means_unavailable(self, coordinator: PolestarCoordinator):
        coordinator.data = None  # initial setup
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.UNAVAILABLE)
        )
        result = coordinator._do_fetch(auth_retry_used=True)
        # No data preserved — VIN absent from target_soc dict (sensor goes unavailable).
        assert result["target_soc"] == {}


class TestDoFetchTelematicsLastKnownGood:
    """The GraphQL dicts were the only coordinator data without a fallback.

    That is why a single bad poll blanked battery_soc rather than holding the
    last value, as the PCCS/CEP reads do via call_or_keep.
    """

    def test_failed_battery_subquery_keeps_previous_values(self, coordinator: PolestarCoordinator):
        coordinator.data = {
            "battery": {VIN: {"vin": VIN, "batteryChargeLevelPercentage": 72}},
            "odometer": {VIN: {"vin": VIN, "odometerMeters": 1000}},
        }
        coordinator.api.get_telematics = MagicMock(
            return_value=(
                {"battery": [], "odometer": [{"vin": VIN, "odometerMeters": 2000}]},
                {"carTelematicsV2.battery": UpdateFailed(_FIELD_UNDEFINED)},
            )
        )
        result = coordinator._do_fetch(auth_retry_used=True)

        assert result["battery"][VIN]["batteryChargeLevelPercentage"] == 72
        # The surviving sub-query still reports fresh data, not the stale value.
        assert result["odometer"][VIN]["odometerMeters"] == 2000

    def test_fresh_rows_win_over_previous(self, coordinator: PolestarCoordinator):
        """Anything the failing cycle did return must not be overwritten."""
        other_vin = "YSMYKEAE1RB000002"
        coordinator.data = {"battery": {VIN: {"batteryChargeLevelPercentage": 72}}}
        coordinator.api.get_vehicles = MagicMock(return_value=[{"vin": VIN}, {"vin": other_vin}])
        coordinator.api.get_telematics = MagicMock(
            return_value=(
                {
                    "battery": [{"vin": VIN, "batteryChargeLevelPercentage": 55}],
                    "odometer": [],
                },
                {"carTelematicsV2.battery": UpdateFailed(_FIELD_UNDEFINED)},
            )
        )
        result = coordinator._do_fetch(auth_retry_used=True)

        assert result["battery"][VIN]["batteryChargeLevelPercentage"] == 55
        # The fallback must not invent rows for VINs the failing query never returned.
        assert other_vin not in result["battery"]

    def test_no_previous_data_means_unavailable(self, coordinator: PolestarCoordinator):
        coordinator.data = None
        coordinator.api.get_telematics = MagicMock(
            return_value=(
                {"battery": [], "odometer": []},
                {"carTelematicsV2.battery": UpdateFailed(_FIELD_UNDEFINED)},
            )
        )
        result = coordinator._do_fetch(auth_retry_used=True)

        assert result["battery"] == {}

    def test_successful_empty_response_does_not_resurrect_previous(
        self, coordinator: PolestarCoordinator
    ):
        """An empty result from a *working* query is real data, not an outage."""
        coordinator.data = {"battery": {VIN: {"batteryChargeLevelPercentage": 72}}}
        coordinator.api.get_telematics = MagicMock(
            return_value=({"battery": [], "odometer": []}, {})
        )
        result = coordinator._do_fetch(auth_retry_used=True)

        assert result["battery"] == {}


class TestDoFetchWarnOnce:
    def test_repeat_failures_warn_once(
        self, coordinator: PolestarCoordinator, caplog: pytest.LogCaptureFixture
    ):
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=_rpc_error(grpc.StatusCode.UNAVAILABLE)
        )
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            for _ in range(3):
                coordinator._do_fetch(auth_retry_used=True)
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "UNAVAILABLE" in r.getMessage()
        ]
        assert len(warnings) == 1


class TestDoFetchGraphqlPartialFailureLogging:
    """Issue #22: a partial telematics failure logged only the endpoint name.

    The GraphQL ``errors[].message`` was built in ``_graphql`` and then dropped,
    so the operator saw ``_NonGrpcError (carTelematicsV2.battery)`` and had no way
    to tell a schema change from an outage.
    """

    def test_warning_carries_the_graphql_error_message(
        self, coordinator: PolestarCoordinator, caplog: pytest.LogCaptureFixture
    ):
        coordinator.api.get_telematics = MagicMock(
            return_value=(
                {"battery": [], "odometer": [{"vin": VIN, "odometerMeters": 1}]},
                {"carTelematicsV2.battery": UpdateFailed(_FIELD_UNDEFINED)},
            )
        )
        with caplog.at_level(logging.WARNING, logger="custom_components.polestar_soc.coordinator"):
            coordinator._do_fetch(auth_retry_used=True)

        graphql_warnings = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and "carTelematicsV2.battery" in r.getMessage()
        ]
        assert len(graphql_warnings) == 1
        assert "FieldUndefined" in graphql_warnings[0]
        assert "chargingStatus" in graphql_warnings[0]
        assert "_NonGrpcError" not in graphql_warnings[0]

    def test_succeeding_subquery_still_reports_its_data(self, coordinator: PolestarCoordinator):
        """The split exists so one broken sub-query cannot take the other down."""
        coordinator.api.get_telematics = MagicMock(
            return_value=(
                {"battery": [], "odometer": [{"vin": VIN, "odometerMeters": 12345678}]},
                {"carTelematicsV2.battery": UpdateFailed(_FIELD_UNDEFINED)},
            )
        )
        result = coordinator._do_fetch(auth_retry_used=True)

        assert result["odometer"][VIN]["odometerMeters"] == 12345678
        assert result["api_health"]["graphql"]["failing_endpoints"] == ["carTelematicsV2.battery"]


# ---------------------------------------------------------------------------
# _async_update_data — gRPC auth retry flow
# ---------------------------------------------------------------------------


class TestAsyncUpdateDataAuthRetry:
    @pytest.mark.usefixtures("hass")
    async def test_permission_denied_triggers_refresh_and_retry(
        self, coordinator: PolestarCoordinator
    ):
        """On PERMISSION_DENIED: refresh tokens, close both channels, retry once."""
        # First call raises _GrpcAuthError; second call (auth_retry_used=True) succeeds.
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=[
                _rpc_error(grpc.StatusCode.PERMISSION_DENIED),
                {"target_soc": 80},
            ]
        )

        with patch.object(coordinator, "_refresh_or_relogin", new_callable=MagicMock) as refresh:
            # Make the patched method awaitable.
            async def _async_refresh():
                return None

            refresh.side_effect = _async_refresh

            result = await coordinator._async_update_data()

        refresh.assert_called_once()
        coordinator.pccs.close.assert_called_once()
        coordinator.cep.close.assert_called_once()
        assert result["target_soc"] == {VIN: {"target_soc": 80}}

    @pytest.mark.usefixtures("hass")
    async def test_retry_failure_recorded_no_second_refresh(self, coordinator: PolestarCoordinator):
        """If retry also fails: layer marked degraded — no second refresh."""
        denied = _rpc_error(grpc.StatusCode.PERMISSION_DENIED)
        # Make every PCCS getter fail so the cycle is full-failure for the
        # layer (partial success would keep consecutive_failures at 0).
        for name in (
            "get_target_soc",
            "get_amp_limit",
            "get_global_charge_timer",
            "get_parking_climate_timers",
            "get_parking_climate_timer_settings",
        ):
            setattr(coordinator.pccs, name, MagicMock(side_effect=denied))

        with patch.object(coordinator, "_refresh_or_relogin", new_callable=MagicMock) as refresh:

            async def _async_refresh():
                return None

            refresh.side_effect = _async_refresh

            result = await coordinator._async_update_data()

        # Refresh called exactly once even though both attempts failed.
        refresh.assert_called_once()
        # Cycle 1 raised _GrpcAuthError before end_cycle(), so its failure
        # is NOT reflected in consecutive_failures.  Cycle 2 (the retry)
        # records the failure and reaches end_cycle(), incrementing the
        # counter to exactly 1 → status "degraded".
        assert result["api_health"]["pccs"]["consecutive_failures"] == 1
        assert result["api_health"]["pccs"]["status"] == "degraded"
        assert result["api_health"]["pccs"]["last_code"] == "PERMISSION_DENIED"
        assert "target_soc" in result["api_health"]["pccs"]["failing_endpoints"]

    @pytest.mark.usefixtures("hass")
    async def test_two_layers_share_one_retry(self, coordinator: PolestarCoordinator):
        """Two layers failing in the same cycle share the single refresh attempt."""
        coordinator.pccs.get_target_soc = MagicMock(
            side_effect=[
                _rpc_error(grpc.StatusCode.PERMISSION_DENIED),
                {"target_soc": 80},
            ]
        )
        coordinator.cep.get_battery = MagicMock(
            side_effect=[
                _rpc_error(grpc.StatusCode.PERMISSION_DENIED),
                {"soc": 76.0},
            ]
        )

        with patch.object(coordinator, "_refresh_or_relogin", new_callable=MagicMock) as refresh:

            async def _async_refresh():
                return None

            refresh.side_effect = _async_refresh

            await coordinator._async_update_data()

        # PCCS is the first layer attempted; it raises, retry runs both layers.
        # CEP fails on retry but auth_retry_used=True so no second refresh.
        refresh.assert_called_once()
        coordinator.pccs.close.assert_called_once()
        coordinator.cep.close.assert_called_once()
