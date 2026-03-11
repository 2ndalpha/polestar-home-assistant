"""Config flow for Polestar State of Charge."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN, PCCS_ACR_VALUES, PCCS_CLIENT_ID, PCCS_REDIRECT_URI, PCCS_SCOPE
from .coordinator import PolestarAPI

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)

STEP_OTP_SCHEMA = vol.Schema(
    {
        vol.Optional("otp", default=""): str,
    }
)


class PolestarSOCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Polestar SOC."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str = ""
        self._password: str = ""
        self._web_tokens: dict = {}
        self._pccs_session_state: dict | None = None
        self._reauth_entry: str | None = None

    # -- Step: user (email + password) ---------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input["email"]
            password = user_input["password"]

            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            api = PolestarAPI()
            try:
                tokens = await self.hass.async_add_executor_job(api.login, email, password)
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "cannot_connect"
            else:
                # Validate we can fetch vehicles
                try:
                    vehicles = await self.hass.async_add_executor_job(api.get_vehicles)
                except Exception:
                    _LOGGER.exception("Failed to fetch vehicles")
                    errors["base"] = "cannot_connect"
                else:
                    if not vehicles:
                        errors["base"] = "no_vehicles"
                    else:
                        # Web login succeeded — initiate PCCS 2FA
                        self._email = email
                        self._password = password
                        self._web_tokens = tokens
                        return await self._initiate_pccs_2fa(email, password)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # -- Step: OTP -----------------------------------------------------------

    async def async_step_otp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the OTP verification step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            otp_code = user_input.get("otp", "").strip()

            pccs_tokens: dict = {}
            if otp_code and self._pccs_session_state:
                # User provided OTP — complete the PCCS login
                pccs_api = PolestarAPI(
                    client_id=PCCS_CLIENT_ID,
                    redirect_uri=PCCS_REDIRECT_URI,
                )
                try:
                    pccs_tokens = await self.hass.async_add_executor_job(
                        pccs_api.login_complete_2fa,
                        self._pccs_session_state,
                        otp_code,
                    )
                except Exception:
                    _LOGGER.debug("OTP verification failed", exc_info=True)
                    errors["base"] = "invalid_otp"

            if not errors:
                return await self._finish_setup(pccs_tokens)

        return self.async_show_form(
            step_id="otp",
            data_schema=STEP_OTP_SCHEMA,
            errors=errors,
        )

    # -- Step: reauth --------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input["email"]
            password = user_input["password"]

            api = PolestarAPI()
            try:
                tokens = await self.hass.async_add_executor_job(api.login, email, password)
            except ConfigEntryAuthFailed:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during re-auth")
                errors["base"] = "cannot_connect"
            else:
                # Web login succeeded — initiate PCCS 2FA
                self._email = email
                self._password = password
                self._web_tokens = tokens
                self._reauth_entry = self.context["entry_id"]
                return await self._initiate_pccs_2fa(email, password)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # -- Private helpers -----------------------------------------------------

    async def _initiate_pccs_2fa(self, email: str, password: str) -> ConfigFlowResult:
        """Trigger the PCCS 2FA email and proceed to the OTP step."""
        pccs_api = PolestarAPI(
            client_id=PCCS_CLIENT_ID,
            redirect_uri=PCCS_REDIRECT_URI,
        )
        try:
            result = await self.hass.async_add_executor_job(
                pccs_api.login_start_2fa,
                email,
                password,
                PCCS_SCOPE,
                PCCS_ACR_VALUES,
            )
        except Exception:
            _LOGGER.warning("Failed to initiate PCCS 2FA login", exc_info=True)
            # PCCS setup failed — continue without it
            self._pccs_session_state = None
            return await self.async_step_otp()

        if result.get("needs_otp"):
            self._pccs_session_state = result["_session_state"]
        else:
            # No 2FA required — tokens returned directly, skip OTP step
            return await self._finish_setup(result)

        return await self.async_step_otp()

    async def _finish_setup(self, pccs_tokens: dict) -> ConfigFlowResult:
        """Create or update the config entry with web + PCCS tokens."""
        entry_data = {
            "email": self._email,
            "password": self._password,
            "access_token": self._web_tokens["access_token"],
            "refresh_token": self._web_tokens.get("refresh_token"),
            "pccs_access_token": pccs_tokens.get("access_token"),
            "pccs_refresh_token": pccs_tokens.get("refresh_token"),
        }

        if self._reauth_entry:
            # Reauth — update existing entry
            entry = self.hass.config_entries.async_get_entry(self._reauth_entry)
            if not entry:
                return self.async_abort(reason="reauth_successful")
            self.hass.config_entries.async_update_entry(entry, data=entry_data)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(title=self._email, data=entry_data)
