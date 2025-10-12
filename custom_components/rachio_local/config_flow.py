"""Config flow for Rachio Local integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientResponseError
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    DEFAULT_NAME,
    API_BASE_URL,
    CLOUD_BASE_URL,
)
from .auth import RachioAuth

_LOGGER = logging.getLogger(__name__)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    auth = RachioAuth(data[CONF_API_KEY])
    try:
        user_info = await auth.async_get_user_info()
        return {"title": f"Rachio ({user_info.get('username', DEFAULT_NAME)})"}
    except ClientResponseError as err:
        if err.status == 429:
            _LOGGER.error("Rate limited by Rachio API - please wait a few minutes and try again")
            raise Exception("rate_limit") from err
        raise
    except Exception as err:
        _LOGGER.error("Failed to connect to Rachio: %s", err)
        raise

class RachioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rachio Local."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(title=info["title"], data=user_input)
            except Exception as err:
                if str(err) == "rate_limit":
                    errors["base"] = "rate_limit"
                else:
                    errors["base"] = "cannot_connect"
                _LOGGER.exception("API key validation failed for Rachio connection")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
            }),
            errors=errors,
        )
