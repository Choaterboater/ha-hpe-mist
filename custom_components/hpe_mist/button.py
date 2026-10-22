"""Restart button per AP / switch / gateway. Only for a token that may change things; disabled by default."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeMistConfigEntry
from .coordinator import HpeMistCoordinator
from .entity import MistEntity, add_entities_as_seen, device_uid, network_device_info
from .errors import SourceError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeMistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    if not c.can_write:
        return

    def candidates():
        for mac, dev in list(c.mist.devices.items()):
            if dev.get("id") and dev.get("site_id"):
                yield f"{device_uid(entry, mac)}_restart", lambda m=mac: RestartButton(c, entry, m)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class RestartButton(MistEntity, ButtonEntity):
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, c: HpeMistCoordinator, entry, mac: str) -> None:
        super().__init__(c)
        self._mac = mac
        self._attr_unique_id = f"{device_uid(entry, mac)}_restart"
        self._attr_device_info = network_device_info(c.hass, entry, c.mist.devices[mac])

    async def async_press(self) -> None:
        dev = self.coordinator.mist.devices[self._mac]
        try:
            await self.coordinator.source.restart_device(dev)
        except SourceError as err:
            raise HomeAssistantError(f"Mist did not restart {dev['name']}: {err}") from err
