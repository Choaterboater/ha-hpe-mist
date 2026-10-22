"""Config flow, options flow, stream + sweep presence, with Home Assistant's test helpers and a fake Mist."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hpe_mist.api import MistError
from custom_components.hpe_mist.const import (
    AWAY_AFTER_SECONDS,
    DOMAIN,
    POLL_SECONDS,
    SAFETY_SWEEP_SECONDS,
)
from custom_components.hpe_mist.coordinator import STREAM_TASK_NAME
from custom_components.hpe_mist.source import MistSource

HOST = "api.mist.com"
BASE = f"https://{HOST}/api/v1/"
ORG = "00000000-0000-0000-0000-0000000000aa"
SITE = "00000000-0000-0000-0000-0000000000b1"
TOKEN = "test-token-not-real"
PHONE = "02:00:00:00:00:76"
PHONE_A = {"mac": "020000000076", "hostname": "phone-a", "ip": "192.0.2.10", "ssid": "Example", "ap_mac": "020000000001",
           "band": "5", "rssi": -52, "snr": 41}
LAPTOP = {"mac": "020000000002", "hostname": "laptop-b", "ip": "192.0.2.11", "ssid": "Example"}
PRINTER = {"mac": "020000a3c7c2", "manufacture": "ExampleVendor", "ip": "192.0.2.12", "ssid": "Example"}
NO_NAME = {"mac": "020000000003", "ip": "192.0.2.13"}
ENTITY = "device_tracker.test_phone"
CLIENTS_URL = BASE + f"sites/{SITE}/stats/clients?limit=1000"
PORTS_URL = BASE + f"sites/{SITE}/stats/ports/search?limit=1000"
SEEN_URL = BASE + f"orgs/{ORG}/clients/search?duration=7d&limit=1000"
SEEN_WIRED_URL = BASE + f"orgs/{ORG}/wired_clients/search?duration=7d&limit=1000"


AP_MAC = "020000000001"
SWITCH_MAC = "020000000005"
AP = {
    "id": "d-ap", "mac": AP_MAC, "type": "ap", "name": "AP 1", "model": "AP-EXAMPLE", "status": "connected", "uptime": 3600,
    "num_clients": 1, "cpu_util": 7, "mem_used_kb": 250, "mem_total_kb": 1000, "env_stat": {"cpu_temp": 56},
    "version": "1.2.3",
    # Trimmed from a real AP's stats.
    "radio_stat": {
        "band_5": {"num_clients": 0, "channel": 36, "bandwidth": 40, "power": 11, "noise_floor": -97, "util_all": 1},
        "band_24": {"num_clients": 1, "channel": 11, "bandwidth": 20, "power": 9, "noise_floor": -95, "util_all": 3},
    },
    "port_stat": {"eth0": {"up": True, "speed": 5000}, "eth1": {"up": False, "speed": 0}},
    "lldp_stat": {"system_name": "core-switch", "port_desc": "1/1/8", "port_id": "1/1/8", "ap_port_name": "eth0",
                  "power_draw": 21100},
}
SWITCH = {
    "id": "d-sw", "mac": SWITCH_MAC, "type": "switch", "name": "Switch 1", "model": "SW-EXAMPLE", "status": "connected", "uptime": 7200,
    "cpu_stat": {"idle": 90}, "memory_stat": {"usage": 25}, "clients_stats": {"total": {"num_wired_clients": 4}},
    "if_stat": {"ge-0/0/1.0": {"port_id": "ge-0/0/1", "up": True}, "ge-0/0/2.0": {"port_id": "ge-0/0/2", "up": False},
                "irb.0": {"port_id": "irb", "up": True}},
    "module_stat": [{"poe": {}, "memory_stat": {"usage": 25}}],  # a virtual switch: no PoE
}


def port_row(port_id, up=True, mac=SWITCH_MAC, **more):
    """One row of sites/<site>/stats/ports/search (trimmed from a real answer)."""
    return {"mac": mac, "port_id": port_id, "up": up, "speed": 1000 if up else 0, "uplink": False, "port_usage": "",
            "device_type": "switch", "site_id": SITE, **more}


PORTS = [
    port_row("cbp0"), port_row("em1", speed=10000), port_row("fxp0"), port_row("irb"),
    port_row("ge-0/0/10", up=False),
    port_row("ge-0/0/2", up=False),  # the sweep's own link state wins: if_stat says down too
    port_row("ge-0/0/1", neighbor_system_name="AP 1", neighbor_port_desc="eth0", uplink=False,
             poe_on=True, power_draw=12.5, port_usage="ap"),
    port_row("ge-0/0/0", uplink=True, neighbor_system_name="core-1", neighbor_port_desc="1/1/1"),
]


class FakeStream:
    """Stands in for the Mist websocket: connects when up, delivers queued clients
    (dicts) and device updates (("device", dict))."""

    def __init__(self) -> None:
        self.up = False
        self.queue: asyncio.Queue = asyncio.Queue()
        self.connects = 0

    def method(self):
        fake = self

        async def stream(_source, on_client, on_device, on_connected):
            if not fake.up:
                raise MistError("stream refused")
            fake.connects += 1
            on_connected()
            while (item := await fake.queue.get()) is not None:
                # The real stream adds the site_id from the channel name.
                if isinstance(item, tuple):
                    on_device({"site_id": SITE, **item[1]})
                else:
                    on_client({"site_id": SITE, **item})
            raise MistError("stream lost")

        return stream


@pytest.fixture
def stream():
    fake = FakeStream()
    with patch.object(MistSource, "stream", fake.method()):
        yield fake


def mist(aioclient_mock, clients=None, status=200, host=HOST, devices=None, alarms=None, role="read",
         ports=None, ports_status=200, seen=None, seen_wired=None, seen_status=200):
    base = f"https://{host}/api/v1/"
    aioclient_mock.clear_requests()
    aioclient_mock.get(base + "self", json={"privileges": [
        {"scope": "org", "org_id": ORG, "name": "Example org", "role": role}, {"scope": "site", "site_id": SITE}]})
    aioclient_mock.get(base + f"orgs/{ORG}/wlans", json=[{"id": "w-org", "ssid": "Example", "enabled": True}])
    aioclient_mock.get(base + f"sites/{SITE}/wlans", json=[{"id": "w-site", "ssid": "Guest", "enabled": False}])
    aioclient_mock.post(base + f"sites/{SITE}/devices/d-ap/restart", text="")
    aioclient_mock.put(base + f"orgs/{ORG}/wlans/w-org", json={"id": "w-org", "enabled": False})
    aioclient_mock.put(base + f"sites/{SITE}/wlans/w-site", status=403)
    aioclient_mock.get(base + f"orgs/{ORG}/sites", json=[{"id": SITE, "name": "Site 1"}])
    aioclient_mock.get(base + f"sites/{SITE}/stats/devices?type=all&limit=1000", json=devices if devices is not None else [AP, SWITCH])
    aioclient_mock.get(base + f"orgs/{ORG}/alarms/search?duration=1d&limit=1000", json={"results": alarms or []})
    aioclient_mock.get(base + f"sites/{SITE}/stats/clients?limit=1000", json=clients if clients is not None else [], status=status)
    aioclient_mock.get(base + f"sites/{SITE}/stats/ports/search?limit=1000",
                       json={"results": ports if ports is not None else PORTS}, status=ports_status)
    aioclient_mock.get(base + f"orgs/{ORG}/clients/search?duration=7d&limit=1000",
                       json={"results": seen or []}, status=seen_status)
    aioclient_mock.get(base + f"orgs/{ORG}/wired_clients/search?duration=7d&limit=1000",
                       json={"results": seen_wired or []}, status=seen_status)


def sweeps(aioclient_mock) -> int:
    return sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == CLIENTS_URL)


async def settle(hass):
    """Let pending work finish, including coordinator refreshes, but not the endless stream task."""
    for _ in range(3):
        await hass.async_block_till_done()
        pending = [t for t in hass._background_tasks if not t.done() and t.get_name() != STREAM_TASK_NAME]
        if pending:
            await asyncio.wait(pending, timeout=5)
    await hass.async_block_till_done()


async def tick(hass, freezer, seconds=POLL_SECONDS + 1):
    """Move time on (the coordinator adds up to 0.5 s of jitter to each sweep)."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def entry(options=None):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Example org",
        unique_id=ORG,
        data={"host": HOST, "token": TOKEN, "org_id": ORG},
        options=options if options is not None else {"tracked": {PHONE: "Test Phone"}},
    )


