"""Tests for constants and configuration values."""

from custom_components.polestar_soc.const import (
    API_URL,
    CHARGING_STATUS_MAP,
    CLIENT_ID,
    DOMAIN,
    OIDC_AUTH_URL,
    OIDC_BASE_URL,
    OIDC_TOKEN_URL,
    PCCS_API_HOST,
    QUERY_TELEMATICS_BATTERY,
    REDIRECT_URI,
    SCAN_INTERVAL,
)


def test_domain_is_set():
    assert DOMAIN == "polestar_soc"


def test_scan_interval_positive():
    assert SCAN_INTERVAL.total_seconds() > 0


def test_oauth_urls_use_https():
    for url in (OIDC_BASE_URL, OIDC_AUTH_URL, OIDC_TOKEN_URL, REDIRECT_URI, API_URL):
        assert url.startswith("https://"), f"{url} does not use HTTPS"


def test_client_id_non_empty():
    assert CLIENT_ID


def test_pccs_host_non_empty():
    assert PCCS_API_HOST


def test_charging_status_map_has_expected_keys():
    """CEP BatteryState field 7: 1=CHARGING, 2=IDLE, 3=SCHEDULED."""
    assert set(CHARGING_STATUS_MAP.keys()) == {1, 2, 3}


def test_charging_status_map_labels():
    assert CHARGING_STATUS_MAP[1] == "Charging"
    assert CHARGING_STATUS_MAP[2] == "Idle"
    assert CHARGING_STATUS_MAP[3] == "Scheduled"


def test_battery_query_omits_removed_charging_status_field():
    """Regression guard for issue #22 — Polestar removed BatteryV2.chargingStatus.

    Selecting it fails validation for the *whole* document, taking
    batteryChargeLevelPercentage and estimatedChargingTimeToFullMinutes with it.
    """
    assert "chargingStatus" not in QUERY_TELEMATICS_BATTERY
    assert "batteryChargeLevelPercentage" in QUERY_TELEMATICS_BATTERY
    assert "estimatedChargingTimeToFullMinutes" in QUERY_TELEMATICS_BATTERY


def test_charging_status_map_values_are_non_empty_strings():
    for key, value in CHARGING_STATUS_MAP.items():
        assert isinstance(value, str) and value, f"Bad value for {key}"
