"""Presence rule and Mist stream parsing, no Home Assistant needed."""

import json

from custom_components.hpe_mist.api import parse_stream_message, stream_url_for
from custom_components.hpe_mist.const import AWAY_AFTER_SECONDS
from custom_components.hpe_mist.presence import PresenceTracker, mac_key, mac_pretty

PHONE = "02:00:00:00:00:76"
KEY = "020000000076"


def test_mac_forms():
    assert mac_key("02:00:00:00:00:76") == KEY
    assert mac_key("02-00-00-00-00-76") == KEY
    assert mac_key(KEY.upper()) == KEY
    assert mac_key("nope") == ""
    assert mac_key(None) == ""
    assert mac_pretty(KEY) == PHONE


def test_away_before_first_sighting():
    t = PresenceTracker({PHONE})
    assert t.update([], 1000.0) == ([], [])
    assert t.state[KEY].home is False


def test_home_on_first_sweep_listing_it():
    t = PresenceTracker({PHONE})
    assert t.update(
        [{"mac": KEY, "ip": "192.0.2.10", "hostname": "phone-a", "ssid": "Example", "ap_mac": "020000000001"}], 1000.0
    ) == ([KEY], [])
    s = t.state[KEY]
    assert s.home and s.ip == "192.0.2.10" and s.hostname == "phone-a" and s.ssid == "Example"
    assert s.ap_mac == "020000000001" and s.last_seen == 1000.0


def test_stream_sighting_is_home_at_once_and_every_client_is_kept():
    t = PresenceTracker({PHONE})
    assert t.see({"mac": "020000000002"}, 5.0) is True
    assert t.see({"mac": PHONE}, 5.0) is True
    assert t.see({"mac": PHONE}, 6.0) is False  # already home: not a new join
    assert t.state[KEY].home is True and t.state["020000000002"].home is True


def test_home_through_short_gap_then_away_after_ten_minutes():
    t = PresenceTracker({PHONE})
    t.see({"mac": KEY}, 1000.0)
    assert t.evaluate(1000.0 + AWAY_AFTER_SECONDS - 1) == []
    assert t.state[KEY].home is True
    assert t.evaluate(1000.0 + AWAY_AFTER_SECONDS) == [KEY]
    assert t.state[KEY].home is False
    assert t.update([{"mac": KEY}], 5000.0) == ([KEY], [])


def test_details_kept_when_a_sighting_lacks_them():
    t = PresenceTracker({PHONE})
    t.update([{"mac": KEY, "ip": "192.0.2.10"}], 1.0)
    t.update([{"mac": KEY}], 31.0)
    assert t.state[KEY].ip == "192.0.2.10"


def test_stream_url_per_cloud():
    assert stream_url_for("api.mist.com") == "wss://api-ws.mist.com/api-ws/v1/stream"
    assert stream_url_for("https://api.eu.mist.com/") == "wss://api-ws.eu.mist.com/api-ws/v1/stream"
    assert stream_url_for("mist.example.net") is None


def test_parse_stream_message():
    client = {"mac": KEY, "ip": "192.0.2.10"}
    frame = {"event": "data", "channel": "/sites/s1/stats/clients", "data": json.dumps(client)}
    assert parse_stream_message(json.dumps(frame)) == ("client", {**client, "site_id": "s1"})
    assert parse_stream_message(json.dumps({**frame, "data": client})) == ("client", {**client, "site_id": "s1"})
    assert parse_stream_message(json.dumps({**frame, "channel": "/sites/s1/stats/devices"})) == ("device", {**client, "site_id": "s1"})
    assert parse_stream_message(json.dumps({"event": "channel_subscribed", "channel": "/sites/s1/stats/clients"})) is None
    assert parse_stream_message(json.dumps({**frame, "channel": "/sites/s1/alarms"})) is None
    assert parse_stream_message("not json") is None


def test_client_label():
    from custom_components.hpe_mist.presence import client_label

    assert client_label({"mac": KEY, "hostname": "phone-a"}) == "phone-a"
    assert client_label({"mac": "020000a3c7c2", "manufacture": "ExampleVendor"}) == "ExampleVendor a3:c7:c2"
    assert client_label({"mac": "020000a3c7c2", "hostname": " "}) == "02:00:00:a3:c7:c2"
    assert client_label({"mac": "020000a3c7c2", "model": "Smart Plug", "manufacture": "ExampleVendor"}) == "Smart Plug a3:c7:c2"


