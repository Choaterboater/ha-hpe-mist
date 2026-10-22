"""Online state of each AP / switch / gateway, link state of each switch port (disabled by default),
and whether the live stream is connected (on each site)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeMistConfigEntry
from .coordinator import HpeMistCoordinator
from .entity import MistEntity, add_entities_as_seen, device_uid, network_device_info, site_device_info, site_uid


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeMistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data

    def candidates():
        if c.source.has_stream:
            for site in list(c.mist.sites):
                yield f"{site_uid(entry, site)}_stream", lambda s=site: StreamConnected(c, entry, s)
        for mac, dev in list(c.mist.devices.items()):
            yield f"{device_uid(entry, mac)}_online", lambda m=mac: DeviceOnline(c, entry, m)
            for port in dev.get("ports") or {}:
                yield f"{device_uid(entry, mac)}_port_{port}", lambda m=mac, p=port: PortUp(c, entry, m, p)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class DeviceOnline(MistEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, c: HpeMistCoordinator, entry, mac: str) -> None:
        super().__init__(c)
        self._mac = mac
        self._attr_unique_id = f"{device_uid(entry, mac)}_online"
        self._attr_device_info = network_device_info(c.hass, entry, c.mist.devices[mac])

    @property
    def is_on(self) -> bool:
        dev = self.coordinator.mist.devices.get(self._mac)
        return bool(dev and dev.get("online"))


class PortUp(MistEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "port"

    def __init__(self, c: HpeMistCoordinator, entry, mac: str, port: str) -> None:
        super().__init__(c)
        self._mac = mac
        self._port = port
        self._attr_unique_id = f"{device_uid(entry, mac)}_port_{port}"
        self._attr_translation_placeholders = {"port": port}
        self._attr_device_info = network_device_info(c.hass, entry, c.mist.devices[mac])

    @property
    def available(self) -> bool:
        dev = self.coordinator.mist.devices.get(self._mac)
        return super().available and dev is not None and bool(dev.get("online"))

    @property
    def is_on(self) -> bool:
        dev = self.coordinator.mist.devices.get(self._mac) or {}
        return bool((dev.get("ports") or {}).get(self._port, {}).get("up"))


class StreamConnected(MistEntity, BinarySensorEntity):
    """On while the Mist websocket stream is connected (sweeps then run every 5 minutes)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "stream"

    def __init__(self, c: HpeMistCoordinator, entry, site: str) -> None:
        super().__init__(c)
        self._attr_unique_id = f"{site_uid(entry, site)}_stream"
        self._attr_device_info = site_device_info(entry, site, c.mist.sites.get(site, site))

    @property
    def available(self) -> bool:
        return True  # the stream state is known even while REST sweeps fail

    @property
    def is_on(self) -> bool:
        return self.coordinator.stream_connected
