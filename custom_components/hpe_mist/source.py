"""Where sightings, device stats and alarms come from.

The coordinator only talks to a PresenceSource, so another controller (for
example HPE Central) can be built on the same design. Everything a source
returns is already normalised into the plain dicts described below.

Client: mac, site_id, ip, user_name (login, when it is not the MAC), hostname, manufacture,
        model (Mist fingerprint), ssid, ap_mac, rssi, snr, band ("2.4 GHz" | "5 GHz" | "6 GHz"),
        wired, port (switch port when wired), switch_mac (wired), vlan, last_seen (epoch s),
        details {DETAIL_FIELDS key: value, connected_since (epoch s)} for the client list card.
Device: mac, site_id, type ("ap" | "switch" | "gateway"), name, model, serial,
        ip, version, online, uptime (s), clients, cpu (%), memory (%),
        temperature (C),
        radios {band_24 | band_5 | band_6: {band, status ("up" | "down" | "disabled"),
                channel (str), bandwidth (MHz), power (dBm), utilization (%),
                noise_floor (dBm), clients}},
        ports {port_id: {up, speed (Mbps), uplink, poe_w, neighbor, neighbor_port,
               vlan, description}} (physical ports only, in natural order),
        uplink {device, port, speed (Mbps), poe_w} (APs),
        poe {draw_w, budget_w} (switches), vlans [{id, name}] (switches),
        wlans [{ssid, enabled, vlan, clients}] (APs).
Alarm:  id, site_id, type, severity, count, acked, timestamp.
Wlan:   id, ssid, enabled, site_id (None for an org-wide WLAN).
Devices also carry "id", the controller's own id, for controls.
Any value may be None when the controller does not report it (uplink, poe,
vlans and wlans as a whole too).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .api import MistAuthError, MistClient, MistError, can_write_org
from .const import DETAIL_SECONDS
from .presence import mac_key, mac_pretty

_LOGGER = logging.getLogger(__name__)

Client = dict[str, Any]
Device = dict[str, Any]
Alarm = dict[str, Any]
Wlan = dict[str, Any]
Port = dict[str, Any]
CLIENT_KEYS = ("mac", "site_id", "ip", "hostname", "manufacture", "ssid", "ap_mac", "rssi")
BANDS = {"band_24": "2.4 GHz", "band_5": "5 GHz", "band_6": "6 GHz"}
_CLIENT_BANDS = {"24": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"}
# A sweep can land a little before the detail interval is up (timer jitter).
_DETAIL_SLACK_SECONDS = 10


@dataclass
class Snapshot:
    """One full sweep."""

    sites: dict[str, str] = field(default_factory=dict)  # site_id -> name
    clients: list[Client] = field(default_factory=list)  # connected now
    devices: list[Device] = field(default_factory=list)
    alarms: list[Alarm] = field(default_factory=list)


class PresenceSource(Protocol):
    """A controller that can sweep everything and, optionally, stream changes."""

    @property
    def has_stream(self) -> bool: ...

    async def sweep(self) -> Snapshot:
        """Everything now. Raises SourceError (or a subclass) on failure."""

    async def clients(self) -> list[Client]:
        """Only the clients connected now (for the options picker)."""

    async def seen_clients(self) -> list[Client]:
        """Clients seen lately, connected or not (for the options picker); [] if not offered."""

    async def stream(
        self,
        on_client: Callable[[Client], None],
        on_device: Callable[[Device], None],
        on_connected: Callable[[], None],
    ) -> None:
        """Run one live connection until it ends. Device updates may be partial."""

    def forget_sites(self) -> None:
        """Drop cached site ids so the next call asks again."""

    # Controls: only used when can_write() is True.

    async def can_write(self) -> bool:
        """True if the credentials may change things."""

    async def wlans(self) -> list[Wlan]:
        """Every WLAN, org-wide and per site."""

    async def restart_device(self, device: Device) -> None: ...

    async def set_wlan_enabled(self, wlan: Wlan, enabled: bool) -> None: ...


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return int(v) if _num(v) is not None else None


def _vlan(v: Any) -> int | None:
    vlan = _int(v)
    return vlan if vlan is not None and vlan > 0 else None


def _model(v: Any) -> str | None:
    """Mist's fingerprint model; search rows give a list, and "Unknown" means none."""
    for m in v if isinstance(v, list) else [v]:
        if isinstance(m, str) and m.strip() and m.strip().lower() != "unknown":
            return m.strip()
    return None