def test_device_from_mist_ap_and_switch():
    from custom_components.hpe_mist.source import device_from_mist, device_update_from_mist

    ap = device_from_mist({
        "mac": "02:00:00:00:00:01", "site_id": "s1", "type": "ap", "name": "AP 1", "status": "connected", "uptime": 60,
        "num_clients": 2, "cpu_util": 7, "mem_used_kb": 250, "mem_total_kb": 1000, "env_stat": {"cpu_temp": 56},
        "version": "", "radio_stat": {"band_24": {"channel": 11, "util_all": 3, "num_clients": 2},
                                      "band_6": {"channel": 5, "disabled": True}},
    })
    assert ap["mac"] == "020000000001" and ap["online"] is True and ap["clients"] == 2
    assert ap["cpu"] == 7 and ap["memory"] == 25.0 and ap["temperature"] == 56 and ap["version"] is None
    assert ap["radios"]["band_24"] == {
        "band": "2.4 GHz", "status": "up", "channel": "11", "bandwidth": None, "power": None, "utilization": 3,
        "noise_floor": None, "clients": 2}
    assert ap["radios"]["band_6"]["status"] == "disabled"
    assert ap["uplink"] is None and ap["poe"] is None

    sw = device_from_mist({
        "mac": "020000000005", "type": "switch", "hostname": "sw", "status": "disconnected",
        "cpu_stat": {"idle": 90}, "memory_stat": {"usage": 25}, "clients_stats": {"total": {"num_wired_clients": 4}},
        "module_stat": [{"temperatures": [{"celsius": 40}, {"celsius": 47}]}],
        "if_stat": {"ge-0/0/1.0": {"port_id": "ge-0/0/1", "up": True}, "ge-0/0/2.0": {"port_id": "ge-0/0/2", "up": False},
                    "irb.0": {"port_id": "irb", "up": True}, "ge-0/0/3.5": {"port_id": "ge-0/0/3.5", "up": True}},
    })
    assert sw["online"] is False and sw["name"] == "sw" and sw["clients"] == 4
    assert sw["cpu"] == 10.0 and sw["memory"] == 25 and sw["temperature"] == 47
    assert {p: v["up"] for p, v in sw["ports"].items()} == {"ge-0/0/1": True, "ge-0/0/2": False}
    assert sw["ports"]["ge-0/0/1"] == {"up": True, "speed": None, "uplink": False, "poe_w": None, "neighbor": None,
                                       "neighbor_port": None, "vlan": None, "description": None}
    assert device_from_mist({"mac": "020000000006", "env_stat": {"cpu_temp": 0}})["temperature"] is None

    assert device_update_from_mist({"mac": "020000000001", "uptime": 5, "site_id": "s1"}) == {
        "mac": "020000000001", "site_id": "s1", "online": True, "uptime": 5}


# Trimmed from a real Mist org's answers (probe of 2026-10-22).
REAL_RADIO_STAT = {
    "band_6": {"num_clients": 0, "num_wlans": 2, "channel": 21, "bandwidth": 80, "power": 11, "noise_floor": -94,
               "usage": "6", "util_all": 0, "mac": "020000001a50"},
    "band_5": {"num_clients": 0, "num_wlans": 5, "channel": 108, "bandwidth": 40, "power": 11, "noise_floor": -97,
               "usage": "5", "util_all": 1, "mac": "020000001a60"},
    "band_24": {"num_clients": 2, "num_wlans": 2, "channel": 11, "bandwidth": 20, "power": 9, "noise_floor": -95,
                "usage": "24", "util_all": 3, "mac": "020000001a70"},
}
REAL_PORT_STAT = {"eth0": {"up": True, "speed": 5000, "full_duplex": True}, "eth1": {"up": False, "speed": 0}}
REAL_LLDP = {"system_name": "core-switch", "system_desc": "Example switch 1.0", "port_desc": "1/1/8",
             "port_id": "1/1/8", "ap_port_name": "eth0", "power_allocated": 21100, "power_draw": 21100}


def test_device_from_mist_real_ap_radios_and_uplink():
    from custom_components.hpe_mist.source import device_from_mist

    ap = device_from_mist({"mac": "020000001a40", "type": "ap", "status": "connected", "radio_stat": REAL_RADIO_STAT,
                           "port_stat": REAL_PORT_STAT, "lldp_stat": REAL_LLDP})
    assert list(ap["radios"]) == ["band_24", "band_5", "band_6"]
    assert ap["radios"]["band_5"] == {"band": "5 GHz", "status": "up", "channel": "108", "bandwidth": 40,
                                      "power": 11.0, "utilization": 1, "noise_floor": -97.0, "clients": 0}
    assert ap["uplink"] == {"device": "core-switch", "port": "1/1/8", "speed": 5000, "poe_w": 21.1}
    assert ap["ports"] == {}  # an AP's eth ports are not switch ports

    # No LLDP neighbour: the uplink still carries the link speed.
    bare = device_from_mist({"mac": "020000001a41", "type": "ap", "port_stat": REAL_PORT_STAT})
    assert bare["uplink"] == {"device": None, "port": None, "speed": 5000, "poe_w": None}


def test_device_from_mist_switch_poe_only_when_reported():
    from custom_components.hpe_mist.source import device_from_mist

    virtual = device_from_mist({"mac": "020000000005", "type": "switch", "module_stat": [{"poe": {}, "_idx": 0}]})
    assert virtual["poe"] is None and virtual["uplink"] is None
    poe = device_from_mist({"mac": "020000000005", "type": "switch", "module_stat": [
        {"poe": {"power_draw": 31.25, "max_power": 370}}, {"poe": {"power_draw": 10, "max_power": 370}}]})
    assert poe["poe"] == {"draw_w": 41.2, "budget_w": 740}


