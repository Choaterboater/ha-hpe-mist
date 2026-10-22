"""The rename_client action: the client list card's rename box, usable from automations too."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import CONF_TRACKED, DOMAIN
from .names import DATA_NAMES
from .presence import mac_key

SERVICE_RENAME_CLIENT = "rename_client"
RENAME_SCHEMA = vol.Schema({vol.Required("mac"): cv.string, vol.Optional("name", default=""): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, SERVICE_RENAME_CLIENT, _rename_client, schema=RENAME_SCHEMA)


async def _rename_client(call: ServiceCall) -> None:
    """Name any client; an empty name goes back to the name the integration found.

    A picked client's name is also its device's name, so it changes in the entry
    options as well (which reloads that entry).
    """
    hass = call.hass
    if not (mac := mac_key(call.data["mac"])):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_mac",
                                     translation_placeholders={"mac": call.data["mac"]})
    name = call.data["name"].strip()
    hass.data[DATA_NAMES].set(mac, name)
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        picked = entry.options.get(CONF_TRACKED, {})
        key = next((k for k in picked if mac_key(k) == mac), None)
        if key is not None and name and picked[key] != name:
            hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_TRACKED: {**picked, key: name}})
        else:
            entry.runtime_data.async_update_listeners()
