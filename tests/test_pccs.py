"""Tests for PCCS protobuf wire-format helpers."""

import struct

import pytest

from custom_components.polestar_soc.pccs import (
    _METHOD_CLIMATIZATION_START,
    _METHOD_CLIMATIZATION_STOP,
    _METHOD_GET_CHARGE_TIMER,
    _METHOD_GET_TARGET_SOC,
    _METHOD_LOCK,
    _METHOD_SET_CHARGE_TIMER,
    _METHOD_SET_TARGET_SOC,
    _METHOD_UNLOCK,
    PccsClient,
    _build_climatization_start_request,
    _build_climatization_stop_request,
    _build_invocation_request,
    _build_lock_request,
    _build_set_charge_timer_request,
    _build_set_target_soc_request,
    _build_time_of_day,
    _build_unlock_request,
    _lock_error_context,
    _parse_charge_timer_response,
    _parse_set_charge_timer_response,
    _parse_target_soc_response,
)
from custom_components.polestar_soc.proto import _parse_invocation_response
from custom_components.polestar_soc.proto import (
    _decode_message,
    _decode_varint,
    _encode_field_bytes,
    _encode_field_fixed32,
    _encode_field_varint,
    _encode_varint,
    _get_bool,
    _get_int,
    _get_submessage,
)

# ---------------------------------------------------------------------------
# Service path constants — must include the pccs. package prefix
# ---------------------------------------------------------------------------


class TestServicePaths:
    def test_target_soc_get_path(self):
        assert _METHOD_GET_TARGET_SOC == "/pccs.chronos.services.v1.TargetSocService/GetTargetSoc"

    def test_target_soc_set_path(self):
        assert _METHOD_SET_TARGET_SOC == "/pccs.chronos.services.v1.TargetSocService/SetTargetSoc"

    def test_charge_timer_get_path(self):
        expected = "/pccs.chronos.services.v2.GlobalChargeTimerService/GetGlobalChargeTimerStream"
        assert expected == _METHOD_GET_CHARGE_TIMER

    def test_charge_timer_set_path(self):
        expected = "/pccs.chronos.services.v2.GlobalChargeTimerService/SetGlobalChargeTimer"
        assert expected == _METHOD_SET_CHARGE_TIMER

    def test_climatization_start_path(self):
        expected = "/pccs.invocation.v1.InvocationService/ClimatizationStart"
        assert expected == _METHOD_CLIMATIZATION_START

    def test_climatization_stop_path(self):
        expected = "/pccs.invocation.v1.InvocationService/ClimatizationStop"
        assert expected == _METHOD_CLIMATIZATION_STOP


# ---------------------------------------------------------------------------
# Varint encoding / decoding
# ---------------------------------------------------------------------------


class TestEncodeVarint:
    def test_zero(self):
        assert _encode_varint(0) == b"\x00"

    def test_single_byte(self):
        assert _encode_varint(1) == b"\x01"
        assert _encode_varint(127) == b"\x7f"

    def test_two_bytes(self):
        # 128 = 0x80 → 0x80 0x01
        assert _encode_varint(128) == b"\x80\x01"
        assert _encode_varint(300) == b"\xac\x02"

    def test_large_value(self):
        result = _encode_varint(100000)
        # Roundtrip check
        decoded, pos = _decode_varint(result, 0)
        assert decoded == 100000
        assert pos == len(result)


class TestDecodeVarint:
    def test_zero(self):
        val, pos = _decode_varint(b"\x00", 0)
        assert val == 0
        assert pos == 1

    def test_single_byte(self):
        val, pos = _decode_varint(b"\x01", 0)
        assert val == 1

    def test_multi_byte(self):
        val, pos = _decode_varint(b"\xac\x02", 0)
        assert val == 300

    def test_with_offset(self):
        data = b"\xff\xac\x02"
        val, pos = _decode_varint(data, 1)
        assert val == 300
        assert pos == 3

    def test_truncated_raises(self):
        with pytest.raises(ValueError, match="Truncated"):
            _decode_varint(b"\x80", 0)  # continuation bit set, no next byte

    def test_roundtrip_various_values(self):
        for value in [0, 1, 127, 128, 255, 256, 16383, 16384, 2**21 - 1, 2**21]:
            encoded = _encode_varint(value)
            decoded, pos = _decode_varint(encoded, 0)
            assert decoded == value, f"Roundtrip failed for {value}"
            assert pos == len(encoded)


