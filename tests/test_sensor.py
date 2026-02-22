"""Tests for sensor value extraction functions."""

from custom_components.polestar_soc.sensor import (
    _battery_soc,
    _charging_status,
    _charging_time_remaining,
    _odometer_km,
)


class TestBatterySoc:
    def test_returns_percentage(self, sample_vehicle, sample_battery):
        assert _battery_soc(sample_vehicle, sample_battery, None) == 72

    def test_none_when_no_battery(self, sample_vehicle):
        assert _battery_soc(sample_vehicle, None, None) is None

    def test_none_when_missing_key(self, sample_vehicle):
        assert _battery_soc(sample_vehicle, {"vin": "X"}, None) is None

    def test_zero_percent(self, sample_vehicle):
        battery = {"batteryChargeLevelPercentage": 0}
        assert _battery_soc(sample_vehicle, battery, None) == 0

    def test_full_charge(self, sample_vehicle):
        battery = {"batteryChargeLevelPercentage": 100}
        assert _battery_soc(sample_vehicle, battery, None) == 100


class TestChargingStatus:
    def test_known_status(self, sample_vehicle, sample_battery):
        assert _charging_status(sample_vehicle, sample_battery, None) == "Charging"

    def test_none_battery_returns_unknown(self, sample_vehicle):
        assert _charging_status(sample_vehicle, None, None) == "Unknown"

    def test_idle(self, sample_vehicle):
        battery = {"chargingStatus": "CHARGING_STATUS_IDLE"}
        assert _charging_status(sample_vehicle, battery, None) == "Idle"

    def test_missing_status_key(self, sample_vehicle):
        battery = {"vin": "X"}
        result = _charging_status(sample_vehicle, battery, None)
        assert result == "Unknown"


class TestChargingTimeRemaining:
    def test_returns_minutes(self, sample_vehicle, sample_battery):
        assert _charging_time_remaining(sample_vehicle, sample_battery, None) == 95

    def test_none_when_no_battery(self, sample_vehicle):
        assert _charging_time_remaining(sample_vehicle, None, None) is None

    def test_zero_minutes(self, sample_vehicle):
        battery = {"estimatedChargingTimeToFullMinutes": 0}
        assert _charging_time_remaining(sample_vehicle, battery, None) == 0


class TestOdometerKm:
    def test_converts_meters_to_km(self, sample_vehicle, sample_odometer):
        result = _odometer_km(sample_vehicle, None, sample_odometer)
        assert result == 12345.7  # 12345678 / 1000, rounded to 1 decimal

    def test_none_when_no_odometer(self, sample_vehicle):
        assert _odometer_km(sample_vehicle, None, None) is None

    def test_none_when_missing_key(self, sample_vehicle):
        assert _odometer_km(sample_vehicle, None, {"vin": "X"}) is None

    def test_zero_meters(self, sample_vehicle):
        odometer = {"odometerMeters": 0}
        assert _odometer_km(sample_vehicle, None, odometer) == 0.0

    def test_small_value(self, sample_vehicle):
        odometer = {"odometerMeters": 500}
        assert _odometer_km(sample_vehicle, None, odometer) == 0.5
