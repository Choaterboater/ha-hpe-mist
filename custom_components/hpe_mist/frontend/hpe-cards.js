// HPE Mist / HPE Central cards: client list, access point, switch. Shipped by both integrations; whichever
// loads first defines them. Source data is always text, never HTML.
//
// Config is just {device: <device id>}; the card finds that device's entities by translation key, so it works
// for both integrations and needs no entity ids.
const HPE_PLATFORMS = ["hpe_mist", "hpe_central"];

function hpeEntities(hass, deviceId) {
  const out = {};
  for (const e of Object.values(hass.entities || {})) {
    if (e.device_id !== deviceId || !HPE_PLATFORMS.includes(e.platform) || e.hidden) continue;
    const key = e.translation_key || e.entity_id.split(".")[1].split("_").pop();
    if (!(key in out)) out[key] = e.entity_id;
  }
  return out;
}

function hpeDevices(hass, key) {
  const ids = new Set(Object.values(hass.entities || {})
    .filter(e => HPE_PLATFORMS.includes(e.platform) && e.translation_key === key).map(e => e.device_id));
  return [...ids].map(id => hass.devices?.[id]).filter(Boolean).sort((a, b) => hpeName(a).localeCompare(hpeName(b)));
}

function hpeName(device) { return device?.name_by_user || device?.name || "Unknown device"; }

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") el.textContent = String(v);
    else if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of [].concat(children)) if (c) el.append(c);
  return el;
}

const num = v => (typeof v === "number" && Number.isFinite(v) ? v : null);
// A state string ("12.5") as a number; missing / "unknown" / "" stay null (Number(null) would be 0).
const numState = v => (v === null || v === undefined || v === "" ? null : num(Number(v)));
const speed = s => (!num(s) ? "—" : s >= 1000 ? `${+(s / 1000).toFixed(1)}G` : `${s}M`);
const portLabel = p => String(p).split(/[\/:]/).pop();
// Stack member of a port: CX "3/1/14" is member 3, Juniper "ge-2/0/14" is FPC (member) 2; null for anything else.
const portMember = p => { const m = /^(?:[a-z]+-)?(\d+)\/\d+\/\d+$/i.exec(String(p)); return m ? Number(m[1]) : null; };
// [[member, ports]] in member order, ports in the order given; ports with no member last.
function portGroups(ports) {
  const groups = new Map();
  for (const p of ports) {
    const m = portMember(p.port);
    if (!groups.has(m)) groups.set(m, []);
    groups.get(m).push(p);
  }
  return [...groups].sort(([a], [b]) => (a ?? Infinity) - (b ?? Infinity));
}

const HPE_STYLE = `
  :host{display:block}ha-card{padding:16px;overflow:hidden}
  .head{display:flex;align-items:center;gap:12px;cursor:pointer}
  .dot{width:12px;height:12px;border-radius:50%;flex:none;background:var(--disabled-color,#888)}
  .dot.on{background:var(--success-color,#4caf50)}.dot.off{background:var(--error-color,#db4437)}
  .title{flex:1;min-width:0}.name{font-size:18px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sub{font-size:12px;color:var(--secondary-text-color)}
  .big{text-align:right}.big b{display:block;font-size:22px;line-height:1}.big span{font-size:11px;color:var(--secondary-text-color)}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 4px}
  .chip{font-size:12px;padding:3px 9px;border-radius:999px;background:var(--secondary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color)}
  .chip.warn{border-color:var(--warning-color,#ffa600)}
  h3{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--secondary-text-color);margin:14px 0 6px}
  .row{display:grid;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--divider-color);font-size:13px}
  .row:last-child{border-bottom:none}.muted{color:var(--secondary-text-color);font-size:12px}
  .radio{grid-template-columns:58px minmax(0,1fr) 64px 30px}
  .bar{height:6px;border-radius:3px;background:var(--divider-color);overflow:hidden}
  .bar>i{display:block;height:100%;background:var(--success-color,#4caf50)}
  .bar.mid>i{background:var(--warning-color,#ffa600)}.bar.hot>i{background:var(--error-color,#db4437)}
  .client{grid-template-columns:minmax(0,1fr) auto}.cname{font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .member{margin-top:6px}.member-head{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--secondary-text-color);margin-bottom:4px}
  .member-head b{color:var(--primary-text-color)}
  .faceplate{overflow-x:auto;padding:6px;border-radius:6px;border:1px solid var(--divider-color)}
  .plate{display:grid;grid-template-rows:repeat(2,34px);grid-auto-flow:column;grid-auto-columns:minmax(28px,1fr);gap:4px}
  .port{height:34px;border-radius:5px;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:11px;cursor:pointer;
        background:var(--secondary-background-color);color:var(--secondary-text-color);border:1px solid var(--divider-color);position:relative}
  .port.up{background:rgba(76,175,80,.18);color:var(--primary-text-color);border-color:var(--success-color,#4caf50)}
  .port.uplink{border-width:2px;border-color:var(--info-color,#039be5)}.port.sel{outline:2px solid var(--primary-color);outline-offset:1px}
  .port small{font-size:9px;line-height:1}.legend{display:flex;gap:12px;font-size:11px;color:var(--secondary-text-color);margin-top:6px}
  .detail{margin-top:8px;font-size:13px;padding:8px;border-radius:6px;background:var(--secondary-background-color)}
  .conn{grid-template-columns:52px 44px 52px minmax(0,1fr) 40px}
  .empty{font-size:13px;color:var(--secondary-text-color);padding:6px 0}
`;

