"""PCCS (Polestar Connected Car Services) gRPC client.

Communicates with the PCCS gRPC API at api.pccs-prod.plstr.io for
vehicle command-and-control operations (charge target, charge timer, etc.).

Uses grpc generic channel methods with manual protobuf wire-format
encoding/decoding (no compiled .proto stubs required).
"""

from __future__ import annotations

import datetime
import logging
import time
import uuid
from collections.abc import Callable

import grpc
from homeassistant.exceptions import HomeAssistantError

from .const import _INVOCATION_INTERMEDIATE_STATUSES, INVOCATION_STATUS_MAP, PCCS_API_HOST
from .proto import (
    _decode_message,
    _encode_field_bytes,
    _encode_field_fixed32,
    _encode_field_varint,
    _get_bool,
    _get_int,
    _get_submessage,
    _identity_deserialize,
    _identity_serialize,
    _parse_invocation_response,
)

_LOGGER = logging.getLogger(__name__)

# ResponseStatus enum values from SetGlobalChargeTimerResponse
_RESPONSE_STATUS_NAMES = {
    0: "UNKNOWN_ERROR",
    1: "SUCCESS",
    2: "VALIDATION_ERROR",
    3: "INTERNAL_ERROR",
}


class PccsError(HomeAssistantError):
    """Error returned by the PCCS server (non-SUCCESS response status)."""


# gRPC service method paths
_SVC_TARGET_SOC = "/pccs.chronos.services.v1.TargetSocService"
_SVC_CHARGE_TIMER = "/pccs.chronos.services.v2.GlobalChargeTimerService"
_SVC_INVOCATION = "/pccs.invocation.v1.InvocationService"

_METHOD_GET_TARGET_SOC = f"{_SVC_TARGET_SOC}/GetTargetSoc"
_METHOD_SET_TARGET_SOC = f"{_SVC_TARGET_SOC}/SetTargetSoc"
_METHOD_GET_CHARGE_TIMER = f"{_SVC_CHARGE_TIMER}/GetGlobalChargeTimerStream"
_METHOD_SET_CHARGE_TIMER = f"{_SVC_CHARGE_TIMER}/SetGlobalChargeTimer"
_METHOD_CLIMATIZATION_START = f"{_SVC_INVOCATION}/ClimatizationStart"
_METHOD_CLIMATIZATION_STOP = f"{_SVC_INVOCATION}/ClimatizationStop"
_METHOD_LOCK = f"{_SVC_INVOCATION}/Lock"
_METHOD_UNLOCK = f"{_SVC_INVOCATION}/Unlock"

_INVOCATION_EXPIRY_MS = 120_000  # command expiry: 120 seconds


# ---------------------------------------------------------------------------
# Protobuf message builders
# ---------------------------------------------------------------------------
# All PCCS requests wrap a ChronosRequest in field 1.
# Response envelope layouts vary by RPC — see docstrings on _parse_*_response().


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
    utc_offset = datetime.datetime.now(datetime.UTC).astimezone().utcoffset()
    offset_minutes = int(utc_offset.total_seconds()) // 60
    # Proto3 int32: negative values need two's-complement encoding as uint64
    encoded_offset = offset_minutes if offset_minutes >= 0 else offset_minutes + (1 << 64)
    tz_msg = _encode_field_varint(1, encoded_offset)
    msg += _encode_field_bytes(4, tz_msg)
    return msg


def _build_get_request(vin: str) -> bytes:
    """Build a Get*Request wrapping a ChronosRequest in field 1."""
    return _encode_field_bytes(1, _build_chronos_request(vin))


def _build_set_target_soc_request(vin: str, target_soc: int, setting_type: int = 3) -> bytes:
    """Build SetTargetSoc request bytes.

    Args:
        vin: Vehicle identification number.
        target_soc: Target state of charge percentage (e.g. 80).
        setting_type: ChargeTargetLevelSettingType enum value.
            1=DAILY, 2=LONG_TRIP, 3=CUSTOM (default).
    """
    msg = _encode_field_bytes(1, _build_chronos_request(vin))
    msg += _encode_field_varint(2, target_soc)
    msg += _encode_field_varint(3, setting_type)
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
    activated: bool = True,
) -> bytes:
    """Build SetGlobalChargeTimer request bytes.

    Args:
        vin: Vehicle identification number.
        start_hour: Charging start hour (0-23).
        start_min: Charging start minute (0-59).
        end_hour: Charging end hour (0-23).
        end_min: Charging end minute (0-59).
        activated: Whether the charge timer is enabled (default True).
    """
    # Build GlobalChargeTimer sub-message: {1: start, 2: stop, 3: activated}
    timer = b""
    timer += _encode_field_bytes(1, _build_time_of_day(start_hour, start_min))
    timer += _encode_field_bytes(2, _build_time_of_day(end_hour, end_min))
    if activated:
        timer += _encode_field_varint(3, 1)

    msg = _encode_field_bytes(1, _build_chronos_request(vin))
    msg += _encode_field_bytes(2, timer)
    return msg


