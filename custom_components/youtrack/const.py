"""Constants for the YouTrack integration."""

from __future__ import annotations

DOMAIN = "youtrack"

CONF_URL = "url"
CONF_API_TOKEN = "api_token"
CONF_VERIFY_SSL = "verify_ssl"

SUBENTRY_PROJECT = "project"
CONF_PROJECT_ID = "project_id"
CONF_PROJECT_KEY = "project_key"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 5  # minutes
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60

# Count queries per project; "{project}" is replaced by the project short name.
COUNT_QUERIES: dict[str, str] = {
    "total": "project: {{{project}}}",
}
