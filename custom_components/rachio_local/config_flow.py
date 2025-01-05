# custom_components/rachio_local/config_flow.py
"""Config flow for Rachio Local Control."""
import voluptuous as vol
import requests
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, CONF_API_KEY, RACHIO_API_URL

class RachioLocalFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Rachio Local config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            
            # Validate the API key by making a test request
            try:
                headers = {"Authorization": f"Bearer {api_key}"}
                response = await self.hass.async_add_executor_job(
                    lambda: requests.get(f"{RACHIO_API_URL}/person/info", headers=headers)
                )
                response.raise_for_status()
                
                return self.async_create_entry(
                    title="Rachio Local Control",
                    data={CONF_API_KEY: api_key}
                )
            except requests.exceptions.RequestException:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
            }),
            errors=errors,
        )
