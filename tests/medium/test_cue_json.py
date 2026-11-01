"""Copy JSON with the keys macros read."""

from collections.abc import Callable
from typing import Any

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.exporters import cue_json
from cues.services import demo
from tests import factories

pytestmark = pytest.mark.django_db

# Every key macros read, in order.
MACRO_KEYS = [
    "sequence_number",
    "cue_count",
    "cue_number",
    "title",
    "cue_number_and_title",
    "cue_number_and_title_with_cue_version",
    "cue_number_with_production_number",
    "cue_number_and_title_with_production_number",
    "cue_number_and_title_with_cue_version_and_production_number",
    "cue_version",
    "cue_version_without_v_prefix",
    "in",
    "out",
    "duration",
    "duration_seconds",
    "sequencer_start",
    "version_in",
    "version_out",
    "version_duration",
    "version_duration_seconds",
    "version_sequencer_start",
    "start_offset",
    "duration_difference",
    "status",
    "current_picture",
    "current_picture_name",
    "current_picture_version",
    "current_picture_in",
    "current_picture_out",
    "current_picture_cue_start_offset",
    "current_picture_cue_version_start_offset",
    "written_to_picture",
    "written_to_picture_name",
    "written_to_picture_version",
    "written_to_picture_in",
    "written_to_picture_out",
    "written_to_picture_cue_start_offset",
    "written_to_picture_cue_version_start_offset",
    "frame_rate",
    "series_title",
    "series_production_number",
    "production_number",
    "project_title",
    "project_sub_title",
    "project_title_with_sub_title",
    "season",
    "episode_number",
    "season_and_episode_number",
    "custom",
]


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    return demo.build_demo().project


@pytest.fixture(name="viewer")
def fixture_viewer(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.VIEWER)


def _get(client: Client, **params: Any) -> Any:
    return client.get(reverse("cues:api_cues_json"), params)


def test_keys_match_what_macros_read(
    viewer: Client, project: models.Project
) -> None:
    cue = project.cues.get(number="2m04")
    data = _get(viewer, ids=str(cue.pk)).json()
    assert list(data[0]) == MACRO_KEYS


def test_values(viewer: Client, project: models.Project) -> None:
    cue = project.cues.get(number="2m04")  # v1 on c0917, picture now c1030
    entry = _get(viewer, ids=str(cue.pk)).json()[0]
    assert entry["cue_count"] == 1
    assert entry["cue_number_and_title"] == "2m04 Keeper's Theme"
    assert entry[
        "cue_number_and_title_with_cue_version_and_production_number"
    ] == ("DEMO101 2m04 Keeper's Theme (v1)")
    assert entry["cue_version_without_v_prefix"] == "1"
    assert entry["in"] == "02:09:15:18"
    assert entry["version_in"] == "02:09:13:06"
    assert entry["duration"] == "2:47"
    assert entry["duration_seconds"] == 167
    assert entry["start_offset"] == "00:00:02:12"
    assert entry["duration_difference"] == "00:00:00:00"
    assert entry["sequencer_start"] == ""
    assert entry["current_picture"] == "2 - c1030"
    assert entry["current_picture_version"] == "c1030"
    assert entry["written_to_picture"] == "2 - c0917"
    assert entry["written_to_picture_name"] == "2"
    assert entry["frame_rate"] == "23.976"
    assert entry["production_number"] == "DEMO101"
    assert entry["project_title_with_sub_title"] == "The Lighthouse Keeper"
    assert entry["custom"] == {}
    assert isinstance(entry["sequence_number"], int)


def test_several_cues_in_the_given_order(
    viewer: Client, project: models.Project
) -> None:
    first, second = (
        project.cues.get(number="3m01"),
        project.cues.get(number="1m01"),
    )
    data = _get(viewer, ids=f"{first.pk},{second.pk},999999").json()
    assert [(d["cue_number"], d["cue_count"]) for d in data] == [
        ("3m01", 1),
        ("1m01", 2),
    ]


def test_a_chosen_version(viewer: Client, project: models.Project) -> None:
    cue = project.cues.get(number="2m04")
    older = models.CueVersion.objects.create(
        cue=cue, label="v0.9", tc_in=1, tc_out=None
    )
    entry = _get(viewer, ids=str(cue.pk), version=str(older.pk)).json()[0]
    assert entry["cue_version"] == "v0.9"
    assert (
        entry["version_duration"] == ""
        and entry["version_duration_seconds"] == ""
    )
    assert entry["written_to_picture"] == ""
    assert entry["current_picture_cue_version_start_offset"].startswith("-")


def test_bad_requests(viewer: Client, project: models.Project) -> None:
    cue, other = project.cues.get(number="1m01"), factories.CueFactory.create()
    assert _get(viewer).status_code == 400
    assert _get(viewer, ids=",".join(["1"] * 501)).status_code == 400
    assert _get(viewer, ids=f"{cue.pk},{other.pk}").status_code == 400
    two = f"{cue.pk},{project.cues.get(number='1m02').pk}"
    response = _get(viewer, ids=two, version="1")
    assert response.json()["error"] == "A version needs exactly one cue."
    assert _get(viewer, ids=str(cue.pk), version="999999").status_code == 404


def test_series_episodes_and_drop_frame() -> None:
    series = models.Series.objects.create(
        title="Broken Halo", production_number="BH"
    )
    project = factories.ProjectFactory.create(
        title="Broken Halo 203",
        sub_title="Pilot",
        production_number="BH203",
        series=series,
        season="2",
        episode_number="3",
        frame_rate="29.97",
        drop_frame=True,
    )
    cue = factories.CueFactory.create(project=project, title="")
    entry = cue_json.entries([(cue, None)], project)[0]
    assert entry["frame_rate"] == "29.97 DF"
    assert (entry["series_title"], entry["series_production_number"]) == (
        "Broken Halo",
        "BH",
    )
    assert entry["season_and_episode_number"] == "203"
    assert entry["project_title_with_sub_title"] == "Broken Halo 203 · Pilot"
    assert entry["cue_number_and_title_with_cue_version"] == cue.number
    assert entry["cue_version"] == "" and entry["start_offset"] == ""
    assert entry["current_picture"] == ""
    assert entry["status"] == ""


@pytest.mark.parametrize(
    ("season", "episode", "joined"),
    [("1", "101", "101"), ("2", "3", "203"), ("", "7", "7"), ("4", "", "4")],
)
def test_season_and_episode(season: str, episode: str, joined: str) -> None:
    project = factories.ProjectFactory.create(
        season=season, episode_number=episode
    )
    cue = factories.CueFactory.create(project=project)
    assert (
        cue_json.entries([(cue, None)], project)[0]["season_and_episode_number"]
        == joined
    )


def test_pages_know_where_to_get_json(
    viewer: Client, project: models.Project
) -> None:
    html = viewer.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert '"jsonUrl": "/api/cues/json/"' in html
    cue = project.cues.get(number="2m04")
    html = viewer.get(reverse("cues:cue", args=[cue.pk])).content.decode()
    # json_script escapes "&" as \u0026.
    assert f"/api/cues/json/?ids={cue.pk}\\u0026version=" in html


def test_turnovers_without_timecodes(project: models.Project) -> None:
    cue = project.cues.get(number="1m01")
    picture_version = cue.picture.current_version()
    assert picture_version is not None
    picture_version.tc_in = None
    picture_version.save()
    entry = cue_json.entries([(cue, cue.current_version)], project)[0]
    assert entry["current_picture_in"] == ""
    assert entry["current_picture_cue_start_offset"] == ""