# ---------------------------------------------------------------------------
# Field encoding
# ---------------------------------------------------------------------------


class TestEncodeFieldVarint:
    def test_field_1_value_80(self):
        result = _encode_field_varint(1, 80)
        decoded = _decode_message(result)
        assert decoded[1] == [80]

    def test_field_number_preserved(self):
        for fn in (1, 2, 5, 15):
            result = _encode_field_varint(fn, 42)
            decoded = _decode_message(result)
            assert fn in decoded
            assert decoded[fn] == [42]


class TestEncodeFieldBytes:
    def test_simple(self):
        payload = b"hello"
        result = _encode_field_bytes(1, payload)
        decoded = _decode_message(result)
        assert decoded[1] == [b"hello"]


class TestEncodeFieldFixed32:
    def test_roundtrip(self):
        result = _encode_field_fixed32(3, 22.0)
        decoded = _decode_message(result)
        assert 3 in decoded
        # _decode_message stores fixed32 as uint32; reinterpret as float
        raw = decoded[3][0]
        value = struct.unpack("<f", struct.pack("<I", raw))[0]
        assert value == pytest.approx(22.0)

    def test_field_number_preserved(self):
        for fn in (1, 3, 5):
            result = _encode_field_fixed32(fn, 18.5)
            decoded = _decode_message(result)
            assert fn in decoded

    def test_negative_temperature(self):
        result = _encode_field_fixed32(3, -5.0)
        decoded = _decode_message(result)
        raw = decoded[3][0]
        value = struct.unpack("<f", struct.pack("<I", raw))[0]
        assert value == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# Message decoding
# ---------------------------------------------------------------------------


class TestDecodeMessage:
    def test_empty(self):
        assert _decode_message(b"") == {}

    def test_single_varint_field(self):
        data = _encode_field_varint(1, 42)
        fields = _decode_message(data)
        assert fields == {1: [42]}

    def test_multiple_fields(self):
        data = _encode_field_varint(1, 10) + _encode_field_varint(2, 20)
        fields = _decode_message(data)
        assert fields == {1: [10], 2: [20]}

    def test_nested_message(self):
        inner = _encode_field_varint(1, 99)
        outer = _encode_field_bytes(3, inner)
        fields = _decode_message(outer)
        assert 3 in fields
        assert isinstance(fields[3][0], bytes)

    def test_unsupported_wire_type_raises(self):
        # Wire type 3 (start group) is unsupported
        bad_tag = (1 << 3) | 3  # field 1, wire type 3
        data = _encode_varint(bad_tag)
        with pytest.raises(ValueError, match="Unsupported wire type"):
            _decode_message(data)


# ---------------------------------------------------------------------------
# Helper extractors
# ---------------------------------------------------------------------------


class TestGetInt:
    def test_existing_field(self):
        fields = {1: [42], 2: [100]}
        assert _get_int(fields, 1) == 42

    def test_missing_field_default(self):
        assert _get_int({}, 1) == 0
        assert _get_int({}, 1, 99) == 99


class TestGetBool:
    def test_true(self):
        assert _get_bool({1: [1]}, 1) is True

    def test_false_zero(self):
        assert _get_bool({1: [0]}, 1) is False

    def test_false_missing(self):
        assert _get_bool({}, 1) is False


class TestGetSubmessage:
    def test_valid(self):
        inner = _encode_field_varint(1, 7)
        fields = {5: [inner]}
        sub = _get_submessage(fields, 5)
        assert sub == {1: [7]}

    def test_missing(self):
        assert _get_submessage({}, 5) is None

    def test_non_bytes(self):
        # If the field is a varint rather than bytes, should return None
        assert _get_submessage({5: [42]}, 5) is None


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


class TestBuildSetTargetSocRequest:
    def test_roundtrip(self):
        data = _build_set_target_soc_request("TESTVIN123", 80)
        fields = _decode_message(data)
        # Field 1 is the ChronosRequest sub-message
        assert 1 in fields
        chronos = _decode_message(fields[1][0])
        assert chronos[2] == [b"TESTVIN123"]
        # Field 2 is the target SOC value
        assert fields[2] == [80]
        # Field 3 is the setting type (default CUSTOM=3)
        assert fields[3] == [3]

    def test_custom_setting_type(self):
        data = _build_set_target_soc_request("TESTVIN123", 80, setting_type=1)
        fields = _decode_message(data)
        assert fields[2] == [80]
        assert fields[3] == [1]


