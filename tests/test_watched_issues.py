"""Tests for the opt-in sensors of watched issues."""

from collections.abc import Callable
from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.config_entries import SubentryFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_WATCH,
    DOMAIN,
    MAX_WATCHED,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.exceptions import YouTrackInvalidQueryError
from custom_components.youtrack.models import Issue
from custom_components.youtrack.sensor import YouTrackWatchedIssueSensor

from .common import load_json_fixture
from .conftest import TOKEN, URL

WATCH_PREFIX = "watch_"
WATCH = "tag: watch-me"
SCOPED_WATCH = "project: {DEMO} and (tag: watch-me and #Unresolved)"


def _entry(watch: str | None = WATCH) -> MockConfigEntry:
    data: dict[str, object] = {CONF_PROJECT_ID: "0-1", CONF_PROJECT_KEY: "DEMO"}
    if watch is not None:
        data[CONF_WATCH] = watch
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={"url": URL, "api_token": TOKEN, "verify_ssl": True},
        subentries_data=[
            {
                "data": data,
                "subentry_id": "sub-demo",
                "subentry_type": SUBENTRY_PROJECT,
                "title": "Demo Project",
                "unique_id": "0-1",
            }
        ],
    )


def _issues(*ids: str) -> list[Issue]:
    payload = load_json_fixture("issues.json")[0]
    return [
        Issue.from_json({**payload, "idReadable": issue_id, "summary": f"Task {issue_id}"})
        for issue_id in ids
    ]


def _answer(by_query: dict[str, list[Issue]]) -> Callable[..., list[Issue]]:
    """Return a ``get_issues`` replacement that answers the watch search and nothing else."""

    async def get_issues(query: str, **_: object) -> list[Issue]:
        return by_query.get(query, [])

    return get_issues  # type: ignore[return-value]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinators["sub-demo"].async_refresh()
    await hass.async_block_till_done()


async def test_each_watched_issue_gets_a_sensor(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """State is the issue's state; details are attributes."""
    mock_client.get_issues.side_effect = _answer({SCOPED_WATCH: _issues("DEMO-5", "DEMO-6")})
    await _setup(hass, _entry())

    state = hass.states.get("sensor.demo_project_demo_5")
    assert state is not None
    assert state.state == "In Progress"
    assert state.attributes["summary"] == "Task DEMO-5"
    assert state.attributes["priority"] == "Major"
    assert state.attributes["assignee"] == "example.user"
    assert state.attributes["url"] == f"{URL}/issue/DEMO-5"
    assert hass.states.get("sensor.demo_project_demo_6") is not None


async def test_nothing_is_watched_by_default(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Without a watch search there are no issue sensors and no extra request."""
    await _setup(hass, _entry(watch=None))

    assert not [
        s for s in hass.states.async_entity_ids("sensor") if "demo_" in s.rsplit("_", 1)[-1]
    ]
    queries = [call.args[0] for call in mock_client.get_issues.await_args_list]
    assert SCOPED_WATCH not in queries


async def test_resolved_issues_lose_their_sensor_and_new_ones_get_one(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """The set of sensors follows the search result."""
    answers = {SCOPED_WATCH: _issues("DEMO-5", "DEMO-6")}
    mock_client.get_issues.side_effect = _answer(answers)
    entry = _entry()
    await _setup(hass, entry)

    answers[SCOPED_WATCH] = _issues("DEMO-6", "DEMO-7")
    await _poll(hass, entry)

    registry = er.async_get(hass)
    assert hass.states.get("sensor.demo_project_demo_5") is None
    assert registry.async_get("sensor.demo_project_demo_5") is None
    assert hass.states.get("sensor.demo_project_demo_6") is not None
    assert hass.states.get("sensor.demo_project_demo_7") is not None


async def test_state_follows_the_issue(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A change in YouTrack shows in the sensor on the next poll."""
    answers = {SCOPED_WATCH: _issues("DEMO-5")}
    mock_client.get_issues.side_effect = _answer(answers)
    entry = _entry()
    await _setup(hass, entry)

    (issue,) = _issues("DEMO-5")
    answers[SCOPED_WATCH] = (
        [Issue(**{**vars(issue), "state": "Done"})] if False else _done("DEMO-5")
    )
    await _poll(hass, entry)

    assert hass.states.get("sensor.demo_project_demo_5").state == "Done"  # type: ignore[union-attr]


def _done(issue_id: str) -> list[Issue]:
    payload = load_json_fixture("issues.json")[1]  # the fixture's issue in state Done
    return [Issue.from_json({**payload, "idReadable": issue_id})]


async def test_sensors_survive_a_failed_poll_with_old_values(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A rejected search keeps the last result instead of deleting every sensor."""
    answers = {SCOPED_WATCH: _issues("DEMO-5")}
    mock_client.get_issues.side_effect = _answer(answers)
    entry = _entry()
    await _setup(hass, entry)

    async def reject(query: str, **_: object) -> list[Issue]:
        raise YouTrackInvalidQueryError("nope")

    mock_client.get_issues.side_effect = reject
    mock_client.count_issues.side_effect = YouTrackInvalidQueryError("nope")
    await _poll(hass, entry)

    assert hass.states.get("sensor.demo_project_demo_5") is not None


async def test_more_than_the_limit_is_cut(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The search asks for at most MAX_WATCHED issues."""
    mock_client.get_issues.side_effect = _answer({})
    await _setup(hass, _entry())

    watch_call = next(
        call for call in mock_client.get_issues.await_args_list if call.args[0] == SCOPED_WATCH
    )
    assert watch_call.kwargs["top"] == MAX_WATCHED


async def _reconfigure_watch(
    hass: HomeAssistant, entry: MockConfigEntry, text: str
) -> SubentryFlowResult:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROJECT),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": "sub-demo"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "watch_issues"}
    )
    assert result["step_id"] == "watch_issues"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_WATCH: text}
    )


