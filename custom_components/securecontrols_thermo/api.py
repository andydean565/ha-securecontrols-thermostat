from __future__ import annotations
from typing import Any
import aiohttp

class SecureControlsClient:
    """Thin async HTTP client. Replace endpoints with the real ones."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._web = session
        self._token: str | None = None
        # TODO: confirm base URL
        self._base = "https://api.securecontrols.example/v1"

    async def login(self, email: str, password: str) -> None:
        # TODO: swap with the real login path / payload
        resp = await self._web.post(f"{self._base}/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        data = await resp.json()
        self._token = data.get("access_token", "dev-token")  # fallback for dev

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def list_devices(self) -> list[dict[str, Any]]:
        # TODO: replace with real endpoint / filtering for thermostats
        # For dev, return a single fake device so the flow works.
        return [{"id": "demo-thermo-1", "name": "Hall Thermostat", "type": "thermostat", "model": "C1727"}]

    async def get_state(self, device_id: str) -> dict[str, Any]:
        # TODO: fetch from cloud. Dev stub returns fake telemetry.
        return {"id": device_id, "name": "Hall Thermostat", "model": "C1727", "ambient_c": 20.5, "target_c": 21.0, "heating_enabled": True, "preset": "none"}

    async def set_target_temp(self, device_id: str, celsius: float) -> None:
        # TODO: POST real command and maybe wait for ack
        return None

    async def set_mode(self, device_id: str, mode: str) -> None:
        # mode: "heat" or "off"
        return None

    async def set_preset(self, device_id: str, preset: str) -> None:
        # preset: vendor-specific (e.g., "away", "schedule")
        return None