class TestBuildTimeOfDay:
    def test_nonzero(self):
        data = _build_time_of_day(14, 30)
        fields = _decode_message(data)
        assert fields[1] == [14]
        assert fields[2] == [30]

    def test_zero_hour(self):
        # Hour 0 is omitted (if hours == 0, the field is skipped)
        data = _build_time_of_day(0, 45)
        fields = _decode_message(data)
        assert 1 not in fields  # hours field omitted
        assert fields[2] == [45]

    def test_zero_minute(self):
        data = _build_time_of_day(8, 0)
        fields = _decode_message(data)
        assert fields[1] == [8]
        assert 2 not in fields  # minutes field omitted


class TestBuildSetChargeTimerRequest:
    def test_nested_structure(self):
        data = _build_set_charge_timer_request("TESTVIN123", 22, 0, 6, 30)
        fields = _decode_message(data)
        # Field 1 is ChronosRequest, field 2 is GlobalChargeTimer sub-message
        assert 1 in fields
        assert 2 in fields
        # Times should NOT be at top level (old bug: fields 2 and 3 were times)
        assert 3 not in fields

        # Decode the GlobalChargeTimer sub-message
        timer = _get_submessage(fields, 2)
        assert timer is not None
        # Field 1 = start DailyTime, field 2 = stop DailyTime, field 3 = activated
        start = _get_submessage(timer, 1)
        stop = _get_submessage(timer, 2)
        assert start is not None
        assert stop is not None
        assert _get_int(start, 1) == 22
        assert _get_int(stop, 1) == 6
        assert _get_int(stop, 2) == 30
        # Default activated=True
        assert _get_bool(timer, 3) is True

    def test_activated_false(self):
        data = _build_set_charge_timer_request("TESTVIN123", 22, 0, 6, 30, activated=False)
        fields = _decode_message(data)
        timer = _get_submessage(fields, 2)
        assert timer is not None
        # activated=False → field 3 omitted (proto3 default)
        assert _get_bool(timer, 3) is False

    def test_midnight_times(self):
        """Verify 00:00 start/stop times work (zero-value edge case)."""
        data = _build_set_charge_timer_request("TESTVIN123", 0, 0, 0, 0)
        fields = _decode_message(data)
        timer = _get_submessage(fields, 2)
        assert timer is not None
        # Start and stop DailyTime messages should still be present (empty = 00:00)
        assert 1 in timer
        assert 2 in timer


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


class TestParseTargetSocResponse:
    def test_empty_data(self):
        result = _parse_target_soc_response(b"")
        assert result["target_soc"] is None
        assert result["setting_type"] == 0

    def test_basic_response(self):
        # Build a response with TargetSoc sub-message in field 3
        # TargetSoc: field 1 = 80 (batteryChargeTargetLevel), field 2 = 3 (CUSTOM)
        inner = _encode_field_varint(1, 80) + _encode_field_varint(2, 3)
        data = _encode_field_bytes(3, inner)
        result = _parse_target_soc_response(data)
        assert result["target_soc"] == 80
        assert result["setting_type"] == 3

    def test_with_pending(self):
        # Build response with both current and pending target SOC
        inner = _encode_field_varint(1, 80) + _encode_field_varint(2, 3)
        pending = _encode_field_varint(1, 90)
        data = _encode_field_bytes(3, inner) + _encode_field_bytes(4, pending)
        result = _parse_target_soc_response(data)
        assert result["target_soc"] == 80
        assert result["pending_target_soc"] == 90


