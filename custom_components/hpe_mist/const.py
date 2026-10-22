"""Constants for the HPE Mist integration."""

DOMAIN = "hpe_mist"

CONF_HOST = "host"
CONF_TOKEN = "token"
CONF_ORG_ID = "org_id"
CONF_TRACKED = "tracked"  # options: {"02:00:00:00:00:01": "Name"}
CONF_ADD_ALL = "add_all_clients"  # options: also add every other client, disabled until enabled
DEFAULT_ADD_ALL = True

MANUFACTURER = "HPE Mist"

# Site event entity: what it can fire.
EVENT_CLIENT_JOINED = "client_joined"
EVENT_CLIENT_LEFT = "client_left"
EVENT_DEVICE_DOWN = "device_down"
EVENT_DEVICE_UP = "device_up"
EVENT_ALARM = "alarm"
EVENT_TYPES = [EVENT_CLIENT_JOINED, EVENT_CLIENT_LEFT, EVENT_DEVICE_DOWN, EVENT_DEVICE_UP, EVENT_ALARM]

# Mist clouds (REST host); the stream host is the same with "api." -> "api-ws.".
MIST_CLOUDS = {
    "api.mist.com": "Global 01",
    "api.gc1.mist.com": "Global 02",
    "api.ac2.mist.com": "Global 03",
    "api.gc2.mist.com": "Global 04",
    "api.gc4.mist.com": "Global 05",
    "api.eu.mist.com": "EMEA 01",
    "api.gc3.mist.com": "EMEA 02",
    "api.ac6.mist.com": "EMEA 03",
    "api.gc6.mist.com": "EMEA 04",
    "api.ac5.mist.com": "APAC 01",
    "api.gc5.mist.com": "APAC 02",
    "api.gc7.mist.com": "APAC 03",
}
DEFAULT_HOST = "api.mist.com"

# REST sweep (per site: clients + device stats; per org: alarms): every 30 s while
# the live stream is down, every 5 minutes as a safety check while it is up.
POLL_SECONDS = 30
SAFETY_SWEEP_SECONDS = 300
# Per-switch port details (one search per site) are fetched at most this often
# and kept between sweeps.
DETAIL_SECONDS = SAFETY_SWEEP_SECONDS
# Searches (ports, clients seen in the last 7 days) follow at most this many pages.
SEARCH_MAX_PAGES = 5
# Every client a sweep lists is remembered (for the options picker) until it has
# not been seen for this many days; saves are batched.
SEEN_DAYS = 30
SEEN_SAVE_DELAY_SECONDS = 60
# List attributes (ports, clients, radios) are cut at this many rows.
MAX_LIST_ROWS = 500
# A client counts as away once neither the stream nor a sweep has seen it for
# this long, so a short Wi-Fi drop or a sleeping radio does not look like leaving.
AWAY_AFTER_SECONDS = 600
# How often away is re-checked between sweeps.
AWAY_CHECK_SECONDS = 30
# Short Mist errors keep the last known state; only after this long without a
# good sweep (and no live stream) do the trackers become unavailable, never not_home.
UNAVAILABLE_AFTER_SECONDS = 300
# Backoff ceiling after 429 / 5xx answers, and between stream reconnects.
MAX_BACKOFF_SECONDS = 300
STREAM_RETRY_SECONDS = 5
# Every Mist request gives up after this long; the stream pings this often.
REQUEST_TIMEOUT_SECONDS = 20
STREAM_HEARTBEAT_SECONDS = 30
