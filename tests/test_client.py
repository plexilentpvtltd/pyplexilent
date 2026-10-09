"""The client against a fake Plexilent cloud (aiohttp test server)."""

import aiohttp
import pyplexilent
import pytest
from aiohttp import web
from pyplexilent import Plexilent, PlexilentAuthError, PlexilentConnectionError

DEV = {
    "id": "m:2",
    "name": "Kitchen",
    "type": "5ch",
    "home": "m",
    "online": True,
    "on": True,
    "brightness": 40,
    "cct": None,
    "cctMin": 2700,
    "cctMax": 6500,
    "hs": [120, 100],
}


@pytest.fixture
async def cloud(monkeypatch):
    state = {"access": "a1", "expire_next": False, "calls": []}

    async def token(req):
        f = dict(await req.post())
        state["calls"].append(("token", f["grant_type"]))
        assert f["client_id"] == "plexilent-homeassistant"
        if f["grant_type"] == "password":
            if f["password"] != "pw":
                return web.json_response({"error": "invalid_grant"}, status=400)
            return web.json_response({"access_token": state["access"], "refresh_token": "r1"})
        if f.get("refresh_token") != "r1":
            return web.json_response({"error": "invalid_grant"}, status=400)
        state["access"] = "a2"
        return web.json_response({"access_token": "a2"})

    def authed(req):
        if state["expire_next"]:
            state["expire_next"] = False
            return web.json_response({"error": "token_expired"}, status=401)
        if req.headers.get("Authorization") != f"Bearer {state['access']}":
            return web.json_response({"error": "invalid_token"}, status=401)
        return None

    async def devices(req):
        return authed(req) or web.json_response({"devices": [DEV]})

    async def command(req):
        state["calls"].append(("cmd", req.match_info["id"], await req.json()))
        return authed(req) or web.json_response({"device": {**DEV, "on": False}})

    async def link(req):
        return authed(req) or web.json_response({})

    app = web.Application()
    app.router.add_post("/voiceToken", token)
    app.router.add_get("/voiceHomeAssistant/devices", devices)
    app.router.add_post("/voiceHomeAssistant/devices/{id}", command)
    app.router.add_delete("/voiceHomeAssistant/link", link)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(pyplexilent, "BASE_URL", f"http://127.0.0.1:{port}")
    yield state
    await runner.cleanup()


async def test_login_list_command_refresh(cloud):
    async with aiohttp.ClientSession() as s:
        p = Plexilent(s)
        with pytest.raises(PlexilentAuthError):
            await p.login("me@x.com", "bad")
        assert await p.login("me@x.com", "pw") == "r1"
        [d] = await p.devices()
        assert (d.type, d.hs, d.cct_max, d.room) == ("5ch", (120, 100), 6500, None)

        cloud["expire_next"] = True  # access token expired: refreshed once, then retried
        d = await p.command("m:2", on=False)
        assert d.on is False
        assert cloud["calls"][-1] == ("cmd", "m:2", {"on": False})
        assert ("token", "refresh_token") in cloud["calls"]

        # Resuming from a stored refresh token alone.
        p2 = Plexilent(s, refresh_token="r1")
        assert len(await p2.devices()) == 1
        await p2.unlink()
        with pytest.raises(PlexilentAuthError):
            await p2.devices()

        with pytest.raises(PlexilentAuthError):
            await Plexilent(s, refresh_token="revoked").devices()


async def test_unreachable(monkeypatch):
    monkeypatch.setattr(pyplexilent, "BASE_URL", "http://127.0.0.1:9")
    async with aiohttp.ClientSession() as s:
        with pytest.raises(PlexilentConnectionError):
            await Plexilent(s).login("me@x.com", "pw")
