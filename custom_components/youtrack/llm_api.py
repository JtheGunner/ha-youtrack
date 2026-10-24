"""An LLM API that lets Assist conversation agents look things up in YouTrack."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm

from .const import CONF_LLM_CREATE, CONF_PROJECT_ID, CONF_PROJECT_KEY, DOMAIN
from .queries import is_balanced, scoped_query
from .services import call_api, service_error

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.llm import LLMContext, ToolInput

    from .data import YouTrackConfigEntry
    from .models import Issue

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
DEFAULT_ARTICLE_LENGTH = 2000
MAX_ARTICLE_LENGTH = 8000
SUMMARY_LIMIT = 200

_READ_ONLY = llm.ToolAnnotations(
    read_only=True, destructive=False, idempotent=True, open_world=True
)
_CREATES = llm.ToolAnnotations(
    read_only=False, destructive=False, idempotent=False, open_world=True
)


@dataclass(frozen=True, slots=True)
class _Scope:
    """The projects of one instance an assistant may see: key to project id."""

    entry: YouTrackConfigEntry
    projects: dict[str, str]

    @classmethod
    def of(cls, entry: YouTrackConfigEntry) -> _Scope:
        return cls(
            entry,
            {
                subentry.data[CONF_PROJECT_KEY]: subentry.data[CONF_PROJECT_ID]
                for subentry in entry.subentries.values()
            },
        )

    def query(self, text: str) -> str:
        """Return ``text`` if it cannot break out of the project restriction."""
        if not is_balanced(text):
            raise service_error("rejected", reason="unbalanced brackets in the search")
        return text

    def keys(self, requested: str | None) -> list[str]:
        """Return the project keys a request covers; unknown keys are refused."""
        if requested is None:
            return list(self.projects)
        key = requested.strip().upper()
        if key not in self.projects:
            raise service_error("unknown_llm_project", project=requested)
        return [key]


class _YouTrackTool(llm.Tool):
    """Shared plumbing of the YouTrack tools."""

    integration = DOMAIN

    def __init__(self, hass: HomeAssistant, scope: _Scope) -> None:
        self._hass = hass
        self._scope = scope

    @property
    def _client(self) -> Any:
        return self._scope.entry.runtime_data.client

    @property
    def _base_url(self) -> str:
        return str(self._scope.entry.data["url"])

    def _issue_data(self, issue: Issue) -> dict[str, Any]:
        return {
            "id": issue.id,
            "summary": issue.summary[:SUMMARY_LIMIT],
            "state": issue.state,
            "priority": issue.priority,
            "assignee": issue.assignee,
            "url": f"{self._base_url}/issue/{issue.id}",
        }


class SearchIssuesTool(_YouTrackTool):
    """Search issues with the YouTrack search syntax."""

    name = "search_youtrack_issues"
    description = (
        "Search YouTrack issues. The query uses YouTrack search syntax, for example "
        "'#Unresolved for: me' or 'heating pump'. Returns the most recently changed matches."
    )
    annotations = _READ_ONLY
    parameters = probatio.Schema(
        {
            probatio.Required("query"): cv.string,
            probatio.Optional("project"): cv.string,
            probatio.Optional("limit", default=DEFAULT_LIMIT): probatio.All(
                cv.positive_int, probatio.Range(max=MAX_LIMIT)
            ),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> llm.ToolResult:
        """Search the issues of the allowed projects."""
        args = self.parameters(tool_input.tool_args)
        query = self._scope.query(args["query"])
        keys = self._scope.keys(args.get("project"))
        limit = args["limit"]
        pages = await call_api(
            hass,
            self._scope.entry,
            lambda: asyncio.gather(
                *(self._client.get_issues(scoped_query(key, query), top=limit) for key in keys)
            ),
        )
        issues = sorted(
            (
                issue
                for page in pages
                for issue in page
                if issue.project in self._scope.projects  # second line of defence
            ),
            key=lambda issue: issue.updated.timestamp() if issue.updated else 0,
            reverse=True,
        )[:limit]
        return llm.ToolResult(data={"issues": [self._issue_data(issue) for issue in issues]})


class SearchArticlesTool(_YouTrackTool):
    """Search the knowledge base."""

    name = "search_youtrack_articles"
    description = (
        "Search the YouTrack knowledge base. Returns article titles and ids; read one with "
        "get_youtrack_article."
    )
    annotations = _READ_ONLY
    parameters = probatio.Schema(
        {
            probatio.Required("query"): cv.string,
            probatio.Optional("project"): cv.string,
            probatio.Optional("limit", default=DEFAULT_LIMIT): probatio.All(
                cv.positive_int, probatio.Range(max=MAX_LIMIT)
            ),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> llm.ToolResult:
        """Search the articles of the allowed projects."""
        args = self.parameters(tool_input.tool_args)
        query = self._scope.query(args["query"])
        keys = self._scope.keys(args.get("project"))
        limit = args["limit"]
        pages = await call_api(
            hass,
            self._scope.entry,
            lambda: asyncio.gather(
                *(
                    self._client.get_articles(query=scoped_query(key, query), top=limit)
                    for key in keys
                )
            ),
        )
        articles = sorted(
            (
                article
                for page in pages
                for article in page
                if article.project in self._scope.projects  # second line of defence
            ),
            key=lambda article: article.updated.timestamp() if article.updated else 0,
            reverse=True,
        )[:limit]
        return llm.ToolResult(
            data={
                "articles": [
                    {
                        "id": article.readable_id,
                        "title": article.summary,
                        "project": article.project,
                        "url": f"{self._base_url}/articles/{article.readable_id}",
                    }
                    for article in articles
                ]
            }
        )


class GetArticleTool(_YouTrackTool):
    """Read one article."""

    name = "get_youtrack_article"
    description = "Read a YouTrack knowledge base article by its id, for example DEMO-A-12."
    annotations = _READ_ONLY
    parameters = probatio.Schema(
        {
            probatio.Required("article_id"): cv.string,
            probatio.Optional("max_length", default=DEFAULT_ARTICLE_LENGTH): probatio.All(
                cv.positive_int, probatio.Range(min=100, max=MAX_ARTICLE_LENGTH)
            ),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> llm.ToolResult:
        """Return the article text, cut to ``max_length`` characters."""
        args = self.parameters(tool_input.tool_args)
        article_id = args["article_id"].strip()
        if not article_id.replace("-", "").replace("_", "").isalnum():
            raise service_error("invalid_article_id", article_id=article_id)
        article = await call_api(
            hass, self._scope.entry, lambda: self._client.get_article(article_id)
        )
        # Articles of projects that were not added to Home Assistant stay out of reach.
        if article.project not in self._scope.projects:
            raise service_error("not_found")
        content = article.content or ""
        return llm.ToolResult(
            data={
                "id": article.readable_id,
                "title": article.summary,
                "content": content[: args["max_length"]],
                "truncated": len(content) > args["max_length"],
                "url": f"{self._base_url}/articles/{article.readable_id}",
            }
        )


class CreateIssueTool(_YouTrackTool):
    """Create an issue; only offered when the user allowed it in the options."""

    name = "create_youtrack_issue"
    description = (
        "Create a YouTrack issue. Only do this when the user clearly asked for it, and say "
        "which issue was created."
    )
    annotations = _CREATES
    parameters = probatio.Schema(
        {
            probatio.Required("summary"): cv.string,
            probatio.Optional("description"): cv.string,
            probatio.Optional("project"): cv.string,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> llm.ToolResult:
        """Create the issue and return its id and link."""
        args = self.parameters(tool_input.tool_args)
        summary = args["summary"].strip()
        if not summary:
            raise service_error("empty_summary")
        keys = self._scope.keys(args.get("project"))
        if len(keys) != 1:
            raise service_error("llm_project_required")
        project_id = self._scope.projects[keys[0]]
        issue_id = await call_api(
            hass,
            self._scope.entry,
            lambda: self._client.create_issue(project_id, summary, args.get("description")),
        )
        return llm.ToolResult(data={"id": issue_id, "url": f"{self._base_url}/issue/{issue_id}"})


@dataclass(slots=True, kw_only=True)
class YouTrackAPI(llm.API):
    """The tools of one YouTrack instance."""

    entry: YouTrackConfigEntry

    async def async_get_api_instance(self, llm_context: LLMContext) -> llm.APIInstance:
        """Return the tools this instance allows."""
        scope = _Scope.of(self.entry)
        tools: list[llm.Tool] = [
            SearchIssuesTool(self.hass, scope),
            SearchArticlesTool(self.hass, scope),
            GetArticleTool(self.hass, scope),
        ]
        prompt = (
            "You can search YouTrack issues and the knowledge base for these projects: "
            f"{', '.join(scope.projects) or 'none'}. Answer from the results and give the link."
        )
        if self.entry.options.get(CONF_LLM_CREATE, False):
            tools.append(CreateIssueTool(self.hass, scope))
            prompt += " You may create issues when the user asks."
        return llm.APIInstance(api=self, api_prompt=prompt, llm_context=llm_context, tools=tools)


def async_register(hass: HomeAssistant, entry: YouTrackConfigEntry) -> None:
    """Offer the instance's tools to conversation agents until the entry unloads."""
    try:
        unregister = llm.async_register_api(
            hass,
            YouTrackAPI(
                hass=hass,
                id=f"{DOMAIN}_{entry.entry_id}",
                name=f"YouTrack ({entry.title})",
                entry=entry,
            ),
        )
    except HomeAssistantError:  # pragma: no cover - the id is unique per entry
        return
    entry.async_on_unload(unregister)
