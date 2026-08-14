"""Tests for the defensive GraphQL battery/odometer split."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.polestar_soc.coordinator import PolestarAPI

VIN = "YSMYKEAE1RB000001"
_BATTERY_DATA = {
    "vin": VIN,
    "batteryChargeLevelPercentage": 72,
    "estimatedChargingTimeToFullMinutes": 95,
}
_ODOMETER_DATA = {"vin": VIN, "odometerMeters": 12345678}


def _api_with_token() -> PolestarAPI:
    api = PolestarAPI()
    api.access_token = "test_token"
    return api


def _stub_graphql(api: PolestarAPI, *, battery=None, odometer=None) -> None:
    """Patch ``api._graphql`` to return per-query payloads or raise UpdateFailed."""

    def fake(query: str, variables: dict | None = None) -> dict:
        if "Battery" in query:
            if isinstance(battery, Exception):
                raise battery
            return {"carTelematicsV2": {"battery": battery or []}}
        if "Odometer" in query:
            if isinstance(odometer, Exception):
                raise odometer
            return {"carTelematicsV2": {"odometer": odometer or []}}
        raise AssertionError(f"Unexpected query: {query[:80]!r}")

    api._graphql = fake  # type: ignore[method-assign]


class TestGetTelematicsHappyPath:
    def test_returns_combined_data_and_no_failures(self):
        api = _api_with_token()
        _stub_graphql(api, battery=[_BATTERY_DATA], odometer=[_ODOMETER_DATA])

        data, failing = api.get_telematics([VIN])

        assert data == {"battery": [_BATTERY_DATA], "odometer": [_ODOMETER_DATA]}
        assert failing == {}


class TestGetTelematicsPartialFailure:
    def test_battery_fails_odometer_succeeds(self):
        api = _api_with_token()
        _stub_graphql(
            api,
            battery=UpdateFailed("battery field removed"),
            odometer=[_ODOMETER_DATA],
        )

        data, failing = api.get_telematics([VIN])

        assert data["battery"] == []
        assert data["odometer"] == [_ODOMETER_DATA]
        assert list(failing) == ["carTelematicsV2.battery"]
        assert "battery field removed" in str(failing["carTelematicsV2.battery"])

    def test_odometer_fails_battery_succeeds(self):
        api = _api_with_token()
        _stub_graphql(
            api,
            battery=[_BATTERY_DATA],
            odometer=UpdateFailed("odometer schema bad"),
        )

        data, failing = api.get_telematics([VIN])

        assert data["battery"] == [_BATTERY_DATA]
        assert data["odometer"] == []
        assert list(failing) == ["carTelematicsV2.odometer"]
        assert "odometer schema bad" in str(failing["carTelematicsV2.odometer"])


class TestGetTelematicsBothFail:
    def test_raises_update_failed(self):
        api = _api_with_token()
        _stub_graphql(
            api,
            battery=UpdateFailed("a"),
            odometer=UpdateFailed("b"),
        )
        with pytest.raises(UpdateFailed) as exc_info:
            api.get_telematics([VIN])
        msg = str(exc_info.value)
        assert "battery=" in msg
        assert "odometer=" in msg


class TestGetTelematicsHttp401Propagates:
    def test_http_error_is_not_caught(self):
        """HTTP 401 must propagate so the existing 401 retry path triggers."""
        import requests

        class FakeResp:
            status_code = 401

        http_err = requests.HTTPError("401")
        http_err.response = FakeResp()  # type: ignore[assignment]

        api = _api_with_token()
        # Both sub-queries raise HTTPError — must propagate, not be caught.
        with (
            patch.object(api, "_graphql", side_effect=http_err),
            pytest.raises(requests.HTTPError),
        ):
            api.get_telematics([VIN])


class TestGetTelematicsEmptyResponses:
    def test_missing_keys_treated_as_empty_lists(self):
        api = _api_with_token()

        def fake(query: str, variables: dict | None = None) -> dict:
            return {"carTelematicsV2": {}}

        api._graphql = fake  # type: ignore[method-assign]

        data, failing = api.get_telematics([VIN])
        assert data == {"battery": [], "odometer": []}
        assert failing == {}
