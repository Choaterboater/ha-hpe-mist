"""Keeps sites, devices, alarms and client presence from the live stream plus REST sweeps."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    AWAY_CHECK_SECONDS,
    CONF_ADD_ALL,
    CONF_TRACKED,
    DEFAULT_ADD_ALL,
    DOMAIN,
    EVENT_ALARM,
    EVENT_CLIENT_JOINED,
    EVENT_CLIENT_LEFT,
    EVENT_DEVICE_DOWN,
    EVENT_DEVICE_UP,
    MAX_BACKOFF_SECONDS,
    POLL_SECONDS,
    SAFETY_SWEEP_SECONDS,
    STREAM_RETRY_SECONDS,
    UNAVAILABLE_AFTER_SECONDS,
)
from .errors import SourceAuthError, SourceBusyError, SourceError
from .names import saved_name
from .presence import PresenceTracker, Sighting, mac_key, mac_pretty
from .seen import SeenClients
from .source import Alarm, Client, Device, PresenceSource, Snapshot, Wlan


@callback
def known_name(hass: HomeAssistant, mac: str, user_name: str | None = None) -> str | None:
    """A name someone gave this MAC: renamed in Home Assistant, else the login it used, else the name
    the integration that owns the device gave it (a Ring camera, a Sonos speaker...)."""
    # A MAC can be on several devices (another integration's, and ours for an AP or switch).
    devices = dr.async_get(hass).async_get_devices(connections={(dr.CONNECTION_NETWORK_MAC, mac_pretty(mac))})
    if renamed := next((d.name_by_user for d in devices if d.name_by_user), None):
        return renamed
    if user_name:
        return user_name
    ours = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    devices.sort(key=lambda d: d.config_entry_id in ours)  # another integration's name first
    return next((d.name for d in devices if d.name), None)

_LOGGER = logging.getLogger(__name__)

STREAM_TASK_NAME = f"{DOMAIN} stream"


@dataclass
class MistData:
    """What the entities read. Mutated in place by stream messages between sweeps."""

    sites: dict[str, str] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)  # mac -> device
    clients: dict[str, Sighting] = field(default_factory=dict)  # mac -> sighting (all ever seen)
    alarms: list[Alarm] = field(default_factory=list)
    wlans: dict[str, Wlan] = field(default_factory=dict)  # id -> WLAN; only fetched when the token can write

    def active_alarms(self, site_id: str) -> int:
        return sum(1 for a in self.alarms if a["site_id"] == site_id and not a["acked"])


def event_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


class HpeMistCoordinator(DataUpdateCoordinator[MistData]):
    """One coordinator per org (config entry).

    The REST sweep is the coordinator's update: every 30 s while the stream is
    down, every 5 minutes while it is up. Stream messages and the away check
    push updates to the entities straight away without moving the sweep timer.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, source: PresenceSource) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=POLL_SECONDS),
        )
        self.source = source
        self.picked: dict[str, str] = tracked(entry)
        self.add_all: bool = entry.options.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
        self.tracker = PresenceTracker(set(self.picked))
        self.mist = MistData(clients=self.tracker.state)
        self.seen = SeenClients(hass, entry.entry_id)  # loaded at setup
        self.stream_connected = False
        self.can_write = False  # set at setup from the token's role
        self._wlans_at = 0.0
        self._seeded = False
        self._alarm_ids: set[Any] = set()
        self._last_ok: float | None = None
        self._backoff = 0.0

    # --- REST sweep -------------------------------------------------------

    def _base_interval(self) -> float:
        return SAFETY_SWEEP_SECONDS if self.stream_connected else POLL_SECONDS

    def _set_interval(self) -> None:
        self.update_interval = timedelta(seconds=max(self._base_interval(), self._backoff))

    async def _async_update_data(self) -> MistData:
        now = time.time()
        try:
            snap = await self.source.sweep()
        except SourceError as err:
            return self._failed(err, now)
        self._backoff = 0.0
        self._set_interval()
        self._last_ok = now
        self._apply(snap, now)
        if self.can_write and now - self._wlans_at >= SAFETY_SWEEP_SECONDS:
            await self.async_refresh_wlans()
        return self.mist

    async def async_refresh_wlans(self) -> None:
        """WLANs change rarely: at most once per safety-sweep interval, and after a toggle."""
        try:
            self.mist.wlans = {w["id"]: w for w in await self.source.wlans()}
            self._wlans_at = time.time()
        except SourceError as err:
            _LOGGER.debug("WLANs not refreshed: %s", err)

    def _apply(self, snap: Snapshot, now: float) -> None:
        events = self._seeded
        self.mist.sites = snap.sites
        joined, left = self.tracker.update(snap.clients, now)
        self.seen.record(snap.clients, now)
        new_devices = {d["mac"]: d for d in snap.devices}
        for mac, dev in new_devices.items():
            was = self.mist.devices.get(mac)
            if events and was is not None and was.get("online") != dev["online"]:
                self._emit(dev["site_id"], EVENT_DEVICE_UP if dev["online"] else EVENT_DEVICE_DOWN, _device_event(dev))
        # A device missing from the sweep keeps its last stats but counts as offline.
        for mac, was in self.mist.devices.items():
            if mac not in new_devices:
                if events and was.get("online"):
                    self._emit(was["site_id"], EVENT_DEVICE_DOWN, _device_event(was))
                new_devices[mac] = {**was, "online": False}
        self.mist.devices = new_devices
        self.mist.alarms = snap.alarms
        if events:
            for mac in joined:
                self._client_event(EVENT_CLIENT_JOINED, mac)
            for mac in left:
                self._client_event(EVENT_CLIENT_LEFT, mac)
            for alarm in snap.alarms:
                if alarm["id"] not in self._alarm_ids:
                    details = {k: alarm[k] for k in ("id", "type", "severity", "count", "timestamp")}
                    self._emit(alarm["site_id"], EVENT_ALARM, details)
        self._alarm_ids = {a["id"] for a in snap.alarms}
        self._seeded = True

    def _failed(self, err: SourceError, now: float) -> MistData:
        """Keep the last state through short trouble; unavailable after that, never not_home."""
        if isinstance(err, SourceBusyError):
            self._backoff = min(MAX_BACKOFF_SECONDS, max(self._backoff * 2 or POLL_SECONDS * 2, err.retry_after or 0))
            self._set_interval()
        self.source.forget_sites()  # sites may have changed; ask again next time
        if self.data is not None and (self.stream_connected or self._recent(now, UNAVAILABLE_AFTER_SECONDS)):
            _LOGGER.warning("Mist sweep failed (%s); keeping last state, next try in %ss",
                            err, int(self.update_interval.total_seconds()))
            return self.data
        raise UpdateFailed(str(err)) from err

    def _recent(self, now: float, seconds: float) -> bool:
        return self._last_ok is not None and now - self._last_ok < seconds

    # --- events -----------------------------------------------------------

    @callback
    def _emit(self, site_id: str | None, event_type: str, data: dict[str, Any]) -> None:
        if site_id and self.config_entry:
            async_dispatcher_send(self.hass, event_signal(self.config_entry.entry_id), site_id, event_type, data)

    @callback
    def _client_event(self, event_type: str, mac: str) -> None:
        s = self.mist.clients[mac]
        self._emit(s.site_id, event_type, {
            "mac": mac_pretty(mac), "name": self.client_name(s), "hostname": s.hostname,
            "ip": s.ip, "ssid": s.ssid, "ap": self.device_name(s.ap_mac), "tracked": mac in self.picked,
        })

    @callback
    def client_name(self, s: Sighting) -> str:
        """Picked or typed name, else a name someone gave the MAC, else hostname / model / vendor."""
        return self.picked.get(s.mac) or saved_name(self.hass, s.mac) or self.found_name(s)

    @callback
    def found_name(self, s: Sighting) -> str:
        """The name without the ones typed into this integration."""
        return known_name(self.hass, s.mac, s.user_name) or s.label

    @callback
    def client_rows(self) -> list[dict[str, Any]]:
        """Every client seen since start, for the client list card: everything Mist says, with its names."""
        assert self.config_entry
        sites = self.mist.sites
        return [{
            **s.details,
            "domain": DOMAIN, "source": "Mist", "entry_id": self.config_entry.entry_id,
            "mac": mac_pretty(s.mac), "name": self.client_name(s), "found_name": self.found_name(s),
            "tracked": s.mac in self.picked, "connected": s.home, "last_seen": s.last_seen,
            "site": sites.get(s.site_id or "") or s.site_id, "ip": s.ip, "user_name": s.user_name,
            "hostname": s.hostname, "vendor": s.manufacture, "model": s.model, "wired": s.wired,
            "ssid": None if s.wired else s.ssid, "band": None if s.wired else s.band, "vlan": s.vlan,
            "ap": None if s.wired else self.device_name(s.ap_mac), "switch": self.device_name(s.switch_mac) if s.wired else None,
            "port": s.port if s.wired else None, "rssi": s.rssi, "rssi_estimated": False, "snr": s.snr,
        } for s in list(self.mist.clients.values()) if s.last_seen]

    def device_name(self, mac: str | None) -> str | None:
        dev = self.mist.devices.get(mac or "")
        return dev["name"] if dev else None

    # --- away check between sweeps -----------------------------------------

    @callback
    def async_start_away_check(self) -> None:
        assert self.config_entry
        self.config_entry.async_on_unload(
            async_track_time_interval(self.hass, self._away_check, timedelta(seconds=AWAY_CHECK_SECONDS))
        )

    @callback
    def _away_check(self, _now: datetime) -> None:
        """Mark clients away on time even with 5-minute sweeps.

        Only while the data is fresh (stream up or a recent good sweep), so a Mist
        outage freezes the state instead of turning everyone not_home.
        """
        now = time.time()
        fresh = self.stream_connected or self._recent(now, POLL_SECONDS * 2 + 15)
        if self.data is None or not fresh:
            return
        if left := self.tracker.evaluate(now):
            for mac in left:
                self._client_event(EVENT_CLIENT_LEFT, mac)
            self.async_update_listeners()

    # --- live stream --------------------------------------------------------

    @callback
    def async_start_stream(self) -> None:
        assert self.config_entry
        if self.source.has_stream:
            self.config_entry.async_create_background_task(self.hass, self._run_stream(), STREAM_TASK_NAME)

    async def _run_stream(self) -> None:
        """Keep the stream connected, reconnecting with backoff; sweeps cover the gaps."""
        delay = STREAM_RETRY_SECONDS
        while True:
            started = time.monotonic()
            try:
                await self.source.stream(self._stream_client, self._stream_device, self._stream_up)
                reason: Any = "closed"
            except SourceAuthError as err:
                reason = err
                delay = MAX_BACKOFF_SECONDS
            except SourceError as err:
                reason = err
            except asyncio.CancelledError:
                self.stream_connected = False
                raise
            except Exception as err:  # noqa: BLE001 - never let the stream task die
                _LOGGER.exception("Mist stream error")
                reason = err
            self._stream_down(reason)
            if time.monotonic() - started > 2 * MAX_BACKOFF_SECONDS:
                delay = STREAM_RETRY_SECONDS
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)

    @callback
    def _stream_up(self) -> None:
        _LOGGER.info("Mist stream connected; sweeping every %ss", SAFETY_SWEEP_SECONDS)
        self.stream_connected = True
        self._set_interval()
        self._resync()
        if self.data is not None:
            self.async_update_listeners()  # the Live stream sensor

    @callback
    def _stream_down(self, reason: Any) -> None:
        was = self.stream_connected
        self.stream_connected = False
        self._set_interval()
        if was:
            _LOGGER.info("Mist stream down (%s); sweeping every %ss", reason, POLL_SECONDS)
            self._resync()
            if self.data is not None:
                self.async_update_listeners()
        else:
            _LOGGER.debug("Mist stream not connected: %s", reason)

    @callback
    def _resync(self) -> None:
        """One sweep now, which also reschedules the next one at the new interval."""
        assert self.config_entry
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} resync"
        )

    @callback
    def _stream_client(self, client: Client) -> None:
        mac = mac_key(client.get("mac"))
        joined = self.tracker.see(client, time.time())
        if self.data is None:
            return
        if joined and self._seeded:
            self._client_event(EVENT_CLIENT_JOINED, mac)
        if joined or mac in self.picked:
            self.async_update_listeners()

    @callback
    def _stream_device(self, update: Device) -> None:
        dev = self.mist.devices.get(update["mac"])
        if dev is None or self.data is None:
            return  # new devices arrive with the next sweep
        came_up = not dev.get("online")
        dev.update(update)
        if came_up:
            self._emit(dev["site_id"], EVENT_DEVICE_UP, _device_event(dev))
            self.async_update_listeners()


def _device_event(dev: Device) -> dict[str, Any]:
    return {"mac": mac_pretty(dev["mac"]), "name": dev["name"], "type": dev["type"], "model": dev.get("model")}


def tracked(entry: ConfigEntry) -> dict[str, str]:
    """{mac_key: name} picked in the entry options."""
    return {mac_key(m): n for m, n in entry.options.get(CONF_TRACKED, {}).items() if mac_key(m)}
