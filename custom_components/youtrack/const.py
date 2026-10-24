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

CONF_QUERIES = "queries"
CONF_QUERY_ID = "id"
CONF_QUERY_NAME = "name"
CONF_QUERY_TEXT = "query"
CONF_QUERY_TOP = "top"
DEFAULT_QUERY_TOP = 10
MIN_QUERY_TOP = 1
MAX_QUERY_TOP = 25

EVENT_PREFIX = f"{DOMAIN}_issue_"

CONF_RESOLVED_STATE = "resolved_state"
CONF_REOPENED_STATE = "reopened_state"
CONF_TODO_CREATE = "todo_create"
DEFAULT_RESOLVED_STATE = "Done"
DEFAULT_REOPENED_STATE = "Open"

KB_INTERVAL_FACTOR = 6  # the knowledge base is polled this many times slower than issues
KB_RECENT_ARTICLES = 5

# Names (lower case) under which projects usually keep the date issues are due.
DUE_DATE_FIELD_NAMES = frozenset({"due date", "due"})
