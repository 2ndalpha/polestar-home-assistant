"""Tests for CEP gRPC client protobuf parsers and request builder."""

import struct

import pytest

from custom_components.polestar_soc.cep import (
    _build_vin_request,
    _format_climate_status,
    _format_heating_intensity,
    _parse_battery_response,
    _parse_climate_response,
)
from custom_components.polestar_soc.proto import (
    _decode_message,
    _encode_field_bytes,
    _get_submessage,
)

# Synthetic test payloads built with a fake VIN.
# ParkingClimatization: climate off, all seat heaters off
CLIMATE_PAYLOAD = bytes.fromhex(
    "121159534d594b4541453152423030303030311a1e0a0c08b0dcb6cd0610808c8d9e02"
    "10021800300348005000580060006800"
)

# Battery: SOC=76.0, avg_consumption=2.9, range=230km/140mi,
# charger_connection=2(DISCONNECTED), charging_status=2(IDLE),
# est_charging_time=0, charging_type=1, charger_power_status=1, field28=5
BATTERY_PAYLOAD = bytes.fromhex(
    "121159534d594b4541453152423030303030311a350a0c08b0dcb6cd0610808c8d9e02"
    "11000000000000534019333333333333074020e601280030023802408c01"
    "880101d00101e00105"
)

TEST_VIN = "YSMYKEAE1RB000001"


class TestBuildVinRequest:
    def test_produces_field_2_string(self):
        result = _build_vin_request(TEST_VIN)
        fields = _decode_message(result)
        # Field 2 should be the VIN as bytes
        assert 2 in fields
        assert fields[2][0] == TEST_VIN.encode("utf-8")

    def test_roundtrip_vin(self):
        result = _build_vin_request(TEST_VIN)
        # Should be: tag(field 2, wire type 2) + varint(len) + VIN bytes
        expected = _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        assert result == expected


class TestParseClimateResponse:
    def test_parsed_response(self):
        result = _parse_climate_response(CLIMATE_PAYLOAD)
        assert result["status"] == "Off"
        assert result["driver_seat_heating"] == "Off"
        assert result["passenger_seat_heating"] == "Off"
        assert result["rear_left_seat_heating"] == "Off"
        assert result["rear_right_seat_heating"] == "Off"
        assert result["steering_wheel_heating"] == "Off"

    def test_empty_response(self):
        result = _parse_climate_response(b"")
        assert result["status"] is None
        assert result["driver_seat_heating"] is None

    def test_two_level_decode(self):
        """Verify outer envelope has field 2=VIN and field 3=state."""
        outer = _decode_message(CLIMATE_PAYLOAD)
        # Field 2 should be VIN
        assert outer[2][0] == TEST_VIN.encode("utf-8")
        # Field 3 should be a sub-message (bytes)
        assert isinstance(outer[3][0], (bytes, bytearray))
        # Inner state should have field 2 (running status)
        state = _get_submessage(outer, 3)
        assert state is not None
        assert 2 in state  # running status field

    def test_missing_state_submessage(self):
        """Response with VIN but no field 3 returns None values."""
        # Just a VIN field with no state sub-message
        data = _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        result = _parse_climate_response(data)
        assert result["status"] is None
        assert result["driver_seat_heating"] is None


class TestParseBatteryResponse:
    def test_parsed_response(self):
        result = _parse_battery_response(BATTERY_PAYLOAD)
        assert result["soc"] == pytest.approx(76.0)
        assert result["estimated_range_km"] == 230
        assert result["avg_energy_consumption_kwh_per_100km"] == pytest.approx(2.9, abs=0.1)
        assert result["charger_connection_status"] == 2  # DISCONNECTED
        assert result["charging_status"] == 2  # IDLE (from field 7)
        assert result["estimated_charging_time_minutes"] is None  # field 5 = 0
        assert result["estimated_range_miles"] == 140
        assert result["charging_power_watts"] is None  # field 10 not in payload

    def test_raw_fields(self):
        result = _parse_battery_response(BATTERY_PAYLOAD)
        raw = result["raw_fields"]
        assert set(raw.keys()) == {5, 7, 8, 17, 26, 28}
        assert raw[5] == 0   # estimated_charging_time_to_full_minutes
        assert raw[7] == 2   # charging_status (IDLE)
        assert raw[8] == 140  # estimated_distance_to_empty_miles
        assert raw[17] == 1  # charging_type
        assert raw[26] == 1  # charger_power_status
        assert raw[28] == 5  # unknown CEP-specific field

    def test_empty_response(self):
        result = _parse_battery_response(b"")
        assert result["soc"] is None
        assert result["estimated_range_km"] is None
        assert result["charging_status"] is None
        assert result["charger_connection_status"] is None
        assert result["charging_power_watts"] is None
        assert result["raw_fields"] == {}

    def test_two_level_decode(self):
        """Verify outer envelope field 3 contains battery state."""
        outer = _decode_message(BATTERY_PAYLOAD)
        assert outer[2][0] == TEST_VIN.encode("utf-8")
        state = _get_submessage(outer, 3)
        assert state is not None
        # Field 2 is SOC (fixed64, stored as uint64)
        assert 2 in state
        raw_soc = state[2][0]
        soc = struct.unpack("<d", struct.pack("<Q", raw_soc))[0]
        assert soc == pytest.approx(76.0)

    def test_missing_state_submessage(self):
        data = _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        result = _parse_battery_response(data)
        assert result["soc"] is None
        assert result["estimated_range_km"] is None
        assert result["raw_fields"] == {}


class TestFormatClimateStatus:
    def test_known_values(self):
        assert _format_climate_status(0) == "Unknown"
        assert _format_climate_status(2) == "Off"
        assert _format_climate_status(3) == "Pre-conditioning"

    def test_unknown_value(self):
        result = _format_climate_status(99)
        assert result == "Unknown (99)"


class TestFormatHeatingIntensity:
    def test_known_values(self):
        assert _format_heating_intensity(0) == "Off"
        assert _format_heating_intensity(1) == "Low"
        assert _format_heating_intensity(2) == "Medium"
        assert _format_heating_intensity(3) == "High"

    def test_unknown_value(self):
        result = _format_heating_intensity(42)
        assert result == "Unknown (42)"
