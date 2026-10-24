"""Async client for the YouTrack REST API.

This module does not import Home Assistant so it can be extracted into a
standalone library later. The caller supplies the aiohttp session.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackInvalidQueryError,
    YouTrackNotFoundError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
    YouTrackResponseError,
)
from .models import Activity, ActivityPage, Issue, Project, ProjectField, ServerInfo, User

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
COUNT_ATTEMPTS = 3
COUNT_RETRY_DELAY = 1.0
PAGE_SIZE = 100

ISSUE_FIELDS = (
    "idReadable,summary,created,updated,resolved,project(shortName),reporter(login),"
    "customFields(name,value(name,login))"
)
PROJECT_FIELDS = "id,name,shortName,archived"
ACTIVITY_CATEGORIES = (
    "IssueCreatedCategory,IssueResolvedCategory,CommentsCategory,CustomFieldCategory"
)
ACTIVITY_FIELDS = (
    "id,timestamp,author(login),added(name,login),removed(name,login),targetMember,"
    "target(idReadable,summary,project(shortName),issue(idReadable,summary,project(shortName)))"
)

_NOT_JSON = object()


def normalize_base_url(url: str) -> str:
    """Return a canonical base URL (scheme, host and optional context path).

    A missing scheme defaults to https. A trailing ``/api`` and trailing slashes
    are removed. Raises ValueError if no usable host is present.
    """
    candidate = url.strip()
    if not candidate:
        raise ValueError("URL is empty")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Not a valid http(s) URL: {url!r}")
    path = parts.path.rstrip("/")
    path = path.removesuffix("/api").rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class YouTrackClient:
    """Client for a single YouTrack instance."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        count_retry_delay: float = COUNT_RETRY_DELAY,
    ) -> None:
        """Create a client; ``base_url`` is normalised."""
        self._session = session
        self._base_url = normalize_base_url(base_url)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._ssl: bool = verify_ssl
        self._count_retry_delay = count_retry_delay

    @property
    def base_url(self) -> str:
        """Return the normalised base URL."""
        return self._base_url

    async def get_server_info(self) -> ServerInfo:
        """Return the server version; also serves as a connection test."""
        data = await self._get_json("/api/config", {"fields": "version,build"})
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise YouTrackResponseError("Server did not report a version")
        build = data.get("build")
        return ServerInfo(version=data["version"], build=build if isinstance(build, str) else None)

    async def get_current_user(self) -> User:
        """Return the user the token belongs to (validates the token)."""
        data = await self._get_json("/api/users/me", {"fields": "login,name"})
        if not isinstance(data, dict) or not isinstance(data.get("login"), str):
            raise YouTrackResponseError("Server did not report the current user")
        name = data.get("name")
        return User(login=data["login"], name=name if isinstance(name, str) else None)

    async def get_projects(self) -> list[Project]:
        """Return all projects visible to the token, including archived ones."""
        projects: list[Project] = []
        skip = 0
        while True:
            page = await self._get_json(
                "/api/admin/projects",
                {"fields": PROJECT_FIELDS, "$top": PAGE_SIZE, "$skip": skip},
            )
            if not isinstance(page, list):
                raise YouTrackResponseError("Expected a list of projects")
            projects.extend(Project.from_json(item) for item in page)
            if len(page) < PAGE_SIZE:
                return projects
            skip += PAGE_SIZE

    async def get_project_fields(self, project_id: str) -> list[ProjectField]:
        """Return the custom fields attached to a project."""
        data = await self._get_json(
            f"/api/admin/projects/{project_id}/customFields",
            {"fields": "field(name,fieldType(id))", "$top": PAGE_SIZE},
        )
        if not isinstance(data, list):
            raise YouTrackResponseError("Expected a list of project fields")
        return [ProjectField.from_json(item) for item in data]

    async def count_issues(self, query: str) -> int | None:
        """Count issues matching ``query``.

        YouTrack answers ``-1`` while it still computes a new query. The call is
        repeated a few times; ``None`` means the count is still unknown.
        """
        for attempt in range(COUNT_ATTEMPTS):
            data = await self._request(
                "POST",
                "/api/issuesGetter/count",
                params={"fields": "count"},
                json={"query": query},
            )
            count = data.get("count") if isinstance(data, dict) else None
            if isinstance(count, bool) or not isinstance(count, int):
                raise YouTrackResponseError("Count response did not contain a number")
            if count >= 0:
                return count
            if attempt < COUNT_ATTEMPTS - 1:
                await asyncio.sleep(self._count_retry_delay)
        return None

    async def get_issues(self, query: str, *, top: int = PAGE_SIZE, skip: int = 0) -> list[Issue]:
        """Return one page of issues matching ``query``, newest update first."""
        page = await self._get_json(
            "/api/issues",
            {"query": query, "fields": ISSUE_FIELDS, "$top": top, "$skip": skip},
        )
        if not isinstance(page, list):
            raise YouTrackResponseError("Expected a list of issues")
        return [Issue.from_json(item) for item in page]

    async def get_activities(
        self, start: datetime, *, top: int = PAGE_SIZE, skip: int = 0, newest_first: bool = False
    ) -> ActivityPage:
        """Return issue activities at or after ``start`` (oldest first unless ``newest_first``).

        Entries that are not issue events (or are malformed) are dropped from ``items`` but still
        count towards ``size``, which is what paging with ``skip`` needs.
        """
        params: dict[str, str | int] = {
            "categories": ACTIVITY_CATEGORIES,
            "fields": ACTIVITY_FIELDS,
            "start": int(start.timestamp() * 1000),
            "$top": top,
            "$skip": skip,
        }
        if newest_first:
            params["reverse"] = "true"
        page = await self._get_json("/api/activities", params)
        if not isinstance(page, list):
            raise YouTrackResponseError("Expected a list of activities")
        activities = (Activity.from_json(item) for item in page if isinstance(item, dict))
        return ActivityPage([a for a in activities if a is not None], len(page))

    async def _get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
    ) -> Any:
        """Send a request and return the decoded JSON body."""
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json,
                headers=self._headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                ssl=self._ssl,
            ) as response:
                return await self._decode(response)
        except TimeoutError as err:
            raise YouTrackConnectionError("Request to YouTrack timed out") from err
        except aiohttp.ClientError as err:
            raise YouTrackConnectionError(f"Cannot connect to YouTrack: {err}") from err

    @staticmethod
    async def _decode(response: aiohttp.ClientResponse) -> Any:
        """Map the HTTP status to an exception or return the JSON body."""
        status = response.status
        if status == 401:
            raise YouTrackAuthError("The token was rejected")
        if status == 403:
            raise YouTrackPermissionError("The token lacks the required permission")
        if status == 404:
            raise YouTrackNotFoundError("Resource not found")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            raise YouTrackRateLimitError(
                "YouTrack is rate limiting requests",
                float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if 300 <= status < 400:
            location = response.headers.get("Location", "unknown location")
            raise YouTrackConnectionError(f"Unexpected redirect to {location}; check the URL")

        try:
            body = await response.json(content_type=None)
        except ValueError:
            body = _NOT_JSON
        if status == 400 and isinstance(body, dict) and body.get("error") == "invalid_query":
            raise YouTrackInvalidQueryError(
                str(body.get("error_description", "Invalid search query"))
            )
        if status >= 400:
            raise YouTrackResponseError(f"Unexpected HTTP status {status}")
        if body is _NOT_JSON:
            raise YouTrackResponseError("Response is not valid JSON; check the URL")
        return body
