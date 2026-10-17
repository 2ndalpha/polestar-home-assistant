"""Tests for CEP gRPC client protobuf parsers, request builders and commands."""

import struct
from collections.abc import Iterable, Iterator
from unittest.mock import MagicMock

import grpc
import pytest

from custom_components.polestar_soc.cep import (
    CepClient,
    CepError,
    _build_climatization_start_request,
    _build_climatization_stop_request,
    _build_location_request,
    _build_lock_request,
    _build_unlock_request,
    _build_vin_request,
    _format_climate_status,
    _format_heating_intensity,
    _lock_error_context,
    _parse_availability_response,
    _parse_battery_response,
    _parse_climate_response,
    _parse_exterior_response,
    _parse_health_response,
    _parse_location_response,
)
from custom_components.polestar_soc.proto import (
    _decode_message,
    _encode_field_bytes,
    _encode_field_fixed32,
    _encode_field_varint,
    _get_float,
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


# ---------------------------------------------------------------------------
# InvocationService commands
# ---------------------------------------------------------------------------

# CEP InvocationRequest: field 1 = VIN, nothing else.
TEST_INVOCATION_REQUEST = _encode_field_bytes(1, TEST_VIN.encode("utf-8"))


def _invocation_response(status: int, message: str = "", lock_error: int = 0) -> bytes:
    """Build one InvocationService stream message as C3 sends it."""
    inner = (
        _encode_field_bytes(1, b"0b6c2f4e-request-id")
        + _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        + _encode_field_varint(3, status)
    )
    if message:
        inner += _encode_field_bytes(4, message.encode("utf-8"))
    inner += _encode_field_varint(5, 1_760_000_000_000)
    response = _encode_field_bytes(1, inner)
    if lock_error:
        response += _encode_field_varint(2, lock_error)
    return response


def _success_stream() -> list[bytes]:
    return [_invocation_response(1), _invocation_response(4), _invocation_response(6)]


def _stream_then_cancel(*responses: bytes) -> Iterator[bytes]:
    yield from responses
    raise grpc.RpcError()


def _client_streaming(responses: Iterable[bytes]) -> tuple[CepClient, MagicMock]:
    """Create a CepClient whose channel streams ``responses`` for any command."""
    channel = MagicMock()
    channel.unary_stream.return_value.return_value = iter(responses)
    client = CepClient("read-token", write_access_token="write-token")
    client._channel = channel
    return client, channel


class TestBuildCommandRequests:
    def test_climatization_start_field_tree(self):
        fields = _decode_message(_build_climatization_start_request(TEST_VIN, 21.5))
        assert sorted(fields) == [1, 2, 3]
        assert fields[1] == [TEST_INVOCATION_REQUEST]
        assert fields[2] == [1]
        assert _get_float(fields, 3) == 21.5

    def test_climatization_start_defaults_to_22_degrees(self):
        fields = _decode_message(_build_climatization_start_request(TEST_VIN))
        assert _get_float(fields, 3) == 22.0

    def test_climatization_stop_field_tree(self):
        data = _build_climatization_stop_request(TEST_VIN)
        assert _decode_message(data) == {1: [TEST_INVOCATION_REQUEST]}

    def test_lock_field_tree_sends_lock_type_zero(self):
        data = _build_lock_request(TEST_VIN)
        assert _decode_message(data) == {1: [TEST_INVOCATION_REQUEST], 2: [0]}

    def test_lock_reduced_guard_field_tree(self):
        data = _build_lock_request(TEST_VIN, lock_type=1)
        assert _decode_message(data) == {1: [TEST_INVOCATION_REQUEST], 2: [1]}

    def test_unlock_field_tree_sends_unspecified_unlock_type(self):
        data = _build_unlock_request(TEST_VIN)
        assert _decode_message(data) == {1: [TEST_INVOCATION_REQUEST], 2: [0]}


class TestCepClientCommands:
    @pytest.mark.parametrize(
        ("command", "method_path", "expected_request"),
        [
            pytest.param(
                "climatization_start",
                "/invocation.InvocationService/ClimatizationStart",
                _build_climatization_start_request(TEST_VIN),
                id="climatization_start",
            ),
            pytest.param(
                "climatization_stop",
                "/invocation.InvocationService/ClimatizationStop",
                _build_climatization_stop_request(TEST_VIN),
                id="climatization_stop",
            ),
            pytest.param(
                "lock",
                "/invocation.InvocationService/Lock",
                _build_lock_request(TEST_VIN),
                id="lock",
            ),
            pytest.param(
                "unlock",
                "/invocation.InvocationService/Unlock",
                _build_unlock_request(TEST_VIN),
                id="unlock",
            ),
        ],
    )
    def test_sends_to_c3_invocation_service_with_write_token_and_vin(
        self, command, method_path, expected_request
    ):
        client, channel = _client_streaming(_success_stream())

        result = getattr(client, command)(TEST_VIN)

        assert channel.unary_stream.call_args.args[0] == method_path
        stream_call = channel.unary_stream.return_value.call_args
        assert stream_call.args[0] == expected_request
        assert stream_call.kwargs["metadata"] == [
            ("authorization", "Bearer write-token"),
            ("vin", TEST_VIN),
        ]
        assert result["status"] == 6

    @pytest.mark.parametrize("vin", ["", "   "], ids=["empty", "whitespace"])
    @pytest.mark.parametrize(
        "command",
        [
            "climatization_start",
            "climatization_stop",
            "lock",
            "unlock",
            "window_open",
            "window_close",
        ],
    )
    def test_empty_vin_raises_before_any_rpc(self, command, vin):
        client, channel = _client_streaming(_success_stream())

        with pytest.raises(CepError, match="not sent: the vehicle VIN is empty"):
            getattr(client, command)(vin)

        channel.unary_stream.assert_not_called()

    def test_car_offline_terminal_status_raises_cep_error(self):
        client, _ = _client_streaming(
            [_invocation_response(1), _invocation_response(2, "Vehicle not reachable")]
        )

        with pytest.raises(
            CepError, match=r"^Climatization failed: CAR_OFFLINE - Vehicle not reachable$"
        ):
            client.climatization_start(TEST_VIN)

    def test_lock_failure_includes_lock_error_context(self):
        client, _ = _client_streaming(
            [_invocation_response(1), _invocation_response(11, lock_error=1)]
        )

        with pytest.raises(
            CepError, match=r"^Lock failed: INVOCATION_SPECIFIC_ERROR \(a door is open\)$"
        ):
            client.lock(TEST_VIN)

    def test_stream_cancelled_after_delivered_returns_delivered(self):
        client, _ = _client_streaming(
            _stream_then_cancel(_invocation_response(1), _invocation_response(4))
        )

        assert client.climatization_stop(TEST_VIN)["status"] == 4

    def test_stream_cancelled_before_delivered_raises_rpc_error(self):
        client, _ = _client_streaming(_stream_then_cancel(_invocation_response(1)))

        with pytest.raises(grpc.RpcError):
            client.unlock(TEST_VIN)


class TestLockErrorContext:
    def test_returns_empty_string_for_empty_response(self):
        assert _lock_error_context(b"") == ""

    def test_no_lock_error(self):
        assert _lock_error_context(_invocation_response(6)) == ""

    def test_door_open_error(self):
        assert _lock_error_context(_invocation_response(11, lock_error=1)) == "a door is open"

    def test_returns_empty_string_for_unspecified_lock_error(self):
        data = _invocation_response(6) + _encode_field_varint(2, 0)
        assert _lock_error_context(data) == ""

    def test_returns_empty_string_for_length_delimited_lock_error(self):
        data = _invocation_response(11) + _encode_field_bytes(2, b"\x01")
        assert _lock_error_context(data) == ""

    def test_unknown_error_code(self):
        data = _invocation_response(11, lock_error=99)
        assert _lock_error_context(data) == "lock error (code 99)"


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
        assert result["charging_type"] == 1  # NONE (not charging)

    def test_raw_fields(self):
        result = _parse_battery_response(BATTERY_PAYLOAD)
        raw = result["raw_fields"]
        assert set(raw.keys()) == {5, 7, 8, 17, 26, 28}
        assert raw[5] == 0  # estimated_charging_time_to_full_minutes
        assert raw[7] == 2  # charging_status (IDLE)
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
        assert result["charging_type"] is None
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


def _build_location_payload(
    vin: str, longitude: float, latitude: float, timestamp_ms: int
) -> bytes:
    """Build a synthetic GetLastKnownLocation response payload."""
    # field 1 = VIN (string), field 2 = longitude (double/fixed64),
    # field 3 = latitude (double/fixed64), field 4 = timestamp_ms (varint)
    data = _encode_field_bytes(1, vin.encode("utf-8"))
    # Wire type 1 (fixed64) for doubles: tag = (field_number << 3) | 1
    data += struct.pack("<B", (2 << 3) | 1) + struct.pack("<d", longitude)
    data += struct.pack("<B", (3 << 3) | 1) + struct.pack("<d", latitude)
    data += _encode_field_varint(4, timestamp_ms)
    return data


LOCATION_PAYLOAD = _build_location_payload(
    TEST_VIN, longitude=18.068581, latitude=59.329323, timestamp_ms=1772990058845
)


class TestBuildLocationRequest:
    def test_produces_field_1_string(self):
        result = _build_location_request(TEST_VIN)
        fields = _decode_message(result)
        assert 1 in fields
        assert fields[1][0] == TEST_VIN.encode("utf-8")
        assert 2 not in fields

    def test_roundtrip_vin(self):
        result = _build_location_request(TEST_VIN)
        expected = _encode_field_bytes(1, TEST_VIN.encode("utf-8"))
        assert result == expected


class TestParseLocationResponse:
    def test_parsed_response(self):
        result = _parse_location_response(LOCATION_PAYLOAD)
        assert result["latitude"] == pytest.approx(59.329323)
        assert result["longitude"] == pytest.approx(18.068581)
        assert result["timestamp_ms"] == 1772990058845

    def test_empty_response(self):
        result = _parse_location_response(b"")
        assert result["latitude"] is None
        assert result["longitude"] is None
        assert result["timestamp_ms"] is None

    def test_missing_coordinates(self):
        """Response with only VIN returns None values."""
        data = _encode_field_bytes(1, TEST_VIN.encode("utf-8"))
        result = _parse_location_response(data)
        assert result["latitude"] is None
        assert result["longitude"] is None
        assert result["timestamp_ms"] is None

    def test_top_level_decode(self):
        """Verify location fields are at top level (no envelope nesting)."""
        fields = _decode_message(LOCATION_PAYLOAD)
        assert fields[1][0] == TEST_VIN.encode("utf-8")
        # Fields 2 and 3 are fixed64 (doubles stored as uint64)
        assert 2 in fields
        assert 3 in fields
        assert 4 in fields


def _build_exterior_state(field_values: dict[int, int]) -> bytes:
    """Build an ExteriorState sub-message from {field_number: varint_value}."""
    data = b""
    for field_num in sorted(field_values):
        data += _encode_field_varint(field_num, field_values[field_num])
    return data


def _build_exterior_payload(vin: str, field_values: dict[int, int]) -> bytes:
    """Build a synthetic GetLatestExterior response with two-level envelope."""
    state_bytes = _build_exterior_state(field_values)
    # Outer envelope: field 2 = VIN, field 3 = ExteriorState sub-message
    data = _encode_field_bytes(2, vin.encode("utf-8"))
    data += _encode_field_bytes(3, state_bytes)
    return data


# All locked/closed, alarm idle
EXTERIOR_ALL_LOCKED = _build_exterior_payload(
    TEST_VIN,
    {
        2: 2,  # central_lock: LOCKED
        3: 2,  # front_left_door: CLOSED
        4: 2,  # front_right_door: CLOSED
        5: 2,  # rear_left_door: CLOSED
        6: 2,  # rear_right_door: CLOSED
        7: 2,  # front_left_window: CLOSED
        8: 2,  # front_right_window: CLOSED
        9: 2,  # rear_left_window: CLOSED
        10: 2,  # rear_right_window: CLOSED
        11: 2,  # hood: CLOSED
        12: 2,  # tailgate: CLOSED
        13: 2,  # tank_lid: CLOSED
        15: 1,  # alarm: IDLE
    },
)

# Mixed state: unlocked, some doors open/ajar, alarm triggered
EXTERIOR_MIXED = _build_exterior_payload(
    TEST_VIN,
    {
        2: 1,  # central_lock: UNLOCKED
        3: 1,  # front_left_door: OPEN
        4: 3,  # front_right_door: AJAR
        5: 2,  # rear_left_door: CLOSED
        6: 2,  # rear_right_door: CLOSED
        7: 1,  # front_left_window: OPEN
        8: 2,  # front_right_window: CLOSED
        9: 2,  # rear_left_window: CLOSED
        10: 2,  # rear_right_window: CLOSED
        11: 2,  # hood: CLOSED
        12: 1,  # tailgate: OPEN
        13: 2,  # tank_lid: CLOSED
        14: 0,  # sunroof: UNSPECIFIED
        15: 2,  # alarm: TRIGGERED
    },
)


class TestParseExteriorResponse:
    def test_all_locked_closed(self):
        result = _parse_exterior_response(EXTERIOR_ALL_LOCKED)
        assert result["central_lock"] == 2  # LOCKED
        assert result["front_left_door"] == 2  # CLOSED
        assert result["front_right_door"] == 2  # CLOSED
        assert result["rear_left_door"] == 2  # CLOSED
        assert result["rear_right_door"] == 2  # CLOSED
        assert result["front_left_window"] == 2  # CLOSED
        assert result["front_right_window"] == 2  # CLOSED
        assert result["rear_left_window"] == 2  # CLOSED
        assert result["rear_right_window"] == 2  # CLOSED
        assert result["hood"] == 2  # CLOSED
        assert result["tailgate"] == 2  # CLOSED
        assert result["tank_lid"] == 2  # CLOSED
        assert result["sunroof"] is None  # not in payload → UNSPECIFIED → None
        assert result["alarm"] == 1  # IDLE

    def test_mixed_open_ajar(self):
        result = _parse_exterior_response(EXTERIOR_MIXED)
        assert result["central_lock"] == 1  # UNLOCKED
        assert result["front_left_door"] == 1  # OPEN
        assert result["front_right_door"] == 3  # AJAR
        assert result["rear_left_door"] == 2  # CLOSED
        assert result["front_left_window"] == 1  # OPEN
        assert result["front_right_window"] == 2  # CLOSED
        assert result["tailgate"] == 1  # OPEN
        assert result["sunroof"] is None  # UNSPECIFIED(0) → None
        assert result["alarm"] == 2  # TRIGGERED

    def test_empty_response(self):
        result = _parse_exterior_response(b"")
        assert result["central_lock"] is None
        assert result["front_left_door"] is None
        assert result["alarm"] is None
        assert len(result) == 14  # all 14 fields present

    def test_missing_state_submessage(self):
        """Response with VIN but no field 3 returns all None."""
        data = _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        result = _parse_exterior_response(data)
        assert result["central_lock"] is None
        assert result["front_left_door"] is None
        assert result["alarm"] is None

    def test_two_level_envelope(self):
        """Verify outer envelope has field 2=VIN and field 3=ExteriorState."""
        outer = _decode_message(EXTERIOR_ALL_LOCKED)
        assert outer[2][0] == TEST_VIN.encode("utf-8")
        assert isinstance(outer[3][0], (bytes, bytearray))
        state = _get_submessage(outer, 3)
        assert state is not None
        assert 2 in state  # central_lock field


# ---------------------------------------------------------------------------
# Availability tests
# ---------------------------------------------------------------------------


def _build_availability_state(field_values: dict[int, int]) -> bytes:
    """Build an Availability state sub-message from {field_number: varint_value}."""
    data = b""
    for field_num in sorted(field_values):
        data += _encode_field_varint(field_num, field_values[field_num])
    return data


def _build_availability_payload(vin: str, field_values: dict[int, int]) -> bytes:
    """Build a synthetic GetLatestAvailability response with two-level envelope."""
    state_bytes = _build_availability_state(field_values)
    data = _encode_field_bytes(2, vin.encode("utf-8"))
    data += _encode_field_bytes(3, state_bytes)
    return data


AVAILABILITY_AVAILABLE = _build_availability_payload(
    TEST_VIN,
    {
        3: 1,  # availability_status: AVAILABLE
        5: 2,  # usage_mode: INACTIVE
    },
)

AVAILABILITY_UNAVAILABLE = _build_availability_payload(
    TEST_VIN,
    {
        3: 2,  # availability_status: UNAVAILABLE
        4: 2,  # unavailable_reason: POWER_SAVING_MODE
        5: 1,  # usage_mode: ABANDONED
    },
)


class TestParseAvailabilityResponse:
    def test_available_vehicle(self):
        result = _parse_availability_response(AVAILABILITY_AVAILABLE)
        assert result["availability_status"] == 1  # AVAILABLE
        assert result["unavailable_reason"] is None  # not set
        assert result["usage_mode"] == 2  # INACTIVE

    def test_unavailable_vehicle(self):
        result = _parse_availability_response(AVAILABILITY_UNAVAILABLE)
        assert result["availability_status"] == 2  # UNAVAILABLE
        assert result["unavailable_reason"] == 2  # POWER_SAVING_MODE
        assert result["usage_mode"] == 1  # ABANDONED

    def test_unspecified_values(self):
        """Value 0 for any field returns None."""
        payload = _build_availability_payload(
            TEST_VIN,
            {3: 0, 4: 0, 5: 0},
        )
        result = _parse_availability_response(payload)
        assert result["availability_status"] is None
        assert result["unavailable_reason"] is None
        assert result["usage_mode"] is None

    def test_empty_response(self):
        result = _parse_availability_response(b"")
        assert result["availability_status"] is None
        assert result["unavailable_reason"] is None
        assert result["usage_mode"] is None

    def test_missing_state_submessage(self):
        """Response with VIN but no field 3 returns all None."""
        data = _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        result = _parse_availability_response(data)
        assert result["availability_status"] is None
        assert result["unavailable_reason"] is None
        assert result["usage_mode"] is None

    def test_two_level_envelope(self):
        """Verify outer envelope has field 2=VIN and field 3=Availability state."""
        outer = _decode_message(AVAILABILITY_AVAILABLE)
        assert outer[2][0] == TEST_VIN.encode("utf-8")
        assert isinstance(outer[3][0], (bytes, bytearray))
        state = _get_submessage(outer, 3)
        assert state is not None
        assert 3 in state  # availability_status field


# ---------------------------------------------------------------------------
# Health tests
# ---------------------------------------------------------------------------


def _build_health_state(varint_fields: dict[int, int], float_fields: dict[int, float]) -> bytes:
    """Build a Health sub-message from varint and float (fixed32) fields."""
    data = b""
    for field_num in sorted(varint_fields):
        data += _encode_field_varint(field_num, varint_fields[field_num])
    for field_num in sorted(float_fields):
        data += _encode_field_fixed32(field_num, float_fields[field_num])
    return data


def _build_health_payload(
    vin: str, varint_fields: dict[int, int], float_fields: dict[int, float]
) -> bytes:
    """Build a synthetic GetHealth response with two-level envelope."""
    state_bytes = _build_health_state(varint_fields, float_fields)
    data = _encode_field_bytes(2, vin.encode("utf-8"))
    data += _encode_field_bytes(3, state_bytes)
    return data


# Full health response: service info, tyre warnings, fluid warnings, pressures
HEALTH_FULL = _build_health_payload(
    TEST_VIN,
    varint_fields={
        3: 180,  # days_to_service
        4: 12000,  # distance_to_service_km
        5: 1,  # service_warning: NO_WARNING
        6: 1,  # brake_fluid_level_warning: NO_WARNING
        7: 1,  # engine_coolant_level_warning: NO_WARNING
        8: 1,  # oil_level_warning: NO_WARNING
        9: 1,  # front_left_tyre_pressure_warning: NO_WARNING
        10: 3,  # front_right_tyre_pressure_warning: LOW
        11: 1,  # rear_left_tyre_pressure_warning: NO_WARNING
        12: 2,  # rear_right_tyre_pressure_warning: VERY_LOW
        13: 1,  # washer_fluid_level_warning: NO_WARNING
        14: 1,  # brake_light_left_warning: NO_WARNING
        15: 2,  # brake_light_center_warning: FAILURE
        38: 1,  # low_voltage_battery_warning: NO_WARNING
    },
    float_fields={
        39: 240.0,  # front_left_tyre_pressure_kpa
        40: 210.5,  # front_right_tyre_pressure_kpa
        41: 245.0,  # rear_left_tyre_pressure_kpa
        42: 190.3,  # rear_right_tyre_pressure_kpa
        43: 240.0,  # front_tyres_reference_pressure_kpa
        44: 250.0,  # rear_tyres_reference_pressure_kpa
    },
)

# Minimal: only tyre pressures, no warnings set
HEALTH_MINIMAL = _build_health_payload(
    TEST_VIN,
    varint_fields={},
    float_fields={
        39: 235.0,
        40: 235.0,
        41: 250.0,
        42: 250.0,
    },
)


class TestParseHealthResponse:
    def test_full_response(self):
        result = _parse_health_response(HEALTH_FULL)
        # Service info
        assert result["days_to_service"] == 180
        assert result["distance_to_service_km"] == 12000
        assert result["service_warning"] == 1  # NO_WARNING
        # Fluid warnings
        assert result["brake_fluid_level_warning"] == 1  # NO_WARNING
        assert result["engine_coolant_level_warning"] == 1  # NO_WARNING
        assert result["oil_level_warning"] == 1  # NO_WARNING
        assert result["washer_fluid_level_warning"] == 1  # NO_WARNING
        # Tyre pressure warnings
        assert result["front_left_tyre_pressure_warning"] == 1  # NO_WARNING
        assert result["front_right_tyre_pressure_warning"] == 3  # LOW
        assert result["rear_left_tyre_pressure_warning"] == 1  # NO_WARNING
        assert result["rear_right_tyre_pressure_warning"] == 2  # VERY_LOW
        # 12V battery
        assert result["low_voltage_battery_warning"] == 1  # NO_WARNING
        # Light warnings
        assert result["brake_light_left_warning"] == 1  # NO_WARNING
        assert result["brake_light_center_warning"] == 2  # FAILURE

    def test_tyre_pressure_floats(self):
        result = _parse_health_response(HEALTH_FULL)
        assert result["front_left_tyre_pressure_kpa"] == pytest.approx(240.0, abs=0.2)
        assert result["front_right_tyre_pressure_kpa"] == pytest.approx(210.5, abs=0.2)
        assert result["rear_left_tyre_pressure_kpa"] == pytest.approx(245.0, abs=0.2)
        assert result["rear_right_tyre_pressure_kpa"] == pytest.approx(190.3, abs=0.2)
        assert result["front_tyres_reference_pressure_kpa"] == pytest.approx(240.0, abs=0.2)
        assert result["rear_tyres_reference_pressure_kpa"] == pytest.approx(250.0, abs=0.2)

    def test_minimal_response_missing_warnings(self):
        """Response with only float fields returns None for varint warnings."""
        result = _parse_health_response(HEALTH_MINIMAL)
        assert result["front_left_tyre_pressure_kpa"] == pytest.approx(235.0, abs=0.2)
        assert result["days_to_service"] is None
        assert result["service_warning"] is None
        assert result["front_left_tyre_pressure_warning"] is None
        assert result["washer_fluid_level_warning"] is None
        assert result["low_voltage_battery_warning"] is None

    def test_empty_response(self):
        result = _parse_health_response(b"")
        assert result["front_left_tyre_pressure_kpa"] is None
        assert result["rear_right_tyre_pressure_kpa"] is None
        assert result["days_to_service"] is None
        assert result["service_warning"] is None
        assert result["front_left_tyre_pressure_warning"] is None
        assert result["washer_fluid_level_warning"] is None
        assert result["low_voltage_battery_warning"] is None
        assert result["brake_light_left_warning"] is None
        assert result["front_tyres_reference_pressure_kpa"] is None

    def test_missing_state_submessage(self):
        """Response with VIN but no field 3 returns all None."""
        data = _encode_field_bytes(2, TEST_VIN.encode("utf-8"))
        result = _parse_health_response(data)
        assert result["front_left_tyre_pressure_kpa"] is None
        assert result["days_to_service"] is None
        assert result["front_left_tyre_pressure_warning"] is None

    def test_two_level_envelope(self):
        """Verify outer envelope has field 2=VIN and field 3=Health state."""
        outer = _decode_message(HEALTH_FULL)
        assert outer[2][0] == TEST_VIN.encode("utf-8")
        assert isinstance(outer[3][0], (bytes, bytearray))
        state = _get_submessage(outer, 3)
        assert state is not None
        assert 3 in state  # days_to_service field

    def test_unspecified_enum_returns_none(self):
        """Enum value 0 (UNSPECIFIED) returns None."""
        payload = _build_health_payload(
            TEST_VIN,
            varint_fields={5: 0, 9: 0, 13: 0, 38: 0},
            float_fields={},
        )
        result = _parse_health_response(payload)
        assert result["service_warning"] is None
        assert result["front_left_tyre_pressure_warning"] is None
        assert result["washer_fluid_level_warning"] is None
        assert result["low_voltage_battery_warning"] is None

    def test_zero_days_to_service_is_not_none(self):
        """days_to_service=0 should return 0, not None."""
        payload = _build_health_payload(
            TEST_VIN,
            varint_fields={3: 0, 4: 0},
            float_fields={},
        )
        result = _parse_health_response(payload)
        assert result["days_to_service"] == 0
        assert result["distance_to_service_km"] == 0

    def test_pressure_rounding(self):
        """Verify tyre pressure values are rounded to 1 decimal."""
        payload = _build_health_payload(
            TEST_VIN,
            varint_fields={},
            float_fields={39: 240.123},
        )
        result = _parse_health_response(payload)
        # IEEE 754 single-precision may not store 240.123 exactly,
        # but the result should be rounded to 1 decimal place
        val = result["front_left_tyre_pressure_kpa"]
        assert val is not None
        assert val == round(val, 1)
