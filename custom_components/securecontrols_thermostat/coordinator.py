from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import InvalidAuth
from .const import DOMAIN, UPDATE_INTERVAL_SECS

_LOGGER = logging.getLogger(__name__)

# Item map for block SI:15
ITEM_TARGET = 1  # target_c
ITEM_AMBIENT = 2  # ambient_c (probe)
ITEM_HVAC = 3  # hvac (0 = off, 1 = heat)
ITEM_PRESET = 6  # presets (1 = away, 2 = home)
ITEM_HUMID = 8  # humidity
ITEM_NEXT_TIME = 9  # next scheduled time (mins)
ITEM_NEXT_VALUE = 10  # next scheduled target temp
ITEM_FROST = 11  # frost_c
THERMOSTAT_STATE_BLOCK = 15
MIN_VALID_DECI_TEMP = -500
MAX_VALID_DECI_TEMP = 5000
PRESET_AWAY = 1
PRESET_HOME = 2


class ThermoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls through short-lived WebSocket transactions."""

    def __init__(self, hass: HomeAssistant, client) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,  # <-- use a standard logger
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECS),
        )
        self.client = client
        self._state_cache: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll path: fetch a full snapshot with state_read()."""
        try:
            raw = await self.client.state_read()
        except InvalidAuth as err:
            # Tell Home Assistant not to schedule further coordinator polls.
            raise ConfigEntryAuthFailed(str(err)) from err
        parsed = self._parse_state_read(raw)
        self._state_cache = parsed
        return parsed

    # ---------- parsing helpers ----------

    def _parse_state_read(self, r: Any) -> dict[str, Any]:
        """
        Normalize 3/1 state.read() into a simple dict for entities.
        Shape of R: {"V":[{"I":<slot>,"SI":<block>,"V":[{I,V,OT,D},...],"S":0},...]}
        """
        state: dict[str, Any] = {
            # previously "power" (0 Off / 2 On); now accurate HVAc flag (0 Off / 1 Heat)
            "hvac": None,  # int: 0=off, 1=heat
            "preset": None,  # str: "away" | "home"
            "target_c": None,  # float
            "ambient_c": None,  # float
            "humidity": None,  # %
            "next_change_mins": None,  # int minutes
            "next_target_c": None,  # float
            "frost_c": None,  # float
        }

        if not isinstance(r, dict):
            return state
        vec = r.get("V")
        if not isinstance(vec, list):
            return state

        for block in vec:
            if not isinstance(block, dict) or block.get("SI") != THERMOSTAT_STATE_BLOCK:
                continue
            for it in block.get("V", []) or []:
                if not isinstance(it, dict):
                    continue
                iid = it.get("I")
                val = it.get("V")

                if iid == ITEM_HVAC:
                    state["hvac"] = self._int_or_none(val)
                elif iid == ITEM_PRESET:
                    state["preset"] = self._preset_name(self._int_or_none(val))
                elif iid == ITEM_TARGET:
                    state["target_c"] = self._deci_to_c(val)
                elif iid == ITEM_AMBIENT:
                    state["ambient_c"] = self._maybe_deci_temp(val)
                elif iid == ITEM_HUMID:
                    state["humidity"] = self._int_or_none(val)
                elif iid == ITEM_NEXT_TIME:
                    state["next_change_mins"] = self._int_or_none(val)
                elif iid == ITEM_NEXT_VALUE:
                    state["next_target_c"] = self._deci_to_c(val)
                elif iid == ITEM_FROST:
                    state["frost_c"] = self._deci_to_c(val)

        return state

    # ---------- unit & parse helpers ----------

    @staticmethod
    def _deci_to_c(v: int | None) -> float | None:
        if v is None:
            return None
        try:
            return float(v) / 10.0
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _maybe_deci_temp(v: int | None) -> float | None:
        if v is None:
            return None
        try:
            iv = int(v)
        except (TypeError, ValueError, OverflowError):
            return None
        if MIN_VALID_DECI_TEMP <= iv <= MAX_VALID_DECI_TEMP:
            return iv / 10.0
        return None

    @staticmethod
    def _int_or_none(v: Any) -> int | None:
        try:
            return int(v)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _preset_name(code: int | None) -> str | None:
        if code == PRESET_AWAY:
            return "away"
        if code == PRESET_HOME:
            return "home"
        return None
