# HPE Mist for Home Assistant (unofficial)

> Not affiliated with or endorsed by Hewlett Packard Enterprise (HPE) or Juniper
> Networks. "HPE Mist" is used only to name the service this integration talks
> to. No HPE or Mist logos are included; the icon is original artwork.

Brings an HPE Mist org into Home Assistant so anything in it can drive an
automation. One config entry per org gives you:

- **A device per site**: clients (total, wireless, wired), clients per SSID, APs
  online / offline, active alarms, an **Events** entity (see below) and a
  diagnostic **Live stream** binary sensor (on while the websocket stream is
  connected).
- **A device per AP, switch and gateway** (under its site): online, clients, and as
  diagnostics CPU, memory, temperature, firmware and last restart where Mist
  reports them; per-radio clients / channel / utilization / power / noise floor
  for APs; per-port link state for switches and gateways (disabled by default).
  See *Entities* below for the detail sensors and their attributes.
- **A tracker per client**: any Wi-Fi client works (phone, laptop, car, printer,
  IoT device). Clients you tick in the options are enabled, named, get their own
  device and sensors (IP address, SSID, access point, signal). With *Add all other
  clients* on (the default), every other client Mist reports gets a tracker too,
  **disabled until you enable it** in one click under Entities.
- **Client names**: the first of the name you gave it (when picking it, or with the
  rename box / `hpe_mist.rename_client`), a rename of its device in Home Assistant,
  the login it authenticated with (802.1X; a MAC-auth login is the MAC and is
  skipped), the name another integration gave the device with that MAC (a Ring
  camera, a Sonos speaker), the DHCP hostname, Mist's device fingerprint
  (`Yoto Player 4d:28`), the vendor + last 3 MAC bytes, the MAC.

- **Controls**, only when the token may change things (org role *admin* or
  *write*), all **disabled by default**: a *Restart* button per AP, switch and
  gateway, and a *WLAN <ssid>* switch per WLAN (see Controls below).

Nothing is installed on the clients; everything is set up and edited in the
Home Assistant UI.

### Entities

Detail sensors (unique id = `<org>_dev_<mac>_<key>` or `<org>_site_<site>_<key>`); a
sensor only appears once Mist reports its data. List attributes are capped at 500
rows and kept out of the recorder.

| Key | On | State | Attributes |
| --- | --- | --- | --- |
| `clients` | site | clients connected now | `clients`: [{name, mac, ip, ssid, band, ap, wired, port, switch, rssi, snr, vendor}] |
| `clients` | AP | clients Mist counts on it | `clients`: [{name, mac, ip, ssid, band, rssi, snr, vendor}], `by_band`, `by_ssid` |
| `radios` | AP | radios up | `radios`: [{band, status, channel, bandwidth, power, utilization, noise_floor, clients}] (2.4, 5, 6 GHz) |
| `band_24_power`, `band_5_noise_floor`, … | AP | dBm (diagnostic) | |
| `uplink` | AP | `<switch> <port>` from LLDP, or `<speed> Mbps` | device, port, speed, poe_w |
| `ports_up` | switch / gateway | physical ports up | `ports_total`, `ports`: [{port, up, speed, uplink, poe_w, neighbor, neighbor_port, vlan, description}] in natural port order |
| `poe_draw` | switch | W drawn (only on PoE switches) | `budget_w` |
| `stream` (binary) | site | on while the live stream is connected | |

`wlans` (per AP) and `vlans` (per switch) exist in the shared design but Mist's
device stats do not report them, so they do not appear for Mist.

### Cards

The integration adds three cards to the dashboard card picker (no YAML, no
resources to add):

- **HPE Client list**: every client from HPE Mist and HPE Central. Sortable
  columns (click a header; again to reverse), search across every field, filters
  (site, wired / wireless, network, type) and *Connected now*. Pick the columns in
  the card editor: IP, network, connected to, signal, SNR, type, vendor, OS, site,
  VLAN, band, channel, security, user name, hostname, MAC, connected for, last
  seen, source. Clicking a client opens everything Mist gives for it (device
  family, model, OS, channel, BSSID, PHY, key management, PSK, tx / rx rate and
  throughput, guest, connected since…) and a rename box. *Download CSV* saves the
  filtered list.
- **HPE Access point** and **HPE Switch**: pick a device.

The card reads its rows with the `hpe_mist/clients` websocket command while it
is shown, so the long rows stay out of the state machine.

### Actions

| Action | Data | Does |
|---|---|---|
| `hpe_mist.rename_client` | `mac`, `name` | Names any client. An empty name goes back to the name the integration found. A picked client's name in the options changes too. Typed names are kept in `.storage/hpe_mist.client_names`. |

### Events

Each site's **Events** entity fires, with details as attributes:

| Event type | When | Attributes |
| --- | --- | --- |
| `client_joined` | a client appears (stream: seconds; sweep otherwise) | mac, name, hostname, ip, ssid, ap, tracked |
| `client_left` | a client has not been seen for 10 minutes | same |
| `device_down` | a sweep reports an AP / switch / gateway disconnected | mac, name, type, model |
| `device_up` | it is back (stream: seconds; sweep otherwise) | same |
| `alarm` | a new Mist alarm for the site | id, type, severity, count, timestamp |

Use them as triggers: *Entity* → the site's Events entity → *attribute
event_type*, or a template condition on `trigger.to_state.attributes`. The first
sweep after a start only takes stock, so a restart does not fire a flood of
"joined" events.

### Controls

With a read-only token none of these exist. With an *admin* or *write* token they
are created disabled; enable the ones you want under Entities. They act at once,
with no extra confirmation beyond Home Assistant's own.

