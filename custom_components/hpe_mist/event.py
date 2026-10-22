"""One event entity per site: client joined / left, device down / up, alarm. Use it as an automation trigger."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeMistConfigEntry
from .const import EVENT_TYPES
from .coordinator import HpeMistCoordinator, event_signal
from .entity import MistEntity, add_entities_as_seen, site_device_info, site_uid


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeMistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data

    def candidates():
        for site in list(c.mist.sites):
            yield f"{site_uid(entry, site)}_events", lambda s=site: SiteEvents(c, entry, s)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class SiteEvents(MistEntity, EventEntity):
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "events"

    def __init__(self, c: HpeMistCoordinator, entry, site: str) -> None:
        super().__init__(c)
        self._site = site
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{site_uid(entry, site)}_events"
        self._attr_device_info = site_device_info(entry, site, c.mist.sites.get(site, site))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, event_signal(self._entry_id), self._on_event))

    @callback
    def _on_event(self, site: str, event_type: str, data: dict[str, Any]) -> None:
        if site == self._site:
            self._trigger_event(event_type, data)
            self.async_write_ha_state()
