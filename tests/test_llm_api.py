"""Tests for the LLM API that exposes YouTrack to Assist."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import (
    CONF_LLM_CREATE,
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    DOMAIN,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.exceptions import YouTrackConnectionError
from custom_components.youtrack.models import Article, Issue

from .common import load_json_fixture
from .conftest import URL

CONTEXT = llm.LLMContext(
    platform="test", context=None, language="en", assistant="conversation", device_id=None
)


async def _api(hass: HomeAssistant, entry: MockConfigEntry) -> llm.APIInstance:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await llm.async_get_api(hass, f"{DOMAIN}_{entry.entry_id}", CONTEXT)


async def _call(api: llm.APIInstance, tool: str, **args: Any) -> dict[str, Any]:
    # The tool is called directly; ``async_call_tool`` only adds a trace through the conversation
    # integration, which is not part of the test requirements.
    target = next(item for item in api.tools if item.name == tool)
    result = await target.async_call(
        api.api.hass, llm.ToolInput(tool_name=tool, tool_args=args), CONTEXT
    )
    assert isinstance(result, llm.ToolResult)
    return dict(result.data)


def _two_projects(entry: MockConfigEntry) -> MockConfigEntry:
    entry.subentries  # noqa: B018 - make sure the entry is built before reading it
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data=dict(entry.data),
        subentries_data=[
            {
                "data": {CONF_PROJECT_ID: f"0-{n}", CONF_PROJECT_KEY: key},
                "subentry_id": f"sub-{key}",
                "subentry_type": SUBENTRY_PROJECT,
                "title": key,
                "unique_id": f"0-{n}",
            }
            for n, key in ((1, "DEMO"), (2, "OPS"))
        ],
    )


async def test_read_tools_only_by_default(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Issue creation is not offered unless the options allow it."""
    api = await _api(hass, mock_project_entry)

    assert [tool.name for tool in api.tools] == [
        "search_youtrack_issues",
        "search_youtrack_articles",
        "get_youtrack_article",
    ]
    assert all(tool.annotations.read_only for tool in api.tools)
    assert all(tool.integration == DOMAIN for tool in api.tools)
    assert "DEMO" in api.api_prompt
    assert "create issues" not in api.api_prompt