def _build_invocation_request(vin: str) -> bytes:
    """Build an InvocationRequest sub-message.

    InvocationRequest (pccs.invocation.v1):
        field 1: id (string)                   — random UUID
        field 2: vin (string)                  — vehicle VIN
        field 3: expiration_timestamp (int64)  — Unix timestamp for command expiry
    """
    msg = b""
    msg += _encode_field_bytes(1, str(uuid.uuid4()).encode("utf-8"))
    msg += _encode_field_bytes(2, vin.encode("utf-8"))
    msg += _encode_field_varint(3, int(time.time() * 1000) + _INVOCATION_EXPIRY_MS)
    return msg


def _build_climatization_start_request(vin: str, temperature: float = 22.0) -> bytes:
    """Build ClimatizationStartRequest bytes.

    Args:
        vin: Vehicle identification number.
        temperature: Target cabin temperature in Celsius (default 22.0).
    """
    msg = _encode_field_bytes(1, _build_invocation_request(vin))
    msg += _encode_field_varint(2, 1)  # start = true
    msg += _encode_field_fixed32(3, temperature)
    return msg


def _build_climatization_stop_request(vin: str) -> bytes:
    """Build ClimatizationStopRequest bytes."""
    return _encode_field_bytes(1, _build_invocation_request(vin))


def _build_lock_request(vin: str, lock_type: int = 0) -> bytes:
    """Build LockRequest bytes.

    LockRequest:
        field 1: InvocationRequest (message)
        field 2: lockType (LockType enum: 0=LOCK, 1=LOCK_REDUCED_GUARD)
    """
    msg = _encode_field_bytes(1, _build_invocation_request(vin))
    if lock_type:
        msg += _encode_field_varint(2, lock_type)
    return msg


def _build_unlock_request(vin: str) -> bytes:
    """Build UnlockRequest bytes.

    Structurally identical to ClimatizationStopRequest —
    InvocationRequest in field 1, no additional fields.
    """
    return _encode_field_bytes(1, _build_invocation_request(vin))


def _lock_error_context(data: bytes) -> str:
    """Extract lock-specific error context from a LockResponse.

    LockResponse field 2 is lockError (LockError enum):
        0 = LOCK_ERROR_UNSPECIFIED
        1 = LOCK_ERROR_DOOR_OPEN
    """
    if not data:
        return ""
    outer = _decode_message(data)
    lock_error = _get_int(outer, 2, 0)
    if lock_error == 1:
        return "a door is open"
    if lock_error > 1:
        return f"lock error (code {lock_error})"
    return ""


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
    """Parse GetGlobalChargeTimerResponse.

    Response structure (verified against live API 2026-03-14):
        field 1: globalChargeTimer (message)        — baseline timer data
        field 2: pendingGlobalChargeTimer (message)  — most recent write (if any)
        field 3: updatedAt (varint)                  — server timestamp

    When a write has been issued, field 2 holds the pending values while
    field 1 retains the baseline.  We prefer field 2 when present so that
    the UI reflects the most recently requested state immediately.

    GlobalChargeTimer sub-message:
        field 1: startTime (TimeOfDay message)   — {1: hours, 2: minutes, 3: tz}
        field 2: endTime (TimeOfDay message)     — {1: hours, 2: minutes, 3: tz}
        field 3: activated (bool)
        field 4: metadata (message)              — {1: id, 2: timestamp, 3: source}
    """
    empty = {
        "start_hour": None,
        "start_min": None,
        "end_hour": None,
        "end_min": None,
        "is_departure_active": False,
    }
    if not data:
        return empty

    envelope = _decode_message(data)

    # Prefer pending timer (field 2) over baseline (field 1)
    timer = _get_submessage(envelope, 2) or _get_submessage(envelope, 1)
    if not timer:
        return empty

    start_time = _get_submessage(timer, 1)
    end_time = _get_submessage(timer, 2)

    return {
        "start_hour": _get_int(start_time, 1) if start_time else None,
        "start_min": _get_int(start_time, 2) if start_time else None,
        "end_hour": _get_int(end_time, 1) if end_time else None,
        "end_min": _get_int(end_time, 2) if end_time else None,
        "is_departure_active": _get_bool(timer, 3),
    }