def test_port_search_grouped_physical_only_natural_order():
    from custom_components.hpe_mist.source import device_from_mist, merge_ports, port_sort_key, ports_from_mist

    def row(mac, port_id, **more):
        return {"mac": mac, "port_id": port_id, "up": True, "speed": 1000, "uplink": False, "port_usage": "",
                "device_type": "switch", **more}

    rows = [
        row("020000002113", "cbp0", speed=0), row("020000002113", "em1", speed=10000), row("020000002113", "fxp0"),
        row("020000002113", "irb"), row("020000002113", "ge-0/0/1.0"),
        row("020000002113", "ge-0/0/10", up=False, speed=0),
        row("020000002113", "ge-0/0/2", neighbor_system_name="dist-switch-2", neighbor_port_desc="ge-0/0/1",
            power_draw=4.5, poe_on=True, vlan=10, port_usage="ap"),
        row("020000002113", "xe-0/1/0", uplink=True, neighbor_mac="020000004cc0"),
        row("0200000031760", "ge-0/0/0"),  # not a MAC: dropped
        row("020000003176", "mge-0/0/3"),
    ]
    found = ports_from_mist(rows)
    assert list(found) == ["020000002113", "020000003176"]
    assert list(found["020000002113"]) == ["ge-0/0/2", "ge-0/0/10", "xe-0/1/0"]
    assert found["020000002113"]["ge-0/0/2"] == {
        "up": True, "speed": 1000, "uplink": False, "poe_w": 4.5, "neighbor": "dist-switch-2",
        "neighbor_port": "ge-0/0/1", "vlan": 10, "description": "ap"}
    assert found["020000002113"]["ge-0/0/10"]["speed"] is None
    assert found["020000002113"]["xe-0/1/0"]["neighbor"] == "02:00:00:00:4c:c0"
    assert found["020000002113"]["xe-0/1/0"]["uplink"] is True
    assert sorted(["1/1/10", "1/1/2", "1/1/1"], key=port_sort_key) == ["1/1/1", "1/1/2", "1/1/10"]

    # The sweep's own link state wins for ports it reports; the search fills in the rest.
    sw = device_from_mist({"mac": "020000002113", "type": "switch",
                           "if_stat": {"ge-0/0/2.0": {"port_id": "ge-0/0/2", "up": False},
                                       "ge-0/0/3.0": {"port_id": "ge-0/0/3", "up": True}}})
    merge_ports(sw, found["020000002113"])
    assert list(sw["ports"]) == ["ge-0/0/2", "ge-0/0/3", "ge-0/0/10", "xe-0/1/0"]
    assert sw["ports"]["ge-0/0/2"]["up"] is False and sw["ports"]["ge-0/0/2"]["neighbor"] == "dist-switch-2"
    assert sw["ports"]["ge-0/0/3"]["up"] is True


def test_client_normalisation_band_and_seen_rows():
    from custom_components.hpe_mist.source import client_from_mist, seen_client_from_mist

    now = client_from_mist({"mac": "020000000076", "band": "5", "snr": 33.0, "vlan_id": 200, "last_seen": 1792675875.0})
    assert now["snr"] == 33 and isinstance(now["snr"], int)
    assert now["band"] == "5 GHz" and now["vlan"] == 200 and now["wired"] is False and now["last_seen"] == 1792675875.0

    wifi = seen_client_from_mist({"mac": "020000000057", "site_id": "s1", "last_ip": "192.0.2.57", "mfg": "Example Vendor",
                                  "last_ssid": "home-wifi", "last_ap": "020000000a40", "band": "24", "last_vlan": 200,
                                  "last_model": "Unknown", "model": ["Unknown", "Smart Plug"],
                                  "timestamp": 1792675875.079}, wired=False)
    assert wifi == {"mac": "020000000057", "site_id": "s1", "ip": "192.0.2.57", "hostname": None,
                    "manufacture": "Example Vendor", "model": "Smart Plug", "ssid": "home-wifi", "ap_mac": "020000000a40",
                    "rssi": None, "snr": None, "band": "2.4 GHz", "wired": False, "port": None, "switch_mac": None, "vlan": 200,
                    "last_seen": 1792675875.079}
    wired = seen_client_from_mist({"mac": "020000004cc0", "last_hostname": "dist-switch-2", "manufacture": "Juniper Networks",
                                   "last_vlan": -1, "last_port_id": "ge-0/0/2", "last_device_mac": "0200000001e9",
                                   "timestamp": 1792676002.625}, wired=True)
    assert wired["hostname"] == "dist-switch-2" and wired["manufacture"] == "Juniper Networks"
    assert wired["wired"] is True and wired["port"] == "ge-0/0/2" and wired["switch_mac"] == "0200000001e9"
    assert wired["vlan"] is None and wired["ssid"] is None
