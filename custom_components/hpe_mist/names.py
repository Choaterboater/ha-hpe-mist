"""Names typed in the client list card, kept across restarts.

A picked client's name lives in the entry options (the rename action updates it
there too); this store also covers every client that is not picked.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .presence import mac_key

SAVE_DELAY_SECONDS = 5


class ClientNames:
    """{mac_key: name} for every config entry of the integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, dict[str, str]]] = Store(hass, 1, f"{DOMAIN}.client_names")
        self.names: dict[str, str] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.names = {
            m: n.strip() for k, n in (data.get("names") or {}).items()
            if (m := mac_key(k)) and isinstance(n, str) and n.strip()
        }

    @callback
    def set(self, mac: str, name: str) -> None:
        """Save a name; an empty one forgets it."""
        if name:
            self.names[mac] = name
        else:
            self.names.pop(mac, None)
        self._store.async_delay_save(lambda: {"names": self.names}, SAVE_DELAY_SECONDS)


DATA_NAMES: HassKey[ClientNames] = HassKey(f"{DOMAIN}_client_names")


@callback
def saved_name(hass: HomeAssistant, mac: str) -> str | None:
    names = hass.data.get(DATA_NAMES)
    return names.names.get(mac) if names else None
