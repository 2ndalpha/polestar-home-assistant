"""Minimal protobuf wire-format helpers.

Shared encoding/decoding utilities for gRPC clients (pccs.py and cep.py)
using manual protobuf wire-format encoding instead of compiled .proto stubs.

Wire types: 0=varint, 1=fixed64, 2=length-delimited, 5=fixed32
"""

from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    pieces = []
    while value > 0x7F:
        pieces.append((value & 0x7F) | 0x80)
        value >>= 7
    pieces.append(value & 0x7F)
    return bytes(pieces)


def _encode_field_varint(field_number: int, value: int) -> bytes:
    """Encode a varint field (tag + value)."""
    tag = (field_number << 3) | 0  # wire type 0
    return _encode_varint(tag) + _encode_varint(value)


def _encode_field_bytes(field_number: int, data: bytes) -> bytes:
    """Encode a length-delimited field (tag + length + data)."""
    tag = (field_number << 3) | 2  # wire type 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def _encode_field_fixed32(field_number: int, value: float) -> bytes:
    """Encode a float field as fixed32 (tag + 4 bytes, IEEE 754 single-precision)."""
    tag = (field_number << 3) | 5  # wire type 5
    return _encode_varint(tag) + struct.pack("<f", value)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint starting at pos, return (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
    raise ValueError("Truncated varint")


def _decode_message(data: bytes) -> dict[int, list]:
    """Decode a protobuf message into {field_number: [values]}.

    Returns raw values: ints for varint fields, bytes for length-delimited.
    Fixed32/64 fields are also handled.
    """
    fields: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            value, pos = _decode_varint(data, pos)
        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
        elif wire_type == 5:  # fixed32
            value = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        elif wire_type == 1:  # fixed64
            value = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        else:
            raise ValueError(f"Unsupported wire type {wire_type}")

        fields.setdefault(field_number, []).append(value)

    return fields


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------


def _get_int(fields: dict[int, list], field_number: int, default: int = 0) -> int:
    """Extract an integer value from decoded fields."""
    vals = fields.get(field_number)
    if vals:
        return vals[0]
    return default


def _get_bool(fields: dict[int, list], field_number: int) -> bool:
    """Extract a boolean value from decoded fields."""
    return bool(_get_int(fields, field_number, 0))


def _get_submessage(fields: dict[int, list], field_number: int) -> dict[int, list] | None:
    """Extract and decode a sub-message from decoded fields."""
    vals = fields.get(field_number)
    if vals and isinstance(vals[0], (bytes, bytearray)):
        return _decode_message(vals[0])
    return None


def _get_double(fields: dict[int, list], field_number: int) -> float | None:
    """Extract a double (IEEE 754) from a fixed64 field.

    _decode_message stores wire type 1 as uint64.  This helper reinterprets
    the raw bits as a double-precision float.
    """
    vals = fields.get(field_number)
    if not vals:
        return None
    raw = vals[0]
    if isinstance(raw, int):
        return struct.unpack("<d", struct.pack("<Q", raw))[0]
    return None


# ---------------------------------------------------------------------------
# Raw serializer/deserializer for grpc channel methods
# ---------------------------------------------------------------------------


def _identity_serialize(data: bytes) -> bytes:
    return data


def _identity_deserialize(data: bytes) -> bytes:
    return data
