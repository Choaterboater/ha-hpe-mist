"""WLAN on / off. Only for a token that may change things; disabled by default.

An org-wide WLAN (from a WLAN template) sits on the org device and changes every
site that uses the template; a site WLAN sits on its site device.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeMistConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import HpeMistCoordinator
from .entity import MistEntity, add_entities_as_seen, org_of, site_device_info
from .errors import SourceError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeMistConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    if not c.can_write:
        return
    if not c.mist.wlans:
        await c.async_refresh_wlans()

    def candidates():
        for wlan_id in list(c.mist.wlans):
            yield f"{org_of(entry)}_wlan_{wlan_id}", lambda w=wlan_id: WlanSwitch(c, entry, w)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class WlanSwitch(MistEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "wlan"

    def __init__(self, c: HpeMistCoordinator, entry, wlan_id: str) -> None:
        super().__init__(c)
        self._wlan_id = wlan_id
        wlan = c.mist.wlans[wlan_id]
        self._attr_unique_id = f"{org_of(entry)}_wlan_{wlan_id}"
        self._attr_translation_placeholders = {"ssid": wlan["ssid"]}
        if wlan["site_id"]:
            self._attr_device_info = site_device_info(entry, wlan["site_id"], c.mist.sites.get(wlan["site_id"], wlan["site_id"]))
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{org_of(entry)}_org")},
                name=entry.title,
                manufacturer=MANUFACTURER,
                model="Organization",
            )

    @property
    def available(self) -> bool:
        return super().available and self._wlan_id in self.coordinator.mist.wlans

    @property
    def is_on(self) -> bool:
        wlan = self.coordinator.mist.wlans.get(self._wlan_id)
        return bool(wlan and wlan["enabled"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        wlan = self.coordinator.mist.wlans[self._wlan_id]
        try:
            await self.coordinator.source.set_wlan_enabled(wlan, enabled)
        except SourceError as err:
            raise HomeAssistantError(f"Mist did not change WLAN {wlan['ssid']}: {err}") from err
        wlan["enabled"] = enabled
        self.async_write_ha_state()
