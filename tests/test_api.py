# tests/test_api.py
import asyncio
import json
from typing import Any, List, Optional

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.securecontrols_thermostat.api import (
    SecureControlsClient,
    _encode_password,
    ApiError,
    InvalidAuth,
    CannotConnect,
    Thermostat,
    WS_SUBPROTOCOL,
    WS_URL,
)
import aiohttp  # for WSMsgType


# ----------------------------
# Fixtures / test utilities
# ----------------------------

@pytest.fixture
def event_loop():
    # pytest-asyncio default loop fixture name
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


class _WSMsg:
    """Minimal message holder that looks like aiohttp.WSMessage enough for __aiter__ consumption."""
    def __init__(self, data: str):
        self.type = aiohttp.WSMsgType.TEXT
        self.data = data


class FakeWS:
    """
    A tiny stand-in for aiohttp.ClientWebSocketResponse.

    - Accepts a list of preloaded JSON-able Python dicts for incoming messages.
    - Collects anything sent via send_json in .sent list.
    - Supports .close() and .closed.
    - Usable in "async for" via __aiter__.
    """
    def __init__(self, incoming: Optional[List[dict]] = None):
        self._incoming = list(incoming or [])
        self.sent: List[Any] = []
        self.closed = False

    async def send_json(self, payload: Any):
        self.sent.append(payload)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        async def gen():
            # Yield each preloaded item as a TEXT WS message
            for it in self._incoming:
                yield _WSMsg(json.dumps(it))
            # After exhausting, just end iteration
            return
            yield  # pragma: no cover  (keep generator syntax)
        return gen()


# ----------------------------
# Unit tests: helpers
# ----------------------------

def test_encode_password_md5_lower_hex_ok():
    digest = _encode_password("secret")
    # Known md5("secret")
    assert digest == "5ebe2294ecd0e0f08eab7690d2a6ee69"
    assert len(digest) == 32
    assert digest == digest.lower()


# ----------------------------
# HTTP login tests
# ----------------------------

@pytest.mark.asyncio
async def test_login_success(session):
    client = SecureControlsClient(session)

    ok_body = {
        "RI": 0,
        "D": {
            "JT": "jwt-token-123",
            "SI": 777,
            "JTT": 123456,
            "UI": 42,
            "GD": [
                {"GMI": 1001, "SN": "ABC", "HN": "Thermo-1", "CS": 1, "UR": 100},
            ],
        },
    }

    with aioresponses() as m:
        m.post(
            "https://app.beanbag.online/api/UserRestAPI/LoginRequest",
            payload=ok_body,
            status=200,
        )
        await client.login("user@example.com", "secret")

    assert client._jwt == "jwt-token-123"
    assert client._session_id == 777
    assert client._user_id == 42
    assert isinstance(client.thermostat, Thermostat)
    assert client.thermostat.gmi == "1001"
    assert client.thermostat.sn == "ABC"


@pytest.mark.asyncio
async def test_login_unauthorized_status_raises_InvalidAuth(session):
    client = SecureControlsClient(session)
    with aioresponses() as m:
        m.post(
            "https://app.beanbag.online/api/UserRestAPI/LoginRequest",
            status=401,
            payload={"D": {}},
        )
        with pytest.raises(InvalidAuth):
            await client.login("user@example.com", "secret")


@pytest.mark.asyncio
async def test_login_server_error_raises_CannotConnect(session):
    client = SecureControlsClient(session)
    with aioresponses() as m:
        m.post(
            "https://app.beanbag.online/api/UserRestAPI/LoginRequest",
            status=500,
            payload={"D": {}},
        )
        with pytest.raises(CannotConnect):
            await client.login("user@example.com", "secret")


@pytest.mark.asyncio
async def test_login_bad_json_raises_CannotConnect(session):
    client = SecureControlsClient(session)
    with aioresponses() as m:
        # Return non-JSON body
        m.post(
            "https://app.beanbag.online/api/UserRestAPI/LoginRequest",
            body="not-json",
            status=200,
            headers={"Content-Type": "text/plain"},
        )
        with pytest.raises(CannotConnect):
            await client.login("user@example.com", "secret")


