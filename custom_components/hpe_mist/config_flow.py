"""Set up HPE Mist in the UI: token and org, then which clients to track."""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MistAuthError, MistClient, MistError, clean_host, orgs_from_self
from .const import (
    CONF_ADD_ALL,
    CONF_HOST,
    CONF_ORG_ID,
    CONF_TOKEN,
    CONF_TRACKED,
    DEFAULT_ADD_ALL,
    DEFAULT_HOST,
    DOMAIN,
    MIST_CLOUDS,
)
from .errors import SourceAuthError, SourceError
from .coordinator import known_name
from .names import saved_name
from .presence import client_label, mac_key, mac_pretty
from .seen import SeenClients
from .source import Client, MistSource

_LOGGER = logging.getLogger(__name__)

CONF_CLIENTS = "clients"
CONF_EXTRA_MAC = "extra_mac"


class HpeMistConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._orgs: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HpeMistOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = clean_host(user_input[CONF_HOST])
            token = user_input[CONF_TOKEN].strip()
            client = MistClient(async_get_clientsession(self.hass), host, token)
            try:
                self._orgs = orgs_from_self(await client.self_info())
            except MistAuthError:
                errors["base"] = "invalid_auth"
            except MistError:
                errors["base"] = "cannot_connect"
            else:
                if not self._orgs:
                    errors["base"] = "no_orgs"
                else:
                    self._data = {CONF_HOST: host, CONF_TOKEN: token}
                    return await self.async_step_org()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=DEFAULT_HOST): SelectSelector(
                        SelectSelectorConfig(
                            options=[SelectOptionDict(value=h, label=f"{n} ({h})") for h, n in MIST_CLOUDS.items()],
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Take over an entry from an earlier build (same host, token and org; no new questions)."""
        org_id = import_data[CONF_ORG_ID]
        await self.async_set_unique_id(org_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=import_data.get("title") or org_id,
            data={k: import_data[k] for k in (CONF_HOST, CONF_TOKEN, CONF_ORG_ID)},
            options={
                CONF_TRACKED: dict(import_data.get("options", {}).get(CONF_TRACKED, {})),
                CONF_ADD_ALL: import_data.get("options", {}).get(CONF_ADD_ALL, DEFAULT_ADD_ALL),
            },
        )

    async def async_step_org(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            org_id = user_input[CONF_ORG_ID]
            await self.async_set_unique_id(org_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._orgs.get(org_id, org_id),
                data={**self._data, CONF_ORG_ID: org_id},
                options={CONF_TRACKED: {}, CONF_ADD_ALL: DEFAULT_ADD_ALL},
            )
        options = [SelectOptionDict(value=k, label=v) for k, v in sorted(self._orgs.items(), key=lambda kv: kv[1].lower())]
        return self.async_show_form(
            step_id="org",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ORG_ID, default=options[0]["value"]): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
        )


class HpeMistOptionsFlow(OptionsFlow):
    """Pick clients from what is connected now or was seen lately (or type a MAC), then name them."""

    def __init__(self) -> None:
        self._picked: list[str] = []
        self._hostnames: dict[str, str] = {}
        self._connected: set[str] = set()
        self._add_all = DEFAULT_ADD_ALL

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current: dict[str, str] = {mac_key(m): n for m, n in self.config_entry.options.get(CONF_TRACKED, {}).items()}
        errors: dict[str, str] = {}
        if user_input is not None:
            picked = [mac_key(m) for m in user_input.get(CONF_CLIENTS, [])]
            extra = [x for x in user_input.get(CONF_EXTRA_MAC, "").replace(";", ",").split(",") if x.strip()]
            for raw in extra:
                if not mac_key(raw):
                    errors[CONF_EXTRA_MAC] = "invalid_mac"
                picked.append(mac_key(raw))
            if not errors:
                self._picked = list(dict.fromkeys(m for m in picked if m))
                self._add_all = user_input.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
                if not self._picked:
                    return self.async_create_entry(data={CONF_TRACKED: {}, CONF_ADD_ALL: self._add_all})
                return await self.async_step_names()

        options, load_error = await self._client_options(current)
        if load_error:
            errors["base"] = load_error
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CLIENTS, default=[mac_pretty(m) for m in current]): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
                    ),
                    vol.Optional(CONF_EXTRA_MAC, default=""): str,
                    vol.Optional(
                        CONF_ADD_ALL, default=self.config_entry.options.get(CONF_ADD_ALL, DEFAULT_ADD_ALL)
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {mac_key(m): n for m, n in self.config_entry.options.get(CONF_TRACKED, {}).items()}
        if user_input is not None:
            names = {
                mac_pretty(m): (user_input.get(mac_pretty(m)) or "").strip() or self._hostnames.get(m) or mac_pretty(m)
                for m in self._picked
            }
            return self.async_create_entry(data={CONF_TRACKED: names, CONF_ADD_ALL: self._add_all})
        schema = {
            vol.Required(mac_pretty(m), default=current.get(m) or self._hostnames.get(m) or mac_pretty(m)): str
            for m in self._picked
        }
        listing = "\n".join(
            f"- `{mac_pretty(m)}`: {self._hostnames.get(m, '')}{'' if m in self._connected else ' (not connected now)'}"
            for m in self._picked
        )
        return self.async_show_form(
            step_id="names",
            data_schema=vol.Schema(schema),
            description_placeholders={"clients": listing},
        )

    async def _client_options(self, current: dict[str, str]) -> tuple[list[SelectOptionDict], str | None]:
        """Tracked clients, then those connected now, then those seen lately (most recent first).

        Seen lately: Mist's 7-day client searches (only run here, when the options
        open) plus every client a sweep has listed in the last 30 days.
        """
        entry = self.config_entry
        client = MistClient(async_get_clientsession(self.hass), entry.data[CONF_HOST], entry.data[CONF_TOKEN])
        source = MistSource(client, entry.data[CONF_ORG_ID])
        rows: list[Client] = list((await self._remembered()).values())
        error = None
        try:
            rows.extend(await source.seen_clients())
        except SourceError as err:
            _LOGGER.debug("Mist client history not listed: %s", err)
        try:
            connected = await source.clients()
        except SourceAuthError:
            error, connected = "invalid_auth", []
        except SourceError as err:
            _LOGGER.warning("Could not list Mist clients: %s", err)
            error, connected = "cannot_connect", []
        known: dict[str, Client] = {}
        # Oldest first, so the newest non-empty values win; connected now last of all.
        for c in sorted(rows, key=lambda c: c.get("last_seen") or 0) + connected:
            if m := mac_key(c.get("mac")):
                known[m] = {**known.get(m, {}), **{k: v for k, v in c.items() if v not in (None, "")}}
        self._connected = {m for c in connected if (m := mac_key(c.get("mac")))}
        now = time.time()
        options: list[tuple[tuple[Any, ...], str, str]] = []
        for m, c in known.items():
            label = saved_name(self.hass, m) or known_name(self.hass, m, c.get("user_name")) or client_label(c)
            self._hostnames[m] = label
            status = "connected now" if m in self._connected else f"seen {_ago(now - (c.get('last_seen') or now))}"
            parts = [label, c.get("ip") or "no ip", mac_pretty(m), c.get("manufacture"), status]
            if m in current:
                key: tuple[Any, ...] = (0, label.lower())
            elif m in self._connected:
                key = (1, 0 if c.get("hostname") else 1, label.lower())
            else:
                key = (2, -(c.get("last_seen") or 0))
            options.append((key, m, " · ".join(str(p) for p in parts if p)))
        for m, name in current.items():
            if m not in known:
                options.append(((0, name.lower()), m, f"{name} · {mac_pretty(m)} · not seen in 30 days"))
        options.sort(key=lambda o: o[0])
        return [SelectOptionDict(value=mac_pretty(m), label=label) for _, m, label in options], error

    async def _remembered(self) -> dict[str, Client]:
        """Clients the sweeps have listed (the running entry's copy, or the saved one)."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is not None:
            return dict(coordinator.seen.clients)
        seen = SeenClients(self.hass, self.config_entry.entry_id)
        await seen.async_load()
        return seen.clients


def _ago(seconds: float) -> str:
    """'just now', '5 min ago', '3 h ago', '2 d ago'."""
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"