class TestParseChargeTimerResponse:
    def test_empty_data(self):
        result = _parse_charge_timer_response(b"")
        assert result["start_hour"] is None
        assert result["end_hour"] is None
        assert result["is_departure_active"] is False

    def test_no_timer_in_envelope(self):
        # Envelope with only a timestamp in field 3 but no timer in field 1
        data = _encode_field_varint(3, 1773247754487)
        result = _parse_charge_timer_response(data)
        assert result["start_hour"] is None
        assert result["end_hour"] is None

    def test_with_times(self):
        # Build GlobalChargeTimer sub-message: field 1=start, field 2=end
        start = _build_time_of_day(22, 30)
        end = _build_time_of_day(6, 0)
        timer = _encode_field_bytes(1, start) + _encode_field_bytes(2, end)
        # Wrap in response envelope: field 1 = globalChargeTimer
        data = _encode_field_bytes(1, timer)
        result = _parse_charge_timer_response(data)
        assert result["start_hour"] == 22
        assert result["start_min"] == 30
        assert result["end_hour"] == 6
        assert result["end_min"] == 0
        assert result["is_departure_active"] is False

    def test_pending_timer_preferred_over_baseline(self):
        """Field 2 (pending) should be preferred over field 1 (baseline)."""
        baseline_start = _build_time_of_day(22, 0)
        baseline_end = _build_time_of_day(6, 0)
        baseline = _encode_field_bytes(1, baseline_start) + _encode_field_bytes(2, baseline_end)

        pending_start = _build_time_of_day(23, 30)
        pending_end = _build_time_of_day(7, 15)
        pending = (
            _encode_field_bytes(1, pending_start)
            + _encode_field_bytes(2, pending_end)
            + _encode_field_varint(3, 1)  # activated=True
        )

        data = _encode_field_bytes(1, baseline) + _encode_field_bytes(2, pending)
        result = _parse_charge_timer_response(data)
        assert result["start_hour"] == 23
        assert result["start_min"] == 30
        assert result["end_hour"] == 7
        assert result["end_min"] == 15
        assert result["is_departure_active"] is True

    def test_with_timezone_in_time_of_day(self):
        """TimeOfDay may include a timezone sub-message in field 3."""
        # Build TimeOfDay with extra field 3 (timezone offset sub-message)
        tz_submsg = _encode_field_varint(1, 120)  # UTC+2 offset in minutes
        start = _build_time_of_day(23, 15) + _encode_field_bytes(3, tz_submsg)
        end = _build_time_of_day(7, 0) + _encode_field_bytes(3, tz_submsg)
        timer = _encode_field_bytes(1, start) + _encode_field_bytes(2, end)
        data = _encode_field_bytes(1, timer)
        result = _parse_charge_timer_response(data)
        assert result["start_hour"] == 23
        assert result["start_min"] == 15
        assert result["end_hour"] == 7
        assert result["end_min"] == 0
        assert result["is_departure_active"] is False


class TestParseSetChargeTimerResponse:
    def test_empty_data(self):
        result = _parse_set_charge_timer_response(b"")
        assert result["id"] == ""
        assert result["status"] == 0
        assert result["message"] == ""
        assert result["has_not_changed"] is False

    def test_success(self):
        data = (
            _encode_field_bytes(1, b"test-uuid") + _encode_field_varint(2, 1)  # SUCCESS
        )
        result = _parse_set_charge_timer_response(data)
        assert result["id"] == "test-uuid"
        assert result["status"] == 1
        assert result["message"] == ""
        assert result["has_not_changed"] is False

    def test_validation_error(self):
        data = (
            _encode_field_bytes(1, b"test-uuid")
            + _encode_field_varint(2, 2)  # VALIDATION_ERROR
            + _encode_field_bytes(3, b"Invalid time range")
        )
        result = _parse_set_charge_timer_response(data)
        assert result["status"] == 2
        assert result["message"] == "Invalid time range"

    def test_has_not_changed(self):
        data = (
            _encode_field_bytes(1, b"test-uuid")
            + _encode_field_varint(2, 1)  # SUCCESS
            + _encode_field_varint(4, 1)  # has_not_changed=True
        )
        result = _parse_set_charge_timer_response(data)
        assert result["status"] == 1
        assert result["has_not_changed"] is True


# ---------------------------------------------------------------------------
# PccsClient write token
# ---------------------------------------------------------------------------


class TestPccsClientWriteToken:
    def test_default_write_token_is_none(self):
        client = PccsClient("read-token")
        assert client.write_access_token is None

    def test_write_token_set_via_constructor(self):
        client = PccsClient("read-token", write_access_token="write-token")
        assert client.write_access_token == "write-token"

    def test_write_token_setter(self):
        client = PccsClient("read-token")
        client.write_access_token = "new-write-token"
        assert client.write_access_token == "new-write-token"

    def test_read_metadata_uses_read_token(self):
        client = PccsClient("read-token", write_access_token="write-token")
        meta = client._metadata("TESTVIN")
        assert ("authorization", "Bearer read-token") in meta
        assert ("vin", "TESTVIN") in meta

    def test_write_metadata_uses_write_token(self):
        client = PccsClient("read-token", write_access_token="write-token")
        meta = client._write_metadata("TESTVIN")
        assert ("authorization", "Bearer write-token") in meta
        assert ("vin", "TESTVIN") in meta

    def test_write_metadata_falls_back_to_read_token(self):
        client = PccsClient("read-token")
        meta = client._write_metadata("TESTVIN")
        assert ("authorization", "Bearer read-token") in meta

    def test_write_metadata_falls_back_when_empty_string(self):
        client = PccsClient("read-token", write_access_token="")
        meta = client._write_metadata("TESTVIN")
        assert ("authorization", "Bearer read-token") in meta


