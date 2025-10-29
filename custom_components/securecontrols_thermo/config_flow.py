from __future__ import annotations

from typing import Any, Dict, List, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_GATEWAY_GMI  # add CONF_GATEWAY if you have it
from .api import SecureControlsClient


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Secure Controls Thermostat."""
    VERSION = 1

    def __init__(self) -> None:
        self._client: Optional[SecureControlsClient] = None
        self._gateways: List[Dict[str, Any]] = []
        self._email: Optional[str] = None
        self._password: Optional[str] = None

    # --------------------------
    # Step 1: credentials
    # --------------------------
    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            try:
                self._client = SecureControlsClient(async_get_clientsession(self.hass))
                await self._client.login(self._email, self._password)

                # Prefer a list of gateways if exposed by the client
                raw_gateways = getattr(self._client, "gateways", None)

                gateways: List[Dict[str, Any]] = []
                if isinstance(raw_gateways, list) and raw_gateways:
                    for gw in raw_gateways:
                        # Support both dicts and dataclass-like objects
                        gmi = str(getattr(gw, "GMI", getattr(gw, "gmi", gw.get("GMI"))))
                        sn = str(getattr(gw, "SN", getattr(gw, "sn", gw.get("SN", ""))))
                        hn = str(getattr(gw, "HN", getattr(gw, "hn", gw.get("HN", ""))))
                        gateways.append({"GMI": gmi, "SN": sn, "HN": hn})
                else:
                    # Fallback: single thermostat selected inside client
                    t = getattr(self._client, "thermostat", None)
                    if t is None:
                        raise RuntimeError("No thermostats available")
                    gateways.append({"GMI": str(t.gmi), "SN": str(t.sn), "HN": str(t.hn)})

                if not gateways:
                    return self.async_abort(reason="no_devices")

                self._gateways = gateways

                # If only one gateway, skip selection UI
                if len(self._gateways) == 1:
                    chosen = self._gateways[0]
                    return await self._create_entry_with_gateway(chosen["GMI"])
                else:
                    return await self.async_step_select_gateway()

            except Exception:
                errors["base"] = "auth"

        # Show credentials form
        schema = vol.Schema({
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # --------------------------
    # Step 2: gateway selection
    # --------------------------
    async def async_step_select_gateway(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        assert self._gateways, "Gateways must be populated from async_step_user"

        errors: Dict[str, str] = {}
        if user_input is not None:
            gmi = user_input[CONF_GATEWAY_GMI]
            return await self._create_entry_with_gateway(gmi)

        # Build nice labels for dropdown: "SN (GMI)" or "HN"
        options = {
            gw["GMI"]: f'{gw.get("SN") or gw.get("HN") or "Thermostat"} ({gw["GMI"]})'
            for gw in self._gateways
        }
        schema = vol.Schema({
            vol.Required(CONF_GATEWAY_GMI): vol.In(options),
        })
        return self.async_show_form(step_id="select_gateway", data_schema=schema, errors=errors)

    # --------------------------
    # Helpers
    # --------------------------
    async def _create_entry_with_gateway(self, gmi: str) -> FlowResult:
        """Create the config entry and set unique_id to GMI so duplicates are prevented."""
        # Avoid duplicate entries for the same physical unit
        await self.async_set_unique_id(gmi)
        self._abort_if_unique_id_configured()

        # Title: prefer SN/HN if available
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
