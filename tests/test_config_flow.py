"""Tests for the Polestar SOC config flow."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.polestar_soc.config_flow import PolestarSOCConfigFlow

_WEB_TOKENS = {
    "access_token": "web_access_tok",
    "refresh_token": "web_refresh_tok",
}
_PCCS_TOKENS = {
    "access_token": "pccs_access_tok",
    "refresh_token": "pccs_refresh_tok",
}
_VEHICLES = [{"vin": "TESTVIN123"}]


def _mock_web_api():
    """Create a mock PolestarAPI for web login."""
    api = MagicMock()
    api.login = MagicMock(return_value=_WEB_TOKENS)
    api.get_vehicles = MagicMock(return_value=_VEHICLES)
    return api


def _mock_pccs_api_needs_otp():
    """Create a mock PolestarAPI for PCCS that needs OTP."""
    api = MagicMock()
    api.login_start_2fa = MagicMock(return_value={
        "needs_otp": True,
        "_session_state": {
            "session": MagicMock(),
            "otp_resume": "url",
            "resume_url": "url",
            "code_verifier": "v",
        },
    })
    api.login_complete_2fa = MagicMock(return_value=_PCCS_TOKENS)
    return api


def _mock_pccs_api_no_otp():
    """Create a mock PolestarAPI for PCCS that doesn't need OTP."""
    api = MagicMock()
    api.login_start_2fa = MagicMock(return_value=_PCCS_TOKENS)
    return api


@pytest.fixture
def flow(hass: HomeAssistant) -> PolestarSOCConfigFlow:
    """Create a config flow instance with hass attached."""
    f = PolestarSOCConfigFlow()
    f.hass = hass
    f.context = {"source": config_entries.SOURCE_USER}
    return f


class TestConfigFlowUser:
    """Test the user (initial setup) config flow."""

    @pytest.mark.usefixtures("hass")
    async def test_full_flow_with_otp(self, hass: HomeAssistant, flow: PolestarSOCConfigFlow):
        """Test the full happy path: credentials -> OTP -> entry created."""
        web_api = _mock_web_api()
        pccs_api = _mock_pccs_api_needs_otp()
        pccs_api_complete = MagicMock()
        pccs_api_complete.login_complete_2fa = MagicMock(return_value=_PCCS_TOKENS)

        with patch(
            "custom_components.polestar_soc.config_flow.PolestarAPI",
            side_effect=[web_api, pccs_api, pccs_api_complete],
        ):
            # Step 1: submit credentials
            result = await flow.async_step_user(
                {"email": "test@polestar.com", "password": "secret"}
            )
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "otp"

            # Step 2: submit OTP
            result = await flow.async_step_otp({"otp": "123456"})
            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["title"] == "test@polestar.com"
            assert result["data"]["access_token"] == "web_access_tok"
            assert result["data"]["pccs_access_token"] == "pccs_access_tok"
            assert result["data"]["pccs_refresh_token"] == "pccs_refresh_tok"

    @pytest.mark.usefixtures("hass")
    async def test_skip_otp(self, hass: HomeAssistant, flow: PolestarSOCConfigFlow):
        """Test skipping OTP: entry created with web tokens only."""
        web_api = _mock_web_api()
        pccs_api = _mock_pccs_api_needs_otp()

        with patch(
            "custom_components.polestar_soc.config_flow.PolestarAPI",
            side_effect=[web_api, pccs_api],
        ):
            result = await flow.async_step_user(
                {"email": "test@polestar.com", "password": "secret"}
            )
            assert result["step_id"] == "otp"

            # Submit blank OTP
            result = await flow.async_step_otp({"otp": ""})
            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["pccs_access_token"] is None
            assert result["data"]["pccs_refresh_token"] is None

    @pytest.mark.usefixtures("hass")
    async def test_invalid_otp_shows_error(self, hass: HomeAssistant, flow: PolestarSOCConfigFlow):
        """Test invalid OTP shows error and allows retry."""
        web_api = _mock_web_api()
        pccs_api_start = _mock_pccs_api_needs_otp()

        pccs_api_bad = MagicMock()
        pccs_api_bad.login_complete_2fa = MagicMock(
            side_effect=Exception("2FA verification failed")
        )

        pccs_api_good = MagicMock()
        pccs_api_good.login_complete_2fa = MagicMock(return_value=_PCCS_TOKENS)

        with patch(
            "custom_components.polestar_soc.config_flow.PolestarAPI",
            side_effect=[web_api, pccs_api_start, pccs_api_bad, pccs_api_good],
        ):
            result = await flow.async_step_user(
                {"email": "test@polestar.com", "password": "secret"}
            )
            assert result["step_id"] == "otp"

            # Submit wrong OTP
            result = await flow.async_step_otp({"otp": "wrong"})
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "otp"
            assert result["errors"]["base"] == "invalid_otp"

            # Retry with correct OTP
            result = await flow.async_step_otp({"otp": "123456"})
            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["pccs_access_token"] == "pccs_access_tok"

    @pytest.mark.usefixtures("hass")
    async def test_web_login_failure_no_otp_step(
        self, hass: HomeAssistant, flow: PolestarSOCConfigFlow
    ):
        """Test that web login failure doesn't proceed to OTP step."""
        web_api = MagicMock()
        web_api.login = MagicMock(side_effect=ConfigEntryAuthFailed("bad creds"))

        with patch(
            "custom_components.polestar_soc.config_flow.PolestarAPI",
            return_value=web_api,
        ):
            result = await flow.async_step_user(
                {"email": "test@polestar.com", "password": "wrong"}
            )
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"]["base"] == "invalid_auth"

    @pytest.mark.usefixtures("hass")
    async def test_pccs_2fa_failure_still_shows_otp_form(
        self, hass: HomeAssistant, flow: PolestarSOCConfigFlow
    ):
        """Test that PCCS 2FA initiation failure still shows OTP form (user can skip)."""
        web_api = _mock_web_api()
        pccs_api = MagicMock()
        pccs_api.login_start_2fa = MagicMock(side_effect=Exception("network error"))

        with patch(
            "custom_components.polestar_soc.config_flow.PolestarAPI",
            side_effect=[web_api, pccs_api],
        ):
            result = await flow.async_step_user(
                {"email": "test@polestar.com", "password": "secret"}
            )
            # Should show OTP form (with no session state)
            assert result["step_id"] == "otp"

            # Submit blank to skip
            result = await flow.async_step_otp({"otp": ""})
            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["access_token"] == "web_access_tok"
            assert result["data"]["pccs_access_token"] is None

    @pytest.mark.usefixtures("hass")
    async def test_no_2fa_required_skips_otp_step(
        self, hass: HomeAssistant, flow: PolestarSOCConfigFlow
    ):
        """Test that when PCCS doesn't require 2FA, OTP step is skipped."""
        web_api = _mock_web_api()
        pccs_api = _mock_pccs_api_no_otp()

        with patch(
            "custom_components.polestar_soc.config_flow.PolestarAPI",
            side_effect=[web_api, pccs_api],
        ):
            result = await flow.async_step_user(
                {"email": "test@polestar.com", "password": "secret"}
            )
            # Should create entry directly, skipping OTP
            assert result["type"] is FlowResultType.CREATE_ENTRY
            assert result["data"]["pccs_access_token"] == "pccs_access_tok"
