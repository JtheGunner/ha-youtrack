"""Config flow for the YouTrack integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

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
    CONF_LLM_CREATE,
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_QUERIES,
    CONF_QUERY_ID,
    CONF_QUERY_NAME,
    CONF_QUERY_TEXT,
    CONF_QUERY_TOP,
    CONF_REOPENED_STATE,
    CONF_RESOLVED_STATE,
    CONF_SCAN_INTERVAL,
    CONF_TODO_CREATE,
    CONF_URL,
    CONF_VERIFY_SSL,
    CONF_WATCH,
    DEFAULT_QUERY_TOP,
    DEFAULT_REOPENED_STATE,
    DEFAULT_RESOLVED_STATE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_QUERY_TOP,
    MAX_SCAN_INTERVAL,
    MAX_WATCHED,
    MIN_QUERY_TOP,
    MIN_SCAN_INTERVAL,
    SUBENTRY_PROJECT,
)
from .exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackError,
    YouTrackInvalidQueryError,
    YouTrackPermissionError,
    YouTrackResponseError,
)
from .queries import scoped_query, watched_text

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
    """Add a YouTrack project to an instance and manage its custom query sensors."""

    _editing: str = ""

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

    def _queries(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._get_reconfigure_subentry().data.get(CONF_QUERIES, ())]

    def _save_queries(self, queries: list[dict[str, Any]]) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        return self.async_update_and_abort(
            self._get_entry(), subentry, data={**subentry.data, CONF_QUERIES: queries}
        )

    async def _validate_query(self, text: str) -> tuple[str | None, str]:
        """Ask YouTrack whether it accepts the query; return an error key and its detail."""
        entry: YouTrackConfigEntry = self._get_entry()
        project_key = self._get_reconfigure_subentry().data[CONF_PROJECT_KEY]
        try:
            await entry.runtime_data.client.count_issues(scoped_query(project_key, text))
        except YouTrackInvalidQueryError as err:
            return "invalid_query", str(err)
        except YouTrackError as err:
            return "cannot_validate", str(err)
        return None, ""

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage the custom query sensors of the project."""
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        menu = ["add_query"]
        if self._queries():
            menu += ["edit_query", "remove_query"]
        menu.append("watch_issues")
        return self.async_show_menu(step_id="reconfigure", menu_options=menu)

    async def async_step_add_query(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a named query."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            error, detail = await self._validate_query(user_input[CONF_QUERY_TEXT])
            if error is None:
                queries = self._queries()
                queries.append(self._query_data(uuid4().hex[:8], user_input))
                return self._save_queries(queries)
            errors[CONF_QUERY_TEXT] = error
            placeholders["error"] = detail
        return self.async_show_form(
            step_id="add_query",
            data_schema=self.add_suggested_values_to_schema(QUERY_SCHEMA, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_edit_query(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the query to change."""
        queries = self._queries()
        if user_input is not None:
            self._editing = user_input[CONF_QUERY_ID]
            return await self.async_step_edit_query_details()
        return self.async_show_form(
            step_id="edit_query",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_QUERY_ID): self._query_selector(queries)}
            ),
        )

    async def async_step_edit_query_details(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change name, text or issue limit of the picked query."""
        queries = self._queries()
        # The picker only offers existing queries, so the id is always found.
        index = next(i for i, item in enumerate(queries) if item[CONF_QUERY_ID] == self._editing)
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            error, detail = await self._validate_query(user_input[CONF_QUERY_TEXT])
            if error is None:
                queries[index] = self._query_data(self._editing, user_input)
                return self._save_queries(queries)
            errors[CONF_QUERY_TEXT] = error
            placeholders["error"] = detail
        return self.async_show_form(
            step_id="edit_query_details",
            data_schema=self.add_suggested_values_to_schema(
                QUERY_SCHEMA, user_input or queries[index]
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_watch_issues(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the issues that get a sensor each; an empty search turns it off."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        placeholders = {"error": "", "max": str(MAX_WATCHED)}
        if user_input is not None:
            text = user_input.get(CONF_WATCH, "").strip()
            error, detail = (
                (None, "") if not text else await self._validate_query(watched_text(text))
            )
            if error is None:
                return self.async_update_and_abort(
                    self._get_entry(), subentry, data={**subentry.data, CONF_WATCH: text}
                )
            errors[CONF_WATCH] = error
            placeholders["error"] = detail
        return self.async_show_form(
            step_id="watch_issues",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema({probatio.Optional(CONF_WATCH): TextSelector()}),
                user_input or {CONF_WATCH: subentry.data.get(CONF_WATCH, "")},
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_remove_query(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove queries; their sensors disappear."""
        queries = self._queries()
        errors: dict[str, str] = {}
        if user_input is not None:
            drop = set(user_input[CONF_QUERIES])
            if drop:
                return self._save_queries([q for q in queries if q[CONF_QUERY_ID] not in drop])
            errors[CONF_QUERIES] = "nothing_selected"
        return self.async_show_form(
            step_id="remove_query",
            errors=errors,
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_QUERIES): self._query_selector(queries, multiple=True),
                }
            ),
        )

    @staticmethod
    def _query_data(query_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            CONF_QUERY_ID: query_id,
            CONF_QUERY_NAME: user_input[CONF_QUERY_NAME].strip(),
            CONF_QUERY_TEXT: user_input[CONF_QUERY_TEXT].strip(),
            CONF_QUERY_TOP: int(user_input[CONF_QUERY_TOP]),
        }

    @staticmethod
    def _query_selector(queries: list[dict[str, Any]], *, multiple: bool = False) -> SelectSelector:
        return SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=item[CONF_QUERY_ID], label=item[CONF_QUERY_NAME])
                    for item in queries
                ],
                multiple=multiple,
                mode=SelectSelectorMode.LIST if multiple else SelectSelectorMode.DROPDOWN,
            )
        )


class YouTrackOptionsFlow(OptionsFlow):
    """Options of a YouTrack instance."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set the polling interval and what the to-do lists may change."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_RESOLVED_STATE: user_input[CONF_RESOLVED_STATE].strip(),
                    CONF_REOPENED_STATE: user_input[CONF_REOPENED_STATE].strip(),
                    CONF_TODO_CREATE: user_input[CONF_TODO_CREATE],
                    CONF_LLM_CREATE: user_input[CONF_LLM_CREATE],
                }
            )
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    probatio.Required(
                        CONF_RESOLVED_STATE,
                        default=options.get(CONF_RESOLVED_STATE, DEFAULT_RESOLVED_STATE),
                    ): TextSelector(),
                    probatio.Required(
                        CONF_REOPENED_STATE,
                        default=options.get(CONF_REOPENED_STATE, DEFAULT_REOPENED_STATE),
                    ): TextSelector(),
                    probatio.Required(
                        CONF_TODO_CREATE, default=options.get(CONF_TODO_CREATE, False)
                    ): BooleanSelector(),
                    probatio.Required(
                        CONF_LLM_CREATE, default=options.get(CONF_LLM_CREATE, False)
                    ): BooleanSelector(),
                }
            ),
        )


QUERY_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_QUERY_NAME): TextSelector(),
        probatio.Required(CONF_QUERY_TEXT): TextSelector(TextSelectorConfig(multiline=True)),
        probatio.Required(CONF_QUERY_TOP, default=DEFAULT_QUERY_TOP): NumberSelector(
            NumberSelectorConfig(
                min=MIN_QUERY_TOP, max=MAX_QUERY_TOP, step=1, mode=NumberSelectorMode.BOX
            )
        ),
    }
)
