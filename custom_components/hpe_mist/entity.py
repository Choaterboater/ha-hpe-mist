"""Shared entity helpers: device info for sites, network devices and clients, and adding entities as things appear."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ORG_ID, DOMAIN, MANUFACTURER
from .coordinator import HpeMistCoordinator
from .presence import mac_pretty
from .source import Device

DEVICE_MODELS = {"ap": "Access point", "switch": "Switch", "gateway": "Gateway"}


def org_of(entry: ConfigEntry) -> str:
    return entry.data[CONF_ORG_ID]


def client_uid(entry: ConfigEntry, mac: str) -> str:
    """Unique id of a client's tracker and of its device (kept from the first release)."""
    return f"{org_of(entry)}_{mac}"


def device_uid(entry: ConfigEntry, mac: str) -> str:
    return f"{org_of(entry)}_dev_{mac}"


def site_uid(entry: ConfigEntry, site_id: str) -> str:
    return f"{org_of(entry)}_site_{site_id}"


def site_device_info(entry: ConfigEntry, site_id: str, name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, site_uid(entry, site_id))},
        name=name,
        manufacturer=MANUFACTURER,
        model="Site",
    )


@callback
def ensure_site_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: HpeMistCoordinator) -> None:
    """Sites are registered first so APs, switches and gateways can sit under them."""
    dev_reg = dr.async_get(hass)
    for site, name in coordinator.mist.sites.items():
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, site_uid(entry, site))},
            name=name,
            manufacturer=MANUFACTURER,
            model="Site",
        )


def network_device_info(hass: HomeAssistant, entry: ConfigEntry, dev: Device) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, device_uid(entry, dev["mac"]))},
        connections={(dr.CONNECTION_NETWORK_MAC, mac_pretty(dev["mac"]))},
        name=dev["name"],
        manufacturer=MANUFACTURER,
        model=dev.get("model") or DEVICE_MODELS.get(dev.get("type") or "", "Device"),
    )
    if dev.get("version"):
        info["sw_version"] = dev["version"]
    if dev.get("serial"):
        info["serial_number"] = dev["serial"]
    if dev.get("site_id"):
        site = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, site_uid(entry, dev["site_id"])), entry.entry_id)
        if site is not None:
            info["via_device_id"] = site.id
    return info


def client_device_info(entry: ConfigEntry, mac: str) -> DeviceInfo:
    """Links client sensors to the device the tracker platform made for a picked client."""
    return DeviceInfo(identifiers={(DOMAIN, client_uid(entry, mac))})


class MistEntity(CoordinatorEntity[HpeMistCoordinator]):
    _attr_has_entity_name = True


@callback
def add_entities_as_seen(
    entry: ConfigEntry,
    coordinator: HpeMistCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    candidates: Callable[[], Iterable[tuple[str, Callable[[], Entity]]]],
) -> None:
    """Add entities now, and later whenever a sweep or stream message brings new ones."""
    added: set[str] = set()

    @callback
    def add_new() -> None:
        new = []
        for uid, make in candidates():
            if uid not in added:
                added.add(uid)
                new.append(make())
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
