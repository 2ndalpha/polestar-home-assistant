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


@pytest.fixture
def sample_climate():
    """Return a sample climate status dict (as returned by CepClient)."""
    return {
        "status": "Off",
        "driver_seat_heating": "Off",
        "passenger_seat_heating": "Off",
        "rear_left_seat_heating": "Off",
        "rear_right_seat_heating": "Off",
        "steering_wheel_heating": "Off",
    }


@pytest.fixture
def sample_cep_battery():
    """Return a sample CEP battery dict (as returned by CepClient)."""
    return {
        "soc": 76.0,
        "estimated_range_km": 230,
        "charger_connection_status": 2,
        "charging_status": 2,
        "avg_energy_consumption_kwh_per_100km": 2.9,
        "estimated_charging_time_minutes": None,
        "estimated_range_miles": 140,
        "charging_power_watts": None,
        "raw_fields": {},
    }


VIN = "YSMYKEAE1RB000001"


@pytest.fixture
def sample_location():
    """Return a sample location dict (as returned by CepClient)."""
    return {
        "latitude": 59.329323,
        "longitude": 18.068581,
        "timestamp_ms": 1772990058845,
    }


@pytest.fixture
def sample_coordinator_data(
    sample_vehicle,
    sample_battery,
    sample_odometer,
    sample_climate,
    sample_cep_battery,
    sample_location,
):
    """Return a full coordinator data dict combining all sources."""
    vin = sample_vehicle["vin"]
    return {
        "vehicles": [sample_vehicle],
        "battery": {vin: sample_battery},
        "odometer": {vin: sample_odometer},
        "target_soc": {},
        "charge_timer": {},
        "climate": {vin: sample_climate},
        "cep_battery": {vin: sample_cep_battery},
        "location": {vin: sample_location},
    }
