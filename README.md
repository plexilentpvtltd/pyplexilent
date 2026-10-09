# pyplexilent

Async Python client for the Plexilent smart-lighting cloud: the lights, wall-panel switches,
fans and curtains of every home in an account, through the home's Wi-Fi gateway. Used by the
Home Assistant `plexilent` integration.

```python
async with aiohttp.ClientSession() as s:
    p = Plexilent(s)
    refresh = await p.login("me@example.com", "password")   # store this, not the password
    for d in await p.devices():
        print(d.name, d.type, d.on)
    await p.command(d.id, on=True, brightness=60)
```

Sign-in uses the Plexilent app account once; keep the returned refresh token, not the password.
