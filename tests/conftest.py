"""Shared fixtures for Polestar SOC tests."""

import grpc
import pytest

# Activate the pytest-homeassistant-custom-component plugin
pytest_plugins = "pytest_homeassistant_custom_component"


# ---------------------------------------------------------------------------
# Shared gRPC error helper for resilience tests
# ---------------------------------------------------------------------------


class FakeRpcError(grpc.RpcError):
    """Real-exception subclass with .code() and .details(), suitable for ``side_effect``.

    ``MagicMock(spec=grpc.RpcError)`` cannot be raised because the mock is
    not a real Exception instance.  This stand-in is.
    """

    def __init__(self, code: grpc.StatusCode, details: str = "boom") -> None:
        super().__init__(f"{code.name}: {details}")
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


def make_rpc_error(code: grpc.StatusCode, details: str = "boom") -> grpc.RpcError:
    """Build a gRPC RpcError with the given status code."""
    return FakeRpcError(code, details)


@pytest.fixture
def sample_vehicle():
    """Return a sample vehicle dict as returned by the GraphQL API."""
    return {
        "vin": "YSMYKEAE1RB000001",
        "internalVehicleIdentifier": "abc123",
        "modelYear": 2025,
        "modelName": "Polestar 4",
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
        "charging_type": 1,
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
def sample_health():
    """Return a sample health state dict (as returned by CepClient)."""
    return {
        "days_to_service": 180,
        "distance_to_service_km": 12000,
        "service_warning": 1,
        "brake_fluid_level_warning": 1,
        "engine_coolant_level_warning": 1,
        "oil_level_warning": 1,
        "front_left_tyre_pressure_warning": 1,
        "front_right_tyre_pressure_warning": 1,
        "rear_left_tyre_pressure_warning": 1,
        "rear_right_tyre_pressure_warning": 1,
        "washer_fluid_level_warning": 1,
        "low_voltage_battery_warning": 1,
        "front_left_tyre_pressure_kpa": 240.0,
        "front_right_tyre_pressure_kpa": 240.0,
        "rear_left_tyre_pressure_kpa": 250.0,
        "rear_right_tyre_pressure_kpa": 250.0,
        "front_tyres_reference_pressure_kpa": 240.0,
        "rear_tyres_reference_pressure_kpa": 250.0,
        "brake_light_left_warning": 1,
        "brake_light_center_warning": 1,
        "brake_light_right_warning": 1,
        "fog_light_front_warning": 1,
        "fog_light_rear_warning": 1,
        "position_light_front_left_warning": 1,
        "position_light_front_right_warning": 1,
        "position_light_rear_left_warning": 1,
        "position_light_rear_right_warning": 1,
        "high_beam_left_warning": 1,
        "high_beam_right_warning": 1,
        "low_beam_left_warning": 1,
        "low_beam_right_warning": 1,
        "daytime_running_light_left_warning": 1,
        "daytime_running_light_right_warning": 1,
        "turn_indication_front_left_warning": 1,
        "turn_indication_front_right_warning": 1,
        "turn_indication_rear_left_warning": 1,
        "turn_indication_rear_right_warning": 1,
        "registration_plate_light_warning": 1,
        "side_mark_lights_warning": 1,
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
    sample_health,
):
    """Return a full coordinator data dict combining all sources."""
    vin = sample_vehicle["vin"]
    return {
        "vehicles": [sample_vehicle],
        "battery": {vin: sample_battery},
        "odometer": {vin: sample_odometer},
        "target_soc": {},
        "amp_limit": {},
        "charge_timer": {},
        "climate_timers": {},
        "climate_timer_settings": {},
        "climate": {vin: sample_climate},
        "cep_battery": {vin: sample_cep_battery},
        "location": {vin: sample_location},
        "exterior": {vin: sample_exterior},
        "availability": {vin: sample_availability},
        "health": {vin: sample_health},
        "api_health": {
            "pccs": {
                "status": "ok",
                "last_code": None,
                "last_success_at": None,
                "failing_endpoints": [],
                "consecutive_failures": 0,
            },
            "cep": {
                "status": "ok",
                "last_code": None,
                "last_success_at": None,
                "failing_endpoints": [],
                "consecutive_failures": 0,
            },
            "graphql": {
                "status": "ok",
                "last_code": None,
                "last_success_at": None,
                "failing_endpoints": [],
                "consecutive_failures": 0,
            },
        },
    }
