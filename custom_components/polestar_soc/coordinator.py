"""DataUpdateCoordinator and API client for Polestar."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cep import CepClient
from .const import (
    API_URL,
    CHARGING_STATUS_MAP,
    CLIENT_ID,
    DOMAIN,
    OIDC_AUTH_URL,
    OIDC_BASE_URL,
    OIDC_TOKEN_URL,
    PCCS_CLIENT_ID,
    PCCS_REDIRECT_URI,
    QUERY_GET_CARS,
    QUERY_TELEMATICS,
    REDIRECT_URI,
    SCAN_INTERVAL,
    SCOPE,
)
from .pccs import PccsClient

_LOGGER = logging.getLogger(__name__)

HTTP_TIMEOUT = 30


def _b64urlencode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class PolestarAPI:
    """Handle Polestar OAuth2 PKCE authentication and GraphQL queries."""

    def __init__(
        self,
        client_id: str = CLIENT_ID,
        redirect_uri: str = REDIRECT_URI,
        otp_callback: Callable[[], str | None] | None = None,
    ) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._otp_callback = otp_callback

    @property
    def client_id(self) -> str:
        """Return the OAuth2 client_id for this API instance."""
        return self._client_id

    def _get_otp_code(self) -> str | None:
        """Get OTP code for 2FA via callback."""
        if self._otp_callback:
            return self._otp_callback()
        return None

    # -- Private auth helpers ------------------------------------------------

    def _start_auth_session(
        self,
        scope: str,
        acr_values: str | None = None,
    ) -> tuple[requests.Session, str, str]:
        """Start OAuth2 PKCE session: auth request + extract resume URL.

        Returns (session, resume_url, code_verifier).
        """
        client_id = self._client_id
        redirect_uri = self._redirect_uri
        code_verifier = _b64urlencode(os.urandom(32))
        code_challenge = _b64urlencode(hashlib.sha256(code_verifier.encode()).digest())
        state = _b64urlencode(os.urandom(12))

        session = requests.Session()

        auth_params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if acr_values:
            auth_params["acr_values"] = acr_values
        resp = session.get(
            OIDC_AUTH_URL,
            params=auth_params,
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()

        match = re.search(r"(/as/[^/]+/resume/as/authorization\.ping)", resp.text)
        if not match:
            raise UpdateFailed("Could not find login form endpoint")

        resume_url = OIDC_BASE_URL + match.group(1)
        return session, resume_url, code_verifier

    def _submit_credentials(
        self,
        session: requests.Session,
        resume_url: str,
        email: str,
        password: str,
    ) -> requests.Response:
        """POST credentials to the login form. Returns the response."""
        return session.post(
            resume_url,
            data={
                "pf.username": email,
                "pf.pass": password,
                "client_id": self._client_id,
            },
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )

    @staticmethod
    def _submit_otp(
        session: requests.Session,
        otp_resume: str,
        otp_code: str,
    ) -> requests.Response:
        """Submit OTP code and handle the success-form continuation."""
        resp = session.post(
            otp_resume,
            data={"otp": otp_code},
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )
        # OTP success returns a page with auto-submit form
        if "otp-success-form" in resp.text:
            action_match = re.search(
                r'action="(/as/[^"]+/resume/as/authorization\.ping)"',
                resp.text,
            )
            continue_url = OIDC_BASE_URL + action_match.group(1) if action_match else otp_resume
            resp = session.post(
                continue_url,
                data={"continue.authentication": "true"},
                allow_redirects=False,
                timeout=HTTP_TIMEOUT,
            )
        return resp

    @staticmethod
    def _extract_auth_code(
        session: requests.Session,
        resp: requests.Response,
        resume_url: str,
    ) -> str:
        """Extract auth code from redirect, handling consent if needed."""
        redirect_url = resp.headers.get("Location", "")
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)

        # Handle consent/confirmation
        if "code" not in params and "uid" in params:
            uid = params["uid"][0]
            resp = session.post(
                resume_url,
                data={"pf.submit": "true", "subject": uid},
                allow_redirects=False,
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code not in (302, 303):
                raise UpdateFailed("Consent confirmation failed")

            redirect_url = resp.headers.get("Location", "")
            parsed = urlparse(redirect_url)
            params = parse_qs(parsed.query)

        if "code" not in params:
            resp = session.get(redirect_url, allow_redirects=False, timeout=HTTP_TIMEOUT)
            if resp.status_code in (302, 303):
                redirect_url = resp.headers.get("Location", "")
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)

        if "code" not in params:
            raise UpdateFailed("No authorization code received")

        return params["code"][0]

    def _exchange_code_for_tokens(
        self,
        session: requests.Session,
        auth_code: str,
        code_verifier: str,
    ) -> dict:
        """Exchange authorization code for tokens and store them."""
        resp = session.post(
            OIDC_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": code_verifier,
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()

        tokens = resp.json()
        if "access_token" not in tokens:
            raise UpdateFailed("Token exchange failed")

        self.access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token")
        return tokens

    @staticmethod
    def _detect_otp_challenge(resp: requests.Response, resume_url: str) -> str | None:
        """Check if a credential-POST response is a 2FA challenge.

        Returns the OTP resume URL if 2FA is required, or None.
        """
        if resp.status_code in (302, 303):
            return None  # No 2FA — redirect means success
        if resp.status_code != 200:
            return None  # Not an OTP page
        if "ERR001" in resp.text or "authMessage" in resp.text:
            return None  # Auth error, not OTP
        action_match = re.search(
            r'action:\s*"(/as/[^"]+/resume/as/authorization\.ping)"',
            resp.text,
        )
        if not action_match:
            return None
        return OIDC_BASE_URL + action_match.group(1)

    # -- Public login methods ------------------------------------------------

    def login(
        self,
        email: str,
        password: str,
        scope: str = SCOPE,
        acr_values: str | None = None,
    ) -> dict:
        """Perform full OAuth2 Authorization Code + PKCE login."""
        session, resume_url, code_verifier = self._start_auth_session(scope, acr_values)
        resp = self._submit_credentials(session, resume_url, email, password)

        if resp.status_code not in (302, 303):
            if "ERR001" in resp.text or "authMessage" in resp.text:
                raise ConfigEntryAuthFailed("Invalid email or password")

            # 2SV: server returned OTP challenge page (200 with form)
            otp_resume = self._detect_otp_challenge(resp, resume_url)
            if otp_resume:
                otp_code = self._get_otp_code()
                if not otp_code:
                    raise UpdateFailed("2FA code required but not provided")

                _LOGGER.debug("Submitting OTP to %s", otp_resume)
                resp = self._submit_otp(session, otp_resume, otp_code)

                if resp.status_code not in (302, 303):
                    raise UpdateFailed(f"2FA verification failed ({resp.status_code})")
            else:
                raise UpdateFailed(f"Unexpected login response ({resp.status_code})")

        auth_code = self._extract_auth_code(session, resp, resume_url)
        return self._exchange_code_for_tokens(session, auth_code, code_verifier)

    def login_start_2fa(
        self,
        email: str,
        password: str,
        scope: str = SCOPE,
        acr_values: str | None = None,
    ) -> dict:
        """Start login with 2FA. Triggers OTP email.

        Returns a dict with ``"needs_otp": True`` and an opaque
        ``"_session_state"`` if the server challenges for OTP.
        If no 2FA is required, completes the flow and returns tokens
        (with ``"access_token"`` present).
        """
        session, resume_url, code_verifier = self._start_auth_session(scope, acr_values)
        resp = self._submit_credentials(session, resume_url, email, password)

        if resp.status_code not in (302, 303):
            if "ERR001" in resp.text or "authMessage" in resp.text:
                raise ConfigEntryAuthFailed("Invalid email or password")

            otp_resume = self._detect_otp_challenge(resp, resume_url)
            if otp_resume:
                # 2FA triggered — return session state for the caller to
                # collect the OTP code and call login_complete_2fa().
                return {
                    "needs_otp": True,
                    "_session_state": {
                        "session": session,
                        "otp_resume": otp_resume,
                        "resume_url": resume_url,
                        "code_verifier": code_verifier,
                    },
                }

            raise UpdateFailed(f"Unexpected login response ({resp.status_code})")

        # No 2FA — complete the flow directly
        auth_code = self._extract_auth_code(session, resp, resume_url)
        return self._exchange_code_for_tokens(session, auth_code, code_verifier)

    def login_complete_2fa(self, session_state: dict, otp_code: str) -> dict:
        """Complete 2FA login by submitting the OTP code.

        ``session_state`` is the ``"_session_state"`` dict returned by
        ``login_start_2fa``.
        """
        session: requests.Session = session_state["session"]
        otp_resume: str = session_state["otp_resume"]
        resume_url: str = session_state["resume_url"]
        code_verifier: str = session_state["code_verifier"]

        _LOGGER.debug("Submitting OTP to %s", otp_resume)
        resp = self._submit_otp(session, otp_resume, otp_code)

        if resp.status_code not in (302, 303):
            raise UpdateFailed(f"2FA verification failed ({resp.status_code})")

        auth_code = self._extract_auth_code(session, resp, resume_url)
        return self._exchange_code_for_tokens(session, auth_code, code_verifier)

    def refresh_tokens(self, refresh_token: str) -> dict:
        """Refresh the access token using a refresh token."""
        client_id = self._client_id
        resp = requests.post(
            OIDC_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        tokens = resp.json()
        if "access_token" not in tokens:
            raise UpdateFailed("Token refresh failed")

        self.access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", refresh_token)
        return tokens

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = requests.post(API_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()

        if "errors" in result:
            messages = [e.get("message", str(e)) for e in result["errors"]]
            raise UpdateFailed(f"GraphQL errors: {'; '.join(messages)}")

        return result.get("data", {})

    def get_vehicles(self) -> list:
        """Fetch list of vehicles."""
        data = self._graphql(QUERY_GET_CARS)
        return data.get("getConsumerCarsV2", [])

    def get_telematics(self, vins: list[str]) -> dict:
        """Fetch telematics data for given VINs."""
        data = self._graphql(QUERY_TELEMATICS, {"vins": vins})
        return data.get("carTelematicsV2", {})


class PolestarCoordinator(DataUpdateCoordinator):
    """Coordinate data updates from the Polestar API."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.config_entry = entry
        self.api = PolestarAPI()
        self.api.access_token = entry.data.get("access_token")
        self.api.refresh_token = entry.data.get("refresh_token")

        # PCCS API instance kept for potential future write operations
        # that may require the PCCS token with 2FA scope.
        self._pccs_api = PolestarAPI(
            client_id=PCCS_CLIENT_ID,
            redirect_uri=PCCS_REDIRECT_URI,
        )
        self._pccs_api.access_token = entry.data.get("pccs_access_token")
        self._pccs_api.refresh_token = entry.data.get("pccs_refresh_token")

        # PCCS chronos services (charge timer, target SOC) accept the web token
        self.pccs = PccsClient(self.api.access_token or "")
        self.cep = CepClient(self.api.access_token or "")
        self._email: str = entry.data["email"]
        self._password: str = entry.data["password"]

    async def _async_update_data(self) -> dict:
        """Fetch data from the Polestar API."""
        try:
            return await self._fetch_data()
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 401:
                _LOGGER.debug("Access token expired, attempting refresh")
            else:
                raise UpdateFailed(f"API error: {err}") from err

        # Token expired — try refresh
        try:
            await self._refresh_or_relogin()
            return await self._fetch_data()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"API error after re-auth: {err}") from err

    async def _refresh_or_relogin(self) -> None:
        """Try token refresh, fall back to full re-login for both API clients."""
        # Refresh/relogin the main (web) API — failure is fatal
        await self._refresh_or_relogin_api(self.api)
        self._update_stored_tokens()
        # Refresh the PCCS API — only try token refresh, not
        # full re-login (which requires 2FA and can't be done in background).
        if self._pccs_api.refresh_token:
            try:
                await self.hass.async_add_executor_job(
                    self._pccs_api.refresh_tokens, self._pccs_api.refresh_token
                )
            except Exception:
                _LOGGER.warning(
                    "PCCS token refresh failed; PCCS sensors will be unavailable "
                    "until the integration is reconfigured",
                    exc_info=True,
                )
        self._update_stored_tokens()

    async def _refresh_or_relogin_api(
        self,
        api: PolestarAPI,
        scope: str = SCOPE,
        acr_values: str | None = None,
    ) -> None:
        """Try token refresh, fall back to full re-login for a single API client."""
        if api.refresh_token:
            try:
                await self.hass.async_add_executor_job(api.refresh_tokens, api.refresh_token)
                return
            except Exception:
                _LOGGER.debug("Refresh token failed for %s, doing full re-login", api.client_id)

        # Full re-login
        try:
            await self.hass.async_add_executor_job(
                api.login,
                self._email,
                self._password,
                scope,
                acr_values,
            )
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise ConfigEntryAuthFailed(
                "Re-login failed. Please reconfigure the integration."
            ) from err

    async def _fetch_data(self) -> dict:
        """Fetch vehicles, telematics, and PCCS data (blocking, run in executor)."""

        def _do_fetch() -> dict:
            vehicles = self.api.get_vehicles()
            if not vehicles:
                return {
                    "vehicles": [],
                    "battery": {},
                    "odometer": {},
                    "target_soc": {},
                    "charge_timer": {},
                    "climate": {},
                    "cep_battery": {},
                }

            vins = [v["vin"] for v in vehicles]
            telematics = self.api.get_telematics(vins)

            battery_by_vin: dict = {}
            for b in telematics.get("battery", []) or []:
                if b:
                    battery_by_vin[b["vin"]] = b

            odometer_by_vin: dict = {}
            for o in telematics.get("odometer", []) or []:
                if o:
                    odometer_by_vin[o["vin"]] = o

            # Fetch PCCS data (charge target + timer) per VIN
            target_soc_by_vin: dict = {}
            charge_timer_by_vin: dict = {}
            for vin in vins:
                try:
                    target_soc_by_vin[vin] = self.pccs.get_target_soc(vin)
                except Exception:
                    _LOGGER.debug("Failed to fetch PCCS target SOC for %s", vin)
                try:
                    charge_timer_by_vin[vin] = self.pccs.get_global_charge_timer(vin)
                except Exception:
                    _LOGGER.debug("Failed to fetch PCCS charge timer for %s", vin)

            # Fetch CEP data (climate status + battery) per VIN
            climate_by_vin: dict = {}
            cep_battery_by_vin: dict = {}
            for vin in vins:
                try:
                    climate_by_vin[vin] = self.cep.get_parking_climatization(vin)
                except Exception:
                    _LOGGER.debug("Failed to fetch CEP climate for %s", vin)
                try:
                    cep_battery_by_vin[vin] = self.cep.get_battery(vin)
                except Exception:
                    _LOGGER.debug("Failed to fetch CEP battery for %s", vin)

            return {
                "vehicles": vehicles,
                "battery": battery_by_vin,
                "odometer": odometer_by_vin,
                "target_soc": target_soc_by_vin,
                "charge_timer": charge_timer_by_vin,
                "climate": climate_by_vin,
                "cep_battery": cep_battery_by_vin,
            }

        return await self.hass.async_add_executor_job(_do_fetch)

    def _update_stored_tokens(self) -> None:
        """Persist refreshed tokens in config entry."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                "access_token": self.api.access_token,
                "refresh_token": self.api.refresh_token,
                "pccs_access_token": self._pccs_api.access_token,
                "pccs_refresh_token": self._pccs_api.refresh_token,
            },
        )
        # Keep gRPC client tokens in sync (both use web token)
        self.pccs.access_token = self.api.access_token or ""
        self.cep.access_token = self.api.access_token or ""

    def close(self) -> None:
        """Close gRPC channels."""
        self.pccs.close()
        self.cep.close()

    @staticmethod
    def format_charging_status(status: str | None) -> str:
        """Convert API charging status to human-readable string."""
        if not status:
            return "Unknown"
        return CHARGING_STATUS_MAP.get(
            status,
            status.replace("CHARGING_STATUS_", "").replace("_", " ").title(),
        )
