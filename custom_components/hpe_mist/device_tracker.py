"""One router-type device_tracker per client.

Clients picked in the options are enabled, named, and get their own device.
With "add all clients" on, every other client Mist reports gets a tracker too,
disabled until the person enables it (as the UniFi integration does).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeMistConfigEntry
from .const import DOMAIN
from .coordinator import HpeMistCoordinator
from .entity import MistEntity, add_entities_as_seen, client_uid
from .presence import mac_pretty


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeMistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    dev_reg = dr.async_get(hass)

    def candidates():
        for mac in coordinator.picked:
            uid = client_uid(entry, mac)
            yield uid, lambda mac=mac, uid=uid: _picked(dev_reg, entry, coordinator, mac, uid)
        if coordinator.add_all:
            for mac, s in list(coordinator.mist.clients.items()):
                if mac not in coordinator.picked:
                    uid = client_uid(entry, mac)
                    yield uid, lambda mac=mac, uid=uid, label=coordinator.client_name(s): MistClientTracker(coordinator, uid, mac, None, label)

    add_entities_as_seen(entry, coordinator, async_add_entities, candidates)


def _picked(dev_reg, entry, coordinator, mac: str, uid: str) -> MistClientTracker:
    # ScannerEntity may not carry device_info; __init__ made the picked client's named device.
    return MistClientTracker(coordinator, uid, mac, dev_reg.async_get_device_by_identifier((DOMAIN, uid), entry.entry_id), None)


class MistClientTracker(MistEntity, ScannerEntity):
    """Home while Mist lists the client; not_home after 10 minutes unlisted."""

    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: HpeMistCoordinator,
        uid: str,
        mac: str,
        device: dr.DeviceEntry | None,
        label: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._uid = uid
        self._mac = mac
        self._picked = mac in coordinator.picked
        # No mac_address on purpose: ScannerEntity would then build its own
        # MAC-keyed device and rename it to the client's hostname.
        if self._picked:
            self.device_entry = device
            self._attr_name = None
        else:
            self._attr_has_entity_name = False
            self._attr_name = label
        self._refresh()

    @property
    def unique_id(self) -> str:
        return self._uid

    @property
    def entity_registry_enabled_default(self) -> bool:
        return self._picked

    @property
    def is_connected(self) -> bool:
        s = self.coordinator.mist.clients.get(self._mac)
        return bool(s and s.home)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.mist.clients.get(self._mac)
        attrs: dict[str, Any] = {"mac": mac_pretty(self._mac)}
        if s is None:
            return attrs
        attrs["ssid"] = s.ssid
        attrs["ap_mac"] = mac_pretty(s.ap_mac) or None
        attrs["ap_name"] = self.coordinator.device_name(s.ap_mac)
        attrs["site"] = self.coordinator.mist.sites.get(s.site_id or "")
        attrs["last_seen"] = datetime.fromtimestamp(s.last_seen, UTC).isoformat() if s.last_seen else None
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh()
        super()._handle_coordinator_update()

    def _refresh(self) -> None:
        s = self.coordinator.mist.clients.get(self._mac)
        self._attr_ip_address = s.ip if s else None
        self._attr_hostname = s.hostname if s else None