@pytest.mark.asyncio
async def test_login_missing_jt_si_raises_InvalidAuth(session):
    client = SecureControlsClient(session)
    broken = {
        "RI": -1,
        "D": {"GD": [{"GMI": 1001, "SN": "ABC", "HN": "Thermo-1"}]},
    }
    with aioresponses() as m:
        m.post(
            "https://app.beanbag.online/api/UserRestAPI/LoginRequest",
            payload=broken,
            status=200,
        )
        with pytest.raises(InvalidAuth):
            await client.login("user@example.com", "secret")


@pytest.mark.asyncio
async def test_login_no_devices_raises_ApiError(session):
    client = SecureControlsClient(session)
    ok_no_devices = {
        "RI": 0,
        "D": {"JT": "jwt", "SI": 9, "GD": []},
    }
    with aioresponses() as m:
        m.post(
            "https://app.beanbag.online/api/UserRestAPI/LoginRequest",
            payload=ok_no_devices,
            status=200,
        )
        with pytest.raises(ApiError):
            await client.login("user@example.com", "secret")


# ----------------------------
# WebSocket / request-response
# ----------------------------

@pytest.mark.asyncio
async def test_connect_starts_recv_and_keepalive_tasks_and_headers(session, monkeypatch):
    client = SecureControlsClient(session)
    # Fake "logged in"
    client._jwt = "jwt"
    client._session_id = 123
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")

    # Prepare a fake ws; no incoming messages for this test
    ws = FakeWS(incoming=[])

    recorded_headers = {}
    recorded_protocols = []

    async def fake_ws_connect(url, *, headers=None, protocols=None, **kwargs):
        assert url == WS_URL
        recorded_headers.update(headers or {})
        recorded_protocols.extend(protocols or [])
        return ws

    # Prevent keepalive loop from running indefinitely
    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()

    # Headers should include Authorization / Session-id and subprotocol
    assert recorded_headers["Authorization"] == "Bearer jwt"
    assert recorded_headers["Session-id"] == "123"
    assert WS_SUBPROTOCOL in recorded_protocols

    await client.disconnect()
    assert ws.closed is True


