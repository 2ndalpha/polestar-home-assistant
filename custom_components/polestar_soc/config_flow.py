"""Config flow for Polestar State of Charge."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN
from .coordinator import PolestarAPI

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)


class PolestarSOCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Polestar SOC."""

    VERSION = 1

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
                        return self.async_create_entry(
                            title=email,
                            data={
                                "email": email,
                                "password": password,
                                "access_token": tokens["access_token"],
                                "refresh_token": tokens.get("refresh_token"),
                            },
                        )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

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
                entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            "email": email,
                            "password": password,
                            "access_token": tokens["access_token"],
                            "refresh_token": tokens.get("refresh_token"),
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
