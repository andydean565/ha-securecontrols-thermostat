from __future__ import annotations

from datetime import timedelta, datetime
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_GATEWAY_GMI
from .coordinator import ThermoCoordinator


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Secure Controls sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator: ThermoCoordinator = data["coordinator"]
    gmi: str = entry.data.get(CONF_GATEWAY_GMI) or getattr(getattr(client, "thermostat", None), "gmi", "unknown")

    entities: list[SensorEntity] = [
        NextChangeTimeSensor(coordinator, client, gmi),
        NextTargetTempSensor(coordinator, client, gmi),
    ]
    async_add_entities(entities, update_before_add=True)


class _BaseSecureSensor(CoordinatorEntity[ThermoCoordinator], SensorEntity):
    """Shared bits for device info & identity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ThermoCoordinator, client, gmi: str) -> None:
        super().__init__(coordinator)
        self.client = client
        self._gmi = gmi

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


class NextChangeTimeSensor(_BaseSecureSensor):
    """Timestamp of the next scheduled change (now + next_change_mins)."""

    _attr_name = "Next Schedule Change"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: ThermoCoordinator, client, gmi: str) -> None:
        super().__init__(coordinator, client, gmi)
        self._attr_unique_id = f"{gmi}_next_change"

    @property
    def native_value(self) -> Optional[datetime]:
        """Return an aware UTC datetime for the next change."""
        s = self.coordinator.data or {}
        mins = s.get("next_change_mins")
        if mins is None:
            return None
        try:
            return dt_util.utcnow() + timedelta(minutes=int(mins))
        except Exception:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.data or {}
        return {
            "next_change_mins": s.get("next_change_mins"),
            "next_target_c": s.get("next_target_c"),
        }


class NextTargetTempSensor(_BaseSecureSensor):
    """The next scheduled target temperature (°C)."""

    _attr_name = "Next Target Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ThermoCoordinator, client, gmi: str) -> None:
        super().__init__(coordinator, client, gmi)
        self._attr_unique_id = f"{gmi}_next_target_c"

    @property
    def native_value(self) -> Optional[float]:
        s = self.coordinator.data or {}
        val = s.get("next_target_c")
        return None if val is None else float(val)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.data or {}
        return {
            "next_change_mins": s.get("next_change_mins"),
        }
