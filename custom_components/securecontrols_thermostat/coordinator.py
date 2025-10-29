from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, UPDATE_INTERVAL_SECS

_LOGGER = logging.getLogger(__name__)

# Item map for block SI:15
ITEM_TARGET = 1           # target_c
ITEM_AMBIENT = 2          # ambient_c (probe)
ITEM_HVAC = 3             # hvac (0 = off, 1 = heat)
ITEM_PRESET = 6           # presets (1 = away, 2 = home)
ITEM_HUMID = 8            # humidity
ITEM_NEXT_TIME = 9        # next scheduled time (mins)
ITEM_NEXT_VALUE = 10      # next scheduled target temp
ITEM_FROST = 11           # frost_c


class ThermoCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator that opens WS once, polls for baseline, and applies push notifies."""

    def __init__(self, hass: HomeAssistant, client) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,  # <-- use a standard logger
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECS),
        )
        self.client = client
        self._ws_started = False
        self._state_cache: Dict[str, Any] = {}

    async def _async_update_data(self) -> Dict[str, Any]:
        """Poll path: fetch a full snapshot with state_read()."""
        if not self._ws_started:
            await self._ensure_ws_started()

        raw = await self.client.state_read()
        parsed = self._parse_state_read(raw)
        self._state_cache = parsed
        return parsed

    async def _ensure_ws_started(self) -> None:
        if self._ws_started:
            return

        await self.client.connect()

        async def _on_notify(msg: Dict[str, Any]) -> None:
            # Expect: {"M":"Notify","P":[{"SI":15,...}, [slot, {I,V,OT,D}]]}
            if msg.get("M") != "Notify":
                return
            p = msg.get("P") or []
            if len(p) != 2 or not isinstance(p[0], dict):
                return
            header, body = p
            if header.get("SI") != 15:
                return
            if not isinstance(body, list) or len(body) != 2 or not isinstance(body[1], dict):
                return

            item = body[1]
            if self._apply_notify_to_cache(item):
                self.async_set_updated_data(self._state_cache)

        self.client.on_update(_on_notify)
        self._ws_started = True

    # ---------- parsing helpers ----------

    def _parse_state_read(self, r: Any) -> Dict[str, Any]:
        """
        Normalize 3/1 state.read() into a simple dict for entities.
        Shape of R: {"V":[{"I":<slot>,"SI":<block>,"V":[{I,V,OT,D},...],"S":0},...]}
        """
        state: Dict[str, Any] = {
            # previously "power" (0 Off / 2 On); now accurate HVAc flag (0 Off / 1 Heat)
            "hvac": None,             # int: 0=off, 1=heat
            "preset": None,           # str: "away" | "home"
            "target_c": None,         # float
            "ambient_c": None,        # float
            "humidity": None,         # %
            "next_change_mins": None, # int minutes
            "next_target_c": None,    # float
            "frost_c": None,          # float
        }

        if not isinstance(r, dict):
            return state
        vec = r.get("V")
        if not isinstance(vec, list):
            return state

        for block in vec:
            if not isinstance(block, dict) or block.get("SI") != 15:
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

    def _apply_notify_to_cache(self, item: Dict[str, Any]) -> bool:
        """Merge a single item change into cache; return True if changed."""
        if not self._state_cache:
            return False
        iid = item.get("I")
        val = item.get("V")
        changed = False

        if iid == ITEM_HVAC:
            newv = self._int_or_none(val)
            changed = self._state_cache.get("hvac") != newv
            self._state_cache["hvac"] = newv
        elif iid == ITEM_PRESET:
            newv = self._preset_name(self._int_or_none(val))
            changed = self._state_cache.get("preset") != newv
            self._state_cache["preset"] = newv
        elif iid == ITEM_TARGET:
            newv = self._deci_to_c(val)
            changed = self._state_cache.get("target_c") != newv
            self._state_cache["target_c"] = newv
        elif iid == ITEM_AMBIENT:
            newv = self._maybe_deci_temp(val)
            changed = self._state_cache.get("ambient_c") != newv
            self._state_cache["ambient_c"] = newv
        elif iid == ITEM_HUMID:
            newv = self._int_or_none(val)
            changed = self._state_cache.get("humidity") != newv
            self._state_cache["humidity"] = newv
        elif iid == ITEM_NEXT_TIME:
            newv = self._int_or_none(val)
            changed = self._state_cache.get("next_change_mins") != newv
            self._state_cache["next_change_mins"] = newv
        elif iid == ITEM_NEXT_VALUE:
            newv = self._deci_to_c(val)
            changed = self._state_cache.get("next_target_c") != newv
            self._state_cache["next_target_c"] = newv
        elif iid == ITEM_FROST:
            newv = self._deci_to_c(val)
            changed = self._state_cache.get("frost_c") != newv
            self._state_cache["frost_c"] = newv

        return changed

    # ---------- unit & parse helpers ----------

    @staticmethod
    def _deci_to_c(v: Optional[int]) -> Optional[float]:
        if v is None:
            return None
        try:
            return float(v) / 10.0
        except Exception:
            return None

    @staticmethod
    def _maybe_deci_temp(v: Optional[int]) -> Optional[float]:
        if v is None:
            return None
        try:
            iv = int(v)
        except Exception:
            return None
        if -500 <= iv <= 5000:
            return iv / 10.0
        return None

    @staticmethod
    def _int_or_none(v: Any) -> Optional[int]:
        try:
            return int(v)
        except Exception:
            return None

    @staticmethod
    def _preset_name(code: Optional[int]) -> Optional[str]:
        if code == 1:
            return "away"
        if code == 2:
            return "home"
        return None
