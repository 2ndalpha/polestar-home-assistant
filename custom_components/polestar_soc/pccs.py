"""PCCS (Polestar Connected Car Services) gRPC client.

Communicates with the PCCS gRPC API at api.pccs-prod.plstr.io for
vehicle command-and-control operations (charge target, charge timer, etc.).

Since we don't have compiled .proto stubs, we use grpc generic channel methods
with manual protobuf wire-format encoding/decoding.
"""

from __future__ import annotations

import datetime
import logging
import uuid

import grpc

from .const import PCCS_API_HOST
from .proto import (
    _decode_message,
    _decode_varint,
    _encode_field_bytes,
    _encode_field_varint,
    _get_bool,
    _get_int,
    _get_submessage,
    _identity_deserialize,
    _identity_serialize,
)

_LOGGER = logging.getLogger(__name__)

# gRPC service method paths
_SVC_TARGET_SOC = "/pccs.chronos.services.v1.TargetSocService"
_SVC_CHARGE_TIMER = "/pccs.chronos.services.v2.GlobalChargeTimerService"

_METHOD_GET_TARGET_SOC = f"{_SVC_TARGET_SOC}/GetTargetSoc"
_METHOD_SET_TARGET_SOC = f"{_SVC_TARGET_SOC}/SetTargetSoc"
_METHOD_GET_CHARGE_TIMER = f"{_SVC_CHARGE_TIMER}/GetGlobalChargeTimerStream"
_METHOD_SET_CHARGE_TIMER = f"{_SVC_CHARGE_TIMER}/SetGlobalChargeTimer"


# ---------------------------------------------------------------------------
# Protobuf message builders
# ---------------------------------------------------------------------------
# Protobuf field numbers for each message type.
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


def _build_chronos_request(vin: str) -> bytes:
    """Build a ChronosRequest sub-message.

    ChronosRequest (pccs.chronos.messages.common.v1):
        field 1: id (string)       — random UUID
        field 2: vin (string)      — vehicle VIN
        field 3: source (string)   — "RCS" (Remote Car Service)
        field 4: timeZone (message) — offset in minutes
    """
    msg = b""
    msg += _encode_field_bytes(1, str(uuid.uuid4()).encode("utf-8"))
    msg += _encode_field_bytes(2, vin.encode("utf-8"))
    msg += _encode_field_bytes(3, b"RCS")
    # TimeZone message: field 1 = offsetMinutes (signed varint)
    utc_offset = datetime.datetime.now(datetime.timezone.utc).astimezone().utcoffset()
    offset_minutes = int(utc_offset.total_seconds()) // 60
    # Proto3 int32: negative values need two's-complement encoding as uint64
    encoded_offset = offset_minutes if offset_minutes >= 0 else offset_minutes + (1 << 64)
    tz_msg = _encode_field_varint(1, encoded_offset)
    msg += _encode_field_bytes(4, tz_msg)
    return msg


def _build_get_request(vin: str) -> bytes:
    """Build a Get*Request wrapping a ChronosRequest in field 1."""
    return _encode_field_bytes(1, _build_chronos_request(vin))


def _build_set_target_soc_request(vin: str, target_soc: int) -> bytes:
    """Build SetTargetSoc request bytes."""
    msg = _encode_field_bytes(1, _build_chronos_request(vin))
    msg += _encode_field_varint(2, target_soc)
    return msg


def _build_time_of_day(hours: int, minutes: int) -> bytes:
    """Build a TimeOfDay sub-message."""
    msg = b""
    if hours:
        msg += _encode_field_varint(1, hours)
    if minutes:
        msg += _encode_field_varint(2, minutes)
    return msg


def _build_set_charge_timer_request(
    vin: str,
    start_hour: int,
    start_min: int,
    end_hour: int,
    end_min: int,
) -> bytes:
    """Build SetGlobalChargeTimer request bytes."""
    msg = _encode_field_bytes(1, _build_chronos_request(vin))
    msg += _encode_field_bytes(2, _build_time_of_day(start_hour, start_min))
    msg += _encode_field_bytes(3, _build_time_of_day(end_hour, end_min))
    return msg


def _parse_target_soc_response(data: bytes) -> dict:
    """Parse GetTargetSocResponse / SetTargetSocResponse.

    Response structure (GetTargetSocResponse):
        field 1: id (string)           — echoed request ID
        field 2: vin (string)          — echoed VIN
        field 3: targetSoc (TargetSoc) — current target SOC data
        field 4: pendingTargetSoc (TargetSoc) — pending change
        field 5: updatedAt (varint)    — server timestamp

    TargetSoc sub-message:
        field 1: batteryChargeTargetLevel (int32)
        field 2: chargeTargetLevelSettingType (enum: 1=DAILY, 2=LONG_TRIP, 3=CUSTOM)
        field 3: updatedAt (varint)
        field 4: source (string)
        field 5: id (string)
    """
    if not data:
        return {"target_soc": None, "setting_type": 0}

    fields = _decode_message(data)

    # Extract TargetSoc sub-message from field 3
    target_soc_msg = _get_submessage(fields, 3)
    pending_msg = _get_submessage(fields, 4)

    target_soc = _get_int(target_soc_msg, 1, 0) if target_soc_msg else 0
    setting_type = _get_int(target_soc_msg, 2, 0) if target_soc_msg else 0
    pending_soc = _get_int(pending_msg, 1, 0) if pending_msg else 0

    return {
        "target_soc": target_soc or None,
        "setting_type": setting_type,
        "pending_target_soc": pending_soc or None,
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
        """Get the current charge target SOC for a vehicle.

        GetTargetSoc is a server-streaming RPC; we take the first response.
        """
        channel = self._get_channel()
        method = channel.unary_stream(
            _METHOD_GET_TARGET_SOC,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        request = _build_get_request(vin)
        try:
            responses = method(request, metadata=self._metadata(vin), timeout=30)
            for response in responses:
                return _parse_target_soc_response(response)
            return _parse_target_soc_response(b"")
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS GetTargetSoc failed: %s", err)
            raise

    def set_target_soc(self, vin: str, percentage: int) -> dict:
        """Set the charge target SOC for a vehicle.

        SetTargetSoc is a server-streaming RPC; we take the first response.
        """
        channel = self._get_channel()
        method = channel.unary_stream(
            _METHOD_SET_TARGET_SOC,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        request = _build_set_target_soc_request(vin, percentage)
        try:
            responses = method(request, metadata=self._metadata(vin), timeout=30)
            for response in responses:
                return _parse_target_soc_response(response)
            return _parse_target_soc_response(b"")
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
        request = _build_get_request(vin)
        try:
            responses = method(request, metadata=self._metadata(vin), timeout=30)
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
        request = _build_set_charge_timer_request(vin, start_hour, start_min, end_hour, end_min)
        try:
            response = method(request, metadata=self._metadata(vin), timeout=30)
            return _parse_charge_timer_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS SetGlobalChargeTimer failed: %s", err)
            raise
