"""Tests for sensor value extraction functions."""

from custom_components.polestar_soc.sensor import (
    _battery_soc,
    _charging_status,
    _charging_time_remaining,
    _climate_heating,
    _climate_status,
    _estimated_range,
    _odometer_km,
)

VIN = "YSMYKEAE1RB000001"


# ---------------------------------------------------------------------------
# Existing sensors (updated signature: data, vin)
# ---------------------------------------------------------------------------


class TestBatterySoc:
    def test_returns_percentage(self, sample_coordinator_data):
        assert _battery_soc(sample_coordinator_data, VIN) == 72

    def test_none_when_no_battery(self, sample_coordinator_data):
        sample_coordinator_data["battery"] = {}
        assert _battery_soc(sample_coordinator_data, VIN) is None

    def test_none_when_missing_key(self):
        data = {"battery": {VIN: {"vin": "X"}}}
        assert _battery_soc(data, VIN) is None

    def test_zero_percent(self):
        data = {"battery": {VIN: {"batteryChargeLevelPercentage": 0}}}
        assert _battery_soc(data, VIN) == 0

    def test_full_charge(self):
        data = {"battery": {VIN: {"batteryChargeLevelPercentage": 100}}}
        assert _battery_soc(data, VIN) == 100


class TestChargingStatus:
    def test_known_status(self, sample_coordinator_data):
        assert _charging_status(sample_coordinator_data, VIN) == "Charging"

    def test_none_battery_returns_unknown(self):
        data = {"battery": {}}
        assert _charging_status(data, VIN) == "Unknown"

    def test_idle(self):
        data = {"battery": {VIN: {"chargingStatus": "CHARGING_STATUS_IDLE"}}}
        assert _charging_status(data, VIN) == "Idle"

    def test_missing_status_key(self):
        data = {"battery": {VIN: {"vin": "X"}}}
        result = _charging_status(data, VIN)
        assert result == "Unknown"


class TestChargingTimeRemaining:
    def test_returns_minutes(self, sample_coordinator_data):
        assert _charging_time_remaining(sample_coordinator_data, VIN) == 95

    def test_none_when_no_battery(self):
        data = {"battery": {}}
        assert _charging_time_remaining(data, VIN) is None

    def test_zero_minutes(self):
        data = {"battery": {VIN: {"estimatedChargingTimeToFullMinutes": 0}}}
        assert _charging_time_remaining(data, VIN) == 0


class TestOdometerKm:
    def test_converts_meters_to_km(self, sample_coordinator_data):
        result = _odometer_km(sample_coordinator_data, VIN)
        assert result == 12345.7  # 12345678 / 1000, rounded to 1 decimal

    def test_none_when_no_odometer(self):
        data = {"odometer": {}}
        assert _odometer_km(data, VIN) is None

    def test_none_when_missing_key(self):
        data = {"odometer": {VIN: {"vin": "X"}}}
        assert _odometer_km(data, VIN) is None

    def test_zero_meters(self):
        data = {"odometer": {VIN: {"odometerMeters": 0}}}
        assert _odometer_km(data, VIN) == 0.0

    def test_small_value(self):
        data = {"odometer": {VIN: {"odometerMeters": 500}}}
        assert _odometer_km(data, VIN) == 0.5


# ---------------------------------------------------------------------------
# New climate sensors
# ---------------------------------------------------------------------------


class TestClimateStatus:
    def test_returns_status(self, sample_coordinator_data):
        assert _climate_status(sample_coordinator_data, VIN) == "Off"

    def test_none_when_no_climate(self):
        data = {"climate": {}}
        assert _climate_status(data, VIN) is None

    def test_none_when_empty_data(self):
        assert _climate_status({}, VIN) is None

    def test_active_status(self):
        data = {"climate": {VIN: {"status": "Pre-conditioning"}}}
        assert _climate_status(data, VIN) == "Pre-conditioning"


class TestDriverSeatHeating:
    def test_returns_level(self, sample_coordinator_data):
        fn = _climate_heating("driver_seat_heating")
        assert fn(sample_coordinator_data, VIN) == "Off"

    def test_none_when_no_climate(self):
        fn = _climate_heating("driver_seat_heating")
        data = {"climate": {}}
        assert fn(data, VIN) is None

    def test_heating_active(self):
        fn = _climate_heating("driver_seat_heating")
        data = {"climate": {VIN: {"driver_seat_heating": "High"}}}
        assert fn(data, VIN) == "High"


class TestPassengerSeatHeating:
    def test_returns_level(self, sample_coordinator_data):
        fn = _climate_heating("passenger_seat_heating")
        assert fn(sample_coordinator_data, VIN) == "Off"


class TestRearLeftSeatHeating:
    def test_returns_level(self, sample_coordinator_data):
        fn = _climate_heating("rear_left_seat_heating")
        assert fn(sample_coordinator_data, VIN) == "Off"


class TestRearRightSeatHeating:
    def test_returns_level(self, sample_coordinator_data):
        fn = _climate_heating("rear_right_seat_heating")
        assert fn(sample_coordinator_data, VIN) == "Off"


class TestSteeringWheelHeating:
    def test_returns_level(self, sample_coordinator_data):
        fn = _climate_heating("steering_wheel_heating")
        assert fn(sample_coordinator_data, VIN) == "Off"

    def test_none_when_no_climate(self):
        fn = _climate_heating("steering_wheel_heating")
        assert fn({}, VIN) is None


class TestEstimatedRange:
    def test_returns_km(self, sample_coordinator_data):
        assert _estimated_range(sample_coordinator_data, VIN) == 230

    def test_none_when_no_cep_battery(self):
        data = {"cep_battery": {}}
        assert _estimated_range(data, VIN) is None

    def test_none_when_empty_data(self):
        assert _estimated_range({}, VIN) is None

    def test_none_when_range_missing(self):
        data = {"cep_battery": {VIN: {"soc": 76.0}}}
        assert _estimated_range(data, VIN) is None