async def test_create_tool_needs_the_option(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Allowing it adds exactly one writing tool."""
    mock_project_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_project_entry, options={CONF_LLM_CREATE: True})
    api = await _api(hass, mock_project_entry)

    writing = [tool for tool in api.tools if not tool.annotations.read_only]
    assert [tool.name for tool in writing] == ["create_youtrack_issue"]
    assert "create issues" in api.api_prompt


async def test_search_issues_is_scoped_to_the_project(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The search is limited to the configured project and returns summaries with links."""
    api = await _api(hass, mock_project_entry)

    data = await _call(api, "search_youtrack_issues", query="pump", limit=2)

    mock_client.get_issues.assert_awaited_with("project: {DEMO} and (pump)", top=2)
    assert data["issues"][0] == {
        "id": "DEMO-2",
        "summary": "Second issue",
        "state": "In Progress",
        "priority": "Major",
        "assignee": "example.user",
        "url": f"{URL}/issue/DEMO-2",
    }
    assert len(data["issues"]) == 2


async def test_search_covers_all_projects_unless_one_is_named(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Without a project every configured project is searched; a named one narrows it."""
    api = await _api(hass, _two_projects(mock_project_entry))

    await _call(api, "search_youtrack_issues", query="pump", limit=3)
    queried = {call.args[0] for call in mock_client.get_issues.await_args_list}
    assert {"project: {DEMO} and (pump)", "project: {OPS} and (pump)"} <= queried

    mock_client.get_issues.reset_mock()
    await _call(api, "search_youtrack_issues", query="pump", project="ops")
    assert [call.args[0] for call in mock_client.get_issues.await_args_list] == [
        "project: {OPS} and (pump)"
    ]


async def test_unknown_project_is_refused(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Projects that are not set up cannot be reached through the assistant."""
    api = await _api(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="not set up"):
        await _call(api, "search_youtrack_issues", query="x", project="SECRET")


async def test_search_articles(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Articles come back newest first with their link."""
    api = await _api(hass, mock_project_entry)

    data = await _call(api, "search_youtrack_articles", query="heating")

    mock_client.get_articles.assert_awaited_with(query="project: {DEMO} and (heating)", top=5)
    assert [item["id"] for item in data["articles"]] == ["DEMO-A-2", "DEMO-A-1"]
    assert data["articles"][0]["url"] == f"{URL}/articles/DEMO-A-2"


async def test_get_article_cuts_long_text(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The article text is limited to the requested length."""
    api = await _api(hass, mock_project_entry)
    mock_client.get_article.return_value = _article("x" * 300)

    data = await _call(api, "get_youtrack_article", article_id="DEMO-A-2", max_length=100)

    assert data["content"] == "x" * 100
    assert data["truncated"] is True
    assert data["title"] == "Heating manual"


async def test_get_article_without_text(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """An empty article is an empty string, not an error."""
    api = await _api(hass, mock_project_entry)
    mock_client.get_article.return_value = _article(None)

    data = await _call(api, "get_youtrack_article", article_id="DEMO-A-2")

    assert data["content"] == ""
    assert data["truncated"] is False


async def test_articles_of_other_projects_stay_hidden(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """An article id from a project that is not set up looks like a missing article."""
    api = await _api(hass, mock_project_entry)
    mock_client.get_article.return_value = _article("secret", project="SECRET")

    with pytest.raises(ServiceValidationError, match="could not find"):
        await _call(api, "get_youtrack_article", article_id="SECRET-A-1")


async def test_bad_article_ids_are_refused(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Path tricks never reach the API."""
    api = await _api(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="not valid"):
        await _call(api, "get_youtrack_article", article_id="../issues")
    mock_client.get_article.assert_not_awaited()


async def test_create_issue_in_the_only_project(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """With one project there is nothing to choose."""
    mock_project_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_project_entry, options={CONF_LLM_CREATE: True})
    api = await _api(hass, mock_project_entry)
    mock_client.create_issue.return_value = "DEMO-12"

    data = await _call(
        api, "create_youtrack_issue", summary=" Pump is noisy ", description="Monday"
    )

    mock_client.create_issue.assert_awaited_once_with("0-1", "Pump is noisy", "Monday")
    assert data == {"id": "DEMO-12", "url": f"{URL}/issue/DEMO-12"}


async def test_create_issue_needs_a_project_when_there_are_several(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The assistant must say where the issue goes, and the summary must not be blank."""
    entry = _two_projects(mock_project_entry)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_LLM_CREATE: True})
    api = await _api(hass, entry)

    with pytest.raises(ServiceValidationError, match="which project"):
        await _call(api, "create_youtrack_issue", summary="Pump")
    with pytest.raises(ServiceValidationError, match="summary"):
        await _call(api, "create_youtrack_issue", summary="  ", project="OPS")
    mock_client.create_issue.assert_not_awaited()


async def test_api_errors_are_readable(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Network problems become errors the assistant can report."""
    api = await _api(hass, mock_project_entry)
    mock_client.get_issues.side_effect = YouTrackConnectionError("down")

    with pytest.raises(HomeAssistantError):
        await _call(api, "search_youtrack_issues", query="x")


async def test_api_is_removed_with_the_entry(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Unloading the entry takes its tools away."""
    await _api(hass, mock_project_entry)
    api_id = f"{DOMAIN}_{mock_project_entry.entry_id}"

    await hass.config_entries.async_unload(mock_project_entry.entry_id)

    with pytest.raises(HomeAssistantError, match="not found"):
        await llm.async_get_api(hass, api_id, CONTEXT)


def _article(content: str | None, *, project: str = "DEMO") -> Article:
    item = {
        **load_json_fixture("articles.json")[0],
        "content": content,
        "project": {"shortName": project},
    }
    return Article.from_json(item)


@pytest.mark.parametrize("tool", ["search_youtrack_issues", "search_youtrack_articles"])
@pytest.mark.parametrize("query", ["x) or (project: SECRET", "x} or {a", "(("])
async def test_queries_that_could_leave_the_project_are_refused(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    tool: str,
    query: str,
) -> None:
    """Unbalanced brackets are rejected before anything is sent to YouTrack."""
    api = await _api(hass, mock_project_entry)
    mock_client.get_issues.reset_mock()
    mock_client.get_articles.reset_mock()

    with pytest.raises(ServiceValidationError, match="unbalanced"):
        await _call(api, tool, query=query)
    mock_client.get_issues.assert_not_awaited()
    mock_client.get_articles.assert_not_awaited()


async def test_results_from_other_projects_are_dropped(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Even if YouTrack returned foreign items, they never reach the assistant."""
    api = await _api(hass, mock_project_entry)
    foreign_issue = Issue.from_json(
        {
            **load_json_fixture("issues.json")[0],
            "idReadable": "SECRET-1",
            "project": {"shortName": "SECRET"},
        }
    )
    mock_client.get_issues.return_value = [foreign_issue]
    mock_client.get_articles.return_value = [_article("x", project="SECRET")]

    assert (await _call(api, "search_youtrack_issues", query="x"))["issues"] == []
    assert (await _call(api, "search_youtrack_articles", query="x"))["articles"] == []
