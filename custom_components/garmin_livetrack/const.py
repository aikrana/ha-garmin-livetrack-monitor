"""Constants for the Garmin LiveTrack Monitor integration."""

DOMAIN = "garmin_livetrack"
INTEGRATION_NAME = "Garmin LiveTrack Monitor"

# ── Config keys ──────────────────────────────────────────────────────────────
CONF_IMAP_SERVER = "imap_server"
CONF_IMAP_PORT = "imap_port"
CONF_IMAP_USERNAME = "imap_username"
CONF_IMAP_PASSWORD = "imap_password"
CONF_IMAP_FOLDER = "imap_folder"
CONF_SENDER = "sender_email"
CONF_PERSONS = "persons"
CONF_PERSON_NAME = "name"
CONF_PERSON_ID = "entity_prefix"
CONF_ENABLE_DEVICE_TRACKER = "enable_device_tracker"
CONF_EMAIL_MAX_AGE = "email_max_age_minutes"
CONF_POLL_INTERVAL = "poll_interval"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_IMAP_PORT = 993
DEFAULT_IMAP_FOLDER = "INBOX"
DEFAULT_SENDER = "noreply@garmin.com"
DEFAULT_EMAIL_MAX_AGE = 5  # minutes
DEFAULT_POLL_INTERVAL = 6  # seconds

# Fallback for the device's track-point posting frequency (seconds).
# Used by the tracking loop to throttle track-point fetches when the
# session response doesn't include `postTrackPointFrequency`.  5 s is
# slightly under the typical 6-second poll interval to avoid throttling
# on devices that post faster than the documented frequency.
DEFAULT_POST_TRACK_POINT_FREQUENCY = 5  # seconds

# ── Events ───────────────────────────────────────────────────────────────────
EVENT_ACTIVITY_STARTED = f"{DOMAIN}_activity_started"
EVENT_ACTIVITY_DETECTED = f"{DOMAIN}_activity_detected"
EVENT_POINT_RECEIVED = f"{DOMAIN}_point_received"
EVENT_ACTIVITY_ENDED = f"{DOMAIN}_activity_ended"

# ── Sensor states ────────────────────────────────────────────────────────────
STATE_IDLE = "idle"
STATE_ACTIVE = "active"
STATE_FINISHED = "finished"

# ── URLs ─────────────────────────────────────────────────────────────────────
LIVETRACK_BASE_URL = "https://livetrack.garmin.com"

# ── Attribute keys ───────────────────────────────────────────────────────────
ATTR_SESSION_ID = "session_id"
ATTR_TOKEN = "token"
ATTR_PERSON_NAME = "person_name"
ATTR_PERSON_ID = "person_id"
ATTR_LIVETRACK_URL = "livetrack_url"
ATTR_SESSION_START = "session_start"
ATTR_SESSION_END = "session_end"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_SPEED = "speed"
ATTR_SPEED_KMH = "speed_kmh"
ATTR_PACE = "pace"
ATTR_ALTITUDE = "altitude"
ATTR_DISTANCE_KM = "distance_km"
ATTR_DURATION = "duration"
ATTR_DURATION_SECS = "duration_secs"
ATTR_HEARTRATE = "heartrate"
ATTR_POWER_WATTS = "power_watts"
ATTR_CADENCE = "cadence"
ATTR_ACTIVITY_TYPE = "activity_type"
ATTR_EVENT_TYPES = "event_types"
ATTR_POINT_STATUS = "point_status"
ATTR_ELEVATION_SOURCE = "elevation_source"
ATTR_DATETIME = "datetime"
ATTR_HAS_LOCATION = "has_location"
ATTR_HAS_POINT_END = "has_point_end"

# ── Regex for LiveTrack URLs in emails ───────────────────────────────────────
LIVETRACK_URL_REGEX = (
    r"https://livetrack\.garmin\.com/session/"
    r"([a-z0-9\-\n\r=]+)/token/"
    r"([a-zA-Z0-9\-\n\r=]+)"
)

# ── Activity icon mapping ────────────────────────────────────────────────────
ACTIVITY_ICON_MAP = {
    "hiking": "mdi:hiking",
    "walking": "mdi:walk",
    "cycling": "mdi:bike",
    "running": "mdi:run-fast",
    "kayak": "mdi:kayaking",
    "other": "mdi:ski",
}
DEFAULT_ICON = "mdi:map-marker-radius"
