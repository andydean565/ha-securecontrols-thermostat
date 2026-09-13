# ruff: noqa: PLR2004

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.securecontrols_thermostat.api import InvalidAuth
from custom_components.securecontrols_thermostat.const import UPDATE_INTERVAL_SECS
from custom_components.securecontrols_thermostat.coordinator import ThermoCoordinator


@pytest.mark.asyncio
async def test_update_fetches_one_polled_snapshot_without_starting_background_tasks():
    raw = {
        "V": [
            {
                "SI": 15,
                "V": [
                    {"I": 1, "V": 215},
                    {"I": 2, "V": 201},
                    {"I": 3, "V": 1},
                ],
            }
        ]
    }
    coordinator = object.__new__(ThermoCoordinator)
    coordinator.client = AsyncMock()
    coordinator.client.state_read.return_value = raw
    coordinator._state_cache = {}

    result = await coordinator._async_update_data()

    coordinator.client.state_read.assert_awaited_once_with()
    assert result["target_c"] == 21.5
    assert result["ambient_c"] == 20.1
    assert result["hvac"] == 1
    assert not hasattr(coordinator, "_ws_started")


def test_polling_interval_remains_45_seconds():
    assert timedelta(seconds=UPDATE_INTERVAL_SECS) == timedelta(seconds=45)


@pytest.mark.asyncio
async def test_auth_rejection_stops_coordinator_polling():
    coordinator = object.__new__(ThermoCoordinator)
    coordinator.client = AsyncMock()
    coordinator.client.state_read.side_effect = InvalidAuth("session rejected")
    coordinator._state_cache = {}

    with pytest.raises(ConfigEntryAuthFailed, match="session rejected"):
        await coordinator._async_update_data()
