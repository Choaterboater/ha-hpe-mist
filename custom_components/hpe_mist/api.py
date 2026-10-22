"""Small async Mist API client (no Home Assistant imports, so it is testable alone)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT_SECONDS, SEARCH_MAX_PAGES, STREAM_HEARTBEAT_SECONDS
from .errors import SourceAuthError, SourceBusyError, SourceError


class MistError(SourceError):
    """Mist could not be reached or answered with an error."""


class MistAuthError(MistError, SourceAuthError):
    """The token was refused (401), or may not read this (403)."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


class MistBusyError(MistError, SourceBusyError):
    """Mist asked us to slow down (429) or had a server error (5xx)."""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"Mist answered {status}")
        self.status = status
        self.retry_after = retry_after


def clean_host(host: str) -> str:
    return host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")


def stream_url_for(host: str) -> str | None:
    """wss URL of the Mist live stream for a REST host ('api.x' -> 'api-ws.x'), or None."""
    host = clean_host(host)
    if not host.startswith("api."):
        return None
    return f"wss://api-ws.{host.removeprefix('api.')}/api-ws/v1/stream"


class MistClient:
    """Read-only calls to the Mist REST API and live stream."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, token: str, stream_url: str | None = None
    ) -> None:
        self._session = session
        self._base = f"https://{clean_host(host)}/api/v1/"
        self.stream_url = stream_url or stream_url_for(host)
        self._headers = {"Authorization": f"Token {token.strip()}", "Accept": "application/json"}

    async def get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, body or {})

    async def put(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("PUT", path, body)

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """One API call; the token never appears in errors."""
        try:
            async with self._session.request(
                method,
                self._base + path,
                headers=self._headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status in (401, 403):
                    raise MistAuthError(f"Mist refused the token ({resp.status})", resp.status)
                if resp.status == 429 or resp.status >= 500:
                    raise MistBusyError(resp.status, _retry_after(resp.headers.get("Retry-After")))
                if resp.status >= 400:
                    raise MistError(f"Mist answered {resp.status} for {method} {path.split('?')[0]}")
                if method != "GET":
                    text = await resp.text()
                    return json.loads(text) if text.strip() else None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise MistError(f"Cannot reach Mist: {type(err).__name__}") from err

    async def self_info(self) -> dict[str, Any]:
        return await self.get("self")

    async def org_sites(self, org_id: str) -> dict[str, str]:
        """{site_id: site name} for the org."""
        return {s["id"]: s.get("name") or s["id"] for s in await self.get(f"orgs/{org_id}/sites") if s.get("id")}

    async def org_site_ids(self, org_id: str) -> list[str]:
        return list(await self.org_sites(org_id))

    async def site_clients(self, site_id: str) -> list[dict[str, Any]]:
        return await self.get(f"sites/{site_id}/stats/clients?limit=1000")

    async def site_devices(self, site_id: str) -> list[dict[str, Any]]:
        """Stats for every AP, switch and gateway of the site (one call)."""
        return await self.get(f"sites/{site_id}/stats/devices?type=all&limit=1000")

    async def org_alarms(self, org_id: str) -> list[dict[str, Any]]:
        """Alarms raised in the last day, across the org (one call)."""
        found = await self.get(f"orgs/{org_id}/alarms/search?duration=1d&limit=1000")
        return [a for a in (found or {}).get("results", []) if isinstance(a, dict)]

    async def site_ports(self, site_id: str) -> list[dict[str, Any]]:
        """Port stats of every switch and gateway of the site (one call, paged only past 1000 ports)."""
        return await self.search(f"sites/{site_id}/stats/ports/search?limit=1000")

    async def org_clients_seen(self, org_id: str, wired: bool = False) -> list[dict[str, Any]]:
        """Wi-Fi (or wired) clients seen in the last 7 days, connected or not."""
        kind = "wired_clients" if wired else "clients"
        return await self.search(f"orgs/{org_id}/{kind}/search?duration=7d&limit=1000")

    async def search(self, path: str, max_pages: int = SEARCH_MAX_PAGES) -> list[dict[str, Any]]:
        """Results of a Mist search, following "next" for at most max_pages pages."""
        out: list[dict[str, Any]] = []
        for _ in range(max_pages):
            found = await self.get(path) or {}
            out.extend(r for r in found.get("results") or [] if isinstance(r, dict))
            nxt = str(found.get("next") or "")
            if not nxt:
                break
            path = nxt.split("/api/v1/", 1)[-1].lstrip("/")
        return out

    async def site_wlans(self, site_id: str) -> list[dict[str, Any]]:
        return await self.get(f"sites/{site_id}/wlans")

    async def org_wlans(self, org_id: str) -> list[dict[str, Any]]:
        return await self.get(f"orgs/{org_id}/wlans")

    async def restart_device(self, site_id: str, device_id: str) -> None:
        await self.post(f"sites/{site_id}/devices/{device_id}/restart")

    async def set_wlan_enabled(self, org_id: str, site_id: str | None, wlan_id: str, enabled: bool) -> None:
        owner = f"sites/{site_id}" if site_id else f"orgs/{org_id}"
        await self.put(f"{owner}/wlans/{wlan_id}", {"enabled": enabled})

    async def org_clients(self, site_ids: list[str]) -> list[dict[str, Any]]:
        """Clients connected now across the given sites (each row carries its site_id)."""
        out: list[dict[str, Any]] = []
        for site, rows in zip(site_ids, await asyncio.gather(*(self.site_clients(s) for s in site_ids)), strict=True):
            out.extend({"site_id": site, **r} for r in rows if isinstance(r, dict))
        return out

    async def stream(
        self,
        site_ids: list[str],
        on_message: Callable[[str, dict[str, Any]], None],
        on_connected: Callable[[], None],
    ) -> None:
        """Subscribe to every site's client and device stats; on_message(kind, data) per frame,
        kind being "client" or "device".

        Returns or raises MistError when the connection ends; the caller reconnects.
        """
        if not self.stream_url:
            raise MistError("No live stream for this host")
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                ws = await self._session.ws_connect(
                    self.stream_url, headers=self._headers, heartbeat=STREAM_HEARTBEAT_SECONDS
                )
            async with ws:
                for site in site_ids:
                    await ws.send_json({"subscribe": f"/sites/{site}/stats/clients"})
                    await ws.send_json({"subscribe": f"/sites/{site}/stats/devices"})
                on_connected()
                async for msg in ws:
                    if msg.type is aiohttp.WSMsgType.TEXT:
                        if (parsed := parse_stream_message(msg.data)) is not None:
                            on_message(*parsed)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        break
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise MistAuthError(f"Mist refused the token for the stream ({err.status})", err.status) from err
            raise MistError(f"Stream handshake failed ({err.status})") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise MistError(f"Stream lost: {type(err).__name__}") from err


_STREAM_KINDS = {"/stats/clients": "client", "/stats/devices": "device"}


def parse_stream_message(raw: str) -> tuple[str, dict[str, Any]] | None:
    """("client" | "device", data with its site_id) from a stream frame, or None for other frames."""
    try:
        frame = json.loads(raw)
        channel = str(frame.get("channel", ""))
        kind = next((k for suffix, k in _STREAM_KINDS.items() if channel.endswith(suffix)), None)
        if frame.get("event") != "data" or kind is None:
            return None
        data = frame.get("data")
        item = json.loads(data) if isinstance(data, str) else data
    except (ValueError, AttributeError):
        return None
    if not isinstance(item, dict) or not item.get("mac"):
        return None
    parts = channel.split("/")
    if "site_id" not in item and len(parts) > 2 and parts[1] == "sites":
        item = {**item, "site_id": parts[2]}
    return kind, item


def can_write_org(info: dict[str, Any], org_id: str) -> bool:
    """True if the token's role in the org allows changes (admin or write)."""
    return any(
        p.get("scope") == "org" and p.get("org_id") == org_id and p.get("role") in ("admin", "write")
        for p in info.get("privileges", [])
    )


def orgs_from_self(info: dict[str, Any]) -> dict[str, str]:
    """{org_id: org name} for every org the token can see."""
    orgs: dict[str, str] = {}
    for p in info.get("privileges", []):
        if p.get("scope") == "org" and p.get("org_id"):
            orgs[p["org_id"]] = p.get("name") or p.get("org_name") or p["org_id"]
    return orgs


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None
