"""Every client a sweep has listed, remembered across restarts for the options picker."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SEEN_DAYS, SEEN_SAVE_DELAY_SECONDS
from .presence import mac_key
from .source import Client

STORE_VERSION = 1
_KEYS = ("mac", "site_id", "ip", "user_name", "hostname", "manufacture", "model", "ssid", "ap_mac", "band", "wired", "port",
         "switch_mac", "vlan", "last_seen")


def store_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.seen_clients"


class SeenClients:
    """{mac_key: client} with last_seen; entries older than SEEN_DAYS are dropped."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key(entry_id))
        self.clients: dict[str, Client] = {}
        self._save_due = 0.0

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.clients = {m: c for m, c in (data.get("clients") or {}).items() if isinstance(c, dict)}
        self._prune(time.time())

    @callback
    def record(self, clients: list[Client], now: float) -> None:
        """Remember the clients connected now; saved within SEEN_SAVE_DELAY_SECONDS."""
        for c in clients:
            if not (m := mac_key(c.get("mac"))):
                continue
            new = {k: c.get(k) for k in _KEYS if c.get(k) not in (None, "")}
            self.clients[m] = {**self.clients.get(m, {}), **new, "mac": m, "last_seen": now}
        self._prune(now)
        # One pending save at a time: re-asking would push it back with every 30 s sweep.
        if now >= self._save_due:
            self._save_due = now + SEEN_SAVE_DELAY_SECONDS
            self._store.async_delay_save(lambda: {"clients": self.clients}, SEEN_SAVE_DELAY_SECONDS)

    def _prune(self, now: float) -> None:
        cutoff = now - SEEN_DAYS * 86400
        self.clients = {m: c for m, c in self.clients.items() if (c.get("last_seen") or 0) >= cutoff}
