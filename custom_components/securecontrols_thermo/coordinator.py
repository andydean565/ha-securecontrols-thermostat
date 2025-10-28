from __future__ import annotations
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.core import HomeAssistant
from .const import DOMAIN, UPDATE_INTERVAL_SECS

class ThermoCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, client, device_id: str):
        super().__init__(
            hass,
            logger=hass.helpers.logger.logging.getLogger(DOMAIN),
            name=f"{DOMAIN}_{device_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECS),
        )
        self.client = client
        self.device_id = device_id

    async def _async_update_data(self):
        return await self.client.get_state(self.device_id)