# ---------------------------------------------------------------------------
# Invocation message builders
# ---------------------------------------------------------------------------


class TestBuildInvocationRequest:
    def test_field_layout(self):
        data = _build_invocation_request("TESTVIN123")
        fields = _decode_message(data)
        # Field 1: UUID string
        assert 1 in fields
        uuid_val = fields[1][0]
        assert isinstance(uuid_val, bytes)
        assert len(uuid_val.decode("utf-8")) == 36  # UUID format
        # Field 2: VIN string
        assert fields[2] == [b"TESTVIN123"]
        # Field 3: expiration timestamp (varint, should be > 0)
        assert 3 in fields
        assert fields[3][0] > 0


class TestBuildClimatizationStartRequest:
    def test_roundtrip(self):
        data = _build_climatization_start_request("TESTVIN123", 22.0)
        fields = _decode_message(data)
        # Field 1: InvocationRequest sub-message
        assert 1 in fields
        invocation = _decode_message(fields[1][0])
        assert invocation[2] == [b"TESTVIN123"]
        # Field 2: start = true (varint 1)
        assert fields[2] == [1]
        # Field 3: temperature as fixed32
        assert 3 in fields
        raw = fields[3][0]
        temp = struct.unpack("<f", struct.pack("<I", raw))[0]
        assert temp == pytest.approx(22.0)

    def test_default_temperature(self):
        data = _build_climatization_start_request("TESTVIN123")
        fields = _decode_message(data)
        raw = fields[3][0]
        temp = struct.unpack("<f", struct.pack("<I", raw))[0]
        assert temp == pytest.approx(22.0)

    def test_custom_temperature(self):
        data = _build_climatization_start_request("TESTVIN123", 25.5)
        fields = _decode_message(data)
        raw = fields[3][0]
        temp = struct.unpack("<f", struct.pack("<I", raw))[0]
        assert temp == pytest.approx(25.5)


class TestBuildClimatizationStopRequest:
    def test_roundtrip(self):
        data = _build_climatization_stop_request("TESTVIN123")
        fields = _decode_message(data)
        # Field 1: InvocationRequest sub-message (only field)
        assert 1 in fields
        invocation = _decode_message(fields[1][0])
        assert invocation[2] == [b"TESTVIN123"]
        # No other fields
        assert 2 not in fields
        assert 3 not in fields


# ---------------------------------------------------------------------------
# Invocation response parser
# ---------------------------------------------------------------------------


class TestParseInvocationResponse:
    def test_empty_data(self):
        result = _parse_invocation_response(b"")
        assert result["id"] == ""
        assert result["vin"] == ""
        assert result["status"] == 0
        assert result["message"] == ""

    def test_success_status(self):
        # Build InvocationResponse: id=1, vin=2, status=3, message=4
        inner = (
            _encode_field_bytes(1, b"test-uuid")
            + _encode_field_bytes(2, b"TESTVIN123")
            + _encode_field_varint(3, 6)  # SUCCESS
        )
        # Wrap in ClimatizationResponse: field 1 = InvocationResponse
        data = _encode_field_bytes(1, inner)
        result = _parse_invocation_response(data)
        assert result["id"] == "test-uuid"
        assert result["vin"] == "TESTVIN123"
        assert result["status"] == 6
        assert result["message"] == ""

    def test_error_status_with_message(self):
        inner = (
            _encode_field_bytes(1, b"test-uuid")
            + _encode_field_bytes(2, b"TESTVIN123")
            + _encode_field_varint(3, 2)  # CAR_OFFLINE
            + _encode_field_bytes(4, b"Vehicle not reachable")
        )
        data = _encode_field_bytes(1, inner)
        result = _parse_invocation_response(data)
        assert result["status"] == 2
        assert result["message"] == "Vehicle not reachable"

    def test_sent_status(self):
        inner = (
            _encode_field_bytes(1, b"test-uuid")
            + _encode_field_bytes(2, b"TESTVIN123")
            + _encode_field_varint(3, 1)  # SENT
        )
        data = _encode_field_bytes(1, inner)
        result = _parse_invocation_response(data)
        assert result["status"] == 1

    def test_no_inner_message(self):
        # Outer message with no field 1
        data = _encode_field_varint(2, 42)
        result = _parse_invocation_response(data)
        assert result["id"] == ""
        assert result["status"] == 0


