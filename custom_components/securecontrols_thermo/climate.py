from __future__ import annotations
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode, ClimateEntityFeature, PRESET_AWAY, PRESET_NONE
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
from .coordinator import ThermoCoordinator

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    entities = []
    for dev_id in data["devices"]:
        coord = ThermoCoordinator(hass, client, dev_id)
        await coord.async_config_entry_first_refresh()
        entities.append(SecureThermostatEntity(coord, dev_id))
    async_add_entities(entities, update_before_add=True)

class SecureThermostatEntity(ClimateEntity):
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, PRESET_AWAY]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: ThermoCoordinator, device_id: str) -> None:
        self.coordinator = coordinator
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_climate"

    @property
    def device_info(self) -> DeviceInfo:
        s = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Secure Meters",
            model=s.get("model","Thermostat"),
            name=s.get("name","Secure Thermostat"),
        )

    @property
    def hvac_mode(self):
        s = self.coordinator.data or {}
        return HVACMode.HEAT if s.get("heating_enabled", True) else HVACMode.OFF

    @property
    def current_temperature(self):
        s = self.coordinator.data or {}
        return s.get("ambient_c")

    @property
    def target_temperature(self):
        s = self.coordinator.data or {}
        return s.get("target_c")

    @property
    def preset_mode(self):
        s = self.coordinator.data or {}
        return s.get("preset", PRESET_NONE)

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE in kwargs:
            await self.coordinator.client.set_target_temp(self._device_id, float(kwargs[ATTR_TEMPERATURE]))
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode):
        await self.coordinator.client.set_mode(self._device_id, "heat" if hvac_mode == HVACMode.HEAT else "off")
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str):
        await self.coordinator.client.set_preset(self._device_id, preset_mode)
        await self.coordinator.async_request_refresh()

    async def async_update(self):
        await self.coordinator.async_request_refresh()
