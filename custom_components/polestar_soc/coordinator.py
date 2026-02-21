"""DataUpdateCoordinator and API client for Polestar."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from urllib.parse import parse_qs, urlparse

import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_URL,
    CHARGING_STATUS_MAP,
    CLIENT_ID,
    DOMAIN,
    OIDC_AUTH_URL,
    OIDC_BASE_URL,
    OIDC_TOKEN_URL,
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

    def __init__(self) -> None:
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    def login(self, email: str, password: str) -> dict:
        """Perform full OAuth2 Authorization Code + PKCE login."""
        code_verifier = _b64urlencode(os.urandom(32))
        code_challenge = _b64urlencode(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        state = _b64urlencode(os.urandom(12))

        session = requests.Session()

        # Step 1: Authorization request
        resp = session.get(
            OIDC_AUTH_URL,
            params={
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "state": state,
                "scope": SCOPE,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
            allow_redirects=True,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()

        # Step 2: Extract resume path
        match = re.search(
            r'(/as/[^/]+/resume/as/authorization\.ping)', resp.text
        )
        if not match:
            raise UpdateFailed("Could not find login form endpoint")

        resume_url = OIDC_BASE_URL + match.group(1)

        # Step 3: Submit credentials
        resp = session.post(
            resume_url,
            data={
                "pf.username": email,
                "pf.pass": password,
                "client_id": CLIENT_ID,
            },
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )

        if resp.status_code not in (302, 303):
            if "ERR001" in resp.text or "authMessage" in resp.text:
                raise ConfigEntryAuthFailed("Invalid email or password")
            raise UpdateFailed(
                f"Unexpected login response ({resp.status_code})"
            )

        redirect_url = resp.headers.get("Location", "")
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)

        # Step 3a: Handle consent/confirmation
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
            resp = session.get(
                redirect_url, allow_redirects=False, timeout=HTTP_TIMEOUT
            )
            if resp.status_code in (302, 303):
                redirect_url = resp.headers.get("Location", "")
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)

        if "code" not in params:
            raise UpdateFailed("No authorization code received")

        auth_code = params["code"][0]

        # Step 4: Exchange code for tokens
        resp = session.post(
            OIDC_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "code_verifier": code_verifier,
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
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

    def refresh_tokens(self, refresh_token: str) -> dict:
        """Refresh the access token using a refresh token."""
        resp = requests.post(
            OIDC_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
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

        resp = requests.post(
            API_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT
        )
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
        self.pccs = PccsClient(self.api.access_token or "")
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
        """Try token refresh, fall back to full re-login."""
        if self.api.refresh_token:
            try:
                await self.hass.async_add_executor_job(
                    self.api.refresh_tokens, self.api.refresh_token
                )
                self._update_stored_tokens()
                return
            except Exception:
                _LOGGER.debug("Refresh token failed, doing full re-login")

        # Full re-login
        try:
            await self.hass.async_add_executor_job(
                self.api.login, self._email, self._password
            )
            self._update_stored_tokens()
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

            return {
                "vehicles": vehicles,
                "battery": battery_by_vin,
                "odometer": odometer_by_vin,
                "target_soc": target_soc_by_vin,
                "charge_timer": charge_timer_by_vin,
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
            },
        )
        # Keep PCCS client token in sync
        self.pccs.access_token = self.api.access_token or ""

    @staticmethod
    def format_charging_status(status: str | None) -> str:
        """Convert API charging status to human-readable string."""
        if not status:
            return "Unknown"
        return CHARGING_STATUS_MAP.get(
            status,
            status.replace("CHARGING_STATUS_", "").replace("_", " ").title(),
        )