def _parse_set_charge_timer_response(data: bytes) -> dict:
    """Parse SetGlobalChargeTimerResponse.

    Response structure:
        field 1: id (string)           — echoed request UUID
        field 2: status (varint enum)  — 0=UNKNOWN_ERROR, 1=SUCCESS,
                                         2=VALIDATION_ERROR, 3=INTERNAL_ERROR
        field 3: message (string)      — error message text
        field 4: has_not_changed (bool) — true if values were already set
    """
    if not data:
        return {"id": "", "status": 0, "message": "", "has_not_changed": False}

    fields = _decode_message(data)

    id_val = fields.get(1, [b""])[0]
    if isinstance(id_val, bytes):
        id_val = id_val.decode("utf-8", errors="replace")

    msg_val = fields.get(3, [b""])[0]
    if isinstance(msg_val, bytes):
        msg_val = msg_val.decode("utf-8", errors="replace")

    return {
        "id": id_val,
        "status": _get_int(fields, 2, 0),
        "message": msg_val,
        "has_not_changed": _get_bool(fields, 4),
    }


# ---------------------------------------------------------------------------
# PccsClient
# ---------------------------------------------------------------------------


class PccsClient:
    """Client for the PCCS gRPC API.

    Uses two tokens: ``access_token`` for read operations (web client token,
    auto-refreshable) and ``write_access_token`` for write operations (PCCS
    2FA token with ``customer:attributes:write`` scope).
    """

    def __init__(self, access_token: str, write_access_token: str | None = None) -> None:
        """Initialize with OAuth access tokens."""
        self._access_token = access_token
        self._write_access_token = write_access_token
        self._channel: grpc.Channel | None = None

    @property
    def access_token(self) -> str:
        return self._access_token

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._access_token = value

    @property
    def write_access_token(self) -> str | None:
        return self._write_access_token

    @write_access_token.setter
    def write_access_token(self, value: str | None) -> None:
        self._write_access_token = value

    def _get_channel(self) -> grpc.Channel:
        """Get or create the gRPC channel."""
        if self._channel is None:
            credentials = grpc.ssl_channel_credentials()
            self._channel = grpc.secure_channel(f"{PCCS_API_HOST}:443", credentials)
        return self._channel

    def _metadata(self, vin: str) -> list[tuple[str, str]]:
        """Build gRPC call metadata for read operations."""
        return [
            ("authorization", f"Bearer {self._access_token}"),
            ("vin", vin),
        ]

    def _write_metadata(self, vin: str) -> list[tuple[str, str]]:
        """Build gRPC call metadata for write operations.

        Uses the write token (PCCS 2FA) when available, otherwise falls
        back to the regular read token.
        """
        token = self._write_access_token or self._access_token
        if not self._write_access_token:
            _LOGGER.debug("No PCCS write token available, falling back to web token")
        return [
            ("authorization", f"Bearer {token}"),
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
        Requires the PCCS 2FA token (customer:attributes:write scope).
        """
        channel = self._get_channel()
        method = channel.unary_stream(
            _METHOD_SET_TARGET_SOC,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        request = _build_set_target_soc_request(vin, percentage)
        try:
            responses = method(request, metadata=self._write_metadata(vin), timeout=30)
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
        activated: bool = True,
    ) -> dict:
        """Set the global charge timer for a vehicle.

        Requires the PCCS 2FA token (customer:attributes:write scope).

        Args:
            activated: Whether the charge timer should be enabled.
        """
        channel = self._get_channel()
        method = channel.unary_stream(
            _METHOD_SET_CHARGE_TIMER,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        request = _build_set_charge_timer_request(
            vin, start_hour, start_min, end_hour, end_min, activated=activated
        )
        try:
            responses = method(request, metadata=self._write_metadata(vin), timeout=30)
            for response in responses:
                result = _parse_set_charge_timer_response(response)
                break
            else:
                result = _parse_set_charge_timer_response(b"")
        except grpc.RpcError as err:
            _LOGGER.warning("PCCS SetGlobalChargeTimer failed: %s", err)
            raise

        status = result.get("status", 0)
        if status != 1:  # Not SUCCESS
            status_name = _RESPONSE_STATUS_NAMES.get(status, f"STATUS_{status}")
            server_msg = result.get("message", "")
            msg = f"SetGlobalChargeTimer failed: {status_name}"
            if server_msg:
                msg += f" - {server_msg}"
            raise PccsError(msg)

        if result.get("has_not_changed"):
            _LOGGER.debug("SetGlobalChargeTimer: values unchanged")

        return result

    # -- Climate (InvocationService) -----------------------------------------

    def _send_invocation(
        self,
        vin: str,
        method_path: str,
        request: bytes,
        *,
        command_name: str = "Command",
        error_context_fn: Callable[[bytes], str] | None = None,
    ) -> dict:
        """Send an InvocationService command and wait for terminal status.

        InvocationService methods are SERVER_STREAMING.  The stream emits
        intermediate statuses (SENT, DELIVERED) before a terminal status
        (SUCCESS or an error).  We iterate until we reach a terminal status.

        The server may cancel the stream (~5s timeout) before SUCCESS
        arrives.  If we received DELIVERED before cancellation, the command
        was accepted by the vehicle and we treat it as success.

        Args:
            command_name: Human-readable name for error messages.
            error_context_fn: Optional callback that receives the raw terminal
                response bytes and returns additional error context text.
        """
        channel = self._get_channel()
        method = channel.unary_stream(
            method_path,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        result = _parse_invocation_response(b"")
        last_raw = b""
        try:
            responses = method(request, metadata=self._write_metadata(vin), timeout=60)
            for response in responses:
                last_raw = response
                result = _parse_invocation_response(response)
                status = result.get("status", 0)
                if status not in _INVOCATION_INTERMEDIATE_STATUSES:
                    break
        except grpc.RpcError as err:
            # If the server cancelled the stream after DELIVERED, the
            # command was accepted — treat as success.
            if result.get("status") == 4:  # DELIVERED
                _LOGGER.debug(
                    "PCCS %s stream cancelled after DELIVERED — treating as success",
                    method_path,
                )
                return result
            _LOGGER.warning("PCCS %s failed: %s", method_path, err)
            raise

        status = result.get("status", 0)
        if status not in (4, 6):  # Not DELIVERED or SUCCESS
            status_name = INVOCATION_STATUS_MAP.get(status, f"STATUS_{status}")
            server_msg = result.get("message", "")
            msg = f"{command_name} failed: {status_name}"
            if server_msg:
                msg += f" - {server_msg}"
            if error_context_fn and last_raw:
                ctx = error_context_fn(last_raw)
                if ctx:
                    msg += f" ({ctx})"
            raise PccsError(msg)

        return result

    def climatization_start(self, vin: str, temperature: float = 22.0) -> dict:
        """Start vehicle climate pre-conditioning.

        Requires the PCCS 2FA token (customer:attributes:write scope).
        """
        request = _build_climatization_start_request(vin, temperature)
        return self._send_invocation(
            vin, _METHOD_CLIMATIZATION_START, request, command_name="Climatization"
        )

    def climatization_stop(self, vin: str) -> dict:
        """Stop vehicle climate pre-conditioning.

        Requires the PCCS 2FA token (customer:attributes:write scope).
        """
        request = _build_climatization_stop_request(vin)
        return self._send_invocation(
            vin, _METHOD_CLIMATIZATION_STOP, request, command_name="Climatization"
        )

    # -- Lock/Unlock (InvocationService) ------------------------------------

    def lock(self, vin: str, lock_type: int = 0) -> dict:
        """Lock the vehicle.

        Requires the PCCS 2FA token (customer:attributes:write scope).

        Args:
            lock_type: LockType enum (0=LOCK, 1=LOCK_REDUCED_GUARD).
        """
        request = _build_lock_request(vin, lock_type)
        return self._send_invocation(
            vin,
            _METHOD_LOCK,
            request,
            command_name="Lock",
            error_context_fn=_lock_error_context,
        )

    def unlock(self, vin: str) -> dict:
        """Unlock the vehicle.

        Requires the PCCS 2FA token (customer:attributes:write scope).
        """
        request = _build_unlock_request(vin)
        return self._send_invocation(vin, _METHOD_UNLOCK, request, command_name="Unlock")
