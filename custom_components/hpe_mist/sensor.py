"""Sensors for each site, each AP / switch / gateway, and each picked client."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeMistConfigEntry
from .const import MAX_LIST_ROWS
from .coordinator import HpeMistCoordinator
from .entity import (
    MistEntity,
    add_entities_as_seen,
    client_device_info,
    client_uid,
    device_uid,
    network_device_info,
    site_device_info,
    site_uid,
)
from .presence import Sighting, mac_pretty
from .source import BANDS, Device

_DIAG = EntityCategory.DIAGNOSTIC
# Lists can be long and change every sweep: kept out of the recorder.
_LIST_ATTRIBUTES = frozenset({"clients", "radios", "ports", "wlans", "vlans"})


@dataclass(frozen=True, kw_only=True)
class MistSensorDescription(SensorEntityDescription):
    value_fn: Callable[..., Any]
    exists_fn: Callable[..., bool] = lambda *_: True
    attrs_fn: Callable[..., dict[str, Any]] | None = None


# --- connected clients as attribute rows ------------------------------------------


def _name(c: HpeMistCoordinator, s: Sighting) -> str:
    """Tracked name, else hostname, else vendor + last 3 MAC bytes, else the MAC."""
    if name := c.picked.get(s.mac) or (s.hostname or "").strip():
        return name
    return f"{s.manufacture} {mac_pretty(s.mac)[-8:]}" if s.manufacture else mac_pretty(s.mac)


def _connected(c: HpeMistCoordinator, keep: Callable[[Sighting], bool]) -> list[Sighting]:
    """Clients home now that pass keep, by name, at most MAX_LIST_ROWS."""
    here = sorted((s for s in c.mist.clients.values() if s.home and keep(s)), key=lambda s: _name(c, s).casefold())
    return here[:MAX_LIST_ROWS]


def _site_clients(c: HpeMistCoordinator, site: str) -> dict[str, Any]:
    return {"clients": [
        {"name": _name(c, s), "mac": mac_pretty(s.mac), "ip": s.ip, "ssid": s.ssid, "band": s.band,
         "ap": c.device_name(s.ap_mac), "wired": s.wired, "port": s.port, "switch": c.device_name(s.switch_mac),
         "rssi": s.rssi, "snr": s.snr, "vendor": s.manufacture}
        for s in _connected(c, lambda s: s.site_id == site)
    ]}


def _ap_clients(c: HpeMistCoordinator, dev: Device) -> dict[str, Any]:
    if dev.get("type") != "ap":
        return {}
    here = _connected(c, lambda s: s.ap_mac == dev["mac"])
    by_band = dict.fromkeys(BANDS.values(), 0)
    for s in here:
        if s.band in by_band:
            by_band[s.band] += 1
    return {
        "clients": [{"name": _name(c, s), "mac": mac_pretty(s.mac), "ip": s.ip, "ssid": s.ssid, "band": s.band,
                     "rssi": s.rssi, "snr": s.snr, "vendor": s.manufacture} for s in here],
        "by_band": by_band,
        "by_ssid": dict(Counter(s.ssid for s in here if s.ssid)),
    }


# --- per site: value_fn(coordinator, site_id) ----------------------------------


def _wireless(c: HpeMistCoordinator, site: str, ssid: str | None = None) -> int:
    return sum(1 for s in c.mist.clients.values() if s.home and s.site_id == site and (ssid is None or s.ssid == ssid))


def _wired(c: HpeMistCoordinator, site: str) -> int | None:
    counts = [d["clients"] for d in c.mist.devices.values()
              if d["site_id"] == site and d["type"] in ("switch", "gateway") and d.get("clients") is not None]
    return int(sum(counts)) if counts else None


def _aps(c: HpeMistCoordinator, site: str, online: bool) -> int:
    return sum(1 for d in c.mist.devices.values() if d["site_id"] == site and d["type"] == "ap" and d["online"] == online)


SITE_SENSORS = (
    MistSensorDescription(key="clients", translation_key="clients", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: _wireless(c, s) + (_wired(c, s) or 0), attrs_fn=_site_clients),
    MistSensorDescription(key="wireless_clients", translation_key="wireless_clients",
                          state_class=SensorStateClass.MEASUREMENT, value_fn=_wireless),
    MistSensorDescription(key="wired_clients", translation_key="wired_clients", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=_wired, exists_fn=lambda c, s: _wired(c, s) is not None),
    MistSensorDescription(key="aps_online", translation_key="aps_online", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: _aps(c, s, True)),
    MistSensorDescription(key="aps_offline", translation_key="aps_offline", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: _aps(c, s, False)),
    MistSensorDescription(key="active_alarms", translation_key="active_alarms", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda c, s: c.mist.active_alarms(s)),
)


# --- per AP / switch / gateway: value_fn(device) --------------------------------


def _radio(band: str, field: str) -> Callable[[Device], Any]:
    def value(d: Device) -> Any:
        v = (d.get("radios") or {}).get(band, {}).get(field)
        # The channel is a string ("56E" is valid); a plain number keeps its statistics.
        return int(v) if field == "channel" and isinstance(v, str) and v.isdigit() else v

    return value


def _radio_exists(band: str, field: str) -> Callable[[Device], bool]:
    def exists(d: Device) -> bool:
        r = (d.get("radios") or {}).get(band)
        if r is None or r.get("status") == "disabled":
            return False
        return field in ("clients", "channel", "utilization") or r.get(field) is not None

    return exists


def _radio_list(d: Device) -> dict[str, Any]:
    return {"radios": [d["radios"][band] for band in BANDS if band in (d.get("radios") or {})]}


def _uplink_state(d: Device) -> str | None:
    u = d.get("uplink") or {}
    if u.get("device"):
        return " ".join(str(x) for x in (u["device"], u.get("port")) if x)
    return f"{u['speed']} Mbps" if u.get("speed") else None


def _ports(d: Device) -> dict[str, Any]:
    ports = d.get("ports") or {}
    return {"ports_total": len(ports), "ports": [{"port": p, **v} for p, v in list(ports.items())[:MAX_LIST_ROWS]]}


def _poe(d: Device, field: str) -> Any:
    return (d.get("poe") or {}).get(field)


_RADIO_UNITS = {"utilization": PERCENTAGE, "power": SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                "noise_floor": SIGNAL_STRENGTH_DECIBELS_MILLIWATT}


DEVICE_SENSORS: tuple[MistSensorDescription, ...] = (
    MistSensorDescription(key="clients", translation_key="device_clients", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: d.get("clients"), exists_fn=lambda d: d.get("clients") is not None,
                          attrs_fn=_ap_clients),
    MistSensorDescription(key="radios", translation_key="radios", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: sum(1 for r in (d.get("radios") or {}).values() if r["status"] == "up"),
                          exists_fn=lambda d: bool(d.get("radios")), attrs_fn=lambda c, d: _radio_list(d)),
    MistSensorDescription(key="uplink", translation_key="uplink", value_fn=_uplink_state,
                          exists_fn=lambda d: d.get("uplink") is not None,
                          attrs_fn=lambda c, d: dict(d.get("uplink") or {})),
    MistSensorDescription(key="wlans", translation_key="wlans", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: sum(1 for w in d.get("wlans") or [] if w.get("enabled")),
                          exists_fn=lambda d: d.get("wlans") is not None,
                          attrs_fn=lambda c, d: {"wlans": (d.get("wlans") or [])[:MAX_LIST_ROWS]}),
    MistSensorDescription(key="ports_up", translation_key="ports_up", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: sum(1 for p in (d.get("ports") or {}).values() if p["up"]),
                          exists_fn=lambda d: d.get("type") != "ap" and bool(d.get("ports")),
                          attrs_fn=lambda c, d: _ports(d)),
    MistSensorDescription(key="poe_draw", translation_key="poe_draw", device_class=SensorDeviceClass.POWER,
                          native_unit_of_measurement=UnitOfPower.WATT, state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: _poe(d, "draw_w"), exists_fn=lambda d: _poe(d, "draw_w") is not None,
                          attrs_fn=lambda c, d: {"budget_w": _poe(d, "budget_w")}),
    MistSensorDescription(key="vlans", translation_key="vlans", state_class=SensorStateClass.MEASUREMENT,
                          value_fn=lambda d: len(d.get("vlans") or []), exists_fn=lambda d: d.get("vlans") is not None,
                          attrs_fn=lambda c, d: {"vlans": (d.get("vlans") or [])[:MAX_LIST_ROWS]}),
    MistSensorDescription(key="cpu", translation_key="cpu", native_unit_of_measurement=PERCENTAGE,
                          state_class=SensorStateClass.MEASUREMENT, entity_category=_DIAG,
                          value_fn=lambda d: d.get("cpu"), exists_fn=lambda d: d.get("cpu") is not None),
    MistSensorDescription(key="memory", translation_key="memory", native_unit_of_measurement=PERCENTAGE,
                          state_class=SensorStateClass.MEASUREMENT, entity_category=_DIAG,
                          value_fn=lambda d: d.get("memory"), exists_fn=lambda d: d.get("memory") is not None),
    MistSensorDescription(key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                          native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
                          entity_category=_DIAG, value_fn=lambda d: d.get("temperature"),
                          exists_fn=lambda d: d.get("temperature") is not None),
    MistSensorDescription(key="firmware", translation_key="firmware", entity_category=_DIAG,
                          value_fn=lambda d: d.get("version"), exists_fn=lambda d: bool(d.get("version"))),
    MistSensorDescription(key="last_restart", translation_key="last_restart", device_class=SensorDeviceClass.TIMESTAMP,
                          entity_category=_DIAG, value_fn=lambda d: d.get("uptime"),
                          exists_fn=lambda d: d.get("uptime") is not None),
) + tuple(
    MistSensorDescription(key=f"{band}_{field}", translation_key=f"radio_{field}", entity_category=_DIAG,
                          translation_placeholders={"band": label}, state_class=SensorStateClass.MEASUREMENT,
                          native_unit_of_measurement=_RADIO_UNITS.get(field),
                          value_fn=_radio(band, field), exists_fn=_radio_exists(band, field))
    for band, label in BANDS.items()
    for field in ("clients", "channel", "utilization", "power", "noise_floor")
)


# --- per picked client: value_fn(coordinator, sighting) ---------------------------

CLIENT_SENSORS = (
    MistSensorDescription(key="ip", translation_key="ip", value_fn=lambda c, s: s.ip),
    MistSensorDescription(key="ssid", translation_key="ssid", value_fn=lambda c, s: s.ssid),
    MistSensorDescription(key="ap", translation_key="ap", value_fn=lambda c, s: c.device_name(s.ap_mac)),
    MistSensorDescription(key="rssi", translation_key="rssi", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                          native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                          state_class=SensorStateClass.MEASUREMENT, entity_category=_DIAG, value_fn=lambda c, s: s.rssi),
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeMistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data

    def candidates():
        mist = c.mist
        for site in list(mist.sites):
            for desc in SITE_SENSORS:
                if desc.exists_fn(c, site):
                    yield f"{site_uid(entry, site)}_{desc.key}", lambda d=desc, s=site: SiteSensor(c, entry, s, d)
            for ssid in sorted({x.ssid for x in mist.clients.values() if x.site_id == site and x.ssid}):
                desc = MistSensorDescription(
                    key=f"ssid_{_slug(ssid)}", translation_key="ssid_clients", translation_placeholders={"ssid": ssid},
                    state_class=SensorStateClass.MEASUREMENT, value_fn=lambda c_, s_, ssid=ssid: _wireless(c_, s_, ssid),
                )
                yield f"{site_uid(entry, site)}_{desc.key}", lambda d=desc, s=site: SiteSensor(c, entry, s, d)
        for mac, dev in list(mist.devices.items()):
            for desc in DEVICE_SENSORS:
                if desc.exists_fn(dev):
                    yield f"{device_uid(entry, mac)}_{desc.key}", lambda d=desc, m=mac: DeviceSensor(c, entry, m, d)
        for mac in c.picked:
            for desc in CLIENT_SENSORS:
                yield f"{client_uid(entry, mac)}_client_{desc.key}", lambda d=desc, m=mac: ClientSensor(c, entry, m, d)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class SiteSensor(MistEntity, SensorEntity):
    entity_description: MistSensorDescription
    _unrecorded_attributes = _LIST_ATTRIBUTES

    def __init__(self, c: HpeMistCoordinator, entry, site: str, desc: MistSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = desc
        self._site = site
        self._attr_unique_id = f"{site_uid(entry, site)}_{desc.key}"
        self._attr_device_info = site_device_info(entry, site, c.mist.sites.get(site, site))

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator, self._site)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self.coordinator, self._site) if attrs_fn else None


class DeviceSensor(MistEntity, SensorEntity):
    entity_description: MistSensorDescription
    _unrecorded_attributes = _LIST_ATTRIBUTES

    def __init__(self, c: HpeMistCoordinator, entry, mac: str, desc: MistSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = desc
        self._mac = mac
        self._attr_unique_id = f"{device_uid(entry, mac)}_{desc.key}"
        self._attr_device_info = network_device_info(c.hass, entry, c.mist.devices[mac])
        self._boot: datetime | None = None

    @property
    def available(self) -> bool:
        dev = self.coordinator.mist.devices.get(self._mac)
        return super().available and dev is not None and bool(dev.get("online"))

    @property
    def native_value(self) -> Any:
        dev = self.coordinator.mist.devices.get(self._mac)
        if dev is None:
            return None
        value = self.entity_description.value_fn(dev)
        if self.entity_description.key == "last_restart":
            return self._restart_time(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        dev = self.coordinator.mist.devices.get(self._mac)
        attrs_fn = self.entity_description.attrs_fn
        return (attrs_fn(self.coordinator, dev) or None) if attrs_fn and dev is not None else None

    def _restart_time(self, uptime: float | None) -> datetime | None:
        """Boot time from uptime; kept steady unless it moves by more than 2 minutes (a real restart)."""
        if uptime is None:
            return self._boot
        boot = (datetime.now(UTC) - timedelta(seconds=uptime)).replace(microsecond=0)
        if self._boot is None or abs((boot - self._boot).total_seconds()) > 120:
            self._boot = boot
        return self._boot


class ClientSensor(MistEntity, SensorEntity):
    entity_description: MistSensorDescription

    def __init__(self, c: HpeMistCoordinator, entry, mac: str, desc: MistSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = desc
        self._mac = mac
        self._attr_unique_id = f"{client_uid(entry, mac)}_client_{desc.key}"
        self._attr_device_info = client_device_info(entry, mac)

    @property
    def native_value(self) -> Any:
        s = self.coordinator.mist.clients.get(self._mac)
        return self.entity_description.value_fn(self.coordinator, s) if s and s.home else None
