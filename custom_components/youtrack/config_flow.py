"""Config flow for the YouTrack integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import YouTrackClient, normalize_base_url
from .const import (
    CONF_API_TOKEN,
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SUBENTRY_PROJECT,
)
from .exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackError,
    YouTrackPermissionError,
    YouTrackResponseError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        probatio.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        probatio.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)

REAUTH_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)

RECONFIGURE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        probatio.Optional(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        probatio.Required(CONF_VERIFY_SSL): BooleanSelector(),
    }
)


async def validate_connection(
    hass: HomeAssistant, url: str, token: str, *, verify_ssl: bool
) -> str | None:
    """Check URL and token; return an error key or None if everything works."""
    client = YouTrackClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl), url, token, verify_ssl=verify_ssl
    )
    try:
        await client.get_server_info()
        await client.get_current_user()
    except YouTrackAuthError:
        return "invalid_auth"
    except YouTrackPermissionError:
        return "no_permission"
    except YouTrackResponseError:
        return "invalid_response"
    except YouTrackConnectionError:
        return "cannot_connect"
    except YouTrackError:
        return "unknown"
    return None


class YouTrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of a YouTrack instance."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (polling interval)."""
        return YouTrackOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: ConfigEntry,
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Projects are added as subentries of the instance entry."""
        return {SUBENTRY_PROJECT: ProjectSubentryFlowHandler}

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after YouTrack rejected the stored token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await validate_connection(
                self.hass,
                entry.data[CONF_URL],
                user_input[CONF_API_TOKEN],
                verify_ssl=entry.data[CONF_VERIFY_SSL],
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change URL, token or SSL verification; an empty token keeps the current one."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input.get(CONF_API_TOKEN) or entry.data[CONF_API_TOKEN]
            try:
                url = normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                error = await validate_connection(
                    self.hass, url, token, verify_ssl=user_input[CONF_VERIFY_SSL]
                )
                if error is None:
                    if url != entry.data[CONF_URL]:
                        await self.async_set_unique_id(url)
                        self._abort_if_unique_id_configured()
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=url,
                        data_updates={
                            CONF_URL: url,
                            CONF_API_TOKEN: token,
                            CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        },
                    )
                errors["base"] = error

        suggested = user_input or {
            CONF_URL: entry.data[CONF_URL],
            CONF_VERIFY_SSL: entry.data[CONF_VERIFY_SSL],
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(RECONFIGURE_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for URL and token and verify them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_base_url(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                error = await validate_connection(
                    self.hass,
                    url,
                    user_input[CONF_API_TOKEN],
                    verify_ssl=user_input[CONF_VERIFY_SSL],
                )
                if error is None:
                    await self.async_set_unique_id(url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=urlsplit(url).netloc,
                        data={**user_input, CONF_URL: url},
                    )
                errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )


class ProjectSubentryFlowHandler(ConfigSubentryFlow):
    """Add a YouTrack project to an instance."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Let the user pick one of the projects that are not added yet."""
        entry: YouTrackConfigEntry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        try:
            projects = await entry.runtime_data.client.get_projects()
        except YouTrackError:
            return self.async_abort(reason="cannot_connect")

        added = {subentry.unique_id for subentry in entry.subentries.values()}
        available = {p.id: p for p in projects if not p.archived and p.id not in added}
        if not available:
            return self.async_abort(reason="no_projects_available")

        if user_input is not None:
            project = available.get(user_input[CONF_PROJECT_ID])
            if project is None:
                return self.async_abort(reason="project_unavailable")
            return self.async_create_entry(
                title=project.name,
                data={CONF_PROJECT_ID: project.id, CONF_PROJECT_KEY: project.short_name},
                unique_id=project.id,
            )

        options = [
            SelectOptionDict(value=project.id, label=f"{project.name} ({project.short_name})")
            for project in sorted(available.values(), key=lambda p: p.name.lower())
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_PROJECT_ID): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
        )


class YouTrackOptionsFlow(OptionsFlow):
    """Options of a YouTrack instance."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set how often YouTrack is polled."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
