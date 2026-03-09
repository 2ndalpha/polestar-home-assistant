"""Tests for PCCS protobuf wire-format helpers."""

import pytest

from custom_components.polestar_soc.pccs import (
    _build_set_charge_timer_request,
    _build_set_target_soc_request,
    _build_time_of_day,
    _parse_charge_timer_response,
    _parse_target_soc_response,
)
from custom_components.polestar_soc.proto import (
    _decode_message,
    _decode_varint,
    _encode_field_bytes,
    _encode_field_varint,
    _encode_varint,
    _get_bool,
    _get_int,
    _get_submessage,
)

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
        data = _build_set_target_soc_request(80)
        fields = _decode_message(data)
        assert fields[1] == [80]


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
    def test_has_start_and_end(self):
        data = _build_set_charge_timer_request(22, 0, 6, 30)
        fields = _decode_message(data)
        # Fields 1 and 2 should be length-delimited sub-messages
        assert 1 in fields
        assert 2 in fields


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


class TestParseTargetSocResponse:
    def test_empty_data(self):
        result = _parse_target_soc_response(b"")
        assert result["target_soc"] is None
        assert result["enabled_values"] == []

    def test_basic_response(self):
        # Encode: field 1 = 80 (target_soc)
        data = _encode_field_varint(1, 80)
        result = _parse_target_soc_response(data)
        assert result["target_soc"] == 80

    def test_with_packed_enabled_values(self):
        # Build a response with target_soc=80 and packed enabled_values
        target = _encode_field_varint(1, 80)
        # Pack values 50, 60, 70, 80, 90, 100 as length-delimited repeated varint
        packed = b""
        for v in [50, 60, 70, 80, 90, 100]:
            packed += _encode_varint(v)
        enabled = _encode_field_bytes(2, packed)
        data = target + enabled
        result = _parse_target_soc_response(data)
        assert result["target_soc"] == 80
        assert result["enabled_values"] == [50, 60, 70, 80, 90, 100]


class TestParseChargeTimerResponse:
    def test_empty_data(self):
        result = _parse_charge_timer_response(b"")
        assert result["start_hour"] is None
        assert result["end_hour"] is None
        assert result["is_departure_active"] is False

    def test_with_times(self):
        start = _build_time_of_day(22, 30)
        end = _build_time_of_day(6, 0)
        data = _encode_field_bytes(1, start) + _encode_field_bytes(2, end)
        result = _parse_charge_timer_response(data)
        assert result["start_hour"] == 22
        assert result["start_min"] == 30
        assert result["end_hour"] == 6
        assert result["end_min"] == 0