async def setup(hass, e=None):
    e = e or entry()
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    return e


# --- config and options flows ---------------------------------------------


async def test_config_flow_offers_clouds_validates_token_and_picks_org(hass, aioclient_mock, stream):
    mist(aioclient_mock)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    cfg = r["data_schema"].schema["host"].config
    assert {"api.mist.com", "api.eu.mist.com", "api.gc4.mist.com"} <= {o["value"] for o in cfg["options"]}
    assert cfg["custom_value"] is True
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"host": HOST, "token": TOKEN})
    assert r["step_id"] == "org"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"org_id": ORG})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Example org"
    assert r["data"] == {"host": HOST, "token": TOKEN, "org_id": ORG}
    assert r["result"].unique_id == ORG
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Token {TOKEN}"


async def test_config_flow_custom_host(hass, aioclient_mock, stream):
    mist(aioclient_mock, host="api.example.test")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"host": "https://api.example.test/", "token": TOKEN})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"org_id": ORG})
    assert r["data"]["host"] == "api.example.test"


async def test_import_takes_over_an_earlier_entry(hass, aioclient_mock, stream):
    mist(aioclient_mock)
    data = {"host": HOST, "token": TOKEN, "org_id": ORG, "title": "Example org", "options": {"tracked": {PHONE: "Test Phone"}}}
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=data)
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Example org"
    assert r["data"] == {"host": HOST, "token": TOKEN, "org_id": ORG}
    assert r["result"].options == {"tracked": {PHONE: "Test Phone"}, "add_all_clients": True}
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=data)
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_config_flow_bad_token(hass, aioclient_mock, stream):
    aioclient_mock.get(BASE + "self", status=401)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"host": HOST, "token": "bad"})
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"base": "invalid_auth"}


