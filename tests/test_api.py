"""Tests for the YouTrack API client."""

from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.youtrack.api import YouTrackClient, normalize_base_url
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackInvalidQueryError,
    YouTrackNotFoundError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
    YouTrackResponseError,
)
from custom_components.youtrack.models import Issue

from .common import load_json_fixture, load_text_fixture

BASE = "https://yt.example.com"
TOKEN = "perm:super-secret-token"
JSON = {"Content-Type": "application/json"}
HTML = {"Content-Type": "text/html"}

type ClientFactory = Callable[..., YouTrackClient]


@pytest.fixture
def make_client(hass: HomeAssistant) -> ClientFactory:
    """Return a factory for clients bound to the mocked session."""

    def factory(base_url: str = BASE, **kwargs: object) -> YouTrackClient:
        kwargs.setdefault("count_retry_delay", 0)
        return YouTrackClient(
            async_get_clientsession(hass),
            base_url,
            TOKEN,
            **kwargs,  # type: ignore[arg-type]
        )

    return factory


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("yt.example.com", "https://yt.example.com"),
        ("  https://yt.example.com/  ", "https://yt.example.com"),
        ("https://yt.example.com/api", "https://yt.example.com"),
        ("https://yt.example.com/youtrack/api/", "https://yt.example.com/youtrack"),
        ("http://yt.local:8080/?x=1#frag", "http://yt.local:8080"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    """URLs are reduced to scheme, host and optional context path."""
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://yt.example.com", "https://"])
def test_normalize_base_url_rejects_invalid(raw: str) -> None:
    """Unusable URLs raise ValueError."""
    with pytest.raises(ValueError, match=r"empty|valid"):
        normalize_base_url(raw)


async def test_server_info(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """The version endpoint is parsed and the token is sent as Bearer."""
    aioclient_mock.get(f"{BASE}/api/config", json=load_json_fixture("config.json"))

    info = await make_client().get_server_info()

    assert (info.version, info.build) == ("2026.2", "00000")
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_server_info_without_version(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A payload without a version is a response error."""
    aioclient_mock.get(f"{BASE}/api/config", json={"build": "1"})

    with pytest.raises(YouTrackResponseError):
        await make_client().get_server_info()


async def test_current_user(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The current user is parsed."""
    aioclient_mock.get(f"{BASE}/api/users/me", json=load_json_fixture("me.json"))

    user = await make_client().get_current_user()

    assert (user.login, user.name) == ("example.user", "Example User")


async def test_current_user_without_login(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A payload without a login is a response error."""
    aioclient_mock.get(f"{BASE}/api/users/me", json={"name": "x"})

    with pytest.raises(YouTrackResponseError):
        await make_client().get_current_user()


async def test_projects(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """Projects, including archived ones, are returned."""
    aioclient_mock.get(f"{BASE}/api/admin/projects", json=load_json_fixture("projects.json"))

    projects = await make_client().get_projects()

    assert [p.short_name for p in projects] == ["DEMO", "OPS", "OLD"]
    assert [p.archived for p in projects] == [False, False, True]


async def test_projects_are_paged(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A full page triggers a request for the next one."""
    full_page = [
        {"id": f"0-{i}", "name": f"P{i}", "shortName": f"P{i}", "archived": False}
        for i in range(100)
    ]
    last_page = [{"id": "0-100", "name": "Last", "shortName": "LAST", "archived": False}]
    aioclient_mock.get(f"{BASE}/api/admin/projects?%24skip=0", json=full_page)
    aioclient_mock.get(f"{BASE}/api/admin/projects?%24skip=100", json=last_page)

    projects = await make_client().get_projects()

    assert len(projects) == 101
    assert projects[-1].short_name == "LAST"


async def test_projects_must_be_a_list(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-list payload is a response error."""
    aioclient_mock.get(f"{BASE}/api/admin/projects", json={"oops": True})

    with pytest.raises(YouTrackResponseError):
        await make_client().get_projects()


async def test_project_missing_field(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A project without a short name is a response error."""
    aioclient_mock.get(f"{BASE}/api/admin/projects", json=[{"id": "0-1", "name": "x"}])

    with pytest.raises(YouTrackResponseError):
        await make_client().get_projects()


async def test_count(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """A computed count is returned with a single request."""
    aioclient_mock.post(f"{BASE}/api/issuesGetter/count", json=load_json_fixture("count_ok.json"))

    assert await make_client().count_issues("project: DEMO") == 37
    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][2] == {"query": "project: DEMO"}


async def test_count_retries_while_computing(make_client: ClientFactory) -> None:
    """-1 means 'still computing'; the call is repeated until a value arrives."""
    client = make_client()

    with patch.object(
        client, "_request", AsyncMock(side_effect=[{"count": -1}, {"count": 12}])
    ) as request:
        assert await client.count_issues("project: DEMO") == 12

    assert request.await_count == 2


async def test_count_gives_up_after_retries(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """If the server never finishes, the count is reported as unknown."""
    aioclient_mock.post(
        f"{BASE}/api/issuesGetter/count", json=load_json_fixture("count_computing.json")
    )

    assert await make_client().count_issues("project: DEMO") is None
    assert aioclient_mock.call_count == 3


@pytest.mark.parametrize("payload", [{"count": "5"}, {"count": True}, {}, []])
async def test_count_rejects_malformed_payload(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker, payload: object
) -> None:
    """A count that is not an integer is a response error."""
    aioclient_mock.post(f"{BASE}/api/issuesGetter/count", json=payload)

    with pytest.raises(YouTrackResponseError):
        await make_client().count_issues("x")


async def test_issues(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """Issues are parsed including custom fields."""
    aioclient_mock.get(f"{BASE}/api/issues", json=load_json_fixture("issues.json"))

    issues = await make_client().get_issues("project: DEMO", top=50)

    open_issue, resolved_issue = issues
    assert open_issue.id == "DEMO-2"
    assert (open_issue.state, open_issue.priority, open_issue.type) == (
        "In Progress",
        "Major",
        "Task",
    )
    assert open_issue.assignee == "example.user"
    assert open_issue.project == "DEMO"
    assert open_issue.reporter == "example.user"
    assert not open_issue.is_resolved
    assert resolved_issue.is_resolved
    assert resolved_issue.assignee is None
    assert resolved_issue.resolved is not None
    assert resolved_issue.resolved.year == 2026
    assert aioclient_mock.mock_calls[0][1].query["$top"] == "50"


async def test_issues_must_be_a_list(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-list payload is a response error."""
    aioclient_mock.get(f"{BASE}/api/issues", json={})

    with pytest.raises(YouTrackResponseError):
        await make_client().get_issues("x")


@pytest.mark.parametrize(
    "payload",
    [
        {"idReadable": "A-1"},
        {"idReadable": "A-1", "summary": "s", "created": "yesterday"},
        {"idReadable": "A-1", "summary": "s", "created": True},
    ],
)
async def test_issue_with_invalid_payload(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker, payload: dict[str, object]
) -> None:
    """Missing mandatory fields or bad timestamps are response errors."""
    aioclient_mock.get(f"{BASE}/api/issues", json=[payload])

    with pytest.raises(YouTrackResponseError):
        await make_client().get_issues("x")


async def test_issue_tolerates_missing_optional_fields(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Projects without State or Assignee fields still parse."""
    aioclient_mock.get(f"{BASE}/api/issues", json=[{"idReadable": "A-1", "summary": "s"}])

    (issue,) = await make_client().get_issues("x")

    assert issue.state is None
    assert issue.assignee is None
    assert issue.created is None


@pytest.mark.parametrize(
    ("status", "headers", "exception"),
    [
        (401, JSON, YouTrackAuthError),
        (403, JSON, YouTrackPermissionError),
        (404, HTML, YouTrackNotFoundError),
        (500, HTML, YouTrackResponseError),
        (302, {"Location": "https://other.example.com/login"}, YouTrackConnectionError),
    ],
)
async def test_status_mapping(
    make_client: ClientFactory,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    headers: dict[str, str],
    exception: type[Exception],
) -> None:
    """HTTP status codes map to dedicated exceptions."""
    aioclient_mock.get(f"{BASE}/api/config", status=status, text="x", headers=headers)

    with pytest.raises(exception):
        await make_client().get_server_info()


async def test_not_found_html_is_not_parsed_as_json(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The HTML 404 page of YouTrack maps to NotFound instead of a decode error."""
    aioclient_mock.get(
        f"{BASE}/api/config", status=404, text=load_text_fixture("not_found.html"), headers=HTML
    )

    with pytest.raises(YouTrackNotFoundError):
        await make_client().get_server_info()


async def test_rate_limit_with_retry_after(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """429 carries the Retry-After hint."""
    aioclient_mock.get(f"{BASE}/api/config", status=429, text="", headers={"Retry-After": "7"})

    with pytest.raises(YouTrackRateLimitError) as err:
        await make_client().get_server_info()

    assert err.value.retry_after == 7.0


async def test_rate_limit_without_retry_after(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """429 without a usable header has no hint."""
    aioclient_mock.get(f"{BASE}/api/config", status=429, text="", headers={"Retry-After": "soon"})

    with pytest.raises(YouTrackRateLimitError) as err:
        await make_client().get_server_info()

    assert err.value.retry_after is None


async def test_invalid_query(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """400 invalid_query carries the server message."""
    aioclient_mock.post(
        f"{BASE}/api/issuesGetter/count",
        status=400,
        json=load_json_fixture("invalid_query.json"),
    )

    with pytest.raises(YouTrackInvalidQueryError, match="Can't parse search query"):
        await make_client().count_issues("((")


async def test_other_bad_request(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 400 that is not an invalid query is a generic response error."""
    aioclient_mock.get(f"{BASE}/api/config", status=400, json={"error": "bad_request"})

    with pytest.raises(YouTrackResponseError):
        await make_client().get_server_info()


async def test_non_json_success_response(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """HTML returned with 200 (wrong URL, proxy page) is reported."""
    aioclient_mock.get(f"{BASE}/api/config", text="<html></html>", headers=HTML)

    with pytest.raises(YouTrackResponseError, match="not valid JSON"):
        await make_client().get_server_info()


async def test_invalid_json_body(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A body that claims JSON but is not decodable is reported."""
    aioclient_mock.get(f"{BASE}/api/config", text="{not json", headers=JSON)

    with pytest.raises(YouTrackResponseError, match="not valid JSON"):
        await make_client().get_server_info()


async def test_timeout(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """A timeout becomes a connection error."""
    aioclient_mock.get(f"{BASE}/api/config", exc=TimeoutError())

    with pytest.raises(YouTrackConnectionError, match="timed out"):
        await make_client().get_server_info()


async def test_client_error_does_not_leak_token(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Connection failures are wrapped and never contain the token."""
    aioclient_mock.get(f"{BASE}/api/config", exc=aiohttp.ClientError("boom"))

    with pytest.raises(YouTrackConnectionError) as err:
        await make_client().get_server_info()

    assert TOKEN not in str(err.value)


async def test_base_url_is_normalised(make_client: ClientFactory) -> None:
    """The client exposes the canonical base URL."""
    assert make_client("yt.example.com/api/").base_url == BASE


def test_issue_ignores_unusable_custom_fields() -> None:
    """Malformed custom field entries and non-string values are skipped."""
    issue = Issue.from_json(
        {
            "idReadable": "A-1",
            "summary": "s",
            "customFields": [
                "garbage",
                {"value": {"name": "no field name"}},
                {"name": "State", "value": ["multi", "value"]},
                {"name": "Priority", "value": {"name": 5}},
            ],
        }
    )

    assert issue.state is None
    assert issue.priority is None


async def test_project_fields(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The custom fields of a project are listed with their types."""
    aioclient_mock.get(
        f"{BASE}/api/admin/projects/0-1/customFields", json=load_json_fixture("project_fields.json")
    )

    fields = await make_client().get_project_fields("0-1")

    assert [(f.name, f.field_type) for f in fields] == [
        ("Priority", "enum[1]"),
        ("State", "state[1]"),
        ("Assignee", "user[1]"),
        ("Due Date", "date"),
    ]


@pytest.mark.parametrize("payload", [{}, [{"nofield": 1}], [{"field": {"fieldType": {"id": "x"}}}]])
async def test_project_fields_malformed(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker, payload: object
) -> None:
    """Unexpected payloads are response errors."""
    aioclient_mock.get(f"{BASE}/api/admin/projects/0-1/customFields", json=payload)

    with pytest.raises(YouTrackResponseError):
        await make_client().get_project_fields("0-1")


async def test_project_field_without_type(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A field without type information gets an empty type."""
    aioclient_mock.get(
        f"{BASE}/api/admin/projects/0-1/customFields", json=[{"field": {"name": "Odd"}}]
    )

    (field,) = await make_client().get_project_fields("0-1")

    assert field.field_type == ""
