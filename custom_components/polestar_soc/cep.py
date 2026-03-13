"""CEP (Volvo Connected Experience Platform) gRPC client.

Communicates with the Volvo CEP gRPC API at cepmobtoken.eu.prod.c3.volvocars.com
for reading vehicle state data (climate status, battery, etc.).

Uses the same Polestar OAuth access token as the rest of the integration.
"""

from __future__ import annotations

import logging

import grpc

from .const import (
    CEP_API_HOST,
    CLIMATE_RUNNING_STATUS_MAP,
    HEATING_INTENSITY_MAP,
)
from .proto import (
    _decode_message,
    _encode_field_bytes,
    _get_double,
    _get_int,
    _get_submessage,
    _identity_deserialize,
    _identity_serialize,
)

_LOGGER = logging.getLogger(__name__)

# gRPC service method paths
_METHOD_GET_CLIMATE = (
    "/services.vehiclestates.parkingclimatization"
    ".ParkingClimatizationService/GetLatestParkingClimatization"
)
_METHOD_GET_BATTERY = "/services.vehiclestates.battery.BatteryService/GetLatestBattery"
_METHOD_GET_EXTERIOR = "/services.vehiclestates.exterior.ExteriorService/GetLatestExterior"
_METHOD_GET_LOCATION = "/dtlinternet.DtlInternetService/GetLastKnownLocation"

# BatteryState field numbers captured in raw_fields for debugging.
_RAW_BATTERY_FIELD_NUMBERS = (5, 7, 8, 17, 26, 28)


# ---------------------------------------------------------------------------
# Request builders
# ---------------------------------------------------------------------------


def _build_vin_request(vin: str) -> bytes:
    """Build a request with VIN as field 2 (string)."""
    return _encode_field_bytes(2, vin.encode("utf-8"))


def _build_location_request(vin: str) -> bytes:
    """Build a request with VIN as field 1 (DtlInternetService uses field 1, not field 2)."""
    return _encode_field_bytes(1, vin.encode("utf-8"))


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


def _format_climate_status(value: int) -> str:
    """Map climate running status enum to string."""
    mapped = CLIMATE_RUNNING_STATUS_MAP.get(value)
    if mapped is not None:
        return mapped
    _LOGGER.debug("Unknown climate running status: %d", value)
    return f"Unknown ({value})"


def _format_heating_intensity(value: int) -> str:
    """Map heating intensity enum to string."""
    mapped = HEATING_INTENSITY_MAP.get(value)
    if mapped is not None:
        return mapped
    _LOGGER.debug("Unknown heating intensity: %d", value)
    return f"Unknown ({value})"


def _parse_climate_response(data: bytes) -> dict:
    """Parse GetLatestParkingClimatization response.

    Two-level decode: outer envelope has field 3 = state sub-message.
    """
    empty = {
        "status": None,
        "driver_seat_heating": None,
        "passenger_seat_heating": None,
        "rear_left_seat_heating": None,
        "rear_right_seat_heating": None,
        "steering_wheel_heating": None,
    }
    if not data:
        return empty

    outer = _decode_message(data)
    state = _get_submessage(outer, 3)
    if state is None:
        return empty

    return {
        "status": _format_climate_status(_get_int(state, 2, 0)),
        "driver_seat_heating": _format_heating_intensity(_get_int(state, 9, 0)),
        "passenger_seat_heating": _format_heating_intensity(_get_int(state, 10, 0)),
        "rear_left_seat_heating": _format_heating_intensity(_get_int(state, 11, 0)),
        "rear_right_seat_heating": _format_heating_intensity(_get_int(state, 12, 0)),
        "steering_wheel_heating": _format_heating_intensity(_get_int(state, 13, 0)),
    }


def _parse_battery_response(data: bytes) -> dict:
    """Parse GetLatestBattery response.

    Two-level decode: outer envelope has field 3 = battery state sub-message.

    Field mapping (BatteryState proto):
        field 2:  battery_charge_level_percentage (double)
        field 3:  average_energy_consumption_kwh_per_100_km (double)
        field 4:  estimated_distance_to_empty_km (varint)
        field 5:  estimated_charging_time_to_full_minutes (varint)
        field 6:  charger_connection_status (enum: 1=CONNECTED, 2=DISCONNECTED, 3=FAULT)
        field 7:  charging_status (enum: 1=CHARGING, 2=IDLE, 3=SCHEDULED, ...)
        field 8:  estimated_distance_to_empty_miles (varint)
        field 10: charging_power_watts (varint)
        field 17: charging_type (enum)
        field 26: charger_power_status (enum)
        field 28: unknown (CEP-specific?)
    """
    empty = {
        "soc": None,
        "estimated_range_km": None,
        "charger_connection_status": None,
        "charging_status": None,
        "avg_energy_consumption_kwh_per_100km": None,
        "estimated_charging_time_minutes": None,
        "estimated_range_miles": None,
        "charging_power_watts": None,
        "raw_fields": {},
    }
    if not data:
        return empty

    outer = _decode_message(data)
    state = _get_submessage(outer, 3)
    if state is None:
        return empty

    raw_fields = {}
    for fn in _RAW_BATTERY_FIELD_NUMBERS:
        vals = state.get(fn)
        if vals is not None:
            raw_fields[fn] = vals[0]

    return {
        "soc": _get_double(state, 2),
        "estimated_range_km": _get_int(state, 4) or None,
        "charger_connection_status": _get_int(state, 6) or None,
        "charging_status": _get_int(state, 7) or None,
        "avg_energy_consumption_kwh_per_100km": _get_double(state, 3),
        "estimated_charging_time_minutes": _get_int(state, 5) or None,
        "estimated_range_miles": _get_int(state, 8) or None,
        "charging_power_watts": _get_int(state, 10) or None,
        "raw_fields": raw_fields,
    }