| Entity | Does | Mist call |
| --- | --- | --- |
| `button.<device>_restart` | restarts that AP, switch or gateway (a gateway restart drops the internet connection for a few minutes) | `POST /sites/<site>/devices/<id>/restart` |
| `switch.<site>_wlan_<ssid>` | turns a site WLAN on or off | `PUT /sites/<site>/wlans/<id>` `{"enabled": …}` |
| `switch.<org>_wlan_<ssid>` | turns an org-wide (template) WLAN on or off for **every site that uses the template** | `PUT /orgs/<org>/wlans/<id>` `{"enabled": …}` |

Switch-port PoE on / off is not offered: Mist sets it in the switch's
`port_config`, which also carries each port's profile, so a toggle would have to
rewrite that map and could override template port settings.

## How presence decides

- **Joins are near-instant.** The integration keeps Mist's live websocket stream
  open (`wss://api-ws.<cloud>/api-ws/v1/stream`, subscribed to every site's client
  stats). A message for a tracked client marks it home straight away.
- **A REST sweep is the safety net.** It lists every site's connected clients and
  device stats and the org's alarms of the last day: every **5 minutes** while
  the stream is up, every **30 seconds** while it is down, and once right after the
  stream (re)connects to resync. If the stream can never connect (for example a
  custom host without one), the 30-second sweep alone does the job.
- **Away = 10 minutes with no sighting** from either the stream or a sweep
  (`AWAY_AFTER_SECONDS` in `const.py`), checked every 30 seconds. A short Wi-Fi
  drop or a sleeping radio does not count as leaving. Before its first sighting a
  client counts as away.
- **A Mist outage never looks like everyone left.** Every call has a timeout;
  429 and 5xx answers back off (doubling, honouring `Retry-After`, at most 5
  minutes); the stream reconnects with backoff. Trackers keep their last state
  while the stream is up or a sweep worked in the last 5 minutes, and become
  **unavailable** (not `not_home`) after that. Away is only decided while the data
  is fresh.

Tracker attributes: `ip`, `mac`, `host_name`, `ssid`, `ap_mac`, `ap_name`, `site`,
`last_seen` (UTC). Device sensors go unavailable while their device is offline;
the *Online* binary sensor shows off.

### API call budget

One sweep is **2 calls per site + 1 per org**: `sites/<site>/stats/clients`,
`sites/<site>/stats/devices?type=all` and `orgs/<org>/alarms/search`. Sites with a
switch or gateway add one `sites/<site>/stats/ports/search` at most every 5
minutes (kept between sweeps). With the stream up that is about
**(3 × sites + 1) × 12 calls per hour** (84 per hour for two sites); with it down,
(2 × sites + 1) × 120 + sites × 12. Add one `orgs/<org>/sites` call after a start
or an error. Opening the options runs `sites/<site>/stats/clients` per site plus
`orgs/<org>/clients/search` and `orgs/<org>/wired_clients/search` (last 7 days,
up to 5 pages each); these searches never run in the background. With a token
that can write, the WLAN list adds (sites + 1) calls at most every 5 minutes, and
one `GET /self` runs at each start to read the token's role. The stream
itself is one websocket connection (subscribed to client and device stats). Mist's
default limit is 5,000 REST calls per hour per token.

## Install

HACS: add this repository as a custom repository (category *Integration*),
install **HPE Mist**, restart Home Assistant.

Manual: copy `custom_components/hpe_mist` into your Home Assistant
`config/custom_components/` folder and restart.

## Set up

1. Settings → Devices & services → Add integration → **HPE Mist**.
2. Pick your Mist cloud (Global 01 is `api.mist.com`, EMEA 01 is
   `api.eu.mist.com`, and so on: the host in your Mist dashboard address with
   `manage` replaced by `api`), or type another API host.
3. Paste an API token that can read the org. It is checked with
   `GET /api/v1/self` and stored only in the config entry.
4. Pick the org. Add the integration again for another org (one entry per org).
5. Open the entry's **Configure** (*Clients*): leave *Add all other clients* on
   (or turn it off to keep only the ticked ones), tick clients (shown as
   `name · ip · mac · vendor · connected now` or `· seen 2 h ago`; a client with no
   hostname shows its vendor and the last three bytes of its MAC, like
   `ExampleVendor a3:c7:c2`) or type the MAC of a client that is not listed, then
   name each one. The list has tracked clients first, then those connected now,
   then every Wi-Fi and wired client Mist saw in the last 7 days, most recent
   first. Clients the sweeps have listed are also remembered for 30 days (in
   Home Assistant's storage), so they stay listed if the token may not run
   Mist's client searches. Saving reloads the entry; a client taken off the
   list is removed.

A phone must keep the same address on that network: on iOS set "Private Wi-Fi
Address" to Fixed; on Android the per-network randomised MAC is fine unless
"non-persistent" randomisation is turned on. A rotating address makes the client
look away.

## Icon

The integration ships its own icon in `custom_components/hpe_mist/brand/`
(original artwork). Home Assistant 2026.10 and later shows it from there; older
versions show "icon not available".

## Planned

- PoE on / off per switch port (draw per port is in the `ports_up` attributes).
- Wired clients as trackable clients (Mist's API does not list wired clients
  connected now the way it lists Wi-Fi clients, so this needs its own design).
- A sibling integration, **HPE Central** (`hpe_central`), built on the same design.

## Design notes

The coordinator only talks to a `PresenceSource` (`source.py`): `sweep()` returns
a `Snapshot` of sites, clients, devices and alarms as plain normalised dicts, and
an optional `stream()` delivers client sightings and device updates. Mist is the
one implementation today; the planned HPE Central integration can reuse the presence rules
(`presence.py`), the coordinator and the entities.

## Tests

```
pip install -r requirements_test.txt
python -m pytest
```
