from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.components.diagnostics import async_redact_data

from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_GATEWAY_GMI

# Redact sensitive fields
TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "jwt",
    "session_id",
}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
):
    """Return diagnostics for a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    client = data.get("client")
    thermo = getattr(client, "thermostat", None)

    diagnostics: dict[str, object] = {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "has_client": bool(client),
    }

    if thermo:
        diagnostics["thermostat"] = {
            "gmi": thermo.gmi,
            "sn": thermo.sn,
            "hn": thermo.hn,
            "cs": thermo.cs,
            "ur": thermo.ur,
            "hi": thermo.hi,
            "dt": thermo.dt,
            "dn": thermo.dn,
        }

    # Optional: include cached websocket state
    if client:
        diagnostics["session"] = {
            "connected": bool(client._ws and not client._ws.closed),
            "jwt_present": bool(client._jwt),
            "session_id": client._session_id,
            "last_tick": getattr(client, "_session_ts", None),
        }

    return diagnostics
