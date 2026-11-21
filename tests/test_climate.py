# ruff: noqa: PLR2004
import importlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from custom_components.securecontrols_thermostat import climate


def test_climate_entity_uses_no_deprecated_temperature_api(caplog):
    with caplog.at_level(logging.WARNING):
        importlib.reload(climate)
    assert not [r for r in caplog.records if "deprecated" in r.getMessage()]


def _entity(data):
    coordinator = SimpleNamespace(data=data, async_request_refresh=AsyncMock())
    client = SimpleNamespace(set_target_temp=AsyncMock(), thermostat=None)
    entity = climate.SecureThermostatEntity.__new__(climate.SecureThermostatEntity)
    entity.coordinator = coordinator
    entity.client = client
    return entity


def test_native_temperatures_come_from_coordinator():
    entity = _entity({"ambient_c": 19.5, "target_c": 20.0})
    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.native_current_temperature == 19.5
    assert entity.native_target_temperature == 20.0


@pytest.mark.asyncio
async def test_set_hvac_mode_offsets_from_current_temperature():
    entity = _entity({"ambient_c": 19.5})
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    entity.client.set_target_temp.assert_awaited_once_with(21.5)
    entity.coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_hvac_mode_without_temperature_raises():
    entity = _entity({})
    with pytest.raises(HomeAssistantError):
        await entity.async_set_hvac_mode(HVACMode.HEAT)
    entity.client.set_target_temp.assert_not_awaited()
