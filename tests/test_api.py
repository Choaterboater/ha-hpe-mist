"""The real stream code against a local websocket server standing in for Mist."""

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web

from custom_components.hpe_mist.api import MistAuthError, MistClient, MistError

TOKEN = "test-token-not-real"


async def serve(handler):
    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/ws"


async def test_stream_subscribes_each_site_and_delivers_clients(socket_enabled):
    got_headers, subscribed = {}, []

    async def handler(request):
        got_headers.update(request.headers)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for _ in range(4):
            subscribed.append(json.loads((await ws.receive()).data))
        await ws.send_str(json.dumps({"event": "channel_subscribed", "channel": "/sites/s1/stats/clients"}))
        await ws.send_str(json.dumps({"event": "data", "channel": "/sites/s1/stats/clients",
                                      "data": json.dumps({"mac": "020000000076", "ip": "192.0.2.10"})}))
        await ws.send_str(json.dumps({"event": "data", "channel": "/sites/s2/stats/devices",
                                      "data": json.dumps({"mac": "020000000001", "uptime": 5})}))
        await ws.close()
        return ws

    runner, url = await serve(handler)
    try:
        async with aiohttp.ClientSession() as session:
            client = MistClient(session, "api.mist.com", TOKEN, stream_url=url)
            seen, connected = [], []
            await client.stream(["s1", "s2"], lambda kind, data: seen.append((kind, data)), lambda: connected.append(True))
    finally:
        await runner.cleanup()
    assert got_headers["Authorization"] == f"Token {TOKEN}"
    assert subscribed == [
        {"subscribe": "/sites/s1/stats/clients"}, {"subscribe": "/sites/s1/stats/devices"},
        {"subscribe": "/sites/s2/stats/clients"}, {"subscribe": "/sites/s2/stats/devices"},
    ]
    assert connected == [True]
    assert seen == [
        ("client", {"mac": "020000000076", "ip": "192.0.2.10", "site_id": "s1"}),
        ("device", {"mac": "020000000001", "uptime": 5, "site_id": "s2"}),
    ]


@pytest.mark.parametrize(("status", "error"), [(401, MistAuthError), (502, MistError)])
async def test_stream_refused(socket_enabled, status, error):
    async def handler(request):
        return web.Response(status=status)

    runner, url = await serve(handler)
    try:
        async with aiohttp.ClientSession() as session:
            client = MistClient(session, "api.mist.com", TOKEN, stream_url=url)
            with pytest.raises(error) as info:
                await asyncio.wait_for(client.stream(["s1"], print, print), 5)
    finally:
        await runner.cleanup()
    assert TOKEN not in str(info.value)


def fake_get(pages):
    """MistClient.get answering from {path: answer}; an int answer is that HTTP status."""
    asked = []

    async def get(path):
        asked.append(path)
        answer = pages[path]
        if answer == 403:
            raise MistAuthError("Mist refused the token (403)", 403)
        return answer

    return get, asked


async def test_seen_client_search_follows_next_and_stops_after_five_pages():
    from custom_components.hpe_mist.source import MistSource

    org = "o1"
    first = f"orgs/{org}/clients/search?duration=7d&limit=1000"
    nxt = f"/api/v1/orgs/{org}/clients/search?end=1792676169.9&limit=1000&search_after=%5B1%5D&start=1792071369.9"
    pages = {
        first: {"results": [{"mac": "020000000057", "last_ip": "192.0.2.57", "band": "24", "timestamp": 1.0}],
                "next": nxt, "total": 2},
        nxt.removeprefix("/api/v1/"): {"results": [{"mac": "020000001b48", "last_hostname": "espressif"}], "total": 2},
        f"orgs/{org}/wired_clients/search?duration=7d&limit=1000": {"results": [
            {"mac": "02000000fe62", "last_ip": "192.0.2.15", "manufacture": "Proxmox Server Solutions GmbH",
             "last_port_id": "ge-0/0/2", "last_device_mac": "020000000301", "last_vlan": 10}]},
    }
    client = MistClient(None, "api.mist.com", TOKEN)
    client.get, asked = fake_get(pages)
    seen = await MistSource(client, org).seen_clients()
    assert [c["mac"] for c in seen] == ["020000000057", "020000001b48", "02000000fe62"]
    assert seen[0]["band"] == "2.4 GHz" and seen[1]["hostname"] == "espressif"
    assert seen[2]["wired"] is True and seen[2]["port"] == "ge-0/0/2" and seen[2]["vlan"] == 10
    assert len(asked) == 3

    # A "next" that never ends: at most 5 pages.
    loop = {"p": {"results": [{"mac": "020000000001"}], "next": "/api/v1/p"}}
    client.get, asked = fake_get(loop)
    assert len(await client.search("p")) == 5 and len(asked) == 5


async def test_seen_client_search_403_is_skipped_401_is_not():
    from custom_components.hpe_mist.source import MistSource

    org = "o1"
    pages = {
        f"orgs/{org}/clients/search?duration=7d&limit=1000": 403,
        f"orgs/{org}/wired_clients/search?duration=7d&limit=1000": {"results": [{"mac": "02000000fe62"}]},
    }
    client = MistClient(None, "api.mist.com", TOKEN)
    client.get, _ = fake_get(pages)
    assert [c["mac"] for c in await MistSource(client, org).seen_clients()] == ["02000000fe62"]

    async def refused(path):
        raise MistAuthError("Mist refused the token (401)", 401)

    client.get = refused
    with pytest.raises(MistAuthError):
        await MistSource(client, org).seen_clients()