async def test_config_flow_one_entry_per_org(hass, aioclient_mock, stream):
    mist(aioclient_mock)
    entry(options={"tracked": {}}).add_to_hass(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"host": HOST, "token": TOKEN})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"org_id": ORG})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_options_flow_pick_connected_and_typed_mac_then_name(hass, aioclient_mock, stream):
    mist(aioclient_mock, [PRINTER, NO_NAME, PHONE_A, LAPTOP])
    e = await setup(hass, entry(options={"tracked": {}}))
    ent_reg = er.async_get(hass)
    # Every client got a tracker, disabled until enabled.
    for eid in ("device_tracker.phone_a", "device_tracker.laptop_b", "device_tracker.examplevendor_a3_c7_c2"):
        assert ent_reg.async_get(eid).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    r = await hass.config_entries.options.async_init(e.entry_id)
    assert r["step_id"] == "init"
    opts = r["data_schema"].schema["clients"].config["options"]
    # Clients with a hostname first, then vendor + last 3 MAC bytes or the bare MAC; never "?".
    assert [o["label"] for o in opts] == [
        "laptop-b · 192.0.2.11 · 02:00:00:00:00:02 · connected now",
        f"phone-a · 192.0.2.10 · {PHONE} · connected now",
        "02:00:00:00:00:03 · 192.0.2.13 · 02:00:00:00:00:03 · connected now",
        "ExampleVendor a3:c7:c2 · 192.0.2.12 · 02:00:00:a3:c7:c2 · ExampleVendor · connected now",
    ]

    r = await hass.config_entries.options.async_configure(r["flow_id"], {"clients": [PHONE], "extra_mac": "zz"})
    assert r["errors"] == {"extra_mac": "invalid_mac"}

    r = await hass.config_entries.options.async_configure(r["flow_id"], {"clients": [PHONE], "extra_mac": "02-00-00-00-00-09"})
    assert r["step_id"] == "names"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {PHONE: "Test Phone", "02:00:00:00:00:09": "Spare"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)
    assert e.options == {"tracked": {PHONE: "Test Phone", "02:00:00:00:00:09": "Spare"}, "add_all_clients": True}

    # Saving reloaded the entry: both trackers exist, each with its own named device. The
    # auto-added tracker of the picked client was enabled and renamed after its new device.
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert hass.states.get("device_tracker.spare").state == STATE_NOT_HOME
    dev = dr.async_get(hass).async_get(er.async_get(hass).async_get(ENTITY).device_id)
    assert dev.name == "Test Phone"

    # Unticked with "add all" on: the tracker stays, disabled, without its device or sensors.
    ent_reg = er.async_get(hass)
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"clients": [PHONE], "extra_mac": ""})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {PHONE: "Test Phone"})
    await settle(hass)
    spare = ent_reg.async_get("device_tracker.spare")
    assert spare.disabled_by is er.RegistryEntryDisabler.INTEGRATION and spare.device_id is None
    assert ent_reg.async_get("sensor.spare_ip_address") is None
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, f"{ORG}_020000000009"), e.entry_id) is None

    # "Add all" off: only ticked clients have trackers.
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"clients": [PHONE], "extra_mac": "", "add_all_clients": False})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {PHONE: "Test Phone"})
    await settle(hass)
    assert ent_reg.async_get("device_tracker.spare") is None
    assert ent_reg.async_get("device_tracker.laptop_b") is None
    assert hass.states.get(ENTITY).state == STATE_HOME


# --- presence ------------------------------------------------------------------


async def test_attributes(hass, aioclient_mock, stream):
    mist(aioclient_mock, [PHONE_A])
    await setup(hass)
    s = hass.states.get(ENTITY)
    assert s.state == STATE_HOME
    assert s.attributes["source_type"] == "router"
    assert s.attributes["ip"] == "192.0.2.10"
    assert s.attributes["ssid"] == "Example"
    assert s.attributes["ap_name"] == "AP 1"
    assert s.attributes["ap_mac"] == "02:00:00:00:00:01"
    assert s.attributes["mac"] == PHONE
    assert s.attributes["last_seen"]
    assert s.attributes["friendly_name"] == "Test Phone"


async def test_stream_message_marks_phone_home_immediately(hass, aioclient_mock, stream):
    stream.up = True
    mist(aioclient_mock, [])
    e = await setup(hass)
    coordinator = e.runtime_data
    assert stream.connects == 1 and coordinator.stream_connected
    assert coordinator.update_interval == timedelta(seconds=SAFETY_SWEEP_SECONDS)
    assert sweeps(aioclient_mock) == 2  # first refresh + one resync right after connecting
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME

    stream.queue.put_nowait({**LAPTOP})
    await settle(hass)
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME
    stream.queue.put_nowait({**PHONE_A})
    await settle(hass)  # no time passes: the stream message alone makes it home
    s = hass.states.get(ENTITY)
    assert s.state == STATE_HOME and s.attributes["ip"] == "192.0.2.10"


