from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

# Redact sensitive fields
TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "jwt",
    "session_id",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry):
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

    # Include connection state without exposing session identifiers.
    if client:
        diagnostics["session"] = {
            "connection_mode": "short_lived_polling",
            "connected": bool(client._ws and not client._ws.closed),
            "jwt_present": bool(client._jwt),
            "auth_rejected": bool(getattr(client, "_auth_rejected", False)),
            "login_timestamp": getattr(client, "_session_ts", None),
        }

    return diagnostics
