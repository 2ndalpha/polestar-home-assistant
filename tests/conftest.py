"""Shared fixtures for Polestar SOC tests."""

import pytest

# Activate the pytest-homeassistant-custom-component plugin
pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def sample_vehicle():
    """Return a sample vehicle dict as returned by the GraphQL API."""
    return {
        "vin": "YSMYKEAE1RB000001",
        "internalVehicleIdentifier": "abc123",
        "modelYear": 2025,
        "content": {"model": {"code": "534", "name": "Polestar 4"}},
        "hasPerformancePackage": False,
        "registrationNo": "ABC123",
        "deliveryDate": "2025-03-01",
        "currentPlannedDeliveryDate": "2025-03-01",
    }


@pytest.fixture
def sample_battery():
    """Return a sample battery telemetry dict."""
    return {
        "vin": "YSMYKEAE1RB000001",
        "batteryChargeLevelPercentage": 72,
        "chargingStatus": "CHARGING_STATUS_CHARGING",
        "estimatedChargingTimeToFullMinutes": 95,
    }


@pytest.fixture
def sample_odometer():
    """Return a sample odometer telemetry dict."""
    return {
        "vin": "YSMYKEAE1RB000001",
        "odometerMeters": 12345678,
    }
