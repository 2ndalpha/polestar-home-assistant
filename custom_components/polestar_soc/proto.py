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


def _get_string(fields: dict[int, list], field_number: int, default: str = "") -> str:
    """Extract and decode a UTF-8 string from a length-delimited field."""
    vals = fields.get(field_number)
    if vals and isinstance(vals[0], (bytes, bytearray)):
        return vals[0].decode("utf-8", errors="replace")
    return default


def _get_float(fields: dict[int, list], field_number: int) -> float | None:
    """Extract a float (IEEE 754) from a fixed32 field.

    _decode_message stores wire type 5 as uint32.  This helper reinterprets
    the raw bits as a single-precision float.
    """
    vals = fields.get(field_number)
    if not vals:
        return None
    raw = vals[0]
    if isinstance(raw, int):
        return struct.unpack("<f", struct.pack("<I", raw))[0]
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


def _decode_packed_varints(data: bytes) -> list[int]:
    """Decode a packed repeated varint field into a list of integers."""
    values: list[int] = []
    pos = 0
    while pos < len(data):
        value, pos = _decode_varint(data, pos)
        values.append(value)
    return values


def _encode_packed_varints(field_number: int, values: list[int]) -> bytes:
    """Encode a list of integers as a packed repeated varint field."""
    if not values:
        return b""
    packed = b""
    for v in values:
        packed += _encode_varint(v)
    return _encode_field_bytes(field_number, packed)


# ---------------------------------------------------------------------------
# Shared response parsers
# ---------------------------------------------------------------------------


def _parse_invocation_response(data: bytes) -> dict:
    """Parse an InvocationResponse from a command response wrapper.

    Used by both PCCS and CEP InvocationService commands.
    The wrapper message (e.g. ClimatizationResponse, WindowControlResponse)
    has field 1 = InvocationResponse sub-message.

    InvocationResponse:
        field 1: id (string)
        field 2: vin (string)
        field 3: status (varint enum)
        field 4: message (string)
        field 5: timestamp (int64)
    """
    empty = {"id": "", "vin": "", "status": 0, "message": ""}
    if not data:
        return empty

    outer = _decode_message(data)
    inner = _get_submessage(outer, 1)
    if inner is None:
        return empty

    id_val = inner.get(1, [b""])[0]
    if isinstance(id_val, bytes):
        id_val = id_val.decode("utf-8", errors="replace")

    vin_val = inner.get(2, [b""])[0]
    if isinstance(vin_val, bytes):
        vin_val = vin_val.decode("utf-8", errors="replace")

    msg_val = inner.get(4, [b""])[0]
    if isinstance(msg_val, bytes):
        msg_val = msg_val.decode("utf-8", errors="replace")

    return {
        "id": id_val,
        "vin": vin_val,
        "status": _get_int(inner, 3, 0),
        "message": msg_val,
    }


# ---------------------------------------------------------------------------
# Raw serializer/deserializer for grpc channel methods
# ---------------------------------------------------------------------------


def _identity_serialize(data: bytes) -> bytes:
    return data


def _identity_deserialize(data: bytes) -> bytes:
    return data