class HpeDeviceCard extends HTMLElement {
  static key = "";
  static sections = {};  // {name: label}: parts of the card that can be hidden, each a show_<name> option
  static getConfigForm() {
    const sections = Object.entries(this.sections);
    return {
      schema: [
        // Only devices with an Online sensor (APs, switches, gateways): clients and sites have none.
        { name: "device", required: true, selector: { device: { filter: HPE_PLATFORMS.map(integration => ({ integration })),
          entity: HPE_PLATFORMS.map(integration => ({ integration, domain: "binary_sensor", device_class: "connectivity" })) } } },
        { name: "name", selector: { text: {} } },
        { name: "max_rows", selector: { number: { min: 0, max: 50, mode: "box" } } },
        ...sections.map(([name]) => ({ name: `show_${name}`, selector: { boolean: {} } })),
      ],
      computeLabel: s => ({ device: "Device (an AP, switch or gateway)", name: "Name (empty: the device's name)",
        max_rows: "Rows to list (0 hides the list)",
        ...Object.fromEntries(sections.map(([name, label]) => [`show_${name}`, `Show ${label}`])) }[s.name]),
    };
  }
  static getStubConfig(hass) { return { device: hpeDevices(hass, this.key)[0]?.id || "" }; }

  setConfig(config) {
    if (!config.device) throw new Error("Pick a device");
    const show = Object.fromEntries(Object.keys(this.constructor.sections).map(name => [`show_${name}`, true]));
    this._config = { max_rows: 10, ...show, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._sig = null;
    if (this._hass) this.hass = this._hass;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    this._ents = hpeEntities(hass, this._config.device);
    const sig = Object.values(this._ents).map(e => hass.states[e]?.last_updated).join("|") + "|" + this._selected;
    if (sig === this._sig) return;
    this._sig = sig;
    this._render();
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  st(key) { return this._hass.states[this._ents[key]]; }
  attr(key, a) { return this.st(key)?.attributes?.[a]; }
  val(key) { const s = this.st(key); return s && !["unknown", "unavailable"].includes(s.state) ? s.state : null; }

  moreInfo(key) {
    const entityId = this._ents[key];
    if (entityId) this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  header(sub, bigValue, bigLabel) {
    const device = this._hass.devices?.[this._config.device];
    const site = this._hass.devices?.[device?.via_device_id];
    const online = this.val("online");
    const platform = { hpe_mist: "Mist", hpe_central: "Central" }[
      Object.values(this._hass.entities).find(e => e.device_id === this._config.device)?.platform];
    return h("div", { class: "head", onclick: () => this.moreInfo("online") }, [
      h("div", { class: `dot ${online === "on" ? "on" : online === "off" ? "off" : ""}`, title: online === "on" ? "Online" : "Offline" }),
      h("div", { class: "title" }, [
        h("div", { class: "name", text: this._config.name || hpeName(device) }),
        h("div", { class: "sub", text: [device?.model || sub, site ? hpeName(site) : null, platform].filter(Boolean).join(" · ") }),
      ]),
      bigValue === null ? null : h("div", { class: "big" }, [h("b", { text: bigValue }), h("span", { text: bigLabel })]),
    ]);
  }

  chips(list) {
    return h("div", { class: "chips" }, list.filter(Boolean).map(([text, warn, key]) =>
      h("span", { class: `chip${warn ? " warn" : ""}`, text, onclick: key ? () => this.moreInfo(key) : null })));
  }

  health() {
    const cpu = numState(this.val("cpu")), mem = numState(this.val("memory"));
    const temp = numState(this.val("temperature") ?? this._tempFallback());
    return [
      cpu !== null && [`CPU ${Math.round(cpu)}%`, cpu >= 85, "cpu"],
      mem !== null && [`Memory ${Math.round(mem)}%`, mem >= 90, "memory"],
      temp !== null && [`${Math.round(temp)} °C`, temp >= 70],
      this.val("firmware") && [`FW ${this.val("firmware")}`, false, "firmware"],
    ];
  }

  _tempFallback() {
    const e = Object.values(this._ents).find(id => id.endsWith("_temperature"));
    const s = e && this._hass.states[e];
    return s && !["unknown", "unavailable"].includes(s.state) ? s.state : null;
  }

  _render() {
    // A client or a site has no Online sensor: say so instead of showing an empty card.
    const card = h("ha-card", {}, this._ents.online ? this.body()
      : h("div", { class: "empty", text: "This device is not an access point, switch or gateway. Pick one in the card editor." }));
    this.shadowRoot.replaceChildren(h("style", { text: HPE_STYLE }), card);
  }
}

class HpeAccessPointCard extends HpeDeviceCard {
  static key = "radios";
  static sections = { radios: "radios", clients: "clients" };

  body() {
    const clients = this.attr("device_clients", "clients") || [];
    const count = this.val("device_clients");
    const radios = this.attr("radios", "radios") || [];
    const up = this.st("uplink")?.attributes || {};
    const uplink = up.device || up.speed ? [
      `Uplink ${[up.device, up.port].filter(Boolean).join(" ") || "wired"}${up.speed ? ` · ${+(up.speed / 1000).toFixed(1)} Gb/s` : ""}${up.poe_w ? ` · ${up.poe_w} W` : ""}`, false, "uplink"] : null;
    const bySsid = this.attr("device_clients", "by_ssid") || {};
    const parts = [this.header("Access point", count ?? "—", "clients"), this.chips([uplink, ...this.health(),
      ...Object.entries(bySsid).map(([ssid, n]) => [`${ssid} ${n}`])])];

    if (this._config.show_radios) parts.push(h("h3", { text: "Radios" }));
    if (this._config.show_radios && !radios.length) parts.push(h("div", { class: "empty", text: "No radio data yet." }));
    for (const r of this._config.show_radios ? radios : []) {
      const u = num(r.utilization);
      const on = r.status === "up";
      parts.push(h("div", { class: "row radio", title: `Noise ${num(r.noise_floor) ?? "—"} dBm` }, [
        h("b", { text: r.band }),
        h("div", {}, [
          h("div", { class: "muted", text: on ? [r.channel && `Ch ${r.channel}`, r.bandwidth && `${r.bandwidth} MHz`,
            num(r.power) !== null && `${r.power} dBm`].filter(Boolean).join(" · ") || "Up" : r.status === "disabled" ? "Disabled" : "Down" }),
          on && u !== null ? h("div", { class: `bar ${u >= 80 ? "hot" : u >= 50 ? "mid" : ""}`, title: `${u}% busy` }, h("i", { style: `width:${Math.min(100, u)}%` })) : null,
        ]),
        h("span", { class: "muted", text: on && u !== null ? `${Math.round(u)}% busy` : "" }),
        h("b", { text: num(r.clients) ?? "", title: "clients" }),
      ]));
    }

    if (this._config.show_clients && this._config.max_rows > 0) {
      parts.push(h("h3", { text: `Clients${clients.length ? ` (${clients.length})` : ""}` }));
      if (!clients.length) parts.push(h("div", { class: "empty", text: count === null ? "Client list unavailable." : "No clients connected." }));
      for (const c of clients.slice(0, this._config.max_rows)) {
        const sig = num(c.rssi) !== null ? `${c.rssi} dBm` : num(c.snr) !== null ? `SNR ${c.snr}` : "";
        const weak = (num(c.rssi) !== null && c.rssi < -75) || (num(c.rssi) === null && num(c.snr) !== null && c.snr < 20);
        parts.push(h("div", { class: "row client", title: [c.mac, c.ip, c.vendor].filter(Boolean).join(" · ") }, [
          h("div", {}, [h("div", { class: "cname", text: c.name || c.mac }),
            h("div", { class: "muted", text: [c.ssid, c.band, c.ip].filter(Boolean).join(" · ") })]),
          h("span", { class: weak ? "chip warn" : "muted", text: (weak ? "⚠ " : "") + sig }),
        ]));
      }
      if (clients.length > this._config.max_rows) parts.push(h("div", { class: "empty", text: `+${clients.length - this._config.max_rows} more` }));
    }
    return parts;
  }
}

class HpeSwitchCard extends HpeDeviceCard {
  static key = "ports_up";
  static sections = { ports: "ports", connected: "connected ports", clients: "wired clients", vlans: "VLANs" };

  // One port tile; clicking it selects the port and shows its detail line.
  portTile(p) {
    return h("div", {
      class: `port${p.up ? " up" : ""}${p.uplink ? " uplink" : ""}${this._selected === p.port ? " sel" : ""}`,
      title: `${p.port}: ${p.up ? "up" : "down"}${p.speed ? " · " + speed(p.speed) : ""}${p.neighbor ? " · " + p.neighbor : ""}`,
      onclick: () => { this._selected = this._selected === p.port ? null : p.port; this._sig = null; this.hass = this._hass; },
    }, [h("span", { text: portLabel(p.port) }), p.poe_w ? h("small", { text: "⚡" }) : p.up ? h("small", { text: speed(p.speed) }) : null]);
  }

  // A stack (ports on more than one member) gets one front panel per member, two rows like the switch itself.
  portPanels(ports) {
    const groups = portGroups(ports);
    if (groups.length === 1) return [h("div", { class: "ports" }, ports.map(p => this.portTile(p)))];
    return groups.map(([member, rows]) => {
      const up = rows.filter(p => p.up).length;
      const poe = rows.reduce((sum, p) => sum + (num(p.poe_w) || 0), 0);
      return h("div", { class: "member" }, [
        h("div", { class: "member-head" }, [
          h("b", { text: member === null ? "Other ports" : `Member ${member}` }),
          h("span", { text: [`${up}/${rows.length} up`, poe ? `PoE ${+poe.toFixed(1)} W` : null].filter(Boolean).join(" · ") }),
        ]),
        h("div", { class: "faceplate" }, h("div", { class: "plate" }, rows.map(p => this.portTile(p)))),
      ]);
    });
  }

  body() {
    const ports = this.attr("ports_up", "ports") || [];
    const upCount = ports.filter(p => p.up).length;
    const poe = numState(this.val("poe_draw"));
    const budget = this.attr("poe_draw", "budget_w");
    const vlans = this.attr("vlans", "vlans") || [];
    const parts = [
      this.header("Switch", ports.length ? `${upCount}/${ports.length}` : (this.val("ports_up") ?? "—"), "ports up"),
      this.chips([poe !== null && [`PoE ${poe} W${budget ? ` of ${budget} W` : ""}`, budget && poe / budget > 0.85, "poe_draw"],
        this.val("device_clients") && [`${this.val("device_clients")} clients`, false, "device_clients"], ...this.health()]),
    ];

    const members = portGroups(ports).filter(([m]) => m !== null).length;
    if (this._config.show_ports) parts.push(h("h3", { text: members > 1 ? `Ports · stack of ${members}` : "Ports" }));
    if (!this._config.show_ports) { /* hidden in the card editor */ }
    else if (!ports.length) parts.push(h("div", { class: "empty", text: "No port data yet." }));
    else {
      parts.push(...this.portPanels(ports));
      parts.push(h("div", { class: "legend" }, [h("span", { text: "green = up" }), h("span", { text: "blue edge = uplink" }), h("span", { text: "⚡ = PoE" })]));
      const sel = ports.find(p => p.port === this._selected);
      if (sel) parts.push(h("div", { class: "detail", text: [sel.port, sel.up ? "up" : "down", sel.speed && speed(sel.speed),
        sel.poe_w && `PoE ${sel.poe_w} W`, sel.neighbor && `→ ${sel.neighbor}${sel.neighbor_port ? " " + sel.neighbor_port : ""}`,
        sel.vlan && `VLAN ${sel.vlan}`, sel.description].filter(Boolean).join(" · ") }));
    }

    const connected = ports.filter(p => p.up && (p.neighbor || p.poe_w || p.uplink));
    if (this._config.show_connected && this._config.max_rows > 0 && connected.length) {
      parts.push(h("h3", { text: "Connected" }));
      for (const p of connected.slice(0, this._config.max_rows)) {
        parts.push(h("div", { class: "row conn" }, [
          h("b", { text: (p.uplink ? "↑ " : "") + (members > 1 ? p.port : portLabel(p.port)), title: p.port }), h("span", { text: speed(p.speed) }),
          h("span", { class: "muted", text: p.poe_w ? `${p.poe_w} W` : "" }),
          h("span", { class: "cname", text: [p.neighbor, p.neighbor_port && `(${p.neighbor_port})`].filter(Boolean).join(" ") || p.description || "—" }),
          h("span", { class: "muted", text: p.vlan ? `v${p.vlan}` : "" }),
        ]));
      }
      if (connected.length > this._config.max_rows) parts.push(h("div", { class: "empty", text: `+${connected.length - this._config.max_rows} more` }));
    }
    const wired = (this.attr("device_clients", "clients") || []).filter(c => c && typeof c === "object");
    if (this._config.show_clients && this._config.max_rows > 0 && wired.length) {
      parts.push(h("h3", { text: `Wired clients (${wired.length})` }));
      for (const c of wired.slice(0, this._config.max_rows)) {
        parts.push(h("div", { class: "row client", title: [c.mac, c.ip, c.vendor].filter(Boolean).join(" · ") }, [
          h("div", {}, [h("div", { class: "cname", text: c.name || c.mac }),
            h("div", { class: "muted", text: [c.ip, c.vlan && `VLAN ${c.vlan}`, c.vendor].filter(Boolean).join(" · ") })]),
          h("b", { text: c.port || "", title: "port" }),
        ]));
      }
      if (wired.length > this._config.max_rows) parts.push(h("div", { class: "empty", text: `+${wired.length - this._config.max_rows} more` }));
    }
    const down = ports.filter(p => !p.up);
    // A stack has hundreds of ports: the faceplates already show which are down.
    if (this._config.show_ports && down.length && ports.length && members <= 1) parts.push(h("div", { class: "empty", text: `Down: ${down.map(p => p.port).join(", ")}` }));
    if (this._config.show_vlans && vlans.length) parts.push(h("h3", { text: "VLANs" }), this.chips(vlans.map(v => [`${v.id}${v.name ? " " + v.name : ""}`])));
    return parts;
  }
}


// --- client list ----------------------------------------------------------------------------------
// Every Mist and Central client, from the integrations' <domain>/clients websocket command (rows are
// too big for state attributes). Sortable, searchable columns; a row opens every field the network
// gives for it and a rename box (the <domain>.rename_client action).

const CLIENT_DOMAINS = ["hpe_mist", "hpe_central"];
const REFETCH_MS = 30000;

const ago = s => {
  if (!num(s)) return null;
  const d = Math.max(0, Date.now() / 1000 - s);
  return d < 90 ? "just now" : d < 5400 ? `${Math.round(d / 60)} min ago` : d < 129600 ? `${Math.round(d / 3600)} h ago` : `${Math.round(d / 86400)} d ago`;
};
const since = s => {
  if (!num(s)) return null;
  const d = Math.max(0, Date.now() / 1000 - s);
  return d < 5400 ? `${Math.max(1, Math.round(d / 60))} min` : d < 129600 ? `${Math.round(d / 3600)} h` : `${Math.round(d / 86400)} d`;
};
const ipKey = ip => (/^\d+\.\d+\.\d+\.\d+$/.test(ip || "") ? ip.split(".").reduce((a, o) => a * 256 + Number(o), 0) : null);
const signalText = r => (num(r.rssi) !== null ? `${r.rssi_estimated ? "≈ " : ""}${r.rssi} dBm` : num(r.snr) !== null ? `SNR ${r.snr} dB` : null);
const via = r => (r.wired ? [r.switch, r.port].filter(Boolean).join(" ") : r.ap || r.connected_to) || null;
// The client's type; Central's "Unclassified" says nothing, so it counts as no type.
const NO_TYPE = /^(unclassified|unknown)$/i;
const clientType = r => { const t = r.category || r.family || r.model; return t && !NO_TYPE.test(String(t)) ? t : null; };

// key: [header, text(row), sort value(row)]. Sort values that are null always sort last.
const CLIENT_COLUMNS = {
  name: ["Client", r => r.name, r => String(r.name || "").toLocaleLowerCase()],
  ip: ["IP address", r => r.ip, r => ipKey(r.ip) ?? r.ip],
  network: ["Network", r => (r.wired ? ["Wired", r.vlan && `VLAN ${r.vlan}`] : [r.ssid, r.band]).filter(Boolean).join(" · ") || null, r => (r.wired ? "~wired" : r.ssid)],
  connected_to: ["Connected to", via, via],
  // A wired client has no signal.
  signal: ["Signal", r => (r.wired ? null : signalText(r)), r => (r.wired ? null : r.rssi ?? (num(r.snr) !== null ? r.snr - 95 : null))],
  snr: ["SNR", r => (num(r.snr) !== null ? `${r.snr} dB` : null), r => r.snr],
  category: ["Type", clientType, clientType],
  vendor: ["Vendor", r => r.vendor, r => r.vendor],
  os: ["OS", r => r.os, r => r.os],
  site: ["Site", r => r.site, r => r.site],
  vlan: ["VLAN", r => [r.vlan, r.vlan_name].filter(v => v !== null && v !== undefined && v !== "").join(" ") || null, r => r.vlan],
  role: ["Role", r => r.role, r => r.role],
  band: ["Band", r => r.band, r => r.band],
  channel: ["Channel", r => r.channel, r => parseInt(r.channel, 10) || null],
  security: ["Security", r => [r.security, r.key_mgmt].filter(Boolean).join(" · ") || null, r => r.security || r.key_mgmt],
  auth: ["Auth", r => r.auth, r => r.auth],
  user_name: ["User name", r => r.user_name, r => r.user_name],
  hostname: ["Hostname", r => r.hostname, r => r.hostname],
  mac: ["MAC", r => r.mac, r => r.mac],
  connected_since: ["Connected for", r => since(r.connected_since), r => (num(r.connected_since) ? -r.connected_since : null)],
  last_seen: ["Last seen", r => (r.connected ? "now" : ago(r.last_seen)), r => (num(r.last_seen) ? -r.last_seen : null)],
  health: ["Health", r => r.health || r.status, r => r.health || r.status],
  source: ["Source", r => [r.source, r.tracked && "tracked"].filter(Boolean).join(" · "), r => r.source],
};
const DEFAULT_COLUMNS = ["name", "ip", "network", "connected_to", "signal", "category"];

// Every field a row can carry, in the order the details panel lists them.
const CLIENT_FIELDS = [
  ["name", "Name"], ["found_name", "Found name"], ["user_name", "User name"], ["hostname", "Hostname"], ["mac", "MAC"],
  ["ip", "IPv4"], ["ipv6", "IPv6"], ["site", "Site"], ["ssid", "Network"], ["band", "Band"], ["channel", "Channel"],
  ["bssid", "BSSID"], ["ap", "Access point"], ["switch", "Switch"], ["port", "Port"], ["connected_to", "Connected to"],
  ["vlan", "VLAN"], ["vlan_name", "VLAN name"], ["role", "Role"], ["rssi", "RSSI (dBm)"], ["snr", "SNR (dB)"],
  ["tx_rate", "Tx rate (Mb/s)"], ["rx_rate", "Rx rate (Mb/s)"], ["tx_bps", "Tx (b/s)"], ["rx_bps", "Rx (b/s)"],
  ["vendor", "Vendor"], ["category", "Category"], ["family", "Family"], ["model", "Model"], ["os", "OS"],
  ["function", "Function"], ["capabilities", "Capabilities"], ["phy", "PHY"], ["security", "Security"],
  ["key_mgmt", "Key management"], ["auth", "Authentication"], ["tunnel", "Tunnel"], ["psk_name", "PSK"],
  ["group", "Group"], ["is_guest", "Guest"], ["health", "Health"], ["status", "Status"],
  ["connected_device_type", "Connected device type"], ["connected_since", "Connected since"],
  ["idle_time", "Idle (s)"], ["last_seen", "Last seen"], ["source", "Source"], ["tracked", "Tracked"],
];
const TIME_FIELDS = new Set(["connected_since", "last_seen"]);

class HpeClientListCard extends HTMLElement {
  // No columns or layout: a new card follows the integration's options (the gear) until set in the card editor.
  static getStubConfig() { return { page_size: 25, connected_only: true }; }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "columns", selector: { select: { multiple: true, reorder: true, mode: "list",
          options: Object.entries(CLIENT_COLUMNS).map(([value, [label]]) => ({ value, label })) } } },
        { name: "page_size", selector: { number: { min: 5, max: 200, mode: "box" } } },
        { name: "connected_only", selector: { boolean: {} } },
        { name: "layout", selector: { select: { mode: "dropdown", options: [
          { value: "auto", label: "Columns, or a list when the card is narrow" },
          { value: "table", label: "Always columns (scrolls sideways when narrow)" },
          { value: "list", label: "Always a list" }] } } },
      ],
      computeLabel: s => ({ title: "Title", columns: "Columns (Client is always first)", page_size: "Rows per page",
        connected_only: "Only clients connected now (a switch on the card shows the rest)", layout: "Layout" }[s.name]),
    };
  }

  setConfig(config) {
    this._own = { columns: Array.isArray(config.columns), layout: Boolean(config.layout) };
    this._raw = config;
    this._applyColumns(config.columns || this._settings?.columns || DEFAULT_COLUMNS);
    this._config = { title: "Clients", page_size: 25, connected_only: true, ...config,
      layout: config.layout || this._settings?.layout || "auto", columns: this._columns,
      page_size: Math.min(200, Math.max(5, Number(config.page_size) || 25)) };
    this._sort = this._sort || { key: "name", dir: 1 };
    this._page = 0;
    this._open = this._open || null;
    this._connectedOnly = this._config.connected_only;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._built = false;
    if (this._hass) this._render();
  }

  _applyColumns(list) {
    this._columns = ["name", ...list.filter(c => c in CLIENT_COLUMNS && c !== "name")];
  }

  // Columns and layout from the integration's options, used where the card sets none of its own.
  _useSettings(settings) {
    if (!settings || typeof settings !== "object") return;
    this._settings = settings;
    if (!this._own.columns && Array.isArray(settings.columns)) { this._applyColumns(settings.columns); this._config.columns = this._columns; }
    if (!this._own.layout && settings.layout) this._config.layout = settings.layout;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._config) return;
    // Refetch when a site's client count moves, at most every few seconds; else every 30 s while shown.
    const sig = Object.values(hass.entities || {})
      .filter(e => CLIENT_DOMAINS.includes(e.platform) && e.translation_key === "clients")
      .map(e => hass.states[e.entity_id]?.last_updated).join("|");
    const stale = Date.now() - (this._fetchedAt || 0) > REFETCH_MS;
    if (first || stale || (sig !== this._sig && Date.now() - (this._fetchedAt || 0) > 5000)) {
      this._sig = sig;
      this._fetch();
    }
    if (first) this._render();
  }

  connectedCallback() { this._timer = setInterval(() => this._hass && this._fetch(), REFETCH_MS); }
  disconnectedCallback() { clearInterval(this._timer); }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full", rows: "auto", min_columns: 6 }; }

  async _fetch() {
    if (this._fetching || !this._hass?.connection) return;
    this._fetching = true;
    this._fetchedAt = Date.now();
    const domains = CLIENT_DOMAINS.filter(d => (this._hass.config?.components || []).includes(d));
    const results = await Promise.allSettled(domains.map(d => this._hass.connection.sendMessagePromise({ type: `${d}/clients` })));
    this._fetching = false;
    this._errors = [];
    const rows = [];
    results.forEach((r, i) => {
      const name = domains[i] === "hpe_mist" ? "HPE Mist" : "HPE Central";
      if (r.status === "fulfilled") {
        rows.push(...(r.value?.clients || []).filter(c => c && typeof c === "object"));
        if (domains[i] === "hpe_central" || !this._settings) this._useSettings(r.value?.settings);
      }
      else this._errors.push(`${name}: ${r.reason?.code === "unknown_command" ? "update the integration to 0.2.0 or later for this card" : r.reason?.message || "no answer"}`);
    });
    this._clients = rows;
    this._loaded = true;
    this._render();
  }

  _build() {
    const root = this.shadowRoot;
    root.replaceChildren(h("style", { text: HPE_STYLE + CLIENT_STYLE }), h("ha-card", {}, h("div", { class: "body" }, [
      h("div", { class: "top" }, [h("h2", { class: "title-text" }), h("button", { class: "csv", type: "button", text: "Download CSV", onclick: () => this._csv() })]),
      h("div", { class: "filters" }, [
        h("input", { type: "search", class: "q", "aria-label": "Search clients", placeholder: "Search any field: name, IP, MAC, AP, vendor, role…" }),
        h("select", { name: "site", "aria-label": "Filter by site" }),
        h("select", { name: "connection", "aria-label": "Filter by connection" }),
        h("select", { name: "network", "aria-label": "Filter by network" }),
        h("select", { name: "category", "aria-label": "Filter by type" }),
        h("label", { class: "toggle" }, [h("input", { type: "checkbox", name: "connected" }), h("span", { text: "Connected now" })]),
      ]),
      h("div", { class: "summary", role: "status", "aria-live": "polite" }), h("div", { class: "notice" }),
      h("div", { class: "table", role: "table", "aria-label": "Network clients" }),
      h("div", { class: "pagination" }, [
        h("button", { class: "previous", type: "button", text: "Previous", onclick: () => { this._page--; this._render(); } }),
        h("span", { class: "page-label" }),
        h("button", { class: "next", type: "button", text: "Next", onclick: () => { this._page++; this._render(); } }),
      ]),
    ])));
    const q = root.querySelector(".q");
    q.addEventListener("input", () => { this._page = 0; this._render(); });
    for (const sel of root.querySelectorAll("select")) sel.addEventListener("change", () => { this._page = 0; this._render(); });
    const toggle = root.querySelector("[name=connected]");
    toggle.checked = this._connectedOnly;
    toggle.addEventListener("change", () => { this._connectedOnly = toggle.checked; this._page = 0; this._render(); });
    this._built = true;
  }

  _options(name, all, values, label = v => v) {
    const sel = this.shadowRoot.querySelector(`[name=${name}]`);
    const current = sel.value;
    sel.replaceChildren(h("option", { value: "", text: all }), ...values.map(v => h("option", { value: v, text: label(v) })));
    sel.value = values.includes(current) ? current : "";
    sel.hidden = values.length < 2 && !current;
    return sel.value;
  }

  _filtered() {
    const root = this.shadowRoot;
    const rows = (this._clients || []).filter(r => !this._connectedOnly || r.connected);
    const uniq = f => [...new Set(rows.map(f).filter(Boolean).map(String))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const site = this._options("site", "All sites", uniq(r => r.site));
    const conn = this._options("connection", "Wired and wireless", ["wireless", "wired"], v => (v === "wired" ? "Wired" : "Wireless"));
    const net = this._options("network", "All networks", uniq(r => (r.wired ? null : r.ssid)));
    const cat = this._options("category", "All types", uniq(clientType));
    const q = root.querySelector(".q").value.trim().toLocaleLowerCase();
    const out = rows.filter(r => (!site || String(r.site) === site) &&
      (!conn || (conn === "wired") === Boolean(r.wired)) && (!net || (!r.wired && r.ssid === net)) &&
      (!cat || clientType(r) === cat) &&
      (!q || Object.values(r).some(v => (typeof v === "string" || typeof v === "number") && String(v).toLocaleLowerCase().includes(q))));
    const [, , key] = CLIENT_COLUMNS[this._sort.key] || CLIENT_COLUMNS.name;
    const name = CLIENT_COLUMNS.name[2];
    return { filtering: Boolean(q || site || conn || net || cat), all: rows.length, rows: out.map(r => [key(r), name(r), r]).sort((a, b) => {
      const x = a[0] ?? null, y = b[0] ?? null;
      if (x === null || x === "") return y === null || y === "" ? a[1].localeCompare(b[1]) : 1;
      if (y === null || y === "") return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" });
      return c * this._sort.dir || a[1].localeCompare(b[1]);
    }).map(t => t[2]) };
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (!this._built) this._build();
    const root = this.shadowRoot;
    root.querySelector(".title-text").textContent = this._config.title || "Clients";
    root.querySelector(".body").className = `body mode-${["table", "list"].includes(this._config.layout) ? this._config.layout : "auto"}`;
    const cols = this._config.columns;
    const { rows, filtering, all } = this._filtered();
    this._visibleRows = rows;
    const size = this._config.page_size;
    const pages = Math.max(1, Math.ceil(rows.length / size));
    this._page = Math.max(0, Math.min(this._page, pages - 1));
    const start = this._page * size, end = Math.min(rows.length, start + size);
    const table = root.querySelector(".table");
    table.style.setProperty("--cols", cols.map(c => (c === "name" ? "minmax(0,2fr)" : "minmax(0,1fr)")).join(" "));
    table.style.setProperty("--n", cols.length);
    const header = h("div", { class: "tr th", role: "row" }, cols.map(c => {
      const active = this._sort.key === c;
      return h("div", { role: "columnheader", "aria-sort": active ? (this._sort.dir > 0 ? "ascending" : "descending") : "none" },
        h("button", { type: "button", class: `sort${active ? " on" : ""}`, text: CLIENT_COLUMNS[c][0] + (active ? (this._sort.dir > 0 ? " ▲" : " ▼") : ""),
          onclick: () => { this._sort = { key: c, dir: active ? -this._sort.dir : 1 }; this._render(); } }));
    }));
    const body = [];
    for (const r of rows.slice(start, end)) {
      const open = this._open === r.mac;
      body.push(h("div", { class: `tr${open ? " open" : ""}${r.connected ? "" : " away"}`, role: "row" }, cols.map(c => {
        const text = CLIENT_COLUMNS[c][1](r);
        // Empty cells show "—" in columns; the list layout leaves them out.
        const blank = text === null || text === undefined || text === "";
        if (c !== "name") return h("div", { class: `td${blank ? " blank" : ""}`, role: "cell", "data-label": CLIENT_COLUMNS[c][0], text: blank ? "—" : text });
        const sub = [clientType(r) || r.vendor, !r.connected && `seen ${ago(r.last_seen) || "earlier"}`].filter(Boolean).join(" · ");
        return h("div", { class: "td id", role: "cell" }, h("button", { type: "button", class: "who", "aria-expanded": open ? "true" : "false",
          onclick: () => { this._open = open ? null : r.mac; this._render(); } }, [
          h("span", { class: "cname", text: text || r.mac }), sub ? h("span", { class: "muted", text: sub }) : null]));
      })));
      if (open) body.push(this._details(r));
    }
    table.replaceChildren(header, ...body);
    header.hidden = !body.length;
    const count = filtering ? `${rows.length} of ${all} clients` : `${rows.length} clients`;
    root.querySelector(".summary").textContent = !this._loaded ? "Loading clients…" : rows.length ? `${count} · showing ${start + 1}–${end}` : count;
    const domains = CLIENT_DOMAINS.filter(d => (this._hass.config?.components || []).includes(d));
    root.querySelector(".notice").textContent = !domains.length ? "Add the HPE Mist or HPE Central integration to list clients."
      : [...(this._errors || []), this._loaded && !rows.length ? (filtering ? "No clients match these filters." : this._connectedOnly ? "No clients connected now." : "No clients reported yet.") : ""].filter(Boolean).join(" ");
    root.querySelector(".previous").disabled = this._page === 0;
    root.querySelector(".next").disabled = this._page >= pages - 1;
    root.querySelector(".pagination").hidden = pages < 2;
    root.querySelector(".page-label").textContent = `Page ${this._page + 1} of ${pages}`;
  }

  _details(r) {
    const value = (k, v) => (TIME_FIELDS.has(k) ? (num(v) ? new Date(v * 1000).toLocaleString() : null)
      : typeof v === "boolean" ? (v ? "Yes" : "No") : k === "rssi" && r.rssi_estimated ? `≈ ${v} (SNR + the AP radio's noise floor)` : v);
    const items = CLIENT_FIELDS.map(([k, label]) => [label, value(k, r[k])]).filter(([, v]) => v !== null && v !== undefined && v !== "");
    const input = h("input", { type: "text", class: "rename", value: r.name || "", placeholder: r.found_name || r.mac, "aria-label": `New name for ${r.name || r.mac}` });
    const status = h("span", { class: "muted", role: "status" });
    const save = async name => {
      status.textContent = "Saving…";
      try {
        await this._hass.callService(r.domain, "rename_client", { mac: r.mac, name });
        status.textContent = name ? "Saved." : "Back to the found name.";
        this._fetchedAt = 0;
        setTimeout(() => this._fetch(), 500);
      } catch (err) {
        status.textContent = `Could not save: ${err?.message || err}`;
      }
    };
    input.addEventListener("keydown", e => { if (e.key === "Enter") save(input.value.trim()); });
    return h("div", { class: "detail-row", role: "row" }, h("div", { class: "details", role: "cell" }, [
      h("div", { class: "rename-row" }, [
        h("label", { text: "Name" }), input,
        h("button", { type: "button", text: "Save", onclick: () => save(input.value.trim()) }),
        h("button", { type: "button", text: "Use found name", title: r.found_name || "", onclick: () => save("") }), status,
      ]),
      h("dl", {}, items.flatMap(([label, v]) => [h("dt", { text: label }), h("dd", { text: v })])),
    ]));
  }

  _csv() {
    const rows = this._visibleRows || [];
    const keys = CLIENT_FIELDS.map(([k]) => k).filter(k => rows.some(r => r[k] !== null && r[k] !== undefined && r[k] !== ""));
    const cell = v => {
      const s = v === null || v === undefined ? "" : String(v);
      const safe = /^[=+\-@]/.test(s) ? `'${s}` : s;  // no formulas when opened in a spreadsheet
      return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
    };
    const csv = [keys.join(","), ...rows.map(r => keys.map(k => cell(TIME_FIELDS.has(k) && num(r[k]) ? new Date(r[k] * 1000).toISOString() : r[k])).join(","))].join("\n");
    const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "clients.csv" });
    this.shadowRoot.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
}

