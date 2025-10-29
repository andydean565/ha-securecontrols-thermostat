# config_flow.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_GATEWAY_GMI
from .api import SecureControlsClient, ApiError, InvalidAuth, CannotConnect

_LOGGER = logging.getLogger(__name__)

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._client: Optional[SecureControlsClient] = None
        self._email: Optional[str] = None
        self._password: Optional[str] = None
        self._gateways: List[Dict[str, str]] = []

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            try:
                self._client = SecureControlsClient(async_get_clientsession(self.hass))
                await self._client.login(self._email, self._password)

                # Prefer list from client.gateways if present; otherwise use selected thermostat
                gateways = []
                raw = getattr(self._client, "gateways", None)
                if isinstance(raw, list) and raw:
                    for gw in raw:
                        gmi = str(getattr(gw, "GMI", getattr(gw, "gmi", gw.get("GMI"))))
                        sn = str(getattr(gw, "SN", getattr(gw, "sn", gw.get("SN", ""))))
                        hn = str(getattr(gw, "HN", getattr(gw, "hn", gw.get("HN", ""))))
                        gateways.append({"GMI": gmi, "SN": sn, "HN": hn})
                else:
                    t = getattr(self._client, "thermostat", None)
                    if t:
                        gateways.append({"GMI": str(t.gmi), "SN": str(t.sn), "HN": str(t.hn)})

                if not gateways:
                    return self.async_abort(reason="no_devices")

                self._gateways = gateways

                # Single device? Create immediately.
                if len(gateways) == 1:
                    return await self._create_entry_with_gateway(gateways[0]["GMI"])
                # Multiple → go to selection
                return await self.async_step_select_gateway()

            except InvalidAuth as e:
                _LOGGER.warning("SecureControls auth failed: %s", e)
                errors["base"] = "invalid_auth"
            except CannotConnect as e:
                _LOGGER.error("SecureControls cannot connect: %s", e)
                errors["base"] = "cannot_connect"
            except ApiError as e:
                _LOGGER.error("SecureControls API error: %s", e)
                errors["base"] = "unknown"
            except Exception as e:
                _LOGGER.exception("Unexpected error during SecureControls login")
                errors["base"] = "unknown"

        schema = vol.Schema({
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select_gateway(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}

        if user_input is not None:
            gmi = str(user_input[CONF_GATEWAY_GMI])
            return await self._create_entry_with_gateway(gmi)

        choices = {
            gw["GMI"]: f'{gw.get("SN") or gw.get("HN") or "Thermostat"} ({gw["GMI"]})'
            for gw in self._gateways
        }
        schema = vol.Schema({
            vol.Required(CONF_GATEWAY_GMI): vol.In(choices)
        })
        return self.async_show_form(step_id="select_gateway", data_schema=schema, errors=errors)

    async def _create_entry_with_gateway(self, gmi: str) -> FlowResult:
        await self.async_set_unique_id(gmi)
        self._abort_if_unique_id_configured()

        title = "Secure Controls Thermostat"
        match = next((gw for gw in self._gateways if gw["GMI"] == gmi), None)
        if match:
            label = match.get("SN") or match.get("HN")
            if label:
                title = f"{title} ({label})"

        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            CONF_GATEWAY_GMI: str(gmi),
        }
        return self.async_create_entry(title=title, data=data)
