"""Tests for sensor value extraction functions."""

from custom_components.polestar_soc.sensor import (
    _battery_soc,
    _charging_power,
    _charging_status,
    _charging_time_remaining,
    _charging_type,
    _climate_heating,
    _climate_status,
    _estimated_range,
    _estimated_range_miles,
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
    """``charging_status`` is sourced from CEP field 7 since issue #22."""

    def test_known_status(self, sample_coordinator_data):
        # sample_cep_battery carries charging_status: 2 (IDLE).
        assert _charging_status(sample_coordinator_data, VIN) == "Idle"

    def test_charging(self):
        data = {"cep_battery": {VIN: {"charging_status": 1}}}
        assert _charging_status(data, VIN) == "Charging"

    def test_scheduled(self):
        data = {"cep_battery": {VIN: {"charging_status": 3}}}
        assert _charging_status(data, VIN) == "Scheduled"

    def test_none_value_with_key_present_returns_none(self):
        """The production no-data case — CEP always emits the key, sometimes as None.

        Without a guard ahead of the map lookup this renders "Unknown (None)".
        """
        data = {"cep_battery": {VIN: {"charging_status": None}}}
        assert _charging_status(data, VIN) is None

    def test_missing_vin_returns_none(self):
        data = {"cep_battery": {}}
        assert _charging_status(data, VIN) is None

    def test_missing_status_key_returns_none(self):
        data = {"cep_battery": {VIN: {"soc": 50.0}}}
        assert _charging_status(data, VIN) is None

    def test_unmapped_enum_value_is_surfaced(self):
        """An undiscovered CEP enum member must be visible, not silently dropped."""
        data = {"cep_battery": {VIN: {"charging_status": 7}}}
        assert _charging_status(data, VIN) == "Unknown (7)"


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


# ---------------------------------------------------------------------------
# Battery/charging sensors (CEP)
# ---------------------------------------------------------------------------


class TestChargingPower:
    def test_returns_watts(self):
        data = {"cep_battery": {VIN: {"charging_power_watts": 11000}}}
        assert _charging_power(data, VIN) == 11000

    def test_none_when_not_charging(self, sample_coordinator_data):
        # Fixture has charging_power_watts=None (not charging)
        assert _charging_power(sample_coordinator_data, VIN) is None

    def test_none_when_no_cep_battery(self):
        assert _charging_power({}, VIN) is None

    def test_none_when_missing_key(self):
        data = {"cep_battery": {VIN: {"soc": 76.0}}}
        assert _charging_power(data, VIN) is None


class TestChargingType:
    def test_not_charging(self, sample_coordinator_data):
        # Fixture has charging_type=1 (NONE)
        assert _charging_type(sample_coordinator_data, VIN) == "Not charging"

    def test_ac(self):
        data = {"cep_battery": {VIN: {"charging_type": 2}}}
        assert _charging_type(data, VIN) == "AC"

    def test_dc(self):
        data = {"cep_battery": {VIN: {"charging_type": 3}}}
        assert _charging_type(data, VIN) == "DC"

    def test_wireless(self):
        data = {"cep_battery": {VIN: {"charging_type": 4}}}
        assert _charging_type(data, VIN) == "Wireless"

    def test_none_when_no_cep_battery(self):
        assert _charging_type({}, VIN) is None

    def test_none_when_missing_key(self):
        data = {"cep_battery": {VIN: {"soc": 76.0}}}
        assert _charging_type(data, VIN) is None

    def test_unknown_value_returns_none(self):
        data = {"cep_battery": {VIN: {"charging_type": 99}}}
        assert _charging_type(data, VIN) is None


class TestEstimatedRangeMiles:
    def test_returns_miles(self, sample_coordinator_data):
        assert _estimated_range_miles(sample_coordinator_data, VIN) == 140

    def test_none_when_no_cep_battery(self):
        assert _estimated_range_miles({}, VIN) is None

    def test_none_when_missing_key(self):
        data = {"cep_battery": {VIN: {"soc": 76.0}}}
        assert _estimated_range_miles(data, VIN) is None
