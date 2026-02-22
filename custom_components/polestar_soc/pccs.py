"""PCCS (Polestar Connected Car Services) gRPC client.

Communicates with the PCCS gRPC API at api.pccs-prod.plstr.io for
vehicle command-and-control operations (charge target, charge timer, etc.).

Since we don't have compiled .proto stubs, we use grpc generic channel methods
with manual protobuf wire-format encoding/decoding.
"""

from __future__ import annotations

import logging
import struct

import grpc

from .const import PCCS_API_HOST

_LOGGER = logging.getLogger(__name__)

# gRPC service method paths
_SVC_TARGET_SOC = "/chronos.services.v1.TargetSocService"
_SVC_CHARGE_TIMER = "/chronos.services.v2.GlobalChargeTimerService"

_METHOD_GET_TARGET_SOC = f"{_SVC_TARGET_SOC}/GetTargetSoc"
_METHOD_SET_TARGET_SOC = f"{_SVC_TARGET_SOC}/SetTargetSoc"
_METHOD_GET_CHARGE_TIMER = f"{_SVC_CHARGE_TIMER}/GetGlobalChargeTimerStream"
_METHOD_SET_CHARGE_TIMER = f"{_SVC_CHARGE_TIMER}/SetGlobalChargeTimer"


# ---------------------------------------------------------------------------
# Minimal protobuf wire-format helpers
# ---------------------------------------------------------------------------
# Wire types: 0=varint, 2=length-delimited


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


# ---------------------------------------------------------------------------
# Protobuf message builders
# ---------------------------------------------------------------------------
# Field numbers based on APK-decompiled message definitions.
#
# TargetSoc message:
#   1: targetSoc (int32)
#   2: enabledTargetSocValues (repeated int32)
#   3: chargeTargetLevelSettingType (enum)
#   4: chargingTimeEstimatedToTargetSocMinutes (int32)
#   5: pendingTargetSoc (int32)
#
# GlobalChargeTimer message:
#   1: startTime (TimeOfDay message)
#   2: endTime (TimeOfDay message)
#   3: departureTimeHours (int32)
#   4: departureTimeMinutes (int32)
#   5: isDepartureTimeActive (bool)
#   6: isLocationChargeTimerActive (bool)
#   7: pendingGlobalChargeTimer (message)
#
# TimeOfDay message:
#   1: hours (int32)
#   2: minutes (int32)


def _build_set_target_soc_request(target_soc: int) -> bytes:
    """Build SetTargetSoc request bytes."""
    return _encode_field_varint(1, target_soc)


def _build_time_of_day(hours: int, minutes: int) -> bytes:
    """Build a TimeOfDay sub-message."""
    msg = b""
    if hours:
        msg += _encode_field_varint(1, hours)
    if minutes:
        msg += _encode_field_varint(2, minutes)
    return msg


def _build_set_charge_timer_request(
    start_hour: int,
    start_min: int,
    end_hour: int,
    end_min: int,
) -> bytes:
    """Build SetGlobalChargeTimer request bytes."""
    msg = b""
    msg += _encode_field_bytes(1, _build_time_of_day(start_hour, start_min))
    msg += _encode_field_bytes(2, _build_time_of_day(end_hour, end_min))
    return msg


def _parse_target_soc_response(data: bytes) -> dict:
    """Parse GetTargetSoc / SetTargetSoc response."""
    if not data:
        return {"target_soc": None, "enabled_values": []}

    fields = _decode_message(data)

    # enabledTargetSocValues may be packed (length-delimited repeated) or unpacked
    enabled_values: list[int] = []
    raw = fields.get(2, [])
    for item in raw:
        if isinstance(item, (bytes, bytearray)):
            # Packed repeated varint
            pos = 0
            while pos < len(item):
                val, pos = _decode_varint(item, pos)
                enabled_values.append(val)
        else:
            enabled_values.append(item)

    return {
        "target_soc": _get_int(fields, 1, 0) or None,
        "enabled_values": enabled_values,
        "setting_type": _get_int(fields, 3, 0),
        "estimated_minutes": _get_int(fields, 4, 0),
        "pending_target_soc": _get_int(fields, 5, 0) or None,
    }


