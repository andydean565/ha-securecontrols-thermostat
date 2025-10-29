from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PLATFORMS, CONF_EMAIL, CONF_PASSWORD, CONF_GATEWAY_GMI
from .api import SecureControlsClient
from .coordinator import ThermoCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Secure Controls Thermostat from a config entry."""
    session = async_get_clientsession(hass)
    client = SecureControlsClient(session)

    email = entry.data[CONF_EMAIL].strip()
    password = entry.data[CONF_PASSWORD]
    gmi = entry.data.get(CONF_GATEWAY_GMI)  # optional for logging/use later

    # Login first; raise proper error so HA can retry if cloud is down
    try:
        await client.login(email, password)
    except Exception as err:
        # ConfigEntryNotReady triggers HA to retry setup later
        raise ConfigEntryNotReady(f"Login failed: {err}") from err

    # Create a single shared coordinator (will open WS on first refresh)
    coordinator = ThermoCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    # Stash objects for platforms
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "gmi": gmi,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
        client: SecureControlsClient | None = data.get("client")
        # Close WebSocket if open
        try:
            if client is not None:
                await client.disconnect()
        except Exception:
            # Swallow; we're shutting down/unloading
            pass
    return unload_ok
