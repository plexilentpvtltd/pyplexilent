"""Async client for the Plexilent cloud: lights, switches, fans and curtains.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp

__all__ = [
    "BASE_URL",
    "Device",
    "Plexilent",
    "PlexilentAuthError",
    "PlexilentConnectionError",
    "PlexilentError",
]

BASE_URL = "https://asia-southeast1-plexilent.cloudfunctions.net"
CLIENT_ID = "plexilent-homeassistant"
TIMEOUT = aiohttp.ClientTimeout(total=15)


class PlexilentError(Exception):
    """Any API failure."""


class PlexilentAuthError(PlexilentError):
    """The account must sign in again (wrong password, or the link was removed)."""


class PlexilentConnectionError(PlexilentError):
    """The cloud could not be reached or answered with a server error."""


@dataclass(frozen=True, slots=True)
class Device:
    """One device as the cloud reports it.

    type: "5ch" (colour + white), "ct" (white), "dim", "onoff", "switch" (wall panel gang),
    "fan", "curtain".
    brightness and position are percent; cct is Kelvin; hs is (hue 0-360, saturation 0-100).
    """

    id: str
    name: str
    type: str
    home: str
    online: bool
    room: str | None = None
    on: bool | None = None
    brightness: int | None = None
    cct: int | None = None
    cct_min: int | None = None
    cct_max: int | None = None
    hs: tuple[float, float] | None = None
    speed: int | None = None
    speeds: int | None = None
    position: int | None = None
    moving: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Device:
        hs = d.get("hs")
        return cls(
            id=d["id"],
            name=d["name"],
            type=d["type"],
            home=d["home"],
            online=bool(d.get("online")),
            room=d.get("room"),
            on=d.get("on"),
            brightness=d.get("brightness"),
            cct=d.get("cct"),
            cct_min=d.get("cctMin"),
            cct_max=d.get("cctMax"),
            hs=(hs[0], hs[1]) if hs else None,
            speed=d.get("speed"),
            speeds=d.get("speeds"),
            position=d.get("position"),
            moving=d.get("moving"),
        )


class Plexilent:
    """One signed-in account. Keep `refresh_token` to resume without the password."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
    ) -> None:
        self._session = session
        self._base = BASE_URL
        self.refresh_token = refresh_token
        self._access: str | None = None

    async def login(self, email: str, password: str) -> str:
        """Sign in; returns the refresh token (the password is not kept)."""
        body = await self._token(
            {"grant_type": "password", "username": email, "password": password}
        )
        self.refresh_token = body["refresh_token"]
        return self.refresh_token

    async def devices(self) -> list[Device]:
        """Every light, switch, fan and curtain in every home of this account."""
        body = await self._api("GET", "/devices")
        return [Device.from_dict(d) for d in body["devices"]]

    async def command(
        self,
        device_id: str,
        *,
        on: bool | None = None,
        brightness: int | None = None,
        cct: int | None = None,
        hs: tuple[float, float] | None = None,
        speed: int | None = None,
        position: int | None = None,
        stop: bool | None = None,
    ) -> Device:
        """Change a device; returns its new state. Only the given fields are sent."""
        fields = {
            "on": on,
            "brightness": brightness,
            "cct": cct,
            "hs": list(hs) if hs is not None else None,
            "speed": speed,
            "position": position,
            "stop": stop,
        }
        body = await self._api(
            "POST",
            f"/devices/{quote(device_id, safe='')}",
            {k: v for k, v in fields.items() if v is not None},
        )
        return Device.from_dict(body["device"])

    async def unlink(self) -> None:
        """Revoke this account's refresh tokens for this client."""
        await self._api("DELETE", "/link")
        self.refresh_token = self._access = None

    async def _token(self, form: dict[str, str]) -> dict[str, Any]:
        status, body = await self._request(
            "POST",
            f"{self._base}/voiceToken",
            data={**form, "client_id": CLIENT_ID},
        )
        if status == 400 and body.get("error") == "invalid_grant":
            raise PlexilentAuthError("sign-in rejected")
        if status != 200 or "access_token" not in body:
            raise PlexilentConnectionError(f"token endpoint HTTP {status}")
        self._access = body["access_token"]
        return body

    async def _api(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.refresh_token:
            raise PlexilentAuthError("not signed in")
        for attempt in range(2):
            if self._access is None:
                await self._token(
                    {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
                )
            status, body = await self._request(
                method,
                f"{self._base}/voiceHomeAssistant{path}",
                json=json,
                headers={"Authorization": f"Bearer {self._access}"},
            )
            if status == 401 and body.get("error") == "token_expired" and attempt == 0:
                self._access = None
                continue
            if status == 401:
                raise PlexilentAuthError(body.get("error", "unauthorized"))
            if status >= 500:
                raise PlexilentConnectionError(f"HTTP {status}")
            if status != 200:
                raise PlexilentError(body.get("error", f"HTTP {status}"))
            return body
        raise PlexilentAuthError("token refresh did not help")  # pragma: no cover

    async def _request(self, method: str, url: str, **kw: Any) -> tuple[int, dict[str, Any]]:
        try:
            async with self._session.request(method, url, timeout=TIMEOUT, **kw) as r:
                try:
                    body = await r.json(content_type=None)
                except ValueError:
                    body = {}
                return r.status, body if isinstance(body, dict) else {}
        except (aiohttp.ClientError, TimeoutError) as e:
            raise PlexilentConnectionError(str(e)) from e
