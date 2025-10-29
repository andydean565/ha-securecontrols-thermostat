from __future__ import annotations

from typing import Any, Optional

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
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
    """Secure thermostat that is single-mode (heat only) and auto-activates heat when target > ambient."""

    # writable: target temp + preset; hvac mode is NOT writable
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    _attr_hvac_modes = [HVACMode.HEAT]  # single fixed mode
    _attr_preset_modes = [PRESET_NONE, "away", "home"]
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

    # ---------- Device info ----------

    @property
    def device_info(self) -> DeviceInfo:
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

    # ---------- State (read-only HVAC mode, derived HVAC action) ----------

    @property
    def hvac_mode(self) -> HVACMode:
        # Device only supports heat; actual firing state is exposed via hvac_action.
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        s = self.coordinator.data or {}
        # coordinator exposes "hvac": 0 = idle, 1 = actively heating
        hvac_val = s.get("hvac")
        if hvac_val == 1:
            return HVACAction.HEATING
        if hvac_val == 0:
            return HVACAction.IDLE
        return None  # unknown during startup

    @property
    def current_temperature(self) -> Optional[float]:
        s = self.coordinator.data or {}
        return s.get("ambient_c")

    @property
    def target_temperature(self) -> Optional[float]:
        s = self.coordinator.data or {}
        return s.get("target_c")

    @property
    def current_humidity(self) -> Optional[float]:
        s = self.coordinator.data or {}
        return s.get("humidity")

    @property
    def preset_mode(self) -> str:
        s = self.coordinator.data or {}
        # coordinator provides "preset": "away" | "home" | None
        return s.get("preset") or PRESET_NONE

    # ---------- Commands (no set_hvac_mode) ----------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TEMPERATURE in kwargs:
            target = float(kwargs[ATTR_TEMPERATURE])
            await self.client.set_target_temp(target)
            await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the thermostat preset (away/home)."""
        if preset_mode not in self._attr_preset_modes:
            return
        if preset_mode == PRESET_NONE:
            # Treat "none" as normal/home (adjust if your API supports a real 'none')
            await self.client.set_preset("home")
        else:
            await self.client.set_preset(preset_mode)
        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        await self.coordinator.async_request_refresh()