async def test_flow_saves_the_watch_search(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A search YouTrack accepts is stored and turns the sensors on."""
    entry = _entry(watch=None)
    await _setup(hass, entry)
    mock_client.get_issues.side_effect = _answer({SCOPED_WATCH: _issues("DEMO-5")})

    result = await _reconfigure_watch(hass, entry, WATCH)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert entry.subentries["sub-demo"].data[CONF_WATCH] == WATCH
    mock_client.count_issues.assert_any_await(SCOPED_WATCH)
    assert hass.states.get("sensor.demo_project_demo_5") is not None


async def test_flow_clears_the_watch_search(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """An empty search switches the feature off and removes the sensors."""
    mock_client.get_issues.side_effect = _answer({SCOPED_WATCH: _issues("DEMO-5")})
    entry = _entry()
    await _setup(hass, entry)
    assert hass.states.get("sensor.demo_project_demo_5") is not None

    await _reconfigure_watch(hass, entry, "")
    await hass.async_block_till_done()

    assert entry.subentries["sub-demo"].data[CONF_WATCH] == ""
    assert hass.states.get("sensor.demo_project_demo_5") is None


async def test_flow_rejects_bad_searches(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A search YouTrack refuses stays in the form with the reason."""
    entry = _entry(watch=None)
    await _setup(hass, entry)
    mock_client.count_issues.side_effect = YouTrackInvalidQueryError("bad syntax")

    result = await _reconfigure_watch(hass, entry, "((")

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WATCH: "invalid_query"}
    assert CONF_WATCH not in entry.subentries["sub-demo"].data


async def test_rejected_watch_search_creates_no_sensors(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """If YouTrack refuses the stored search, the issue sensors wait for a good result."""
    fake = mock_client.count_issues.side_effect

    async def count(query: str) -> int:
        if query == SCOPED_WATCH:
            raise YouTrackInvalidQueryError("nope")
        return int(await fake(query))

    mock_client.count_issues.side_effect = count
    entry = _entry()
    await _setup(hass, entry)

    assert hass.states.get("sensor.demo_project_demo_5") is None
    assert mock_client.get_issues.await_count > 0


async def test_sensor_of_a_vanished_issue_is_unavailable_and_empty(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Between the poll and the cleanup a sensor does not show stale details."""
    entry = _entry()
    await _setup(hass, entry)
    sensor = YouTrackWatchedIssueSensor(entry.runtime_data.coordinators["sub-demo"], "DEMO-99")

    assert not sensor.available
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
