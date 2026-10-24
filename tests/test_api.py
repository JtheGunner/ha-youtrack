"""Tests for the YouTrack API client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.youtrack.api import normalize_base_url
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackBadRequestError,
    YouTrackConnectionError,
    YouTrackInvalidQueryError,
    YouTrackNotFoundError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
    YouTrackResponseError,
)
from custom_components.youtrack.models import Issue

from .common import load_json_fixture, load_text_fixture
from .conftest import TOKEN, URL, ClientFactory

BASE = URL
JSON = {"Content-Type": "application/json"}
HTML = {"Content-Type": "text/html"}


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
    """A 400 that is not an invalid query is a bad request."""
    aioclient_mock.get(f"{BASE}/api/config", status=400, json={"error": "bad_request"})

    with pytest.raises(YouTrackBadRequestError):
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


async def test_create_issue(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The project and text are sent; the readable id comes back."""
    aioclient_mock.post(f"{BASE}/api/issues", json={"idReadable": "DEMO-9"}, headers=JSON)

    issue_id = await make_client().create_issue("0-1", "Title", "Body")

    assert issue_id == "DEMO-9"
    body = aioclient_mock.mock_calls[0][2]
    assert body == {"project": {"id": "0-1"}, "summary": "Title", "description": "Body"}


async def test_create_issue_without_description_and_id(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A response without id is an error; the description is optional."""
    aioclient_mock.post(f"{BASE}/api/issues", json={}, headers=JSON)

    with pytest.raises(YouTrackResponseError):
        await make_client().create_issue("0-1", "Title")

    assert aioclient_mock.mock_calls[0][2] == {"project": {"id": "0-1"}, "summary": "Title"}


async def test_add_comment(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """The text is posted to the issue's comments."""
    aioclient_mock.post(f"{BASE}/api/issues/DEMO-1/comments", json={"id": "4-1"}, headers=JSON)

    await make_client().add_comment("DEMO-1", "Hello")

    assert aioclient_mock.mock_calls[0][2] == {"text": "Hello"}


async def test_run_command_accepts_an_empty_answer(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Commands may answer with an empty body."""
    aioclient_mock.post(f"{BASE}/api/commands", text="")

    await make_client().run_command("DEMO-1", "State Done")

    assert aioclient_mock.mock_calls[0][2] == {
        "query": "State Done",
        "issues": [{"idReadable": "DEMO-1"}],
    }


async def test_rejected_command(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A command YouTrack does not understand is a bad request with its explanation."""
    aioclient_mock.post(
        f"{BASE}/api/commands",
        status=400,
        json={"error": "bad_request", "error_description": "Unknown command: Bogus"},
        headers=JSON,
    )

    with pytest.raises(YouTrackBadRequestError, match="Unknown command"):
        await make_client().run_command("DEMO-1", "Bogus")


async def test_log_work(make_client: ClientFactory, aioclient_mock: AiohttpClientMocker) -> None:
    """Duration, text, date and type are sent."""
    aioclient_mock.post(
        f"{BASE}/api/issues/DEMO-1/timeTracking/workItems", json={"id": "1"}, headers=JSON
    )

    await make_client().log_work(
        "DEMO-1", "1h", text="Done", date=datetime(2026, 10, 24, tzinfo=UTC), work_type="Dev"
    )

    assert aioclient_mock.mock_calls[0][2] == {
        "duration": {"presentation": "1h"},
        "text": "Done",
        "date": 1792800000000,
        "type": {"name": "Dev"},
    }


async def test_log_work_minimal(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only the duration is mandatory."""
    aioclient_mock.post(
        f"{BASE}/api/issues/DEMO-1/timeTracking/workItems", json={"id": "1"}, headers=JSON
    )

    await make_client().log_work("DEMO-1", "5m")

    assert aioclient_mock.mock_calls[0][2] == {"duration": {"presentation": "5m"}}


async def test_bad_request_without_description(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A bare 400 still becomes a bad request error."""
    aioclient_mock.post(f"{BASE}/api/issues", status=400, json={}, headers=JSON)

    with pytest.raises(YouTrackBadRequestError, match="Bad request"):
        await make_client().create_issue("0-1", "Title")


async def test_get_articles(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Articles are parsed and the query and paging parameters are sent."""
    aioclient_mock.get(
        f"{BASE}/api/articles", json=load_json_fixture("articles.json"), headers=JSON
    )

    articles = await make_client().get_articles(query="project: DEMO heating", top=20, skip=40)

    child, root = articles
    assert (child.readable_id, child.parent_id, child.has_children) == (
        "DEMO-A-2",
        "DEMO-A-1",
        False,
    )
    assert (root.readable_id, root.parent_id, root.has_children) == ("DEMO-A-1", None, True)
    assert child.project == "DEMO"
    assert child.author == "example.user"
    assert child.content is None
    query = aioclient_mock.mock_calls[0][1].query
    assert (query["query"], query["$top"], query["$skip"]) == ("project: DEMO heating", "20", "40")
    assert "content" not in query["fields"]


async def test_get_articles_without_query_and_with_bad_payload(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The query is optional; a non-list answer is an error."""
    aioclient_mock.get(f"{BASE}/api/articles", json={}, headers=JSON)

    with pytest.raises(YouTrackResponseError):
        await make_client().get_articles()

    assert "query" not in aioclient_mock.mock_calls[0][1].query


async def test_get_article_includes_markdown_content(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A single article carries its Markdown content."""
    aioclient_mock.get(
        f"{BASE}/api/articles/DEMO-A-2", json=load_json_fixture("article.json"), headers=JSON
    )

    article = await make_client().get_article("DEMO-A-2")

    assert article.content == "# Heating manual\n\nBleed the radiators once a year."
    assert "content" in aioclient_mock.mock_calls[0][1].query["fields"]


async def test_get_article_errors(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Unknown articles are not found; a list instead of an article is an error."""
    aioclient_mock.get(
        f"{BASE}/api/articles/DEMO-A-9", status=404, text="<html></html>", headers=HTML
    )
    aioclient_mock.get(f"{BASE}/api/articles/DEMO-A-8", json=[], headers=JSON)

    with pytest.raises(YouTrackNotFoundError):
        await make_client().get_article("DEMO-A-9")
    with pytest.raises(YouTrackResponseError):
        await make_client().get_article("DEMO-A-8")


async def test_article_with_invalid_payload(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """Articles need an id, a readable id and a summary."""
    aioclient_mock.get(
        f"{BASE}/api/articles", json=[{"id": "1", "idReadable": "A-A-1"}], headers=JSON
    )

    with pytest.raises(YouTrackResponseError):
        await make_client().get_articles()


async def test_get_all_articles_pages_until_the_last_page(make_client: ClientFactory) -> None:
    """All pages are read."""
    client = make_client()
    full = [object()] * 100
    with patch.object(
        client, "get_articles", AsyncMock(side_effect=[full, full[:7]])
    ) as get_articles:
        articles, capped = await client.get_all_articles(query="project: DEMO")

    assert (len(articles), capped) == (107, False)
    assert [call.kwargs["skip"] for call in get_articles.await_args_list] == [0, 100]


async def test_get_all_articles_stops_at_the_cap(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Huge knowledge bases are reported as capped."""
    monkeypatch.setattr("custom_components.youtrack.api.MAX_LISTED_ARTICLES", 200)
    client = make_client()
    with patch.object(client, "get_articles", AsyncMock(return_value=[object()] * 100)):
        articles, capped = await client.get_all_articles()

    assert (len(articles), capped) == (200, True)
