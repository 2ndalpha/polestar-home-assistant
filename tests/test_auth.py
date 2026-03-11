"""Tests for PolestarAPI two-phase login (login_start_2fa / login_complete_2fa)."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.polestar_soc.coordinator import PolestarAPI

# Fake HTML pages returned by the OIDC server during login flow.
_AUTH_PAGE_HTML = '<form action="/as/abc123/resume/as/authorization.ping">login</form>'

_CRED_REDIRECT_HEADERS = {"Location": "https://example.com/callback?code=AUTH_CODE_123"}

_OTP_CHALLENGE_HTML = """
<script>
var globalContext = {
    action: "/as/otp789/resume/as/authorization.ping"
};
</script>
"""

_OTP_SUCCESS_HTML = '<form id="otp-success-form" action="/as/otpdone/resume/as/authorization.ping">'

_TOKEN_RESPONSE = {
    "access_token": "pccs_access_tok",
    "refresh_token": "pccs_refresh_tok",
    "token_type": "Bearer",
    "expires_in": 3600,
}


def _mock_get_auth_page(url, **kwargs):
    """Mock the initial auth GET — returns a page with the resume URL."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _AUTH_PAGE_HTML
    resp.raise_for_status = MagicMock()
    return resp


def _mock_post_creds_redirect(url, **kwargs):
    """Mock credential POST that returns 302 (no 2FA)."""
    resp = MagicMock()
    resp.status_code = 302
    resp.headers = _CRED_REDIRECT_HEADERS
    return resp


def _mock_post_creds_otp_challenge(url, **kwargs):
    """Mock credential POST that returns OTP challenge page (200)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _OTP_CHALLENGE_HTML
    return resp


def _mock_post_creds_auth_error(url, **kwargs):
    """Mock credential POST that returns auth error."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ERR001: invalid credentials"
    return resp


def _mock_post_otp_success(url, **kwargs):
    """Mock OTP submission that returns success form."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = _OTP_SUCCESS_HTML
    return resp


def _mock_post_continue_redirect(url, **kwargs):
    """Mock the continue.authentication POST that redirects with code."""
    resp = MagicMock()
    resp.status_code = 302
    resp.headers = {"Location": "polestar-explore://explore.polestar.com?code=PCCS_CODE"}
    return resp


def _mock_post_token_exchange(url, **kwargs):
    """Mock the token exchange POST."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=_TOKEN_RESPONSE)
    resp.raise_for_status = MagicMock()
    return resp


class TestLoginStart2fa:
    """Tests for login_start_2fa."""

    def test_returns_needs_otp_when_2fa_triggered(self):
        """When the server challenges for OTP, return session state."""
        api = PolestarAPI(client_id="test_client", redirect_uri="https://test/callback")

        session_mock = MagicMock()
        session_mock.get = _mock_get_auth_page
        # Credential POST returns OTP challenge
        session_mock.post = _mock_post_creds_otp_challenge

        mock_path = "custom_components.polestar_soc.coordinator.requests.Session"
        with patch(mock_path, return_value=session_mock):
            result = api.login_start_2fa(
                "user@test.com", "pass123", scope="openid", acr_values="2sv"
            )

        assert result["needs_otp"] is True
        assert "_session_state" in result
        state = result["_session_state"]
        assert state["session"] is session_mock
        assert "otp_resume" in state
        assert "code_verifier" in state
        assert "resume_url" in state

    def test_returns_tokens_when_no_2fa(self):
        """When the server doesn't challenge for 2FA, complete login directly."""
        api = PolestarAPI(client_id="test_client", redirect_uri="https://test/callback")

        session_mock = MagicMock()
        session_mock.get = _mock_get_auth_page

        call_count = 0

        def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Credential POST — redirect (no 2FA)
                return _mock_post_creds_redirect(url, **kwargs)
            # Token exchange
            return _mock_post_token_exchange(url, **kwargs)

        session_mock.post = post_side_effect

        mock_path = "custom_components.polestar_soc.coordinator.requests.Session"
        with patch(mock_path, return_value=session_mock):
            result = api.login_start_2fa("user@test.com", "pass123")

        assert "access_token" in result
        assert result["access_token"] == "pccs_access_tok"
        assert api.access_token == "pccs_access_tok"

    def test_raises_on_invalid_credentials(self):
        """Auth error response raises ConfigEntryAuthFailed."""
        api = PolestarAPI(client_id="test_client", redirect_uri="https://test/callback")

        session_mock = MagicMock()
        session_mock.get = _mock_get_auth_page
        session_mock.post = _mock_post_creds_auth_error

        mock_path = "custom_components.polestar_soc.coordinator.requests.Session"
        with (
            patch(mock_path, return_value=session_mock),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            api.login_start_2fa("user@test.com", "wrong_pass")


class TestLoginComplete2fa:
    """Tests for login_complete_2fa."""

    def test_submits_otp_and_returns_tokens(self):
        """Happy path: OTP submitted, tokens returned."""
        api = PolestarAPI(client_id="test_client", redirect_uri="https://test/callback")

        session_mock = MagicMock()
        call_count = 0

        def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # OTP submission
                return _mock_post_otp_success(url, **kwargs)
            if call_count == 2:
                # Continue authentication redirect
                return _mock_post_continue_redirect(url, **kwargs)
            # Token exchange
            return _mock_post_token_exchange(url, **kwargs)

        session_mock.post = post_side_effect

        session_state = {
            "session": session_mock,
            "otp_resume": "https://polestarid.eu.polestar.com/as/otp789/resume/as/authorization.ping",
            "resume_url": "https://polestarid.eu.polestar.com/as/abc123/resume/as/authorization.ping",
            "code_verifier": "test_verifier_123",
        }

        result = api.login_complete_2fa(session_state, "123456")
        assert result["access_token"] == "pccs_access_tok"
        assert api.access_token == "pccs_access_tok"

    def test_raises_on_invalid_otp(self):
        """Invalid OTP returns non-redirect status, raises UpdateFailed."""
        api = PolestarAPI(client_id="test_client", redirect_uri="https://test/callback")

        session_mock = MagicMock()

        def post_side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "Invalid OTP code"
            return resp

        session_mock.post = post_side_effect

        session_state = {
            "session": session_mock,
            "otp_resume": "https://example.com/otp",
            "resume_url": "https://example.com/resume",
            "code_verifier": "test_verifier",
        }

        with pytest.raises(UpdateFailed, match="2FA verification failed"):
            api.login_complete_2fa(session_state, "wrong_code")
