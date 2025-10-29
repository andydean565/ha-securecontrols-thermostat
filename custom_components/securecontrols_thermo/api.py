from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Awaitable, Callable, AsyncIterator
import aiohttp
import asyncio
import hashlib
import json
import time
import secrets
import contextlib
import logging

# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------
_LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Types / constants
# --------------------------------------------------------------------------------------
Json = Dict[str, Any]
UpdateHandler = Callable[[Json], Awaitable[None]]

WS_URL = "wss://app.beanbag.online/api/TransactionRestAPI/ConnectWebSocket"
WS_SUBPROTOCOL = "BB-BO-01"


# ---- Thermostat metadata (gateway == device) ----
@dataclass
class Thermostat:
    gmi: str
    sn: str
    hn: str
    cs: Optional[int] = None
    ur: Optional[int] = None
    hi: Optional[int] = None
    dt: Optional[int] = None
    dn: Optional[str] = None


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------
class ApiError(Exception):
    pass


class InvalidAuth(ApiError):
    pass


class CannotConnect(ApiError):
    pass


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _encode_password(pw: str) -> str:
    """
    Secure Controls / Beanbag expects: SHA1(password).hexdigest() truncated to 32 chars.
    """
    digest = hashlib.sha1(pw.encode("utf-8")).hexdigest()[:32]
    # DEBUG: Log the digest (still sensitive; enable only in development)
    _LOGGER.debug("SecureControls: computed password digest (SHA1[:32]) = %s", digest)
    return digest


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------
class SecureControlsClient:
    """
    Secure Controls / Beanbag client
    - HTTP login to get JWT + SessionId + GD
    - WebSocket control with BB-BO-01 subprotocol
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._http = session
        self._base = "https://app.beanbag.online"

        # Auth/session
        self._jwt: Optional[str] = None           # D.JT
        self._session_id: Optional[int] = None    # D.SI
        self._session_ts: Optional[int] = None    # D.JTT
        self._user_id: Optional[int] = None       # D.UI

        # Device (gateway == thermostat)
        self.thermostat: Optional[Thermostat] = None

        # WS
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

        # Correlation (I -> future)
        self._pending: Dict[str, asyncio.Future] = {}

        # Push/notify
        self._updates_q: asyncio.Queue[Json] = asyncio.Queue(maxsize=200)
        self._handlers: list[UpdateHandler] = []

        # Keepalive cadence
        self._keepalive_secs = 45  # send time.tick every ~45s

    # --------------- Utilities ---------------
    @staticmethod
    def _now_epoch() -> int:
        # Field guide says epoch seconds (UTC)
        return int(time.time())

    @staticmethod
    def c_to_deci(c: float) -> int:
        return int(round(c * 10))

    @staticmethod
    def deci_to_c(v: int) -> float:
        return float(v) / 10.0

    def _new_corr(self) -> str:
        # "I": "{sessionId}-{rand32}"
        sid = str(self._session_id or "0")
        return f"{sid}-{secrets.token_hex(4)}"

    # --------------- HTTP: Login ---------------
    async def login(self, email: str, password: str) -> None:
        """
        Perform login:
          - P = SHA1(password).hexdigest()[:32]
          - UEI = email as provided (trimmed; do NOT lowercase)
        """
        payload = {
            "ULC": {
                "OI": 1550005,
                "NT": "SetLogin",
                "UEI": email.strip(),          # do not .lower()
                "P": _encode_password(password),
            }
        }
        _LOGGER.debug("SecureControls: sending LoginRequest for UEI=%s", payload["ULC"]["UEI"])

        try:
            resp = await self._http.post(f"{self._base}/api/UserRestAPI/LoginRequest", json=payload)
        except aiohttp.ClientError as e:
            _LOGGER.error("SecureControls: HTTP exception during login: %s", e)
            raise CannotConnect(f"HTTP error connecting: {e}") from e

        # Fast-path status mapping (many backends still return 200+error body)
        if resp.status in (401, 403):
            _LOGGER.warning("SecureControls: login HTTP status %s (unauthorized/forbidden)", resp.status)
            raise InvalidAuth("HTTP unauthorized/forbidden")
        if resp.status >= 500:
            _LOGGER.error("SecureControls: server error %s on login", resp.status)
            raise CannotConnect(f"Server error: {resp.status}")

        # Parse JSON (or surface raw body on failure)
        try:
            root = await resp.json()
        except Exception:
            txt = await resp.text()
            _LOGGER.error("SecureControls: bad JSON from login (HTTP %s): %s", resp.status, txt[:400])
            raise CannotConnect(f"Bad JSON from login (HTTP {resp.status})")

        d = root.get("D") or {}
        jwt = d.get("JT")
        si = d.get("SI")
        gd = d.get("GD") or []

        if not jwt or not si:
            # Log full response (trimmed) to help diagnose (RI often -1 on failure)
            _LOGGER.warning(
                "SecureControls: login missing JT/SI. RI=%s, payload=%s",
                root.get("RI"),
                json.dumps(root)[:800],
            )
            raise InvalidAuth(f"Missing JT/SI in response (RI={root.get('RI')})")

        # Save session/auth
        self._jwt = jwt
        self._session_id = si
        self._session_ts = d.get("JTT")
        self._user_id = d.get("UI")

        _LOGGER.debug(
            "SecureControls: login ok. SI=%s UI=%s JTT=%s, GD count=%s",
            self._session_id, self._user_id, self._session_ts, len(gd),
        )

        if not gd:
            # Not strictly auth failure, but tell the caller why we can’t continue.
            _LOGGER.error("SecureControls: login ok but no devices (GD empty)")
            raise ApiError("Login ok but no devices (GD empty)")

        # Select first thermostat (= gateway)
        gw = gd[0]
        self.thermostat = Thermostat(
            gmi=str(gw["GMI"]),
            sn=str(gw["SN"]),
            hn=str(gw["HN"]),
            cs=gw.get("CS"),
            ur=gw.get("UR"),
            hi=gw.get("HI"),
            dt=gw.get("DT"),
            dn=gw.get("DN"),
        )
        _LOGGER.debug(
            "SecureControls: selected thermostat GMI=%s SN=%s HN=%s",
            self.thermostat.gmi, self.thermostat.sn, self.thermostat.hn,
        )

    # --------------- WebSocket lifecycle ---------------
    def _ws_headers(self) -> Dict[str, str]:
        # Required headers per field guide
        return {
            "Authorization": f"Bearer {self._jwt}",
            "Session-id": str(self._session_id),
            "Request-id": "1",  # any string; not strictly used after connect
        }

    async def connect(self) -> None:
        if not self.thermostat:
            raise RuntimeError("Call login() first")

        self._stop.clear()
        _LOGGER.debug("SecureControls: opening WebSocket to %s", WS_URL)
        self._ws = await self._http.ws_connect(
            WS_URL,
            headers=self._ws_headers(),
            protocols=[WS_SUBPROTOCOL],
            heartbeat=None,  # we handle keepalive via time.tick op
            autoping=True,
        )
        _LOGGER.debug("SecureControls: WebSocket connected (protocol=%s)", WS_SUBPROTOCOL)

        # Start receiver & keepalive
        self._recv_task = asyncio.create_task(self._recv_loop(), name="bbbo-recv")
        self._ping_task = asyncio.create_task(self._keepalive_loop(), name="bbbo-keepalive")

    async def disconnect(self) -> None:
        self._stop.set()
        _LOGGER.debug("SecureControls: disconnect requested")

        if self._ping_task:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None

        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None

        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        # Fail any pending requests
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(asyncio.CancelledError())
        self._pending.clear()

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                _LOGGER.debug("SecureControls: non-JSON WS frame: %s", msg.data[:120])
                continue

            # Replies (to our requests)
            corr = payload.get("I")
            if corr and ("R" in payload or "E" in payload):
                fut = self._pending.pop(corr, None)
                if fut and not fut.done():
                    if "R" in payload:
                        fut.set_result(payload["R"])
                    else:
                        _LOGGER.warning("SecureControls: WS error reply: %s", payload.get("E"))
                        fut.set_exception(RuntimeError(payload.get("E")))
                continue

            # Push notifies
            if payload.get("M") == "Notify":
                await self._dispatch_notify(payload)
                continue

            _LOGGER.debug("SecureControls: unhandled WS payload: %s", json.dumps(payload)[:400])

    async def _keepalive_loop(self) -> None:
        # Send time.tick (HI/SI = 2/103) periodically
        while not self._stop.is_set():
            try:
                await self.time_tick()  # fire-and-forget is fine
            except Exception as e:
                _LOGGER.debug("SecureControls: keepalive tick failed: %s", e)
            await asyncio.wait_for(asyncio.sleep(self._keepalive_secs), timeout=None)

    # --------------- Envelope + send ---------------
    async def _send_request(self, *, hi: int, si: int, args: Optional[list[Any]] = None) -> Any:
        if not self._ws or self._ws.closed:
            raise RuntimeError("WebSocket not connected")
        if not self.thermostat:
            raise RuntimeError("No thermostat selected")

        corr = self._new_corr()
        env: Json = {
            "V": "1.0",
            "DTS": self._now_epoch(),
            "I": corr,
            "M": "Request",
            "P": [
                {"GMI": int(self.thermostat.gmi), "HI": hi, "SI": si},
            ],
        }
        if args is not None:
            env["P"].append(args)

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[corr] = fut
        await self._ws.send_json(env)
        _LOGGER.debug("SecureControls: sent request HI/SI=%s/%s corr=%s", hi, si, corr)
        return await fut

    async def _send_fire_and_forget(self, *, hi: int, si: int, args: Optional[list[Any]] = None) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("WebSocket not connected")
        if not self.thermostat:
            raise RuntimeError("No thermostat selected")

        env: Json = {
            "V": "1.0",
            "DTS": self._now_epoch(),
            "I": self._new_corr(),
            "M": "Request",
            "P": [
                {"GMI": int(self.thermostat.gmi), "HI": hi, "SI": si},
            ],
        }
        if args is not None:
            env["P"].append(args)
        await self._ws.send_json(env)

    # --------------- Notify / push ---------------
    async def _dispatch_notify(self, payload: Json) -> None:
        # Queue it
        try:
            self._updates_q.put_nowait(payload)
        except asyncio.QueueFull:
            with contextlib.suppress(Exception):
                _ = self._updates_q.get_nowait()
            await self._updates_q.put(payload)

        # Call handlers
        for h in self._handlers:
            asyncio.create_task(h(payload))

    def on_update(self, handler: UpdateHandler) -> None:
        self._handlers.append(handler)

    async def updates(self) -> AsyncIterator[Json]:
        while True:
            yield await self._updates_q.get()

    # --------------- Reads (HI/SI pairs) ---------------
    async def zones_read(self) -> Any:
        # 49/11
        return await self._send_request(hi=49, si=11)

    async def time_tick(self) -> Any:
        # 2/103 with [epochSeconds]
        return await self._send_request(hi=2, si=103, args=[self._now_epoch()])

    async def device_metadata_read(self) -> Any:
        # 17/11
        return await self._send_request(hi=17, si=11)

    async def device_config_read(self) -> Any:
        # 14/11
        return await self._send_request(hi=14, si=11)

    async def state_read(self) -> Any:
        # 3/1 → returns blocks with items
        return await self._send_request(hi=3, si=1)

    # --------------- Writes (Thermostat SI:15, slot=1) ---------------
    async def set_target_temp(self, celsius: float) -> Any:
        # thermostat.state.write — 2/15
        # args: [ slot, { "I":1, "V":<deciC>, "OT":1, "D":0 } ]
        return await self._send_request(
            hi=2, si=15,
            args=[1, {"I": 1, "V": self.c_to_deci(celsius), "OT": 1, "D": 0}]
        )

    async def set_mode(self, on: bool) -> Any:
        # I:6 (0=Off, 2=On)
        return await self._send_request(
            hi=2, si=15,
            args=[1, {"I": 6, "V": (2 if on else 0), "OT": 1, "D": 0}]
        )

    async def set_timed_hold(self, celsius: float, minutes: int) -> Any:
        # OT:2 with D:<minutes> on I:1
        return await self._send_request(
            hi=2, si=15,
            args=[1, {"I": 1, "V": self.c_to_deci(celsius), "OT": 2, "D": int(minutes)}]
        )

    # --------------- Context manager ---------------
    async def __aenter__(self) -> "SecureControlsClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()
