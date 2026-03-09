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


# ---------------------------------------------------------------------------
# Request builders
# ---------------------------------------------------------------------------


def _build_vin_request(vin: str) -> bytes:
    """Build a request with VIN as field 2 (string)."""
    return _encode_field_bytes(2, vin.encode("utf-8"))


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
    if not data:
        return {
            "status": None,
            "driver_seat_heating": None,
            "passenger_seat_heating": None,
            "rear_left_seat_heating": None,
            "rear_right_seat_heating": None,
            "steering_wheel_heating": None,
        }

    outer = _decode_message(data)
    state = _get_submessage(outer, 3)
    if state is None:
        return {
            "status": None,
            "driver_seat_heating": None,
            "passenger_seat_heating": None,
            "rear_left_seat_heating": None,
            "rear_right_seat_heating": None,
            "steering_wheel_heating": None,
        }

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
    SOC and charging_power are IEEE 754 doubles (wire type 1 / fixed64).
    """
    if not data:
        return {
            "soc": None,
            "estimated_range_km": None,
            "charging_status": None,
            "charging_power_kw": None,
        }

    outer = _decode_message(data)
    state = _get_submessage(outer, 3)
    if state is None:
        return {
            "soc": None,
            "estimated_range_km": None,
            "charging_status": None,
            "charging_power_kw": None,
        }

    return {
        "soc": _get_double(state, 2),
        "estimated_range_km": _get_int(state, 4) or None,
        "charging_status": _get_int(state, 6) or None,
        "charging_power_kw": _get_double(state, 3),
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