# ExteriorState field numbers → dict keys
_EXTERIOR_FIELDS: tuple[tuple[int, str], ...] = (
    (2, "central_lock"),
    (3, "front_left_door"),
    (4, "front_right_door"),
    (5, "rear_left_door"),
    (6, "rear_right_door"),
    (7, "front_left_window"),
    (8, "front_right_window"),
    (9, "rear_left_window"),
    (10, "rear_right_window"),
    (11, "hood"),
    (12, "tailgate"),
    (13, "tank_lid"),
    (14, "sunroof"),
    (15, "alarm"),
)


def _parse_exterior_response(data: bytes) -> dict:
    """Parse GetLatestExterior response.

    Two-level decode: outer envelope has field 3 = ExteriorState sub-message.
    Returns raw integer enum values (0-3) for each field, or None if missing.
    """
    empty: dict = {key: None for _, key in _EXTERIOR_FIELDS}
    if not data:
        return empty

    outer = _decode_message(data)
    state = _get_submessage(outer, 3)
    if state is None:
        return empty

    result: dict = {}
    for field_num, key in _EXTERIOR_FIELDS:
        val = _get_int(state, field_num)
        result[key] = val if val else None
    return result


def _parse_location_response(data: bytes) -> dict:
    """Parse GetLastKnownLocation response.

    Unlike climate/battery, location fields are at the top level (no envelope).
    Field mapping:
        field 1 (string): VIN
        field 2 (double): longitude
        field 3 (double): latitude
        field 4 (varint): timestamp_ms (milliseconds since epoch)
    """
    empty: dict = {"latitude": None, "longitude": None, "timestamp_ms": None}
    if not data:
        return empty

    fields = _decode_message(data)
    latitude = _get_double(fields, 3)
    longitude = _get_double(fields, 2)
    if latitude is None or longitude is None:
        return empty

    return {
        "latitude": latitude,
        "longitude": longitude,
        "timestamp_ms": _get_int(fields, 4) or None,
    }


# ---------------------------------------------------------------------------
# CepClient
# ---------------------------------------------------------------------------


class CepClient:
    """Client for the Volvo CEP gRPC API."""

    def __init__(self, access_token: str) -> None:
        self._access_token = access_token
        self._channel: grpc.Channel | None = None

    @property
    def access_token(self) -> str:
        return self._access_token

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._access_token = value

    def _get_channel(self) -> grpc.Channel:
        if self._channel is None:
            credentials = grpc.ssl_channel_credentials()
            self._channel = grpc.secure_channel(f"{CEP_API_HOST}:443", credentials)
        return self._channel

    def _metadata(self, vin: str) -> list[tuple[str, str]]:
        return [
            ("authorization", f"Bearer {self._access_token}"),
            ("vin", vin),
        ]

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None

    def get_parking_climatization(self, vin: str) -> dict:
        """Get current parking climatization state."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_GET_CLIMATE,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        try:
            response = method(_build_vin_request(vin), metadata=self._metadata(vin), timeout=30)
            return _parse_climate_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("CEP GetLatestParkingClimatization failed: %s", err)
            raise

    def get_battery(self, vin: str) -> dict:
        """Get current battery state."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_GET_BATTERY,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        try:
            response = method(_build_vin_request(vin), metadata=self._metadata(vin), timeout=30)
            return _parse_battery_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("CEP GetLatestBattery failed: %s", err)
            raise

    def get_exterior(self, vin: str) -> dict:
        """Get current exterior state (lock, doors, windows, etc.)."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_GET_EXTERIOR,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        try:
            response = method(_build_vin_request(vin), metadata=self._metadata(vin), timeout=30)
            return _parse_exterior_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("CEP GetLatestExterior failed: %s", err)
            raise

    def get_location(self, vin: str) -> dict:
        """Get last known vehicle location."""
        channel = self._get_channel()
        method = channel.unary_unary(
            _METHOD_GET_LOCATION,
            request_serializer=_identity_serialize,
            response_deserializer=_identity_deserialize,
        )
        try:
            response = method(
                _build_location_request(vin), metadata=self._metadata(vin), timeout=30
            )
            return _parse_location_response(response)
        except grpc.RpcError as err:
            _LOGGER.warning("CEP GetLastKnownLocation failed: %s", err)
            raise
