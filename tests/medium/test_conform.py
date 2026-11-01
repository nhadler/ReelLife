"""The conform assistant."""

from collections.abc import Callable
import datetime
from decimal import Decimal

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.services import conform
from cues.services import history
from cues.services import projects as project_service
from cues.services import tools
from cues.services import turnovers
from tests import factories

pytestmark = pytest.mark.django_db

SECOND = 24
HOUR = 3600 * SECOND


def _turnover(
    project: models.Project,
    label: str,
    day: int,
    reels: dict[str, tuple[int | None, int]],
) -> models.Turnover:
    return turnovers.create_turnover(
        project,
        label=label,
        received_date=datetime.date(2026, 9, day),
        reels=[
            turnovers.ReelEntry(
                project.pictures.filter(name=name).first(), name, tc_in, tc_out
            )
            for name, (tc_in, tc_out) in reels.items()
        ],
    )


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    """Reels 1 and 2 at c0917, each with a cue written to it."""
    project = factories.ProjectFactory.create()
    _turnover(
        project,
        "c0917",
        17,
        {"1": (HOUR, HOUR + 1000), "2": (2 * HOUR, 2 * HOUR + 1000)},
    )
    for name in ("1", "2"):
        picture = project.pictures.get(name=name)
        cue = factories.CueFactory.create(
            project=project,
            picture=picture,
            number=f"{name}m01",
            tc_in=int(name) * HOUR + 10 * SECOND,
            tc_out=int(name) * HOUR + 40 * SECOND,
        )
        version = factories.CueVersionFactory.create(
            cue=cue, label="v1", written_to=picture.current_version()
        )
        cue.current_version = version
        cue.save()
    return project


def _recut(project: models.Project) -> models.Turnover:
    """c1030: reel 1's head two seconds longer; reel 2 as it was."""
    return _turnover(
        project,
        "c1030",
        30,
        {
            "1": (HOUR + 2 * SECOND, HOUR + 1048),
            "2": (2 * HOUR, 2 * HOUR + 1000),
        },
    )


def test_moved_reels_and_shift(project: models.Project) -> None:
    turnover = _recut(project)
    move = conform.moved_reels(turnover)[0]
    assert (move.picture.name, move.delta, move.cues) == ("1", 48, 1)
    assert move.label(project.rate) == "+00:00:02:00"
    assert conform.shift_reels(project, [move]) == 1
    cue = project.cues.get(number="1m01")
    assert cue.tc_in == HOUR + 12 * SECOND
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    cue.refresh_from_db()
    assert cue.tc_in == HOUR + 10 * SECOND


def test_a_shift_moves_each_cue_once(project: models.Project) -> None:
    turnover = _recut(project)
    picture = project.pictures.get(name="1")
    # Spotted after the recut, on the new picture: already in place.
    placed = factories.CueFactory.create(
        project=project,
        picture=picture,
        number="1m02",
        tc_in=HOUR + 50 * SECOND,
        tc_out=HOUR + 60 * SECOND,
    )
    placed.current_version = factories.CueVersionFactory.create(
        cue=placed, label="v1", written_to=picture.current_version()
    )
    placed.save()
    move = conform.moved_reels(turnover)[0]
    assert move.cues == 1
    assert conform.shift_reels(project, [move]) == 1
    again = conform.moved_reels(turnover)[0]
    assert again.cues == 0
    assert conform.shift_reels(project, [again]) == 0
    cue = project.cues.get(number="1m01")
    assert cue.tc_in == HOUR + 12 * SECOND  # moved once, not twice
    placed.refresh_from_db()
    assert placed.tc_in == HOUR + 50 * SECOND
    history.undo(  # undoing the shift offers it again
        models.ChangeSet.objects.filter(label__startswith="Conform").latest(
            "pk"
        )
    )
    assert conform.moved_reels(turnover)[0].cues == 1


def test_same_day_turnovers_compare_with_the_one_before(
    project: models.Project,
) -> None:
    morning = _turnover(
        project, "c0917b", 17, {"1": (HOUR + SECOND, HOUR + 1024)}
    )
    evening = _turnover(
        project, "c0917c", 17, {"1": (HOUR + 3 * SECOND, HOUR + 1072)}
    )
    assert [m.delta for m in conform.moved_reels(morning)] == [SECOND]
    assert [m.delta for m in conform.moved_reels(evening)] == [2 * SECOND]


def test_unmoved_and_new_reels_are_not_offered(project: models.Project) -> None:
    first = project.turnovers.get(label="c0917")
    assert not conform.moved_reels(first)  # nothing before it
    turnover = _turnover(project, "c1101", 30, {"3": (3 * HOUR, 3 * HOUR + 10)})
    assert not conform.moved_reels(turnover)
    blank = _turnover(project, "c1102", 30, {"1": (None, HOUR + 10)})
    assert not conform.moved_reels(blank)


