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
    expected_keys = {
        "CHARGING_STATUS_CHARGING",
        "CHARGING_STATUS_IDLE",
        "CHARGING_STATUS_DONE",
        "CHARGING_STATUS_FAULT",
        "CHARGING_STATUS_UNSPECIFIED",
        "CHARGING_STATUS_SCHEDULED",
    }
    assert set(CHARGING_STATUS_MAP.keys()) == expected_keys


def test_charging_status_map_values_are_non_empty_strings():
    for key, value in CHARGING_STATUS_MAP.items():
        assert isinstance(value, str) and value, f"Bad value for {key}"
