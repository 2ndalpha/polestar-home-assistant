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


@pytest.fixture
def sample_exterior():
    """Return a sample exterior state dict (as returned by CepClient)."""
    return {
        "central_lock": 2,  # LOCKED
        "front_left_door": 2,  # CLOSED
        "front_right_door": 2,  # CLOSED
        "rear_left_door": 2,  # CLOSED
        "rear_right_door": 2,  # CLOSED
        "front_left_window": 2,  # CLOSED
        "front_right_window": 2,  # CLOSED
        "rear_left_window": 2,  # CLOSED
        "rear_right_window": 2,  # CLOSED
        "hood": 2,  # CLOSED
        "tailgate": 2,  # CLOSED
        "tank_lid": 2,  # CLOSED
        "sunroof": 0,  # UNSPECIFIED (no sunroof)
        "alarm": 1,  # IDLE
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
def sample_charge_timer():
    """Return a sample charge timer dict."""
    return {
        "start_hour": 22,
        "start_min": 0,
        "end_hour": 6,
        "end_min": 30,
        "is_departure_active": True,
    }


@pytest.fixture
def sample_climate_timers():
    """Return a sample list of parking climate timer dicts."""
    return [
        {
            "timer_id": "aaaaaaaa-1111-2222-3333-444444444444",
            "index": 0,
            "hour": 7,
            "minute": 30,
            "activated": True,
            "repeat": True,
            "weekdays": [1, 2, 3, 4, 5],
            "metadata_raw": None,
            "start_date_raw": None,
        },
        {
            "timer_id": "bbbbbbbb-1111-2222-3333-444444444444",
            "index": 1,
            "hour": 8,
            "minute": 0,
            "activated": False,
            "repeat": False,
            "weekdays": [],
            "metadata_raw": None,
            "start_date_raw": None,
        },
    ]


@pytest.fixture
def sample_climate_timer_settings():
    """Return a sample climate timer settings dict."""
    return {
        "temperature": 22.0,
    }


@pytest.fixture
def sample_availability():
    """Return a sample availability state dict (as returned by CepClient)."""
    return {
        "availability_status": 1,  # AVAILABLE
        "unavailable_reason": None,
        "usage_mode": 2,  # INACTIVE
    }


@pytest.fixture
def sample_coordinator_data(
    sample_vehicle,
    sample_battery,
    sample_odometer,
    sample_climate,
    sample_cep_battery,
    sample_location,
    sample_exterior,
    sample_availability,
):
    """Return a full coordinator data dict combining all sources."""
    vin = sample_vehicle["vin"]
    return {
        "vehicles": [sample_vehicle],
        "battery": {vin: sample_battery},
        "odometer": {vin: sample_odometer},
        "target_soc": {},
        "charge_timer": {},
        "climate_timers": {},
        "climate_timer_settings": {},
        "climate": {vin: sample_climate},
        "cep_battery": {vin: sample_cep_battery},
        "location": {vin: sample_location},
        "exterior": {vin: sample_exterior},
        "availability": {vin: sample_availability},
    }