def _early(project: models.Project) -> None:
    """Puts 1m01 ten seconds before its reel's IN, so a big shift fails."""
    cue = project.cues.get(number="1m01")
    cue.tc_in = HOUR - 10 * SECOND
    cue.save()


def test_a_shift_out_of_range_is_refused(project: models.Project) -> None:
    _early(project)
    turnover = _turnover(project, "c1030", 30, {"1": (0, 1000)})
    move = conform.moved_reels(turnover)[0]
    with pytest.raises(tools.ToolError):
        conform.shift_reels(project, [move])


def test_needing_conform_and_marking(project: models.Project) -> None:
    assert not conform.needing_conform(project)
    _recut(project)
    # Both reels came again in c1030, so both cues are on older picture.
    stale_cues = conform.needing_conform(project)
    assert [s.cue.number for s in stale_cues] == ["1m01", "2m01"]
    stale = stale_cues[0]
    assert stale.written_to == "c0917"
    assert stale.start_offset == "+00:00:00:00"
    assert stale.duration_difference == "+00:00:00:00"
    conform.mark_conformed(project, [stale.cue], new_version=False)
    assert [s.cue.number for s in conform.needing_conform(project)] == ["2m01"]
    cue = project.cues.get(number="1m01")
    assert cue.current_version is not None
    assert cue.current_version.label == "v1"
    assert cue.current_version.written_to == cue.picture.current_version()
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    cue.refresh_from_db()
    old = cue.current_version
    assert old is not None
    old.notes, old.bpm, old.spotting_notes = "Strings only", 92, "Door slam"
    old.sub_status = project.sub_statuses.first()
    old.save()
    stale_cues = conform.needing_conform(project)
    conform.mark_conformed(
        project, [s.cue for s in stale_cues], new_version=True
    )
    cue.refresh_from_db()
    new = cue.current_version
    assert new is not None
    assert new.label == "v2"
    # It keeps the notes and sub status, as the New Version dialog does.
    assert (new.notes, new.bpm, new.spotting_notes, new.sub_status) == (
        "Strings only",
        Decimal(92),
        "Door slam",
        old.sub_status,
    )
    assert not conform.needing_conform(project)


def test_a_cue_without_times_shows_no_offsets(project: models.Project) -> None:
    _recut(project)
    cue = project.cues.get(number="1m01")
    cue.tc_in = None
    cue.save()
    stale = conform.needing_conform(project)[0]
    assert (stale.start_offset, stale.duration_difference) == ("", "")
    version = cue.current_version
    assert version is not None
    version.written_to = None
    version.save()
    assert [s.cue.number for s in conform.needing_conform(project)] == ["2m01"]


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def test_page_shift_and_mark(editor: Client, project: models.Project) -> None:
    url = reverse("cues:conform", args=[project.pk])
    html = editor.get(url).content.decode()
    assert "No reel's IN moved in c0917." in html
    assert "Every cue is on its current picture." in html
    turnover = _recut(project)
    html = editor.get(url).content.decode()
    assert "Shift Reel 1's 1 cue by +00:00:02:00" in html
    assert "1m01" in html
    page = editor.get(url, {"turnover": "999"}).content.decode()
    assert "No turnovers yet." in page
    shift = reverse("cues:conform_shift", args=[project.pk])
    back = editor.post(shift, {"turnover": turnover.pk}, follow=True)
    assert "Tick a reel to shift." in back.content.decode()
    picture = project.pictures.get(name="1")
    done = editor.post(
        shift, {"turnover": turnover.pk, "reel": picture.pk}, follow=True
    )
    assert "Shifted 1 cue." in done.content.decode()
    html = done.content.decode()
    assert "its cues are already shifted" in html
    assert "Shift Cues</button>" not in html
    mark = reverse("cues:conform_mark", args=[project.pk])
    back = editor.post(mark, {"turnover": turnover.pk}, follow=True)
    assert "Tick the cues to mark conformed." in back.content.decode()
    cue = project.cues.get(number="1m01")
    done = editor.post(mark, {"cue": cue.pk, "how": "new"}, follow=True)
    assert "Marked 1 cue conformed with new versions." in done.content.decode()


def test_page_reports_a_refused_shift(
    editor: Client, project: models.Project
) -> None:
    _early(project)
    turnover = _turnover(project, "c1030", 30, {"1": (0, 1000)})
    picture = project.pictures.get(name="1")
    page = editor.post(
        reverse("cues:conform_shift", args=[project.pk]),
        {"turnover": turnover.pk, "reel": picture.pk},
        follow=True,
    ).content.decode()
    assert "outside" in page


