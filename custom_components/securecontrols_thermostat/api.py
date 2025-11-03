from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Awaitable, Callable, AsyncIterator, List, Union, cast
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

# Thermostat "block" constants (per your usage)
THERMO_HI_WRITE = 2     # thermostat.state.write
THERMO_SI = 15          # thermostat state block
THERMO_SLOT = 1         # slot used in your integration

# Item map (SI:15, slot 1) — updated
ITEM_TARGET = 1           # target_c (deci °C)
ITEM_AMBIENT = 2          # ambient_c (deci °C)
ITEM_HVAC = 3             # hvac: 0=off, 1=heat
ITEM_PRESET = 6           # preset: 1=away, 2=home
ITEM_HUMID = 8            # %RH
ITEM_NEXT_TIME = 9        # next schedule time (mins)
ITEM_NEXT_TARGET = 10     # next scheduled target temp (deci °C)
ITEM_FROST = 11           # frost_c (deci °C)


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
    SecureControls / Beanbag login expects MD5(password).hexdigest()
    (32 lowercase hex characters). Do NOT truncate.
    """
    digest = hashlib.md5(pw.encode("utf-8")).hexdigest()
    if len(digest) != 32 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("Password digest must be a 32-character lowercase hex string")
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
        self._last_rx_ts: int = 0  # last time we received anything on WS (epoch seconds)

        # Correlation (I -> future)
        self._pending: Dict[str, asyncio.Future] = {}

        # Push/notify
        self._updates_q: asyncio.Queue[Json] = asyncio.Queue(maxsize=200)
        self._handlers: list[UpdateHandler] = []

        # Keepalive cadence
        self._keepalive_secs = 45  # send time.tick every ~45s

        # Reconnect guard
        self._reconnect_lock = asyncio.Lock()

    # --------------- Utilities ---------------
    @staticmethod
    def _now_epoch() -> int:
        return int(time.time())

    @staticmethod
    def c_to_deci(c: float) -> int:
        return int(round(c * 10))

    @staticmethod
    def deci_to_c(v: int) -> float:
        return float(v) / 10.0

    def _new_corr(self) -> str:
        sid = str(self._session_id or "0")
        return f"{sid}-{secrets.token_hex(4)}"

    # --------------- HTTP: Login ---------------
    async def login(self, email: str, password: str) -> None:
        payload = {
            "ULC": {
                "OI": 1550005,
                "NT": "SetLogin",
                "UEI": email.strip(),
                "P": _encode_password(password),
            }
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Request-id": "1",
        }
        try:
            resp = await self._http.post(f"{self._base}/api/UserRestAPI/LoginRequest", json=payload, headers=headers)
        except aiohttp.ClientError as e:
            _LOGGER.error("SecureControls: HTTP exception during login: %s", e)
            raise CannotConnect(f"HTTP error connecting: {e}") from e

        if resp.status in (401, 403):
            _LOGGER.warning("SecureControls: login HTTP status %s (unauthorized/forbidden)", resp.status)
            raise InvalidAuth("HTTP unauthorized/forbidden")
        if resp.status >= 500:
            _LOGGER.error("SecureControls: server error %s on login", resp.status)
            raise CannotConnect(f"Server error: {resp.status}")

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
            _LOGGER.warning(
                "SecureControls: login missing JT/SI. RI=%s, payload=%s",
                root.get("RI"),
                json.dumps(root)[:800],
            )
            raise InvalidAuth(f"Missing JT/SI in response (RI={root.get('RI')})")

        self._jwt = jwt
        self._session_id = si
        self._session_ts = d.get("JTT")
        self._user_id = d.get("UI")

        _LOGGER.debug(
            "SecureControls: login ok. SI=%s UI=%s JTT=%s, GD count=%s",
            self._session_id, self._user_id, self._session_ts, len(gd),
        )

        if not gd:
            _LOGGER.error("SecureControls: login ok but no devices (GD empty)")
            raise ApiError("Login ok but no devices (GD empty)")

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
        return {
            "Authorization": f"Bearer {self._jwt}",
            "Session-id": str(self._session_id),
            "Request-id": "1",
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
        self._last_rx_ts = self._now_epoch()  # mark WS activity
        _LOGGER.debug("SecureControls: WebSocket connected (protocol=%s)", WS_SUBPROTOCOL)

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

        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(asyncio.CancelledError())
        self._pending.clear()

    async def _reconnect(self, reason: Optional[str] = None) -> None:
        """
        Tear down the current WS and re-establish it with exponential backoff.
        Does NOT attempt to re-login (we don't have credentials here). If the token
        has expired and the server returns 401/403, raise InvalidAuth so the caller
        can perform a fresh login.
        """
        if self._stop.is_set():
            _LOGGER.debug("SecureControls: reconnect skipped; client is stopping")
            return

        async with self._reconnect_lock:
            # If another task already reconnected us, bail.
            if self._ws and not self._ws.closed and self._recv_task and not self._recv_task.done():
                _LOGGER.debug("SecureControls: reconnect not needed (WS alive)")
                return

            if reason:
                _LOGGER.warning("SecureControls: reconnecting WS (%s)", reason)
            else:
                _LOGGER.warning("SecureControls: reconnecting WS")

            # Best-effort cleanup of old tasks/socket
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
                with contextlib.suppress(Exception):
                    await self._ws.close()
            self._ws = None

            # Fail any in-flight requests so callers aren't left hanging
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError("WebSocket reconnecting; request cancelled"))
            self._pending.clear()

            # Retry loop with backoff
            attempt = 0
            while not self._stop.is_set():
                attempt += 1
                # Immediate first attempt; then 1,2,4,8,... up to 30s + small jitter
                if attempt > 1:
                    base = min(30, 2 ** (attempt - 2))  # 1,2,4,8,16,30,30...
                    jitter = (secrets.randbelow(1000) / 1000.0)  # 0..1 second
                    delay = base + jitter
                    _LOGGER.debug("SecureControls: reconnect backoff %.2fs (attempt %s)", delay, attempt)
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=delay)
                        _LOGGER.debug("SecureControls: reconnect aborted due to stop")
                        return
                    except asyncio.TimeoutError:
                        pass

                try:
                    if not self._jwt or not self._session_id:
                        raise InvalidAuth("Missing JWT/session for reconnect")

                    _LOGGER.debug("SecureControls: opening WebSocket (reconnect attempt %s)", attempt)
                    self._ws = await self._http.ws_connect(
                        WS_URL,
                        headers=self._ws_headers(),
                        protocols=[WS_SUBPROTOCOL],
                        heartbeat=None,
                        autoping=True,
                    )
                    self._last_rx_ts = self._now_epoch()
                    _LOGGER.info("SecureControls: WebSocket reconnected on attempt %s", attempt)

                    # Restart background tasks
                    self._recv_task = asyncio.create_task(self._recv_loop(), name="bbbo-recv")
                    self._ping_task = asyncio.create_task(self._keepalive_loop(), name="bbbo-keepalive")
                    return

                except aiohttp.WSServerHandshakeError as e:
                    if e.status in (401, 403):
                        _LOGGER.error("SecureControls: WS handshake unauthorized (%s); need re-login", e.status)
                        raise InvalidAuth(f"WS unauthorized ({e.status})") from e
                    _LOGGER.warning("SecureControls: WS handshake error %s; will retry", e.status)
                except aiohttp.ClientError as e:
                    _LOGGER.warning("SecureControls: WS connect error: %s; will retry", e)
                except Exception as e:
                    _LOGGER.exception("SecureControls: unexpected error during reconnect: %s", e)

            _LOGGER.debug("SecureControls: reconnect loop exited (stop set)")

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                # update last RX timestamp on any frame
                self._last_rx_ts = self._now_epoch()

                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(cast(str, msg.data))
                except json.JSONDecodeError:
                    _LOGGER.debug("SecureControls: non-JSON WS frame: %s", str(msg.data)[:120])
                    continue

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

                if payload.get("M") == "Notify":
                    await self._dispatch_notify(payload)
                    continue

                _LOGGER.debug("SecureControls: unhandled WS payload: %s", json.dumps(payload)[:400])
        except Exception as e:
            _LOGGER.warning("SecureControls: recv loop terminated: %s", e)
            # Try to reconnect unless stopping
            if not self._stop.is_set():
                with contextlib.suppress(Exception):
                    await self._reconnect("recv loop error")

    async def _keepalive_loop(self) -> None:
        stale_after = 180  # seconds without RX before we reconnect (tunable)
        while not self._stop.is_set():
            try:
                # Fire-and-forget tick so we don't hang the loop if replies are dropped
                await self.time_tick_ff()
            except Exception as e:
                _LOGGER.debug("SecureControls: keepalive tick failed: %s", e)

            # Stale socket detection -> reconnect
            try:
                if self._last_rx_ts and (self._now_epoch() - self._last_rx_ts > stale_after):
                    _LOGGER.warning(
                        "SecureControls: WS appears stale (no RX >%ss); attempting reconnect",
                        stale_after,
                    )
                    await self._reconnect("stale socket")
            except Exception as e:
                _LOGGER.debug("SecureControls: keepalive stale-check error: %s", e)

            await asyncio.sleep(self._keepalive_secs)

    # --------------- Envelope + send ---------------
    async def _send_request(self, *, hi: int, si: int, args: Optional[List[Any]] = None) -> Any:
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

    async def _send_fire_and_forget(self, *, hi: int, si: int, args: Optional[List[Any]] = None) -> None:
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
        try:
            self._updates_q.put_nowait(payload)
        except asyncio.QueueFull:
            with contextlib.suppress(Exception):
                _ = self._updates_q.get_nowait()
            await self._updates_q.put(payload)

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
        # 2/103 with [epochSeconds] — request/response variant
        return await self._send_request(hi=2, si=103, args=[self._now_epoch()])

    async def time_tick_ff(self) -> None:
        """Fire-and-forget keepalive tick (recommended)."""
        await self._send_fire_and_forget(hi=2, si=103, args=[self._now_epoch()])

    async def device_metadata_read(self) -> Any:
        # 17/11
        return await self._send_request(hi=17, si=11)

    async def device_config_read(self) -> Any:
        # 14/11
        return await self._send_request(hi=14, si=11)

    async def state_read(self) -> Any:
        # 3/1 → returns blocks with items
        return await self._send_request(hi=3, si=1)

    # --------------- Generic writer helper ---------------
    async def _write_item(self, item_id: int, value: int, *, ot: int = 1, d: int = 0) -> Any:
        """
        Write a single state item on SI:15 / slot 1.
        ot: 1=immediate set, 2=timed override (minutes in D)
        """
        return await self._send_request(
            hi=THERMO_HI_WRITE,
            si=THERMO_SI,
            args=[THERMO_SLOT, {"I": int(item_id), "V": int(value), "OT": int(ot), "D": int(d)}],
        )

    # --------------- Writes (Thermostat SI:15, slot=1) ---------------
    async def set_target_temp(self, celsius: float) -> Any:
        # I:1 target (deci °C), OT:1 immediate
        return await self._write_item(ITEM_TARGET, self.c_to_deci(celsius), ot=1, d=0)

    async def set_mode(self, on: bool) -> Any:
        """
        Backward-compatible name used by the climate entity.
        With the updated mapping, this toggles HVAC (I:3): 0=off, 1=heat.
        """
        hvac_val = 1 if on else 0
        return await self._write_item(ITEM_HVAC, hvac_val, ot=1, d=0)

    async def set_hvac(self, *, heat: bool) -> Any:
        """Alias that makes intent explicit."""
        return await self.set_mode(heat)

    async def set_preset(self, preset: Union[str, int]) -> Any:
        """
        Set preset (I:6): 1=away, 2=home.
        Accepts either 'away'/'home' (case-insensitive) or 1/2.
        """
        if isinstance(preset, str):
            p = preset.strip().lower()
            if p == "away":
                code = 1
            elif p == "home":
                code = 2
            else:
                raise ValueError(f"Unsupported preset '{preset}' (expected 'away' or 'home')")
        else:
            code = int(preset)
            if code not in (1, 2):
                raise ValueError(f"Unsupported preset code {code} (expected 1 or 2)")
        return await self._write_item(ITEM_PRESET, code, ot=1, d=0)

    async def set_timed_hold(self, celsius: float, minutes: int) -> Any:
        # Timed override on target (I:1, OT:2) for D:<minutes>
        return await self._write_item(ITEM_TARGET, self.c_to_deci(celsius), ot=2, d=int(minutes))

    # --------------- Context manager ---------------
    async def __aenter__(self) -> "SecureControlsClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()
