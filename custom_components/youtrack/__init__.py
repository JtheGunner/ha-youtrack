"""The YouTrack integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from . import llm_api
from .activity import ActivityFeed
from .api import YouTrackClient
from .const import (
    CONF_API_TOKEN,
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import YouTrackProjectCoordinator
from .data import YouTrackRuntimeData
from .exceptions import YouTrackAuthError, YouTrackError, YouTrackPermissionError
from .issue_cache import IssueCache
from .kb_coordinator import KnowledgeBaseCoordinator
from .repairs import async_sync_projects
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.typing import ConfigType

    from .data import YouTrackConfigEntry

_LOGGER = logging.getLogger(__name__)

# Seconds to wait for further project changes before the entry reloads.
RELOAD_DELAY = 1.0
# An issue cache that survived a reload is only trusted when it was refreshed this recently;
# an older one would need a delta request longer than the cache can read.
CACHE_MAX_AGE = timedelta(hours=1)

# The issue caches of each entry by project id; they outlive a reload so it need not read every
# issue again.
_ISSUE_CACHES: HassKey[dict[str, dict[str, IssueCache]]] = HassKey(DOMAIN)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.TODO,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service actions once for all entries."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YouTrackConfigEntry) -> bool:
    """Connect to YouTrack and store the client for the platforms."""
    verify_ssl: bool = entry.data[CONF_VERIFY_SSL]
    client = YouTrackClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        entry.data[CONF_URL],
        entry.data[CONF_API_TOKEN],
        verify_ssl=verify_ssl,
    )
    try:
        await client.get_current_user()
    except (YouTrackAuthError, YouTrackPermissionError) as err:
        raise ConfigEntryAuthFailed("YouTrack rejected the stored token") from err
    except YouTrackError as err:
        raise ConfigEntryNotReady(f"Cannot reach YouTrack: {err}") from err

    entry.runtime_data = YouTrackRuntimeData(client=client, feed=ActivityFeed(client))
    skipped = await async_sync_projects(hass, entry)
    outcomes = await asyncio.gather(
        *(
            _async_setup_project(hass, entry, subentry)
            for subentry in list(entry.subentries.values())
            if subentry.subentry_id not in skipped
        ),
        return_exceptions=True,
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome

    # A project removed while the loop above was refreshing has no subentry left to add entities to.
    for subentry_id in set(entry.runtime_data.coordinators) - set(entry.subentries):
        del entry.runtime_data.coordinators[subentry_id]
        del entry.runtime_data.knowledge_bases[subentry_id]

    llm_api.async_register(hass, entry)
    reload_debouncer = Debouncer(
        hass,
        _LOGGER,
        cooldown=RELOAD_DELAY,
        immediate=False,
        function=partial(hass.config_entries.async_schedule_reload, entry.entry_id),
    )
    entry.async_on_unload(reload_debouncer.async_shutdown)
    entry.async_on_unload(
        entry.add_update_listener(partial(_async_reload_on_update, reload_debouncer))
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_project(
    hass: HomeAssistant, entry: YouTrackConfigEntry, subentry: ConfigSubentry
) -> None:
    """Load the issues of one project and start reading its knowledge base."""
    coordinator = YouTrackProjectCoordinator(
        hass, entry, subentry, _issue_cache(hass, entry, subentry)
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data.coordinators[subentry.subentry_id] = coordinator
    knowledge_base = KnowledgeBaseCoordinator(hass, entry, subentry)
    entry.runtime_data.knowledge_bases[subentry.subentry_id] = knowledge_base
    # Not every token may read articles, and a large knowledge base takes long to read; neither
    # may hold back the issues. Its entities stay unavailable until the first read has finished.
    entry.async_create_task(
        hass,
        knowledge_base.async_refresh(),
        f"{DOMAIN} {coordinator.project_key} knowledge base first refresh",
    )


def _issue_cache(
    hass: HomeAssistant, entry: YouTrackConfigEntry, subentry: ConfigSubentry
) -> IssueCache:
    """Return the issue cache of a project, reusing the one of a previous load if still good."""
    caches = hass.data.setdefault(_ISSUE_CACHES, {}).setdefault(entry.entry_id, {})
    project_id: str = subentry.data[CONF_PROJECT_ID]
    project_key: str = subentry.data[CONF_PROJECT_KEY]
    client = entry.runtime_data.client
    cache = caches.get(project_id)
    if (
        cache is None
        or cache.project_key != project_key
        or cache.is_stale(dt_util.utcnow(), CACHE_MAX_AGE)
    ):
        cache = caches[project_id] = IssueCache(
            client, project_key, dt_util.get_default_time_zone()
        )
    else:
        cache.use_client(client)
    return cache


async def async_remove_entry(hass: HomeAssistant, entry: YouTrackConfigEntry) -> None:
    """Forget the issue caches of a removed entry."""
    hass.data.get(_ISSUE_CACHES, {}).pop(entry.entry_id, None)


async def async_unload_entry(hass: HomeAssistant, entry: YouTrackConfigEntry) -> bool:
    """Unload the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: YouTrackConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting the device of a project that is no longer set up."""
    active = {
        (DOMAIN, f"{entry.entry_id}_{subentry.data[CONF_PROJECT_ID]}")
        for subentry in entry.subentries.values()
        if subentry.subentry_id in entry.runtime_data.coordinators
    }
    return not (device_entry.identifiers & active)


async def _async_reload_on_update(
    debouncer: Debouncer[None], hass: HomeAssistant, entry: YouTrackConfigEntry
) -> None:
    """Reload the entry once after a burst of project changes."""
    await debouncer.async_call()
