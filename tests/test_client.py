from custom_components.securecontrols_thermo.api import SecureControlsClient
import pytest
import aiohttp
import asyncio

@pytest.mark.asyncio
async def test_client_list_devices():
    async with aiohttp.ClientSession() as s:
        c = SecureControlsClient(s)
        await c.login("user@example.com", "pw")
        devs = await c.list_devices()
        assert isinstance(devs, list)
        assert devs and devs[0]["type"] == "thermostat"
