"""The dashboard cards (frontend/hpe-cards.js), run under Node with a tiny stand-in for the browser DOM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CARDS = next((Path(__file__).parent.parent / "custom_components").glob("*/frontend/hpe-cards.js"))
NODE = shutil.which("node")

# Just enough DOM for the cards: elements with class, text, attributes and children.
DOM = r"""
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.className = ""; this._text = ""; this.style = { setProperty() {} }; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(" "); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = c; }
  all(cls) { return [...(this.className.split(" ").includes(cls) ? [this] : []), ...this.children.flatMap(c => c.all ? c.all(cls) : [])]; }
}
globalThis.document = { createElement: tag => new El(tag) };
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = new El("root"); return this.shadowRoot; } dispatchEvent() {} };
globalThis.customElements = { _d: {}, get(n) { return this._d[n]; }, define(n, c) { this._d[n] = c; } };
globalThis.window = globalThis;
"""


def run(script: str):
    if NODE is None:
        pytest.skip("node is not installed")
    code = DOM + CARDS.read_text() + "\n" + script
    out = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# A switch card for one device whose entities and states are given; prints what the tests look at.
SWITCH = r"""
function card(ports, clients = [], online = true, config = {}) {
  const ents = { "sensor.sw_ports_up": "ports_up", "sensor.sw_clients": "device_clients" };
  if (online) ents["binary_sensor.sw_online"] = "online";
  const hass = {
    entities: Object.fromEntries(Object.entries(ents).map(([e, k]) => [e, { entity_id: e, device_id: "d1", platform: "hpe_central", translation_key: k }])),
    devices: { d1: { id: "d1", name: "Stack", model: "6300" } },
    states: {
      "sensor.sw_ports_up": { state: String(ports.filter(p => p.up).length), attributes: { ports } },
      "sensor.sw_clients": { state: String(clients.length), attributes: { clients } },
      "binary_sensor.sw_online": { state: "on", attributes: {} },
    },
  };
  const c = new (customElements.get("hpe-switch-card"))();
  c.setConfig({ device: "d1", ...config });
  c.hass = hass;
  const root = c.shadowRoot;
  return {
    text: root.textContent,
    members: root.all("member").map(m => m.all("member-head")[0].textContent.trim()),
    plates: root.all("plate").map(p => p.children.length),
    flat: root.all("ports").length,
  };
}
"""

STACK = [{"port": f"{m}/1/{p}", "up": p == 1, "speed": 1000} for m in range(1, 7) for p in range(1, 49)]


def test_a_six_member_stack_gets_one_front_panel_per_member():
    out = run(SWITCH + f"console.log(JSON.stringify(card({json.dumps(STACK)})));")
    assert [m.split()[0:2] for m in out["members"]] == [["Member", str(m)] for m in range(1, 7)]
    assert all("1/48 up" in m for m in out["members"])
    assert out["plates"] == [48] * 6 and out["flat"] == 0
    assert "Ports · stack of 6" in out["text"]
    assert "Down:" not in out["text"]  # 282 down ports are on the faceplates, not in a list


def test_a_single_switch_keeps_one_port_grid():
    ports = [{"port": f"1/1/{p}", "up": True, "speed": 1000} for p in range(1, 25)]
    out = run(SWITCH + f"console.log(JSON.stringify(card({json.dumps(ports)})));")
    assert out["members"] == [] and out["flat"] == 1
    assert "stack of" not in out["text"]


def test_switch_card_lists_wired_clients_with_their_stack_port():
    clients = [{"name": "apc.pdu.r1.L", "mac": "02:00:00:00:00:11", "ip": "10.0.9.11", "port": "6/1/11", "vlan": 10}]
    out = run(SWITCH + f"console.log(JSON.stringify(card({json.dumps(STACK)}, {json.dumps(clients)})));")
    assert "Wired clients (1)" in out["text"]
    assert "apc.pdu.r1.L" in out["text"] and "6/1/11" in out["text"] and "VLAN 10" in out["text"]


def test_switch_card_sections_can_be_hidden():
    clients = [{"name": "pdu", "mac": "02:00:00:00:00:11", "port": "6/1/11"}]
    out = run(SWITCH + f"console.log(JSON.stringify(card({json.dumps(STACK)}, {json.dumps(clients)}, true, "
                       "{show_ports: false, show_clients: false, name: 'Core stack'})));")
    assert out["members"] == [] and "Wired clients" not in out["text"]
    assert "Core stack" in out["text"]


def test_a_client_picked_in_the_switch_card_says_so():
    out = run(SWITCH + "console.log(JSON.stringify(card([], [], false)));")
    assert "not an access point, switch or gateway" in out["text"]


def test_device_picker_only_offers_network_devices():
    out = run("console.log(JSON.stringify(customElements.get('hpe-switch-card').getConfigForm().schema[0].selector.device));")
    # Clients and sites have no connectivity (Online) sensor, so they are not offered.
    assert {"integration": "hpe_central", "domain": "binary_sensor", "device_class": "connectivity"} in out["entity"]


def test_client_list_follows_the_options_unless_the_card_sets_its_own():
    out = run(r"""
