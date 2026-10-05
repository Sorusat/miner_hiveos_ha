"""HTTP client for the HiveOS local CGI interface.

No cloud, no bearer tokens: the local web interface answers HTTP digest
authentication with the device root credentials.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import aiohttp

from .const import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    ENDPOINT_RESUME,
    ENDPOINT_START,
    ENDPOINT_STATUS,
    ENDPOINT_STOP,
    STATE_MINING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_SUSPENDED,
)

# At or above this 5-second hashrate (GH/s) the miner is actively mining.
MINING_THRESHOLD_GHS = 1.0

# Local bmminer TCP API, used only to tell suspended from stopped.
LOCAL_API_PORT = 4028


class HiveosMinerError(Exception):
    """Raised when the miner cannot be reached or refuses a command."""


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kv(blob: str, key: str) -> float | None:
    """Pull a single numeric ``key=value`` pair out of the devs[].freq blob."""
    match = re.search(rf"(?:^|,){re.escape(key)}=([0-9.]+)", blob or "")
    return _to_float(match.group(1)) if match else None


def _kvs(blob: str, key: str) -> str | None:
    """Pull a single string ``key=value`` pair out of the devs[].freq blob."""
    match = re.search(rf"(?:^|,){re.escape(key)}=([^,]*)", blob or "")
    return match.group(1).strip() if match else None


def _build_digest_auth(username: str, password: str):
    """aiohttp renamed digest auth; support both spellings."""
    factory = getattr(aiohttp, "DigestAuthMiddleware", None) or aiohttp.DigestAuth
    return factory(login=username, password=password)


class HiveosMinerApi:
    """Minimal async client for a single miner."""

    def __init__(
        self,
        host: str,
        username: str = DEFAULT_USERNAME,
        password: str = DEFAULT_PASSWORD,
        port: int = LOCAL_API_PORT,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._port = int(port)
        self._session = session
        self._owns_session = session is None
        self._auth = _build_digest_auth(username, password)

    @property
    def host(self) -> str:
        return self._host

    def _url(self, endpoint: str) -> str:
        return f"http://{self._host}{endpoint}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                auth=self._auth,
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def async_get(self, endpoint: str) -> Any:
        session = await self._ensure_session()
        try:
            async with session.get(self._url(endpoint)) as response:
                if response.status in (401, 403):
                    raise HiveosMinerError(
                        f"{self._host}: digest auth rejected for {endpoint}"
                    )
                if response.status == 404:
                    raise HiveosMinerError(f"{self._host}: no {endpoint} on device")
                if response.status >= 400:
                    raise HiveosMinerError(
                        f"{self._host}: HTTP {response.status} on {endpoint}"
                    )
                body = await response.text()
        except asyncio.TimeoutError as err:
            raise HiveosMinerError(f"{self._host}: timeout on {endpoint}") from err
        except aiohttp.ClientError as err:
            raise HiveosMinerError(f"{self._host}: {err}") from err

        try:
            return json.loads(body)
        except ValueError as err:
            raise HiveosMinerError(
                f"{self._host}: non-JSON reply on {endpoint}: {body[:120]!r}"
            ) from err

    async def async_command(self, endpoint: str) -> None:
        session = await self._ensure_session()
        try:
            async with session.get(self._url(endpoint)) as response:
                if response.status >= 400:
                    raise HiveosMinerError(
                        f"{self._host}: HTTP {response.status} on {endpoint}"
                    )
                await response.read()
        except asyncio.TimeoutError as err:
            raise HiveosMinerError(f"{self._host}: timeout on {endpoint}") from err
        except aiohttp.ClientError as err:
            raise HiveosMinerError(f"{self._host}: {err}") from err

    async def async_start(self) -> None:
        await self.async_command(ENDPOINT_START)

    async def async_stop(self) -> None:
        await self.async_command(ENDPOINT_STOP)

    async def async_resume(self) -> None:
        await self.async_command(ENDPOINT_RESUME)

    async def async_log_tail(self, limit: int = 4000) -> str:
        """Read the bmminer log from the local TCP API.

        Only source that distinguishes a suspended miner from a stopped one,
        so every failure here is non-fatal and returns an empty string.
        """
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=5
            )
            writer.write(json.dumps({"command": "get_log"}).encode() + b"\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(limit), timeout=5)
        except (asyncio.TimeoutError, OSError, ConnectionError):
            return ""
        finally:
            if writer is not None:
                writer.close()
        text = raw.decode(errors="replace")
        try:
            payload = json.loads(text)
        except ValueError:
            return text
        return str(payload.get("log") or payload.get("message") or "")

    async def async_validate(self) -> bool:
        data = await self.async_get(ENDPOINT_STATUS)
        return isinstance(data, dict) and "summary" in data


def derive_state(
    ghs5s: float | None, log: str = "", pending_start: bool = False
) -> str:
    """Resolve one of the miner states.

    ``pending_start`` covers the window right after start/resume, when the
    status JSON is entirely zeroed (elapsed, hashrate, devs and pools all
    empty) and therefore identical to a powered-off miner. Only the local log
    distinguishes those, and it can stay silent for the first few polls, so
    the issued command is tracked as a fallback.
    """
    if log:
        tail = log[-2000:]
        if "SUSPENDED" in tail:
            return STATE_SUSPENDED
        if "Starting" in tail:
            return STATE_STARTING
    if ghs5s and ghs5s >= MINING_THRESHOLD_GHS:
        return STATE_MINING
    return STATE_STARTING if pending_start else STATE_STOPPED


def parse_miner_status(
    data: dict[str, Any], log: str = "", pending_start: bool = False
) -> dict[str, Any]:
    """Turn the raw status JSON into the values the entities expose.

    Deliberately free of Home Assistant imports so it stays unit testable.
    """
    summary = data.get("summary") or {}
    devs = data.get("devs") or []

    temps: list[float] = []
    power = None
    boards = 0
    version = None
    if devs:
        blob = str(devs[0].get("freq") or "")
        temps = [
            value
            for value in (
                _kv(blob, "temp1"),
                _kv(blob, "temp2"),
                _kv(blob, "temp3"),
            )
            if value
        ]
        chip = [
            float(part)
            for group in re.findall(r"temp_chip[0-9]+=([0-9]+(?:-[0-9]+)?)", blob)
            for part in group.split("-")
        ]
        if chip:
            temps.append(max(chip))
        power = _kv(blob, "total_power")
        version = _kvs(blob, "miner_version")
        boards = len([d for d in devs if _to_float(d.get("temp"))])

    return {
        "state": derive_state(_to_float(summary.get("ghs5s")), log, pending_start),
        "hashrate": _to_float(summary.get("ghs5s")),
        "hashrate_avg": _to_float(summary.get("ghsav")),
        "uptime_minutes": _to_float(summary.get("elapsed")),
        "temperatures": temps,
        "temp_max": max(temps) if temps else None,
        "power": power,
        "accepted": _to_float(summary.get("accepted")),
        "rejected": _to_float(summary.get("rejected")),
        "boards_alive": boards,
        "miner_version": version,
        "pools": [
            {"url": p.get("url"), "status": p.get("status")}
            for p in (data.get("pools") or [])
            if p.get("url") not in ("*", "**")
        ],
    }