# ---------------------------------------------------------------------------
# Lock/Unlock service paths
# ---------------------------------------------------------------------------


class TestLockServicePaths:
    def test_lock_path(self):
        assert _METHOD_LOCK == "/pccs.invocation.v1.InvocationService/Lock"

    def test_unlock_path(self):
        assert _METHOD_UNLOCK == "/pccs.invocation.v1.InvocationService/Unlock"


# ---------------------------------------------------------------------------
# Lock/Unlock message builders
# ---------------------------------------------------------------------------


class TestBuildLockRequest:
    def test_default_lock_type(self):
        data = _build_lock_request("TESTVIN123")
        fields = _decode_message(data)
        # Field 1: InvocationRequest sub-message
        assert 1 in fields
        invocation = _decode_message(fields[1][0])
        assert invocation[2] == [b"TESTVIN123"]
        # Field 2: lockType omitted for default (0 = LOCK)
        assert 2 not in fields

    def test_reduced_guard_lock_type(self):
        data = _build_lock_request("TESTVIN123", lock_type=1)
        fields = _decode_message(data)
        assert 1 in fields
        # Field 2: lockType = 1 (LOCK_REDUCED_GUARD)
        assert fields[2] == [1]

    def test_invocation_request_has_uuid_and_expiry(self):
        data = _build_lock_request("TESTVIN123")
        fields = _decode_message(data)
        invocation = _decode_message(fields[1][0])
        # Field 1: UUID
        uuid_val = invocation[1][0]
        assert isinstance(uuid_val, bytes)
        assert len(uuid_val.decode("utf-8")) == 36
        # Field 3: expiration timestamp
        assert invocation[3][0] > 0


class TestBuildUnlockRequest:
    def test_roundtrip(self):
        data = _build_unlock_request("TESTVIN123")
        fields = _decode_message(data)
        # Field 1: InvocationRequest sub-message (only field)
        assert 1 in fields
        invocation = _decode_message(fields[1][0])
        assert invocation[2] == [b"TESTVIN123"]
        # No other fields
        assert 2 not in fields
        assert 3 not in fields

    def test_structurally_identical_to_climatization_stop(self):
        """UnlockRequest has the same wire structure as ClimatizationStopRequest."""
        lock_data = _build_unlock_request("SAMEVIN")
        stop_data = _build_climatization_stop_request("SAMEVIN")
        # Both have only field 1, but UUIDs differ — check field layout
        lock_fields = _decode_message(lock_data)
        stop_fields = _decode_message(stop_data)
        assert set(lock_fields.keys()) == set(stop_fields.keys()) == {1}


# ---------------------------------------------------------------------------
# Lock error context
# ---------------------------------------------------------------------------


class TestLockErrorContext:
    def test_empty_data(self):
        assert _lock_error_context(b"") == ""

    def test_no_lock_error(self):
        # Response with only InvocationResponse in field 1, no field 2
        inner = _encode_field_varint(3, 6)  # status = SUCCESS
        data = _encode_field_bytes(1, inner)
        assert _lock_error_context(data) == ""

    def test_door_open_error(self):
        # LockResponse with lockError=1 (LOCK_ERROR_DOOR_OPEN) in field 2
        inner = _encode_field_varint(3, 11)  # status = INVOCATION_SPECIFIC_ERROR
        data = _encode_field_bytes(1, inner) + _encode_field_varint(2, 1)
        assert _lock_error_context(data) == "a door is open"

    def test_unspecified_error(self):
        # lockError=0 (LOCK_ERROR_UNSPECIFIED) should return empty
        inner = _encode_field_varint(3, 6)
        data = _encode_field_bytes(1, inner) + _encode_field_varint(2, 0)
        assert _lock_error_context(data) == ""

    def test_unknown_error_code(self):
        # Unknown lockError value should return generic message
        inner = _encode_field_varint(3, 11)
        data = _encode_field_bytes(1, inner) + _encode_field_varint(2, 99)
        assert _lock_error_context(data) == "lock error (code 99)"