def test_viewers_see_but_cannot_act(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    _recut(project)
    viewer = role_client(Role.VIEWER)
    html = viewer.get(
        reverse("cues:conform", args=[project.pk])
    ).content.decode()
    assert "disabled" in html and "Shift Cues" not in html
    response = viewer.post(reverse("cues:conform_mark", args=[project.pk]), {})
    assert response.status_code == 403


def test_logging_a_turnover_opens_the_assistant(
    editor: Client, project: models.Project
) -> None:
    reels = list(project.pictures.order_by("order", "name"))
    data = {
        "label": "c1030",
        "received_date": "2026-10-30",
        "notes": "",
        "reels-TOTAL_FORMS": str(len(reels)),
        "reels-INITIAL_FORMS": "0",
        "reels-MIN_NUM_FORMS": "0",
        "reels-MAX_NUM_FORMS": "200",
        "new-TOTAL_FORMS": "0",
        "new-INITIAL_FORMS": "0",
        "new-MIN_NUM_FORMS": "0",
        "new-MAX_NUM_FORMS": "40",
        "reels-0-include": "on",
        "reels-0-tc_in": "1:00:02:00",
        "reels-0-tc_out": "1:20:00:00",
    }
    response = editor.post(
        reverse("cues:turnover_create", args=[project.pk]), data
    )
    assert response["Location"].startswith(
        reverse("cues:conform", args=[project.pk])
    )
    page = editor.get(
        reverse("cues:project", args=[project.pk])
    ).content.decode()
    assert reverse("cues:conform", args=[project.pk]) in page


def test_marking_a_cue_without_a_version_does_nothing(
    project: models.Project,
) -> None:
    bare = factories.CueFactory.create(
        project=project, picture=project.pictures.get(name="1")
    )
    conform.mark_conformed(project, [bare], new_version=False)
    assert not bare.versions.exists()


def test_cues_on_na_are_placed_once_reels_have_timecodes(
    editor: Client,
) -> None:
    project = factories.ProjectFactory.create()
    placeholder = project_service.placeholder_picture(project)
    early = factories.CueFactory.create(
        project=project,
        picture=placeholder,
        number="1m01",
        tc_in=HOUR + 10 * SECOND,
        tc_out=HOUR + 40 * SECOND,
    )
    early.current_version = factories.CueVersionFactory.create(
        cue=early, label="v1", written_to=None
    )
    early.save()
    lost = factories.CueFactory.create(
        project=project,
        picture=placeholder,
        number="9m01",
        tc_in=9 * HOUR,
        tc_out=9 * HOUR + SECOND,
    )
    bare = factories.CueFactory.create(  # no version yet
        project=project,
        picture=placeholder,
        number="1m02",
        tc_in=HOUR + 50 * SECOND,
        tc_out=HOUR + 60 * SECOND,
    )
    assert not turnovers.unplaced(project)  # no reel has timecodes yet
    _turnover(
        project,
        "c0917",
        17,
        {"1": (HOUR, HOUR + 2000), "2": (None, 2 * HOUR)},  # 2: no IN yet
    )
    found = turnovers.unplaced(project)
    assert [(p.cue.number, p.picture.name) for p in found] == [
        ("1m01", "1"),
        ("1m02", "1"),
    ]
    page = reverse("cues:picture", args=[project.pk])
    html = editor.get(page).content.decode()
    assert "Cues Not on a Reel" in html
    place = reverse("cues:place_cues", args=[project.pk])
    assert (
        "Tick the cues" in editor.post(place, {}, follow=True).content.decode()
    )
    done = editor.post(
        place, {"cue": [early.pk, bare.pk, lost.pk]}, follow=True
    )
    assert "Placed 2 cues on reels." in done.content.decode()
    early.refresh_from_db()
    assert early.picture.name == "1"
    assert early.current_version is not None
    assert early.current_version.written_to == early.picture.current_version()
    lost.refresh_from_db()
    assert lost.picture == placeholder
    assert not turnovers.unplaced(project)
    history.undo(models.ChangeSet.objects.filter(project=project).latest("pk"))
    early.refresh_from_db()
    assert early.picture == placeholder


def test_logging_the_first_turnover_offers_to_place_cues(
    editor: Client,
) -> None:
    project = factories.ProjectFactory.create()
    factories.CueFactory.create(
        project=project,
        picture=project_service.placeholder_picture(project),
        number="1m01",
        tc_in=HOUR + SECOND,
        tc_out=HOUR + 2 * SECOND,
    )
    data = {
        "label": "c0917",
        "received_date": "2026-09-17",
        "notes": "",
        "reels-TOTAL_FORMS": "0",
        "reels-INITIAL_FORMS": "0",
        "reels-MIN_NUM_FORMS": "0",
        "reels-MAX_NUM_FORMS": "200",
        "new-TOTAL_FORMS": "1",
        "new-INITIAL_FORMS": "0",
        "new-MIN_NUM_FORMS": "0",
        "new-MAX_NUM_FORMS": "40",
        "new-0-name": "1",
        "new-0-tc_in": "1:00:00:00",
        "new-0-tc_out": "1:20:00:00",
    }
    response = editor.post(
        reverse("cues:turnover_create", args=[project.pk]), data
    )
    assert response["Location"] == reverse("cues:picture", args=[project.pk])
    assert turnovers.unplaced(project)  # and the page offers to place it