async def test_safety_sweep_every_five_minutes_while_stream_up(hass, aioclient_mock, stream, freezer):
    stream.up = True
    mist(aioclient_mock, [])
    await setup(hass)
    before = sweeps(aioclient_mock)
    for _ in range(9):  # 9 x 31 s = 279 s
        await tick(hass, freezer)
    assert sweeps(aioclient_mock) == before
    await tick(hass, freezer, 30)
    assert sweeps(aioclient_mock) == before + 1


async def test_stream_down_falls_back_to_30_s_polling(hass, aioclient_mock, stream, freezer):
    stream.up = True
    mist(aioclient_mock, [])
    e = await setup(hass)
    coordinator = e.runtime_data
    assert coordinator.update_interval == timedelta(seconds=SAFETY_SWEEP_SECONDS)

    stream.up = False
    before = sweeps(aioclient_mock)
    stream.queue.put_nowait(None)  # connection lost; reconnects are refused from now on
    await settle(hass)
    assert not coordinator.stream_connected
    assert coordinator.update_interval == timedelta(seconds=POLL_SECONDS)
    await tick(hass, freezer, 11)  # resync sweep (the refresh debouncer allows one per 10 s)
    assert sweeps(aioclient_mock) == before + 1

    mist(aioclient_mock, [PHONE_A])
    await tick(hass, freezer)
    assert sweeps(aioclient_mock) == 1
    assert hass.states.get(ENTITY).state == STATE_HOME

    # Stream back: one resync sweep, then 5-minute sweeps again.
    stream.up = True
    for _ in range(12):
        await tick(hass, freezer, 10)
        if coordinator.stream_connected:
            break
    assert coordinator.stream_connected
    assert coordinator.update_interval == timedelta(seconds=SAFETY_SWEEP_SECONDS)


async def test_poll_alone_works_when_stream_never_connects(hass, aioclient_mock, stream, freezer):
    mist(aioclient_mock, [])
    e = await setup(hass)
    assert not e.runtime_data.stream_connected
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME
    mist(aioclient_mock, [PHONE_A])
    await tick(hass, freezer)
    assert hass.states.get(ENTITY).state == STATE_HOME


async def test_away_after_ten_minutes_without_stream_or_sweep_sighting(hass, aioclient_mock, stream, freezer):
    stream.up = True
    mist(aioclient_mock, [])
    await setup(hass)
    stream.queue.put_nowait({**PHONE_A})
    await settle(hass)
    assert hass.states.get(ENTITY).state == STATE_HOME

    elapsed = 0
    while elapsed + 31 < AWAY_AFTER_SECONDS:
        await tick(hass, freezer)
        elapsed += 31
        assert hass.states.get(ENTITY).state == STATE_HOME, elapsed
    await tick(hass, freezer, 40)  # next 30 s away check after the 10 minutes
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME

    stream.queue.put_nowait({**PHONE_A})
    await settle(hass)
    assert hass.states.get(ENTITY).state == STATE_HOME


async def test_away_after_ten_minutes_poll_only(hass, aioclient_mock, stream, freezer):
    mist(aioclient_mock, [PHONE_A])
    await setup(hass)
    mist(aioclient_mock, [])
    elapsed = 0
    while elapsed + 31 < AWAY_AFTER_SECONDS:
        await tick(hass, freezer)
        elapsed += 31
        assert hass.states.get(ENTITY).state == STATE_HOME, elapsed
    await tick(hass, freezer)
    assert hass.states.get(ENTITY).state == STATE_NOT_HOME


@pytest.mark.parametrize("status", [429, 503])
async def test_mist_trouble_keeps_state_then_unavailable_never_not_home(hass, aioclient_mock, stream, freezer, status):
    mist(aioclient_mock, [PHONE_A])
    e = await setup(hass)
    coordinator = e.runtime_data

    mist(aioclient_mock, [], status=status)
    await tick(hass, freezer)
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert coordinator.update_interval == timedelta(seconds=60)  # backed off

    seen = set()
    for _ in range(20):
        await tick(hass, freezer, coordinator.update_interval.seconds + 1)
        seen.add(hass.states.get(ENTITY).state)
    assert STATE_NOT_HOME not in seen
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    assert coordinator.update_interval <= timedelta(seconds=300)

    mist(aioclient_mock, [PHONE_A])
    await tick(hass, freezer, coordinator.update_interval.seconds + 1)
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert coordinator.update_interval == timedelta(seconds=30)


async def test_rest_trouble_with_stream_up_stays_available(hass, aioclient_mock, stream, freezer):
    stream.up = True
    mist(aioclient_mock, [PHONE_A])
    await setup(hass)
    mist(aioclient_mock, [], status=503)
    for _ in range(4):
        await tick(hass, freezer, 301)
        stream.queue.put_nowait({**PHONE_A})
        await settle(hass)
        assert hass.states.get(ENTITY).state == STATE_HOME


async def test_setup_retries_when_mist_down(hass, aioclient_mock, stream):
    mist(aioclient_mock, [], status=500)
    e = entry()
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    assert e.state is config_entries.ConfigEntryState.SETUP_RETRY


