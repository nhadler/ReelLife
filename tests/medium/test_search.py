"""The Ctrl+K palette's search."""

from collections.abc import Callable
from typing import Any

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.services import search
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(name="films")
def fixture_films() -> tuple[models.Project, models.Project]:
    """ABC101 and XYZ202, each with 1m01, 1m010 and a Main Title."""
    abc = factories.ProjectFactory.create(
        production_number="ABC101", title="Harbour Lights"
    )
    xyz = factories.ProjectFactory.create(
        production_number="XYZ202", title="Night Ferry"
    )
    for project in (abc, xyz):
        factories.CueFactory.create(
            project=project, number="1m010", title="Coda", tc_in=10
        )
        factories.CueFactory.create(
            project=project, number="1m01", title="Main Title", tc_in=20
        )
    return abc, xyz


def _cues(query: str, project_id: int | None = None) -> list[tuple[str, str]]:
    return [(hit.number, hit.detail) for hit in search.cues(query, project_id)]


def test_production_number_and_cue_number(
    films: tuple[models.Project, models.Project],
) -> None:
    del films
    assert _cues("ABC101 1m01") == [
        ("1m01", "ABC101 Harbour Lights"),
        ("1m010", "ABC101 Harbour Lights"),
    ]


def test_number_and_title_words(
    films: tuple[models.Project, models.Project],
) -> None:
    del films
    assert [n for n, _ in _cues("1m01 main")] == ["1m01", "1m01"]
    assert _cues("coda ferry") == [("1m010", "XYZ202 Night Ferry")]
    assert not _cues("1m01 nothing")
    assert not _cues("   ")


