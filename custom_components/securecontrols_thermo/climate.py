from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACMode,
    ClimateEntityFeature,
    PRESET_NONE,
)
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_GATEWAY_GMI
from .coordinator import ThermoCoordinator


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator: ThermoCoordinator = data["coordinator"]  # created in __init__.py
    gmi: str = entry.data[CONF_GATEWAY_GMI]

    entity = SecureThermostatEntity(coordinator, client, gmi)
    async_add_entities([entity], update_before_add=True)


class SecureThermostatEntity(CoordinatorEntity[ThermoCoordinator], ClimateEntity):
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5
    _attr_has_entity_name = True
    _attr_name = "Thermostat"

    def __init__(self, coordinator: ThermoCoordinator, client, gmi: str) -> None:
        super().__init__(coordinator)
        self.client = client
        self._gmi = gmi
        self._attr_unique_id = f"{gmi}_climate"

    # ------------- Device/identity -------------

    @property
    def device_info(self) -> DeviceInfo:
        # Pull whatever we can from coordinator/client for nicer labels
        ther = getattr(self.client, "thermostat", None)
        model = "Thermostat"
        name = "Secure Thermostat"
        sn = None
        hn = None
        if ther:
            sn = getattr(ther, "sn", None)
            hn = getattr(ther, "hn", None)
            name = hn or sn or name
        return DeviceInfo(
            identifiers={(DOMAIN, self._gmi)},
            manufacturer="Secure Meters",
            model=model,
            name=name,
            serial_number=sn,
        )

    # ------------- State -------------

    @property
    def hvac_mode(self) -> HVACMode:
        s = self.coordinator.data or {}
        # coordinator maps "power" 0=Off, 2=On
        return HVACMode.OFF if s.get("power") in (0, None) else HVACMode.HEAT

    @property
    def current_temperature(self) -> Optional[float]:
        s = self.coordinator.data or {}
        return s.get("ambient_c")

    @property
    def target_humidity(self) -> Optional[float]:
        s = self.coordinator.data or {}
        return s.get("humidity")

    @property
    def preset_mode(self) -> str:
        # Not implementing presets yet; expose "none" to keep UX simple
        return PRESET_NONE

    # ------------- Commands -------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TEMPERATURE in kwargs:
            target = float(kwargs[ATTR_TEMPERATURE])
            await self.client.set_target_temp(target)
            # Push updates will arrive via WS; still request a refresh as a fallback
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        on = hvac_mode == HVACMode.HEAT
        await self.client.set_mode(on)
        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        # Let the coordinator drive updates
        await self.coordinator.async_request_refresh()