async def test_unload_stops_stream(hass, aioclient_mock, stream):
    stream.up = True
    mist(aioclient_mock, [])
    e = await setup(hass)
    assert any(t.get_name() == STREAM_TASK_NAME and not t.done() for t in hass._background_tasks)
    assert await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()
    assert not any(t.get_name() == STREAM_TASK_NAME and not t.done() for t in hass._background_tasks)


async def test_token_never_logged(hass, aioclient_mock, stream, caplog, freezer):
    mist(aioclient_mock, [PHONE_A])
    await setup(hass)
    mist(aioclient_mock, [], status=503)
    for _ in range(10):
        await tick(hass, freezer, 301)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE
    assert "Mist answered 503" in caplog.text
    assert TOKEN not in caplog.text


# --- sites, devices, events ------------------------------------------------------


async def test_site_and_device_entities(hass, aioclient_mock, stream):
    mist(aioclient_mock, [PHONE_A, LAPTOP])
    e = await setup(hass)
    st = lambda eid: hass.states.get(eid).state  # noqa: E731
    assert st("sensor.site_1_wireless_clients") == "2"
    assert st("sensor.site_1_wired_clients") == "4"
    assert st("sensor.site_1_clients") == "6"
    assert st("sensor.site_1_clients_on_example") == "2"
    assert st("sensor.site_1_aps_online") == "1"
    assert st("sensor.site_1_aps_offline") == "0"
    assert st("sensor.site_1_active_alarms") == "0"
    assert st("binary_sensor.ap_1_online") == "on"
    assert st("sensor.ap_1_clients") == "1"
    assert st("sensor.ap_1_cpu") == "7"
    assert st("sensor.ap_1_memory") == "25.0"
    assert st("sensor.ap_1_temperature") == "56"
    assert st("sensor.ap_1_firmware") == "1.2.3"
    assert st("sensor.ap_1_2_4_ghz_channel") == "11"
    assert st("sensor.ap_1_5_ghz_utilization") == "1"
    assert hass.states.get("sensor.ap_1_6_ghz_channel") is None  # no such radio
    assert st("sensor.switch_1_cpu") == "10.0"
    assert hass.states.get("sensor.switch_1_temperature") is None  # not reported
    assert hass.states.get("sensor.ap_1_last_restart").state.endswith("+00:00")
    ent_reg = er.async_get(hass)
    port = ent_reg.async_get("binary_sensor.switch_1_port_ge_0_0_1")
    assert port.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert ent_reg.async_get("binary_sensor.switch_1_port_irb") is None
    # Picked client sensors live on the client's device.
    assert st("sensor.test_phone_ip_address") == "192.0.2.10"
    assert st("sensor.test_phone_access_point") == "AP 1"
    # Devices: the AP sits under its site.
    dev_reg = dr.async_get(hass)
    site = dev_reg.async_get_device_by_identifier((DOMAIN, f"{ORG}_site_{SITE}"), e.entry_id)
    ap = dev_reg.async_get_device_by_identifier((DOMAIN, f"{ORG}_dev_{AP_MAC}"), e.entry_id)
    assert site.name == "Site 1" and ap.name == "AP 1" and ap.via_device_id == site.id and ap.model == "AP-EXAMPLE"


async def test_new_devices_clients_and_ssids_appear_without_reload(hass, aioclient_mock, stream, freezer):
    mist(aioclient_mock, [], devices=[AP])
    await setup(hass)
    assert hass.states.get("binary_sensor.switch_1_online") is None
    mist(aioclient_mock, [{**LAPTOP, "ssid": "Guest"}], devices=[AP, SWITCH])
    await tick(hass, freezer)
    assert hass.states.get("binary_sensor.switch_1_online").state == "on"
    assert hass.states.get("sensor.site_1_clients_on_guest").state == "1"
    assert er.async_get(hass).async_get("device_tracker.laptop_b") is not None


def events_of(hass):
    s = hass.states.get("event.site_1_events")
    return s


