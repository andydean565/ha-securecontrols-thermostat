from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

import custom_components.securecontrols_thermostat as integration
from custom_components.securecontrols_thermostat.api import CannotConnect, InvalidAuth
from custom_components.securecontrols_thermostat.const import CONF_EMAIL, CONF_PASSWORD


@pytest.mark.asyncio
async def test_setup_does_not_retry_rejected_authentication(monkeypatch):
    class RejectingClient:
        def __init__(self, _session):
            pass

        async def login(self, _email, _password):
            raise InvalidAuth("session rejected")

    monkeypatch.setattr(integration, "SecureControlsClient", RejectingClient)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: object())
    entry = SimpleNamespace(data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"})

    with pytest.raises(ConfigEntryAuthFailed):
        await integration.async_setup_entry(SimpleNamespace(), entry)


@pytest.mark.asyncio
async def test_setup_keeps_transient_connection_failures_retryable(monkeypatch):
    class OfflineClient:
        def __init__(self, _session):
            pass

        async def login(self, _email, _password):
            raise CannotConnect("offline")

    monkeypatch.setattr(integration, "SecureControlsClient", OfflineClient)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: object())
    entry = SimpleNamespace(data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"})

    with pytest.raises(ConfigEntryNotReady):
        await integration.async_setup_entry(SimpleNamespace(), entry)