def _user_name(c: dict[str, Any], *keys: str) -> str | None:
    """The login the client authenticated with; MAC auth logs in as the MAC, which is no name."""
    for key in keys:
        name = c.get(key)
        name = (name[-1] if name else None) if isinstance(name, list) else name
        if isinstance(name, str) and name.strip() and mac_key(name) != mac_key(c.get("mac")):
            return name.strip()
    return None


# Everything else Mist says about a client, for the client list card: {our key: Mist's keys}.
DETAIL_FIELDS = {
    "family": ("family", "last_family"),
    "os": ("os", "last_os"),
    "os_version": ("os_version", "last_os_version"),
    "group": ("group",),
    "channel": ("channel",),
    "bssid": ("bssid",),
    "phy": ("proto",),
    "key_mgmt": ("key_mgmt",),
    "psk_name": ("psk_name",),
    "is_guest": ("is_guest",),
    "tx_rate": ("tx_rate",),
    "rx_rate": ("rx_rate",),
    "tx_bps": ("tx_bps",),
    "rx_bps": ("rx_bps",),
    "idle_time": ("idle_time",),
    "vlan_name": ("last_vlan_name",),
}


def client_details(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, sources in DETAIL_FIELDS.items():
        for src in sources:
            value = c.get(src)
            value = (value[-1] if value else None) if isinstance(value, list) else value
            if isinstance(value, str):
                value = value.strip()
                if not value or value.lower() == "unknown":
                    continue
            if isinstance(value, (str, int, float, bool)):
                out[key] = value
                break
    if (assoc := _num(c.get("assoc_time"))) is not None and assoc > 0:
        out["connected_since"] = assoc
    elif (uptime := _num(c.get("uptime"))) is not None:
        out["connected_since"] = time.time() - uptime
    return out


def client_from_mist(c: dict[str, Any]) -> Client:
    """A client connected now (/stats/clients row or stream frame)."""
    return {
        **{k: c.get(k) for k in CLIENT_KEYS},
        "user_name": _user_name(c, "username"),
        "model": _model(c.get("model")),
        "details": client_details(c),
        "snr": _int(c.get("snr")),
        "band": _CLIENT_BANDS.get(str(c.get("band"))),
        "wired": False,
        "port": None,
        "switch_mac": None,
        "vlan": _vlan(c.get("vlan_id")),
        "last_seen": _num(c.get("last_seen")) or time.time(),
    }


def seen_client_from_mist(c: dict[str, Any], wired: bool) -> Client:
    """One row of orgs/<org>/clients/search or wired_clients/search (last 7 days)."""
    return {
        "mac": c.get("mac"),
        "site_id": c.get("site_id"),
        "ip": c.get("last_ip") or None,
        "user_name": _user_name(c, "last_username", "username"),
        "hostname": c.get("last_hostname") or None,
        "manufacture": c.get("mfg") or c.get("manufacture") or None,
        "model": _model(c.get("last_model")) or _model(c.get("model")),
        "ssid": None if wired else c.get("last_ssid") or None,
        "ap_mac": None if wired else c.get("last_ap") or None,
        "rssi": None,
        "snr": None,
        "band": None if wired else _CLIENT_BANDS.get(str(c.get("band"))),
        "wired": wired,
        "port": (c.get("last_port_id") or None) if wired else None,
        "switch_mac": (mac_key(c.get("last_device_mac")) or None) if wired else None,
        "vlan": _vlan(c.get("last_vlan")),
        "last_seen": _num(c.get("timestamp")),
    }


def _physical_port(port_id: str) -> bool:
    return port_id.startswith(("ge-", "xe-", "mge-", "et-")) and "." not in port_id


def port_sort_key(port_id: str) -> list[Any]:
    """Natural order: ge-0/0/2 before ge-0/0/10, 1/1/2 before 1/1/10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", port_id)]


def _natural(ports: dict[str, Port]) -> dict[str, Port]:
    return {p: ports[p] for p in sorted(ports, key=port_sort_key)}


def _port(up: bool, **found: Any) -> Port:
    return {"up": up, "speed": None, "uplink": False, "poe_w": None, "neighbor": None,
            "neighbor_port": None, "vlan": None, "description": None, **found}


def port_from_mist(r: dict[str, Any]) -> Port:
    """One row of sites/<site>/stats/ports/search."""
    draw = _num(r.get("power_draw"))
    return _port(
        bool(r.get("up")),
        speed=_int(r.get("speed")) or None,
        uplink=bool(r.get("uplink")),
        poe_w=float(draw) if draw is not None else None,
        neighbor=r.get("neighbor_system_name") or mac_pretty(r.get("neighbor_mac")) or None,
        neighbor_port=r.get("neighbor_port_desc") or r.get("neighbor_port_id") or None,
        vlan=_vlan(r.get("vlan")),
        description=r.get("port_desc") or r.get("description") or r.get("port_usage") or None,
    )


def ports_from_mist(rows: list[dict[str, Any]]) -> dict[str, dict[str, Port]]:
    """{device mac: {port_id: port}} from a site's port search; physical ports only."""
    out: dict[str, dict[str, Port]] = {}
    for r in rows:
        port_id = str(r.get("port_id") or "")
        if (mac := mac_key(r.get("mac"))) and _physical_port(port_id):
            out.setdefault(mac, {})[port_id] = port_from_mist(r)
    return {mac: _natural(ports) for mac, ports in out.items()}


def merge_ports(dev: Device, found: dict[str, Port] | None) -> None:
    """Add the port search's details; link state stays the sweep's own where it has the port."""
    if not found:
        return
    ports = {p: dict(v) for p, v in found.items()}
    for port_id, now in (dev.get("ports") or {}).items():
        ports[port_id] = {**ports[port_id], "up": now["up"]} if port_id in ports else now
    dev["ports"] = _natural(ports)


def _radio(band: str, r: dict[str, Any]) -> dict[str, Any]:
    channel = _int(r.get("channel"))
    status = "disabled" if r.get("disabled") else "up" if channel else "down"
    power, noise = _num(r.get("power")), _num(r.get("noise_floor"))
    return {
        "band": BANDS[band],
        "status": status,
        "channel": str(channel) if channel else None,
        "bandwidth": _int(r.get("bandwidth")) or None,
        "power": float(power) if power is not None else None,
        "utilization": _num(r.get("util_all")),
        "noise_floor": float(noise) if noise is not None else None,
        "clients": _int(r.get("num_clients")),
    }


def _uplink(d: dict[str, Any]) -> dict[str, Any] | None:
    """An AP's wired uplink from LLDP and its port stats (LLDP power_draw is in mW)."""
    lldp = d.get("lldp_stat") or {}
    port = (d.get("port_stat") or {}).get(lldp.get("ap_port_name") or "eth0") or {}
    draw = _num(lldp.get("power_draw"))
    uplink = {
        "device": lldp.get("system_name") or None,
        "port": lldp.get("port_desc") or lldp.get("port_id") or None,
        "speed": _int(port.get("speed")) or None,
        "poe_w": round(draw / 1000, 1) if draw else None,
    }
    return uplink if any(v is not None for v in uplink.values()) else None


def _poe(d: dict[str, Any]) -> dict[str, Any] | None:
    """Switch PoE totals from module_stat[].poe (empty on switches without PoE)."""
    draws, budgets = [], []
    for m in d.get("module_stat") or []:
        poe = (m.get("poe") if isinstance(m, dict) else None) or {}
        if _num(poe.get("power_draw")) is not None:
            draws.append(poe["power_draw"])
        if _num(poe.get("max_power")) is not None:
            budgets.append(poe["max_power"])
    if not draws and not budgets:
        return None
    return {"draw_w": round(sum(draws), 1) if draws else None, "budget_w": round(sum(budgets), 1) if budgets else None}


def device_from_mist(d: dict[str, Any]) -> Device:
    """Normalise one entry of /sites/<id>/stats/devices."""
    mac = mac_key(d.get("mac"))
    kind = d.get("type")
    cpu = _num(d.get("cpu_util"))
    if cpu is None and _num((d.get("cpu_stat") or {}).get("idle")) is not None:
        cpu = round(100.0 - d["cpu_stat"]["idle"], 1)
    memory = _num((d.get("memory_stat") or {}).get("usage"))
    if memory is None and _num(d.get("mem_used_kb")) is not None and _num(d.get("mem_total_kb")):
        memory = round(100.0 * d["mem_used_kb"] / d["mem_total_kb"], 1)
    # Some models report 0 when they have no sensor; treat that as not reported.
    temperature = _num((d.get("env_stat") or {}).get("cpu_temp")) or None
    if temperature is None:
        temps = [
            t["celsius"]
            for m in d.get("module_stat") or []
            if isinstance(m, dict)
            for t in m.get("temperatures") or []
            if isinstance(t, dict) and _num(t.get("celsius"))
        ]
        temperature = max(temps) if temps else None
    clients = _num(d.get("num_clients"))
    if clients is None:
        clients = _num(((d.get("clients_stats") or {}).get("total") or {}).get("num_wired_clients"))
    radio_stat = d.get("radio_stat") or {}
    radios = {band: _radio(band, radio_stat[band]) for band in BANDS if isinstance(radio_stat.get(band), dict)}
    # Link state from the device stats; the port search adds the details (merge_ports).
    ports: dict[str, Port] = {}
    for p in (d.get("if_stat") or {}).values():
        if isinstance(p, dict) and _physical_port(str(p.get("port_id", ""))):
            was = ports.get(p["port_id"])
            ports[p["port_id"]] = _port(bool(was and was["up"]) or bool(p.get("up")), vlan=_vlan(p.get("vlan")))
    return {
        "mac": mac,
        "id": d.get("id"),
        "site_id": d.get("site_id"),
        "type": kind,
        "name": d.get("name") or d.get("hostname") or mac,
        "model": d.get("model") or None,
        "serial": d.get("serial") or None,
        "ip": d.get("ip") or None,
        "version": d.get("version") or None,
        "online": d.get("status") == "connected",
        "uptime": _num(d.get("uptime")),
        "clients": clients,
        "cpu": cpu,
        "memory": memory,
        "temperature": temperature,
        "radios": radios,
        "ports": _natural(ports),
        "uplink": _uplink(d) if kind == "ap" else None,
        "poe": _poe(d) if kind == "switch" else None,
        "vlans": None,
        "wlans": None,
    }


def device_update_from_mist(d: dict[str, Any]) -> Device:
    """A stream frame only carries some fields; it does prove the device is up."""
    out: Device = {"mac": mac_key(d.get("mac")), "site_id": d.get("site_id"), "online": True}
    for key in ("name", "ip", "version"):
        if d.get(key):
            out[key] = d[key]
    if _num(d.get("uptime")) is not None:
        out["uptime"] = d["uptime"]
    return out


def alarm_from_mist(a: dict[str, Any]) -> Alarm:
    return {
        "id": a.get("id"),
        "site_id": a.get("site_id"),
        "type": a.get("type"),
        "severity": a.get("severity"),
        "count": a.get("count"),
        "acked": bool(a.get("acked")),
        "timestamp": a.get("timestamp") or a.get("last_seen"),
    }


class MistSource:
    """HPE Mist: REST sweep of every site in the org, plus the websocket stream."""

    def __init__(self, client: MistClient, org_id: str) -> None:
        self.client = client
        self.org_id = org_id
        self._sites: dict[str, str] = {}
        self._ports: dict[str, dict[str, dict[str, Port]]] = {}  # site -> device mac -> ports
        self._ports_at: dict[str, float] = {}

    @property
    def has_stream(self) -> bool:
        return self.client.stream_url is not None

    async def sites(self) -> dict[str, str]:
        if not self._sites:
            self._sites = await self.client.org_sites(self.org_id)
        return self._sites

    def forget_sites(self) -> None:
        self._sites = {}

    async def clients(self) -> list[Client]:
        return [client_from_mist(c) for c in await self.client.org_clients(list(await self.sites()))]

    async def seen_clients(self) -> list[Client]:
        """Wi-Fi and wired clients of the last 7 days; a search the token may not use (403) is skipped."""
        out: list[Client] = []
        for wired in (False, True):
            try:
                rows = await self.client.org_clients_seen(self.org_id, wired)
            except MistAuthError as err:
                if err.status != 403:
                    raise
                _LOGGER.debug("Mist %s search not allowed for this token", "wired client" if wired else "client")
                continue
            out.extend(seen_client_from_mist(r, wired) for r in rows)
        return out

    async def sweep(self) -> Snapshot:
        sites = await self.sites()
        clients = await self.clients()
        devices: list[Device] = []
        for site in sites:
            found = [device_from_mist({"site_id": site, **d}) for d in await self.client.site_devices(site)]
            if any(d["type"] in ("switch", "gateway") for d in found):
                await self._refresh_ports(site)
            for dev in found:
                merge_ports(dev, self._ports.get(site, {}).get(dev["mac"]))
            devices.extend(found)
        alarms = [alarm_from_mist(a) for a in await self.client.org_alarms(self.org_id)]
        return Snapshot(sites=dict(sites), clients=clients, devices=[d for d in devices if d["mac"]], alarms=alarms)

    async def _refresh_ports(self, site: str) -> None:
        """One port search per site at most every DETAIL_SECONDS; kept between, never fails the sweep."""
        now = time.time()
        if now - self._ports_at.get(site, 0.0) < DETAIL_SECONDS - _DETAIL_SLACK_SECONDS:
            return
        self._ports_at[site] = now
        try:
            self._ports[site] = ports_from_mist(await self.client.site_ports(site))
        except MistError as err:
            _LOGGER.debug("Mist port stats for site %s not refreshed: %s", site, err)

    async def stream(
        self,
        on_client: Callable[[Client], None],
        on_device: Callable[[Device], None],
        on_connected: Callable[[], None],
    ) -> None:
        def on_message(kind: str, data: dict[str, Any]) -> None:
            if kind == "client":
                on_client(client_from_mist(data))
            else:
                on_device(device_update_from_mist(data))

        await self.client.stream(list(await self.sites()), on_message, on_connected)

    async def can_write(self) -> bool:
        return can_write_org(await self.client.self_info(), self.org_id)

    async def wlans(self) -> list[Wlan]:
        rows = [(None, w) for w in await self.client.org_wlans(self.org_id)]
        for site in await self.sites():
            rows.extend((site, w) for w in await self.client.site_wlans(site))
        return [
            {"id": w["id"], "ssid": w.get("ssid") or w["id"], "enabled": bool(w.get("enabled", True)), "site_id": site}
            for site, w in rows
            if isinstance(w, dict) and w.get("id")
        ]

    async def restart_device(self, device: Device) -> None:
        await self.client.restart_device(device["site_id"], device["id"])

    async def set_wlan_enabled(self, wlan: Wlan, enabled: bool) -> None:
        await self.client.set_wlan_enabled(self.org_id, wlan["site_id"], wlan["id"], enabled)