async def test_events_client_join_leave_device_down_up_alarm(hass, aioclient_mock, stream, freezer):
    stream.up = True
    mist(aioclient_mock, [LAPTOP])
    await setup(hass)
    assert events_of(hass).state == "unknown"  # the first sweep only seeds

    stream.queue.put_nowait({**PHONE_A})
    await settle(hass)
    ev = events_of(hass)
    assert ev.attributes["event_type"] == "client_joined"
    assert ev.attributes["mac"] == PHONE and ev.attributes["name"] == "Test Phone" and ev.attributes["tracked"] is True
    assert ev.attributes["ap"] == "AP 1"

    mist(aioclient_mock, [LAPTOP], devices=[{**AP, "status": "disconnected"}, SWITCH],
         alarms=[{"id": "a1", "site_id": SITE, "type": "ap_offline", "severity": "warn", "count": 1}])
    await tick(hass, freezer, SAFETY_SWEEP_SECONDS + 1)
    # The sweep fired device_down then alarm; the entity keeps the last one.
    assert events_of(hass).attributes["event_type"] == "alarm"
    assert events_of(hass).attributes["severity"] == "warn"
    assert hass.states.get("binary_sensor.ap_1_online").state == "off"
    assert hass.states.get("sensor.site_1_active_alarms").state == "1"
    assert hass.states.get("sensor.ap_1_cpu").state == "unavailable"

    stream.queue.put_nowait(("device", {"mac": AP_MAC, "site_id": SITE, "online": True}))
    await settle(hass)
    assert events_of(hass).attributes["event_type"] == "device_up"
    assert hass.states.get("binary_sensor.ap_1_online").state == "on"

    # LAPTOP listed by every sweep; PHONE_A never again: it leaves after 10 minutes.
    mist(aioclient_mock, [LAPTOP])
    for _ in range(AWAY_AFTER_SECONDS // 31 + 2):
        await tick(hass, freezer)
    ev = events_of(hass)
    assert ev.attributes["event_type"] == "client_left" and ev.attributes["mac"] == PHONE


async def test_device_down_event_from_sweep(hass, aioclient_mock, stream, freezer):
    mist(aioclient_mock, [])
    await setup(hass)
    seen = []
    hass.bus.async_listen("state_changed", lambda e: e.data["entity_id"] == "event.site_1_events" and seen.append(
        e.data["new_state"].attributes.get("event_type")))
    mist(aioclient_mock, [], devices=[AP, {**SWITCH, "status": "disconnected"}])
    await tick(hass, freezer)
    assert seen == ["device_down"]
    assert events_of(hass).attributes["name"] == "Switch 1"


# --- controls ---------------------------------------------------------------------


async def test_read_only_token_has_no_controls(hass, aioclient_mock, stream):
    mist(aioclient_mock, [], role="read")
    e = await setup(hass)
    ents = er.async_entries_for_config_entry(er.async_get(hass), e.entry_id)
    assert not [x for x in ents if x.domain in ("button", "switch")]
    assert not any("wlans" in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_admin_token_controls_disabled_by_default_and_work(hass, aioclient_mock, stream):
    from homeassistant.exceptions import HomeAssistantError

    mist(aioclient_mock, [], role="admin")
    e = await setup(hass)
    ent_reg = er.async_get(hass)
    ids = {"button.ap_1_restart", "button.switch_1_restart", "switch.example_org_wlan_example", "switch.site_1_wlan_guest"}
    for eid in ids:
        assert ent_reg.async_get(eid).disabled_by is er.RegistryEntryDisabler.INTEGRATION, eid
        ent_reg.async_update_entity(eid, disabled_by=None)
    assert await hass.config_entries.async_reload(e.entry_id)
    await settle(hass)
    assert hass.states.get("switch.example_org_wlan_example").state == "on"
    assert hass.states.get("switch.site_1_wlan_guest").state == "off"

    await hass.services.async_call("button", "press", {"entity_id": "button.ap_1_restart"}, blocking=True)
    assert any(c[0] == "POST" and str(c[1]).endswith(f"sites/{SITE}/devices/d-ap/restart") for c in aioclient_mock.mock_calls)

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.example_org_wlan_example"}, blocking=True)
    put = [c for c in aioclient_mock.mock_calls if c[0] == "PUT"]
    assert str(put[-1][1]).endswith(f"orgs/{ORG}/wlans/w-org") and put[-1][2] == {"enabled": False}
    assert hass.states.get("switch.example_org_wlan_example").state == "off"

    with pytest.raises(HomeAssistantError, match="403"):
        await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.site_1_wlan_guest"}, blocking=True)
    assert hass.states.get("switch.site_1_wlan_guest").state == "off"


# --- richer device data, picker history, live stream sensor ---------------------------


async def test_ports_radios_uplink_and_client_lists(hass, aioclient_mock, stream):
    mist(aioclient_mock, [PHONE_A, LAPTOP, PRINTER, {"mac": "020000000003"}])
    await setup(hass)
    ports = hass.states.get("sensor.switch_1_ports_up")
    # ge-0/0/0, ge-0/0/1 up; ge-0/0/2, ge-0/0/10 down; cbp0 / em1 / fxp0 / irb are not physical ports.
    assert ports.state == "2" and ports.attributes["ports_total"] == 4
    assert [p["port"] for p in ports.attributes["ports"]] == ["ge-0/0/0", "ge-0/0/1", "ge-0/0/2", "ge-0/0/10"]
    assert ports.attributes["ports"][1] == {
        "port": "ge-0/0/1", "up": True, "speed": 1000, "uplink": False, "poe_w": 12.5, "neighbor": "AP 1",
        "neighbor_port": "eth0", "vlan": None, "description": "ap"}
    assert ports.attributes["ports"][0]["uplink"] is True
    # The port binary sensors keep reading the link state; new physical ports get one too (disabled).
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("binary_sensor.switch_1_port_ge_0_0_10") is not None
    assert ent_reg.async_get("binary_sensor.switch_1_port_em1") is None
    assert hass.states.get("sensor.switch_1_poe_draw") is None  # module_stat poe is {}
    assert hass.states.get("sensor.switch_1_vlans") is None
    assert hass.states.get("sensor.ap_1_ports_up") is None

    radios = hass.states.get("sensor.ap_1_radios")
    assert radios.state == "2"
    assert [r["band"] for r in radios.attributes["radios"]] == ["2.4 GHz", "5 GHz"]
    assert radios.attributes["radios"][1] == {"band": "5 GHz", "status": "up", "channel": "36", "bandwidth": 40,
                                              "power": 11.0, "utilization": 1, "noise_floor": -97.0, "clients": 0}
    assert hass.states.get("sensor.ap_1_2_4_ghz_power").state == "9.0"
    assert hass.states.get("sensor.ap_1_5_ghz_noise_floor").state == "-97.0"
    assert hass.states.get("sensor.ap_1_6_ghz_power") is None
    uplink = hass.states.get("sensor.ap_1_uplink")
    assert uplink.state == "core-switch 1/1/8"
    assert {k: uplink.attributes[k] for k in ("device", "port", "speed", "poe_w")} == {
        "device": "core-switch", "port": "1/1/8", "speed": 5000, "poe_w": 21.1}
    assert hass.states.get("sensor.ap_1_wlans") is None  # Mist AP stats do not list WLANs

    ap = hass.states.get("sensor.ap_1_clients")
    assert ap.attributes["clients"] == [
        {"name": "Test Phone", "mac": PHONE, "ip": "192.0.2.10", "ssid": "Example", "band": "5 GHz", "rssi": -52,
         "snr": 41, "vendor": None}]
    assert ap.attributes["by_band"] == {"2.4 GHz": 0, "5 GHz": 1, "6 GHz": 0}
    assert ap.attributes["by_ssid"] == {"Example": 1}
    assert "clients" not in hass.states.get("sensor.switch_1_clients").attributes

    site = hass.states.get("sensor.site_1_clients")
    # Tracked name, else hostname, else vendor + last 3 MAC bytes, else the MAC; by name.
    assert [c["name"] for c in site.attributes["clients"]] == [
        "02:00:00:00:00:03", "ExampleVendor a3:c7:c2", "laptop-b", "Test Phone"]
    assert site.attributes["clients"][1]["vendor"] == "ExampleVendor"
    assert site.attributes["clients"][3] == {
        "name": "Test Phone", "mac": PHONE, "ip": "192.0.2.10", "ssid": "Example", "band": "5 GHz", "ap": "AP 1",
        "wired": False, "port": None, "switch": None, "rssi": -52, "snr": 41, "vendor": None}

    # List attributes stay out of the recorder.
    from custom_components.hpe_mist.sensor import DeviceSensor, SiteSensor
    for cls in (DeviceSensor, SiteSensor):
        assert {"clients", "radios", "ports", "wlans", "vlans"} <= cls._unrecorded_attributes


def port_searches(aioclient_mock) -> int:
    return sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == PORTS_URL)


async def test_port_search_once_per_detail_cycle_and_never_fails_the_sweep(hass, aioclient_mock, stream, freezer):
    mist(aioclient_mock, [PHONE_A])
    await setup(hass)
    assert port_searches(aioclient_mock) == 1
    for _ in range(8):  # 8 x 31 s of 30 s sweeps: the cached ports are used
        await tick(hass, freezer)
    assert port_searches(aioclient_mock) == 1
    assert hass.states.get("sensor.switch_1_ports_up").attributes["ports_total"] == 4

    mist(aioclient_mock, [PHONE_A], ports_status=404)
    for _ in range(3):
        await tick(hass, freezer)
    assert port_searches(aioclient_mock) == 1  # asked again once the 5 minutes were up
    # The sweep itself went fine; the cached details stay.
    assert hass.states.get(ENTITY).state == STATE_HOME
    assert hass.states.get("sensor.switch_1_ports_up").attributes["ports_total"] == 4

    # An AP-only site never asks.
    mist(aioclient_mock, [PHONE_A], devices=[AP])
    for _ in range(12):
        await tick(hass, freezer)
    assert port_searches(aioclient_mock) == 0


async def test_seen_clients_are_remembered_and_old_ones_dropped(hass, aioclient_mock, stream, freezer, hass_storage):
    import time

    from custom_components.hpe_mist.seen import store_key

    e = entry()
    now = time.time()
    hass_storage[store_key(e.entry_id)] = {"version": 1, "minor_version": 1, "key": store_key(e.entry_id), "data": {
        "clients": {
            "020000000031": {"mac": "020000000031", "hostname": "old-tablet", "last_seen": now - 31 * 86400},
            "020000000032": {"mac": "020000000032", "hostname": "tv", "ip": "192.0.2.32", "last_seen": now - 3600},
        }}}
    mist(aioclient_mock, [PHONE_A])
    await setup(hass, e)
    await tick(hass, freezer, 61)  # the batched save
    saved = hass_storage[store_key(e.entry_id)]["data"]["clients"]
    assert set(saved) == {"020000000032", "020000000076"}
    assert saved["020000000076"]["hostname"] == "phone-a" and saved["020000000076"]["band"] == "5 GHz"

    # 30 s sweeps (stream down) do not keep pushing the save back.
    mist(aioclient_mock, [PHONE_A, LAPTOP])
    for _ in range(3):
        await tick(hass, freezer)
    assert "020000000002" in hass_storage[store_key(e.entry_id)]["data"]["clients"]


OFFLINE = {"mac": "020000000057", "site_id": SITE, "last_ip": "192.0.2.57", "mfg": "Example Vendor", "last_ssid": "Example",
           "band": "24"}
OFFLINE_NAMED = {"mac": "020000001b48", "site_id": SITE, "last_ip": "192.0.2.82", "last_hostname": "espressif",
                 "mfg": "Espressif Inc."}
WIRED = {"mac": "02000000fe62", "site_id": SITE, "last_ip": "192.0.2.15", "manufacture": "Proxmox Server Solutions GmbH",
         "last_port_id": "ge-0/0/2", "last_device_mac": SWITCH_MAC, "last_vlan": 10}


async def test_options_picker_lists_clients_seen_in_the_last_7_days(hass, aioclient_mock, stream, freezer):
    import time

    now = time.time()
    mist(aioclient_mock, [LAPTOP], seen=[{**OFFLINE, "timestamp": now - 7200}, {**OFFLINE_NAMED, "timestamp": now - 300}],
         seen_wired=[{**WIRED, "timestamp": now - 2 * 86400}])
    e = await setup(hass)
    r = await hass.config_entries.options.async_init(e.entry_id)
    labels = [o["label"] for o in r["data_schema"].schema["clients"].config["options"]]
    # Tracked first (never seen), then connected now, then offline by most recent.
    assert labels == [
        f"Test Phone · {PHONE} · not seen in 30 days",
        "laptop-b · 192.0.2.11 · 02:00:00:00:00:02 · connected now",
        "espressif · 192.0.2.82 · 02:00:00:00:1b:48 · Espressif Inc. · seen 5 min ago",
        "Example Vendor 00:00:57 · 192.0.2.57 · 02:00:00:00:00:57 · Example Vendor · seen 2 h ago",
        "Proxmox Server Solutions GmbH 00:fe:62 · 192.0.2.15 · 02:00:00:00:fe:62 · Proxmox Server Solutions GmbH · seen 2 d ago",
    ]
    assert any(str(c[1]) == SEEN_URL for c in aioclient_mock.mock_calls)
    assert any(str(c[1]) == SEEN_WIRED_URL for c in aioclient_mock.mock_calls)

    # An offline client can be picked like any other.
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"clients": [PHONE, "02:00:00:00:1b:48"]})
    assert r["step_id"] == "names"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {PHONE: "Test Phone", "02:00:00:00:1b:48": "Plug"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert e.options["tracked"] == {PHONE: "Test Phone", "02:00:00:00:1b:48": "Plug"}


async def test_options_picker_falls_back_to_remembered_clients_on_403(hass, aioclient_mock, stream, freezer):
    mist(aioclient_mock, [LAPTOP], seen_status=403)
    e = await setup(hass)
    mist(aioclient_mock, [], seen_status=403)  # the laptop has left since; the sweeps remember it
    r = await hass.config_entries.options.async_init(e.entry_id)
    assert "errors" not in r or not r["errors"]
    labels = [o["label"] for o in r["data_schema"].schema["clients"].config["options"]]
    assert labels == [
        f"Test Phone · {PHONE} · not seen in 30 days",
        "laptop-b · 192.0.2.11 · 02:00:00:00:00:02 · seen just now",
    ]


async def test_live_stream_binary_sensor(hass, aioclient_mock, stream, freezer):
    stream.up = True
    mist(aioclient_mock, [])
    await setup(hass)
    assert hass.states.get("binary_sensor.site_1_live_stream").state == "on"
    reg = er.async_get(hass).async_get("binary_sensor.site_1_live_stream")
    assert reg.unique_id == f"{ORG}_site_{SITE}_stream" and reg.entity_category == "diagnostic"

    stream.up = False
    stream.queue.put_nowait(None)  # connection lost; reconnects refused
    await settle(hass)
    assert hass.states.get("binary_sensor.site_1_live_stream").state == "off"

    stream.up = True
    for _ in range(12):
        await tick(hass, freezer, 10)
        if hass.states.get("binary_sensor.site_1_live_stream").state == "on":
            break
    assert hass.states.get("binary_sensor.site_1_live_stream").state == "on"


async def test_no_live_stream_sensor_without_a_stream_host(hass, aioclient_mock, stream):
    mist(aioclient_mock, [], host="example.test")
    e = MockConfigEntry(domain=DOMAIN, title="Example org", unique_id=ORG,
                        data={"host": "example.test", "token": TOKEN, "org_id": ORG}, options={"tracked": {}})
    await setup(hass, e)
    assert hass.states.get("sensor.site_1_clients") is not None
    assert hass.states.get("binary_sensor.site_1_live_stream") is None


async def test_client_named_by_another_integration(hass, aioclient_mock, stream):
    # A camera another integration already knows by MAC is listed by that name, not its hostname.
    other = MockConfigEntry(domain="example_camera")
    other.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, name="Front door",
                                           connections={(dr.CONNECTION_NETWORK_MAC, "02:00:00:00:00:02")})
    mist(aioclient_mock, [PHONE_A, LAPTOP])
    await setup(hass)
    site = hass.states.get("sensor.site_1_clients")
    assert [c["name"] for c in site.attributes["clients"]] == ["Front door", "Test Phone"]