def test_exact_numbers_then_the_current_project(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, xyz = films
    ranked = _cues("1m01", xyz.pk)
    assert ranked == [
        ("1m01", "XYZ202 Night Ferry"),
        ("1m01", "ABC101 Harbour Lights"),
        ("1m010", "XYZ202 Night Ferry"),
        ("1m010", "ABC101 Harbour Lights"),
    ]
    assert _cues("1m01", abc.pk)[0] == ("1m01", "ABC101 Harbour Lights")


def test_inactive_cues_rank_last_and_are_tagged(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    abc.cues.filter(number="1m01").update(active=False)
    hits = search.cues("1m01 main")
    assert [hit.tag for hit in hits] == ["", "Inactive"]
    assert hits[0].url.startswith("/cues/")


def test_status_colour_comes_along(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    status = abc.statuses.first()
    assert status is not None
    abc.cues.update(status=status)
    assert search.cues("ABC101 coda")[0].color == status.color


def test_projects(films: tuple[models.Project, models.Project]) -> None:
    abc, xyz = films
    series = models.Series.objects.create(title="Ferries")
    xyz.series = series
    xyz.save()
    assert [hit.label for hit in search.projects("ferr")] == [
        "XYZ202 Night Ferry"
    ]
    assert [hit.label for hit in search.projects("abc")] == [
        "ABC101 Harbour Lights"
    ]
    abc.archived = True
    abc.save()
    latest = search.projects("")
    assert latest[-1].label == "ABC101 Harbour Lights"
    assert latest[-1].tag == "Archived"
    assert latest[0].url == reverse("cues:project", args=[xyz.pk])


def test_long_queries_are_cut(
    films: tuple[models.Project, models.Project],
) -> None:
    del films
    assert search.words("a " * 20) == ["a"] * search.MAX_WORDS
    assert len("".join(search.words("x" * 500))) == search.MAX_QUERY
    assert len(search.cues("1m")) == 4


def test_many_title_matches_never_hide_an_exact_number(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    for n in range(search.MAX_CUES + 5):
        factories.CueFactory.create(
            project=abc, number=f"9m{n:02d}", title="1m01"
        )
    assert _cues("1m01")[0][0] == "1m01"


def test_endpoint_for_viewers(
    role_client: Callable[[Role], Client],
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    viewer = role_client(Role.VIEWER)
    body = viewer.get(
        reverse("cues:api_search"), {"q": "harbour", "project": str(abc.pk)}
    ).json()
    kinds = [hit["kind"] for hit in body["results"]]
    assert kinds == ["project", "cue", "cue"]
    cue = body["results"][1]
    assert (cue["number"], cue["label"]) == ("1m010", "Coda")  # by IN
    odd = viewer.get(reverse("cues:api_search"), {"project": "x"}).json()
    assert [hit["kind"] for hit in odd["results"]] == ["project", "project"]


def test_pages_carry_the_palette(
    role_client: Callable[[Role], Client],
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    editor = role_client(Role.EDITOR)
    html = editor.get(reverse("cues:project", args=[abc.pk])).content.decode()
    assert 'id="palette"' in html and f'data-project="{abc.pk}"' in html
    assert 'data-palette="Export Cues"' in html
    assert 'data-palette="Import Cues"' in html
    viewer = role_client(Role.VIEWER)
    html = viewer.get(reverse("cues:project", args=[abc.pk])).content.decode()
    assert 'data-palette="Import Cues"' not in html
    assert 'data-palette="App Settings"' not in html
    dashboard = viewer.get(reverse("cues:dashboard")).content.decode()
    assert 'id="palette"' in dashboard and "data-project=" not in dashboard


def _version(cue: models.Cue, label: str, **notes: str) -> models.CueVersion:
    """Adds a version with these notes and makes it current."""
    version = factories.CueVersionFactory.create(cue=cue, label=label, **notes)
    cue.current_version = version
    cue.save()
    return version


def test_notes_matches_come_after_number_and_title_matches(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    titled = factories.CueFactory.create(
        project=abc, number="2m01", title="Door Slam"
    )
    noted = factories.CueFactory.create(
        project=abc, number="2m02", title="Quiet"
    )
    _version(noted, "v1", spotting_notes="Starts as the door\ncloses.")
    hits = search.cues("door")
    assert [hit.id for hit in hits] == [titled.pk, noted.pk]
    assert (hits[0].field, hits[0].snippet) == ("", "")
    assert hits[1].field == "Spotting Notes (v1)"
    assert hits[1].snippet == "Starts as the door closes."
    assert hits[1].url == reverse("cues:cue", args=[noted.pk])


def test_an_older_versions_notes_open_that_version(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    cue = factories.CueFactory.create(project=abc, title="Quiet")
    old = _version(cue, "v1", notes="Harp glissando into the reveal.")
    _version(cue, "v2", notes="Strings only.", revision_notes="Lose the harp")
    hits = search.cues("glissando")
    assert [(hit.field, hit.snippet) for hit in hits] == [
        ("Notes (v1)", "Harp glissando into the reveal.")
    ]
    assert hits[0].url == (
        reverse("cues:cue", args=[cue.pk]) + f"?version={old.pk}"
    )
    # The current version is looked at first.
    assert search.cues("harp")[0].field == "Revision Notes (v2)"


def test_scene_and_cue_notes_match_without_a_version(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    factories.CueFactory.create(
        project=abc, title="Quiet", scene="Lighthouse stairs"
    )
    factories.CueFactory.create(
        project=abc, title="Calm", mix_notes="Stems to the dub stage"
    )
    assert [(h.field, h.snippet) for h in search.cues("lighthouse")] == [
        ("Scene", "Lighthouse stairs")
    ]
    assert [h.field for h in search.cues("dub")] == ["Mix Notes"]


def test_every_word_must_match_somewhere(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    cue = abc.cues.get(number="1m01")
    _version(cue, "v1", temp="Zimmer, Time", in_note="On the door")
    hits = search.cues("1m01 door")
    assert [(h.number, h.detail, h.field) for h in hits] == [
        ("1m01", "ABC101 Harbour Lights", "IN Notes (v1)")
    ]
    assert not search.cues("door zimmer xyz202")
    # 'harbour' names the project, so the snippet is for 'zimmer'.
    hit = search.cues("harbour zimmer")[0]
    assert (hit.field, hit.snippet) == ("Temp (v1)", "Zimmer, Time")


def test_notes_matches_only_fill_the_room_left(
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    noted = factories.CueFactory.create(project=abc, title="Quiet")
    _version(noted, "v1", notes="storm")
    for n in range(search.MAX_CUES):
        factories.CueFactory.create(project=abc, title=f"Storm {n}")
    hits = search.cues("storm")
    assert len(hits) == search.MAX_CUES
    assert noted.pk not in [hit.id for hit in hits]


def test_notes_search_reads_versions_in_one_query(
    films: tuple[models.Project, models.Project],
    django_assert_max_num_queries: Callable[..., Any],
) -> None:
    abc, _ = films
    for n in range(8):
        cue = factories.CueFactory.create(project=abc, title=f"Quiet {n}")
        _version(cue, "v1", notes="rain")
        _version(cue, "v2", notes="dry")
    with django_assert_max_num_queries(4):
        hits = search.cues("rain")
    assert {hit.field for hit in hits} == {"Notes (v1)"}
    assert len(hits) == 8


def test_endpoint_says_where_notes_matched(
    role_client: Callable[[Role], Client],
    films: tuple[models.Project, models.Project],
) -> None:
    abc, _ = films
    cue = abc.cues.get(number="1m010")
    _version(cue, "v1", scene_description="Ferry docks at night")
    body = (
        role_client(Role.VIEWER)
        .get(reverse("cues:api_search"), {"q": "docks"})
        .json()
    )
    assert [(h["field"], h["snippet"]) for h in body["results"]] == [
        ("Scene Description (v1)", "Ferry docks at night")
    ]
