"""HPE Mist (unofficial): sites, APs / switches / gateways, client presence and events from a Mist org."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .api import MistClient
from .cards import async_register_cards
from .const import CONF_ADD_ALL, CONF_HOST, CONF_ORG_ID, CONF_TOKEN, DEFAULT_ADD_ALL, DOMAIN, MANUFACTURER
from .coordinator import HpeMistCoordinator, tracked
from .entity import ensure_site_devices
from .errors import SourceError
from .names import DATA_NAMES, ClientNames
from .services import async_setup_services
from .source import MistSource
from .websocket import async_setup_websocket

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HpeMistConfigEntry = ConfigEntry[HpeMistCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Once for all entries: typed client names, the rename action, the client rows and the cards."""
    names = ClientNames(hass)
    await names.async_load()
    hass.data[DATA_NAMES] = names
    async_setup_services(hass)
    async_setup_websocket(hass)
    await async_register_cards(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HpeMistConfigEntry) -> bool:
    client = MistClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_TOKEN])
    source = MistSource(client, entry.data[CONF_ORG_ID])
    coordinator = HpeMistCoordinator(hass, entry, source)
    try:
        # Controls (restart, WLAN on / off) only appear for a token that may change things.
        coordinator.can_write = await source.can_write()
    except SourceError as err:
        raise ConfigEntryNotReady(f"Cannot reach Mist: {err}") from err
    await coordinator.seen.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _sync_client_registry(hass, entry)
    ensure_site_devices(hass, entry, coordinator)
    entry.async_on_unload(coordinator.async_add_listener(lambda: ensure_site_devices(hass, entry, coordinator)))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Both stop on unload: the stream is an entry background task, the check an unload callback.
    coordinator.async_start_away_check()
    coordinator.async_start_stream()
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpeMistConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _sync_client_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Make the registry match the picked clients before the platforms add entities.

    Picked: a named device; its tracker is enabled, moved onto that device and,
    if it was an auto-added tracker, renamed after the device. Not picked: its
    sensors and device go; its tracker stays (disabled) only with "add all" on.
    """
    picked = tracked(entry)
    add_all = entry.options.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
    org = entry.data[CONF_ORG_ID]
    client_id = re.compile(rf"^{re.escape(org)}_([0-9a-f]{{12}})(_client_.+)?$")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    devices = {}
    for mac, name in picked.items():
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{org}_{mac}")},
            name=name,
            manufacturer=MANUFACTURER,
            model="Client",
        )
        if device.name != name:
            device = dev_reg.async_update_device(device.id, name=name) or device
        devices[mac] = device
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if not (m := client_id.match(ent.unique_id)):
            continue
        mac = m.group(1)
        if ent.domain != "device_tracker":
            if mac not in picked:
                ent_reg.async_remove(ent.entity_id)
        elif mac in picked:
            device = devices[mac]
            if ent.device_id != device.id:
                ent_reg.async_update_entity(
                    ent.entity_id,
                    device_id=device.id,
                    disabled_by=None,
                    new_entity_id=ent_reg.async_get_available_entity_id(
                        "device_tracker", slugify(device.name or mac), current_entity_id=ent.entity_id
                    ),
                )
        elif not add_all:
            ent_reg.async_remove(ent.entity_id)
        elif ent.device_id is not None:
            ent_reg.async_update_entity(
                ent.entity_id, device_id=None, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for d, ident in dev.identifiers:
            if d == DOMAIN and (m := client_id.match(ident)) and m.group(1) not in picked:
                dev_reg.async_remove_device(dev.id)