// The stacked "label: value" layout: always in list mode, and in auto mode when the card is under 640 px wide.
const listStyle = m => `
    .${m} .th{display:none}.${m} .tr{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:4px 14px}.${m} .td.id{grid-column:1/-1}
    .${m} .td:not(.id)::before{content:attr(data-label) ": ";color:var(--secondary-text-color);font-size:12px}.${m} .td.blank{display:none}
    .${m} dl{grid-template-columns:minmax(0,1fr)}.${m} dd{margin-bottom:6px}`;

const CLIENT_STYLE = `
  ha-card{padding:0}.body{padding:16px;container-type:inline-size;container-name:clients}
  .top{display:flex;align-items:center;gap:10px}.top h2{flex:1;margin:0 0 10px;font-size:20px;font-weight:600}
  .filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  input,select,button{font:inherit;color:inherit;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:6px;min-height:38px;padding:6px 10px}
  .q{flex:2 1 240px;min-width:0}select{flex:1 1 130px;min-width:0}select[hidden]{display:none}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}
  input:focus,select:focus,button:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}
  .toggle{display:flex;align-items:center;gap:6px;font-size:14px;white-space:nowrap}.toggle input{min-height:0}
  .summary{color:var(--secondary-text-color);margin:10px 0 4px;font-size:13px}.notice{color:var(--secondary-text-color);font-size:13px}.notice:empty{display:none}
  .tr{display:grid;grid-template-columns:var(--cols);gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--divider-color)}
  .tr.away{opacity:.65}.th{position:sticky;top:0;background:var(--card-background-color,#fff);z-index:1}.th[hidden]{display:none}
  .sort{border:none;background:none;padding:2px 0;min-height:0;font-size:12px;font-weight:600;color:var(--secondary-text-color);text-align:left}
  .sort.on{color:var(--primary-text-color)}.td{min-width:0;overflow-wrap:anywhere;font-size:14px}
  .who{border:none;background:none;padding:0;min-height:0;text-align:left;display:flex;flex-direction:column;width:100%}
  .who .cname{font-weight:600;white-space:normal}.tr.open .cname{color:var(--primary-color)}
  .detail-row{border-bottom:1px solid var(--divider-color)}.details{padding:10px 12px 14px;background:var(--secondary-background-color);border-radius:8px;margin:6px 0 10px}
  .rename-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}.rename-row label{font-weight:600;font-size:13px}.rename{flex:1 1 200px;min-width:0}
  dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 14px;margin:0;font-size:13px}dt{color:var(--secondary-text-color)}dd{margin:0;overflow-wrap:anywhere}
  .pagination{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:12px}.pagination[hidden]{display:none}.page-label{font-size:13px;color:var(--secondary-text-color)}
  @container clients (max-width:640px){${listStyle("mode-auto")}
  }${listStyle("mode-list")}
  .mode-table .table{overflow-x:auto}.mode-table .tr{min-width:calc(var(--n) * 120px)}
`;

if (!customElements.get("hpe-access-point-card")) customElements.define("hpe-access-point-card", HpeAccessPointCard);
if (!customElements.get("hpe-switch-card")) customElements.define("hpe-switch-card", HpeSwitchCard);
if (!customElements.get("hpe-client-list-card")) customElements.define("hpe-client-list-card", HpeClientListCard);
window.customCards = window.customCards || [];
for (const card of [
  { type: "hpe-client-list-card", name: "HPE Client list", description: "Every Mist and Central client: sortable, searchable columns, every detail the network gives, and renaming." },
  { type: "hpe-access-point-card", name: "HPE Access point", description: "One Mist or Central AP: radios, uplink, health and connected clients." },
  { type: "hpe-switch-card", name: "HPE Switch", description: "One Mist or Central switch: port grid, PoE, what is plugged in, VLANs." },
]) if (!window.customCards.some(c => c.type === card.type)) window.customCards.push({ ...card, preview: false });
