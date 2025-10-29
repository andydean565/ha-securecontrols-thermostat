from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, UPDATE_INTERVAL_SECS

# Item map (SI:15, slot 1) for convenience
ITEM_TARGET = 1
ITEM_POWER = 6
ITEM_PROBE = 7
ITEM_HUMID = 8
ITEM_NEXT_TIME = 9
ITEM_NEXT_VALUE = 10
ITEM_FROST = 11


class ThermoCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """
    Coordinator that:
      - Opens a single WS connection (on first refresh)
      - Polls with state_read() for a baseline
      - Applies push Notifies to cached state between polls
    """

    def __init__(self, hass: HomeAssistant, client) -> None:
        super().__init__(
            hass,
            logger=hass.helpers.logger.logging.getLogger(DOMAIN),
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECS),
        )
        self.client = client
        self._ws_started = False
        self._state_cache: Dict[str, Any] = {}

    # ---------- lifecycle / polling ----------

    async def _async_update_data(self) -> Dict[str, Any]:
        """Poll path: make one state_read() request and normalize it."""
        # Start WS + subscribe to push updates on first run
        if not self._ws_started:
            await self._ensure_ws_started()

        # Read full state snapshot
        raw = await self.client.state_read()
        parsed = self._parse_state_read(raw)

        # Cache & return
        self._state_cache = parsed
        return parsed

    async def _ensure_ws_started(self) -> None:
        if self._ws_started:
            return
        # Connect WS if not already (login must have been called earlier)
        await self.client.connect()

        async def _on_notify(msg: Dict[str, Any]) -> None:
            """
            Handle push messages:
            { "M": "Notify", "P":[ { "GMI":..., "SI": <block>, "HI": 4 }, [ <slot>, { "I":<item>, "V":<val>, ... } ] ] }
            """
            if msg.get("M") != "Notify":
                return
            p = msg.get("P") or []
            if len(p) != 2:
                return
            header, body = p
            block = header.get("SI")
            if block != 15:
                # Only process thermostat primary block here; extend if needed
                return
            # body is: [ slot, {I,V,OT,D} ]
            if not isinstance(body, list) or len(body) != 2:
                return
            _slot, item = body
            if not isinstance(item, dict):
                return

            # Update the coordinator's cached state and push it to listeners
            updated = self._apply_notify_to_cache(item)
            if updated:
                # Tell HA the data changed outside the polling cycle
                self.async_set_updated_data(self._state_cache)

        self.client.on_update(_on_notify)
        self._ws_started = True

    # ---------- parsing helpers ----------

    def _parse_state_read(self, r: Any) -> Dict[str, Any]:
        """
        Normalize the 3/1 state.read() payload into a flat dict your entities can use.

        Expected shape (R stripped):
        {
          "V": [
            { "I": <slot>, "SI": <block>, "V": [ { "I":<item>, "V":<value>, "OT":..., "D":... }, ... ], "S": 0 },
            ...
          ]
        }
        """
        state: Dict[str, Any] = {
            "power": None,           # 0=Off, 2=On
            "target_c": None,        # float
            "ambient_c": None,       # float (probe temp, if provided)
            "humidity": None,        # %
            "next_change_mins": None,
            "next_target_c": None,
            "frost_c": None,
        }

        if not isinstance(r, dict):
            return state
        vec = r.get("V")
        if not isinstance(vec, list):
            return state

        # Find block SI:15, slot 1
        for block in vec:
            if not isinstance(block, dict):
                continue
            if block.get("SI") != 15:
                continue
            items = block.get("V") or []
            for it in items:
                try:
                    iid = it.get("I")
                    val = it.get("V")
                except AttributeError:
                    continue

                if iid == ITEM_POWER:
                    state["power"] = int(val)
                elif iid == ITEM_TARGET:
                    state["target_c"] = self._deci_to_c(val)
                elif iid == ITEM_PROBE:
                    # Some firmwares report probe in deci-°C here; if it looks like a temp, convert
                    state["ambient_c"] = self._maybe_deci_temp(val)
                elif iid == ITEM_HUMID:
                    state["humidity"] = int(val)
                elif iid == ITEM_NEXT_TIME:
                    state["next_change_mins"] = int(val)
                elif iid == ITEM_NEXT_VALUE:
                    state["next_target_c"] = self._deci_to_c(val)
                elif iid == ITEM_FROST:
                    # often read-only here
                    state["frost_c"] = self._deci_to_c(val)

        return state

    def _apply_notify_to_cache(self, item: Dict[str, Any]) -> bool:
        """
        Merge a single item change (from Notify) into the cached state.
        Returns True if cache changed.
        """
        if not self._state_cache:
            # If we don't have baseline yet, ignore until first poll completes.
            return False

        iid = item.get("I")
        val = item.get("V")

        changed = False

        if iid == ITEM_POWER:
            newv = int(val)
            changed = self._state_cache.get("power") != newv
            self._state_cache["power"] = newv

        elif iid == ITEM_TARGET:
            newv = self._deci_to_c(val)
            changed = self._state_cache.get("target_c") != newv
            self._state_cache["target_c"] = newv

        elif iid == ITEM_PROBE:
            newv = self._maybe_deci_temp(val)
            changed = self._state_cache.get("ambient_c") != newv
            self._state_cache["ambient_c"] = newv

        elif iid == ITEM_HUMID:
            newv = int(val)
            changed = self._state_cache.get("humidity") != newv
            self._state_cache["humidity"] = newv

        elif iid == ITEM_NEXT_TIME:
            newv = int(val)
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

    # ---------- unit helpers ----------

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
        """
        Some devices report probe temperature in deci-°C (e.g., 205 -> 20.5).
        If it's a plausible deci-°C, convert; otherwise leave None.
        """
        if v is None:
            return None
        try:
            iv = int(v)
        except Exception:
            return None
        # Heuristic: valid temps typically 0..500 deci-°C (-? not expected here)
        if -500 <= iv <= 5000:
            return iv / 10.0
        return None
