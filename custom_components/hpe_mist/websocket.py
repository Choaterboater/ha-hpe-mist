"""<domain>/clients: every client with everything the network says about it, for the client list card.

Rows can be long, so they stay out of state attributes and the card asks for them while it is shown.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_clients)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/clients"})
@callback
def ws_clients(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    rows = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        rows.extend(entry.runtime_data.client_rows())
    connection.send_result(msg["id"], {"clients": rows})