@pytest.mark.asyncio
async def test_send_request_correlates_and_resolves_future(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 42
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")

    # Make DTS/correlation deterministic
    monkeypatch.setattr(SecureControlsClient, "_now_epoch", staticmethod(lambda: 1700000000))
    monkeypatch.setattr(SecureControlsClient, "_new_corr", lambda self: "42-deadbeef")

    # Incoming message is a reply carrying the same correlation id → "R"
    reply = {"I": "42-deadbeef", "R": {"ok": 1}}
    ws = FakeWS(incoming=[reply])

    async def fake_ws_connect(*args, **kwargs):
        return ws

    # Disable keepalive
    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()

    # Call a read op; it should send once and then resolve with reply["R"]
    res = await client.device_metadata_read()  # 17/11
    assert res == {"ok": 1}

    # Check the last sent payload
    assert len(ws.sent) == 1
    sent = ws.sent[0]
    assert sent["M"] == "Request"
    assert sent["I"] == "42-deadbeef"
    assert sent["DTS"] == 1700000000
    assert sent["P"][0]["HI"] == 17
    assert sent["P"][0]["SI"] == 11
    assert int(sent["P"][0]["GMI"]) == 1001

    await client.disconnect()


@pytest.mark.asyncio
async def test_send_fire_and_forget_no_reply_needed(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 99
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")

    monkeypatch.setattr(SecureControlsClient, "_new_corr", lambda self: "99-cafe1234")
    ws = FakeWS(incoming=[])  # nothing will be received

    async def fake_ws_connect(*args, **kwargs):
        return ws

    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()

    # Use a method that builds on _send_request (so it *does* await). We'll just make sure it sends correctly.
    task = asyncio.create_task(client.time_tick())  # waits on a reply, but none is coming
    # Simulate the reply by poking the pending future using what we know the corr id is.
    # But we didn't patch _new_corr for time_tick, so cancel the task and just validate send payload.
    await asyncio.sleep(0)  # let it send
    assert len(ws.sent) == 1
    sent = ws.sent[0]
    assert sent["P"][0]["HI"] == 2 and sent["P"][0]["SI"] == 103
    assert isinstance(sent["P"][1][0], int)  # epoch seconds arg

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await client.disconnect()


@pytest.mark.asyncio
async def test_notify_frames_are_queued_and_handlers_called(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 7
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")

    payload = {"M": "Notify", "data": {"foo": "bar"}}
    ws = FakeWS(incoming=[payload])

    async def fake_ws_connect(*args, **kwargs):
        return ws

    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    called = asyncio.Event()
    seen = {}

    async def handler(p):
        seen.update(p)
        called.set()

    client.on_update(handler)

    await client.connect()

    # Wait for handler to be called by recv loop
    await asyncio.wait_for(called.wait(), timeout=1.0)

    # Also verify it was placed on the updates queue
    gen = client.updates()
    # Use an async comprehension trick to get next item
    async def get_one():
        agen = gen.__aiter__()
        return await agen.__anext__()
    got = await get_one()
    assert got["M"] == "Notify"
    assert seen["data"]["foo"] == "bar"

    await client.disconnect()


# ----------------------------
# Write calls payload shaping
# ----------------------------

@pytest.mark.asyncio
async def test_set_target_temp_payload(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 1
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")
    monkeypatch.setattr(SecureControlsClient, "_new_corr", lambda self: "1-abcd")

    # Reply that satisfies await
    reply = {"I": "1-abcd", "R": {"ok": True}}
    ws = FakeWS(incoming=[reply])

    async def fake_ws_connect(*args, **kwargs):
        return ws
    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()
    res = await client.set_target_temp(21.5)
    assert res == {"ok": True}

    sent = ws.sent[0]
    assert sent["P"][0]["HI"] == 2 and sent["P"][0]["SI"] == 15
    args = sent["P"][1]
    assert args[0] == 1
    body = args[1]
    assert body["I"] == 1
    assert body["OT"] == 1
    assert body["D"] == 0
    assert body["V"] == 215  # deci-degrees

    await client.disconnect()


@pytest.mark.asyncio
async def test_set_mode_payload(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 2
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")
    monkeypatch.setattr(SecureControlsClient, "_new_corr", lambda self: "2-ef01")

    reply = {"I": "2-ef01", "R": {"ok": True}}
    ws = FakeWS(incoming=[reply])

    async def fake_ws_connect(*args, **kwargs):
        return ws
    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()
    await client.set_mode(True)

    sent = ws.sent[0]
    body = sent["P"][1][1]
    assert body["I"] == 6
    assert body["V"] == 2  # on
    assert body["OT"] == 1
    assert body["D"] == 0

    await client.disconnect()


@pytest.mark.asyncio
async def test_set_timed_hold_payload(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 3
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")
    monkeypatch.setattr(SecureControlsClient, "_new_corr", lambda self: "3-ef02")

    reply = {"I": "3-ef02", "R": {"ok": True}}
    ws = FakeWS(incoming=[reply])

    async def fake_ws_connect(*args, **kwargs):
        return ws
    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()
    await client.set_timed_hold(19.0, 45)

    sent = ws.sent[0]
    body = sent["P"][1][1]
    assert body["I"] == 1
    assert body["OT"] == 2
    assert body["D"] == 45
    assert body["V"] == 190

    await client.disconnect()


# ----------------------------
# Disconnect cancels pending
# ----------------------------

@pytest.mark.asyncio
async def test_disconnect_cancels_pending_futures(session, monkeypatch):
    client = SecureControlsClient(session)
    client._jwt = "jwt"
    client._session_id = 10
    client.thermostat = Thermostat(gmi="1001", sn="SN", hn="HN")
    monkeypatch.setattr(SecureControlsClient, "_new_corr", lambda self: "10-dead")

    ws = FakeWS(incoming=[])  # never sends a reply

    async def fake_ws_connect(*args, **kwargs):
        return ws
    async def fake_keepalive_loop(self):
        return

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    monkeypatch.setattr(SecureControlsClient, "_keepalive_loop", fake_keepalive_loop)

    await client.connect()

    # Kick off a request that will remain pending
    task = asyncio.create_task(client.zones_read())

    # Let it send
    await asyncio.sleep(0)

    # Disconnect should cancel the pending future
    await client.disconnect()

    with pytest.raises(asyncio.CancelledError):
        await task
