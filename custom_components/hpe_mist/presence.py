"""Presence rules, kept free of Home Assistant so they can be tested alone.

A client is home as soon as any sighting (live stream message or REST sweep)
lists its MAC, and away once nothing has listed it for AWAY_AFTER_SECONDS.
Before the first sighting it counts as away. Every client seen is kept, so any
of them can be tracked; join / leave transitions are reported for events.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .const import AWAY_AFTER_SECONDS

_HEX = re.compile(r"[^0-9a-f]")


def mac_key(mac: Any) -> str:
    """'02:00:00:00:00:01' / '020000000001' -> '020000000001' ('' if not a MAC)."""
    key = _HEX.sub("", str(mac or "").lower())
    return key if len(key) == 12 else ""


def mac_pretty(mac: Any) -> str:
    key = mac_key(mac)
    return ":".join(key[i : i + 2] for i in range(0, 12, 2)) if key else ""


def client_label(client: Any) -> str:
    """Hostname, or vendor + last 3 MAC bytes for clients without one ('Canon a3:c7:c2')."""
    get = client.get if isinstance(client, dict) else lambda k: getattr(client, k, None)
    if hostname := str(get("hostname") or "").strip():
        return hostname
    return f"{get('manufacture') or 'Unknown'} {mac_pretty(get('mac'))[-8:]}".strip()


@dataclass
class Sighting:
    """What the controller last said about one client."""

    mac: str = ""
    last_seen: float | None = None
    home: bool = False
    site_id: str | None = None
    ip: str | None = None
    hostname: str | None = None
    manufacture: str | None = None
    ssid: str | None = None
    ap_mac: str | None = None
    rssi: float | None = None
    snr: int | None = None
    band: str | None = None
    vlan: int | None = None
    wired: bool = False
    port: str | None = None
    switch_mac: str | None = None

    @property
    def label(self) -> str:
        return client_label(self)


@dataclass
class PresenceTracker:
    """Turns sightings into home / away per client MAC."""

    macs: set[str] = field(default_factory=set)  # always kept, even before a sighting
    away_after: float = AWAY_AFTER_SECONDS
    state: dict[str, Sighting] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for m in {mac_key(m) for m in self.macs} - {""}:
            self.state.setdefault(m, Sighting(mac=m))

    def see(self, client: dict[str, Any], now: float) -> bool:
        """Record one sighting; True if the client just joined (was away or unknown)."""
        m = mac_key(client.get("mac"))
        if not m:
            return False
        s = self.state.setdefault(m, Sighting(mac=m))
        joined = not s.home
        s.last_seen = now
        s.home = True
        for key in ("site_id", "ip", "hostname", "manufacture", "ssid", "rssi", "snr", "band", "vlan", "wired", "port"):
            if client.get(key) not in (None, ""):
                setattr(s, key, client[key])
        s.ap_mac = mac_key(client.get("ap_mac")) or s.ap_mac
        s.switch_mac = mac_key(client.get("switch_mac")) or s.switch_mac
        return joined

    def evaluate(self, now: float) -> list[str]:
        """Apply the away rule; the MACs that just left."""
        left = []
        for m, s in self.state.items():
            home = s.last_seen is not None and now - s.last_seen < self.away_after
            if s.home and not home:
                left.append(m)
            s.home = home
        return left

    def update(self, clients: list[dict[str, Any]], now: float) -> tuple[list[str], list[str]]:
        """Apply one full sweep; (joined, left) MACs."""
        joined = [mac_key(c.get("mac")) for c in clients if self.see(c, now)]
        return joined, self.evaluate(now)
