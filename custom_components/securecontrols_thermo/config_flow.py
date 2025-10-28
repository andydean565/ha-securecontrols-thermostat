from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD
from .api import SecureControlsClient

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            try:
                client = SecureControlsClient(async_get_clientsession(self.hass))
                await client.login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
                devices = await client.list_devices()
                tstats = [d for d in devices if d.get("type") in {"thermostat","ptd","c1727","h3747"} or True]
                if not tstats:
                    return self.async_abort(reason="no_devices")
                chosen_ids = [d.get("id","unknown-id") for d in tstats]
                return self.async_create_entry(
                    title="Secure Controls Thermostat",
                    data={"email": user_input[CONF_EMAIL], "password": user_input[CONF_PASSWORD], "devices": chosen_ids},
                )
            except Exception:
                errors["base"] = "auth"

        schema = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
