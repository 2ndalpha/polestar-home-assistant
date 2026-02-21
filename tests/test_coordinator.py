"""Tests for coordinator utility functions."""

import base64

from custom_components.polestar_soc.coordinator import (
    PolestarCoordinator,
    _b64urlencode,
)


class TestB64UrlEncode:
    def test_simple_bytes(self):
        result = _b64urlencode(b"hello")
        # URL-safe base64 of "hello" without padding
        expected = base64.urlsafe_b64encode(b"hello").rstrip(b"=").decode()
        assert result == expected

    def test_no_padding(self):
        # Ensure no '=' padding characters
        for length in range(1, 50):
            result = _b64urlencode(bytes(range(length)))
            assert "=" not in result

    def test_url_safe_chars(self):
        # Standard base64 uses + and /, URL-safe uses - and _
        # Use bytes that produce + and / in standard base64
        data = b"\xfb\xff\xfe"
        result = _b64urlencode(data)
        assert "+" not in result
        assert "/" not in result

    def test_empty_input(self):
        result = _b64urlencode(b"")
        assert result == ""


class TestFormatChargingStatus:
    def test_known_statuses(self):
        assert PolestarCoordinator.format_charging_status("CHARGING_STATUS_CHARGING") == "Charging"
        assert PolestarCoordinator.format_charging_status("CHARGING_STATUS_IDLE") == "Idle"
        assert PolestarCoordinator.format_charging_status("CHARGING_STATUS_DONE") == "Fully charged"
        assert PolestarCoordinator.format_charging_status("CHARGING_STATUS_FAULT") == "Fault"
        assert (
            PolestarCoordinator.format_charging_status("CHARGING_STATUS_UNSPECIFIED") == "Unknown"
        )
        assert (
            PolestarCoordinator.format_charging_status("CHARGING_STATUS_SCHEDULED") == "Scheduled"
        )

    def test_none_returns_unknown(self):
        assert PolestarCoordinator.format_charging_status(None) == "Unknown"

    def test_empty_string_returns_unknown(self):
        assert PolestarCoordinator.format_charging_status("") == "Unknown"

    def test_unknown_status_formatted(self):
        # Unknown statuses should be formatted by stripping prefix and title-casing
        result = PolestarCoordinator.format_charging_status("CHARGING_STATUS_NEW_VALUE")
        assert result == "New Value"