const C = customElements.get("hpe-client-list-card");
const settings = { columns: ["ip", "snr"], layout: "table" };
const plain = new C(); plain.setConfig({}); plain._useSettings(settings);
const own = new C(); own.setConfig({ columns: ["vendor"], layout: "list" }); own._useSettings(settings);
console.log(JSON.stringify({ plain: [plain._config.columns, plain._config.layout], own: [own._config.columns, own._config.layout],
  stub: C.getStubConfig() }));
""")
    assert out["plain"] == [["name", "ip", "snr"], "table"]
    assert out["own"] == [["name", "vendor"], "list"]
    assert "columns" not in out["stub"] and "layout" not in out["stub"]  # new cards follow the gear


def test_client_list_has_80211_capabilities_phy_and_bssid_columns():
    out = run(r"""
const CC = eval("CLIENT_COLUMNS");
const r = { capabilities: "802.11ac, 802.11k", phy: "802.11a", bssid: "8c:79:09:76:5b:22" };
console.log(JSON.stringify({ cap: [CC.capabilities[0], CC.capabilities[1](r)], phy: [CC.phy[0], CC.phy[1](r)], bssid: CC.bssid[1](r) }));
""")
    assert out["cap"] == ["802.11 capabilities", "802.11ac, 802.11k"]
    assert out["phy"] == ["PHY type", "802.11a"] and out["bssid"] == "8c:79:09:76:5b:22"


def test_client_details_are_grouped_with_a_wireless_section():
    out = run(r"""
const c = new (customElements.get("hpe-client-list-card"))();
c.setConfig({});
c._hass = { callService() {} };
const panel = c._details({ name: "Phone", mac: "02:00:00:00:00:76", ssid: "outside", band: "5 GHz", channel: "56E (80 MHz)",
  snr: 36, security: "CCM-128", key_mgmt: "OWE", phy: "802.11a", capabilities: "802.11ac, 802.11k, 802.11v",
  bssid: "8c:79:09:76:5b:22", vendor: "Apple" });
const sects = panel.all("sect").map(s => s.children[0].textContent.trim());
const wireless = panel.all("sect").find(s => s.children[0].textContent.trim() === "Wireless");
console.log(JSON.stringify({ sects, wireless: wireless.textContent, good: panel.all("good").map(e => e.textContent),
  wide: panel.all("wide").length }));
""")
    assert out["sects"] == ["Client", "Wireless", "Device"]  # empty groups left out
    for text in ("outside", "5 GHz", "56E (80 MHz)", "36 dB", "CCM-128", "OWE", "802.11a",
                 "802.11ac, 802.11k, 802.11v", "8c:79:09:76:5b:22"):
        assert text in out["wireless"], text
    assert out["good"] == ["36 dB"]  # a strong SNR is shown in green
    assert out["wide"] == 2  # capabilities and BSSID take a full row


def test_client_list_shows_no_signal_for_wired_clients_and_no_unclassified_type():
    out = run(r"""
const c = new (customElements.get("hpe-client-list-card"))();
c.setConfig({});
const cols = c.constructor.getConfigForm().schema.find(s => s.name === "columns").selector.select.options.map(o => o.value);
// The column functions, through the card's own table: one wired and one Wi-Fi row.
const CC = eval("CLIENT_COLUMNS");
const wired = { wired: true, snr: 30, rssi: -60, category: "Unclassified" };
const wifi = { wired: false, rssi: -90, rssi_estimated: true, category: "Computing Systems" };
console.log(JSON.stringify({
  wiredSignal: CC.signal[1](wired), wifiSignal: CC.signal[1](wifi),
  wiredType: CC.category[1](wired), wifiType: CC.category[1](wifi),
  layouts: c.constructor.getConfigForm().schema.find(s => s.name === "layout").selector.select.options.map(o => o.value),
  layout: c._config.layout, cols,
}));
""")
    assert out["wiredSignal"] is None and out["wifiSignal"] == "≈ -90 dBm"
    assert out["wiredType"] is None and out["wifiType"] == "Computing Systems"
    assert out["layouts"] == ["auto", "table", "list"] and out["layout"] == "auto"