def _parse_charge_timer_response(data: bytes) -> dict:
    """Parse GetGlobalChargeTimerStream / SetGlobalChargeTimer response."""
    if not data:
        return {
            "start_hour": None,
            "start_min": None,
            "end_hour": None,
            "end_min": None,
            "departure_hour": None,
            "departure_min": None,
            "is_departure_active": False,
            "is_location_timer_active": False,
        }

    fields = _decode_message(data)

    start_time = _get_submessage(fields, 1)
    end_time = _get_submessage(fields, 2)

    return {
        "start_hour": _get_int(start_time, 1) if start_time else None,
        "start_min": _get_int(start_time, 2) if start_time else None,
        "end_hour": _get_int(end_time, 1) if end_time else None,
        "end_min": _get_int(end_time, 2) if end_time else None,
        "departure_hour": _get_int(fields, 3, 0) or None,
        "departure_min": _get_int(fields, 4, 0) or None,
        "is_departure_active": _get_bool(fields, 5),
        "is_location_timer_active": _get_bool(fields, 6),
    }


# ---------------------------------------------------------------------------
# Raw serializer/deserializer for grpc channel methods
# ---------------------------------------------------------------------------


def _identity_serialize(data: bytes) -> bytes:
    return data


def _identity_deserialize(data: bytes) -> bytes:
    return data


# ---------------------------------------------------------------------------
# PccsClient
# ---------------------------------------------------------------------------


class PccsClient:
    """Client for the PCCS gRPC API."""

    def __init__(self, access_token: str) -> None:
        """Initialize with an OAuth access token."""
        self._access_token = access_token
        self._channel: grpc.Channel | None = None

    @property
    def access_token(self) -> str:
        return self._access_token

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._access_token = value

    def _get_channel(self) -> grpc.Channel:
        """Get or create the gRPC channel."""
        if self._channel is None:
            credentials = grpc.ssl_channel_credentials()
            self._channel = grpc.secure_channel(f"{PCCS_API_HOST}:443", credentials)
        return self._channel

    def _metadata(self, vin: str) -> list[tuple[str, str]]:
        """Build gRPC call metadata."""
        return [
            ("authorization", f"Bearer {self._access_token}"),
            ("vin", vin),
        ]

    def close(self) -> None:
        """Close the gRPC channel."""
        if self._channel is not None:
            self._channel.close()
            self._channel = None

    # -- Target SOC ----------------------------------------------------------

    def get_target_soc(self, vin: str) -> dict:
        """Get the current charge target SOC for a vehicle."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_GET_TARGET_SOC,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        try:
            response = method(b"", metadata=self._metadata(vin), timeout=30)
            return _parse_target_soc_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS GetTargetSoc failed: %s", err)
            raise

    def set_target_soc(self, vin: str, percentage: int) -> dict:
        """Set the charge target SOC for a vehicle."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_SET_TARGET_SOC,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        request = _build_set_target_soc_request(percentage)
        try:
            response = method(request, metadata=self._metadata(vin), timeout=30)
            return _parse_target_soc_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS SetTargetSoc failed: %s", err)
            raise

    # -- Global Charge Timer -------------------------------------------------

    def get_global_charge_timer(self, vin: str) -> dict:
        """Get the global charge timer for a vehicle.

        GetGlobalChargeTimerStream is a server-streaming RPC.
        We take the first response from the stream.
        """
        channel = self._get_channel()
        method = channel.unary_stream(
            _METHOD_GET_CHARGE_TIMER,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        try:
            responses = method(b"", metadata=self._metadata(vin), timeout=30)
            for response in responses:
                return _parse_charge_timer_response(response)
            # Empty stream
            return _parse_charge_timer_response(b"")
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS GetGlobalChargeTimer failed: %s", err)
            raise

    def set_global_charge_timer(
        self,
        vin: str,
        start_hour: int,
        start_min: int,
        end_hour: int,
        end_min: int,
    ) -> dict:
        """Set the global charge timer for a vehicle."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_SET_CHARGE_TIMER,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        request = _build_set_charge_timer_request(start_hour, start_min, end_hour, end_min)
        try:
            response = method(request, metadata=self._metadata(vin), timeout=30)
            return _parse_charge_timer_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS SetGlobalChargeTimer failed: %s", err)
            raise
