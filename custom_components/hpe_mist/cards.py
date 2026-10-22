"""Serve the HPE cards (client list, access point, switch) and add them to every dashboard."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARDS_URL = f"/{DOMAIN}/hpe-cards.js"
CARDS_FILE = Path(__file__).parent / "frontend" / "hpe-cards.js"


async def async_register_cards(hass: HomeAssistant) -> None:
    if hass.http is None:  # no web server (tests)
        return
    await hass.http.async_register_static_paths([StaticPathConfig(CARDS_URL, str(CARDS_FILE), False)])
    # HPE Mist and HPE Central ship the same file; the cards define themselves once.
    version = (await async_get_integration(hass, DOMAIN)).version
    add_extra_js_url(hass, f"{CARDS_URL}?v={version}")
