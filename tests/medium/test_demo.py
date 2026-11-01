"""The seed_demo project."""

import io

from django.core.management import CommandError
from django.core.management import call_command
import pytest

from cues import models
from cues.services import demo

pytestmark = pytest.mark.django_db


def _snapshot(project: models.Project) -> list[tuple[object, ...]]:
    return list(
        project.cues.order_by("number").values_list(
            "number", "title", "tc_in", "tc_out", "status__label", "active"
        )
    )


def _needs_conform(cue: models.Cue) -> bool:
    assert cue.current_version is not None
    return cue.current_version.written_to != cue.picture.current_version()


def test_demo_has_the_promised_shape() -> None:
    summary = demo.build_demo()
    project = summary.project
    assert (summary.pictures, summary.turnovers, summary.cues) == (3, 2, 40)
    assert project.pictures.count() == 3
    assert project.turnovers.count() == 2
    assert project.cues.count() == 40
    assert (
        models.CueVersion.objects.filter(cue__project=project).count()
        == summary.versions
        > 0
    )
    assert project.cues.filter(active=False).count() == 1
    assert project.deadlines.count() == 3


def test_demo_rows_are_all_valid() -> None:
    project = demo.build_demo().project
    for cue in project.cues.all():
        cue.full_clean()
        for version in cue.versions.all():
            version.full_clean()


def test_demo_has_cues_on_old_picture() -> None:
    project = demo.build_demo().project
    versioned = project.cues.exclude(current_version=None)
    stale = [cue for cue in versioned if _needs_conform(cue)]
    assert stale
    assert all(cue.picture.name in ("2", "3") for cue in stale)
    reel2 = next(cue for cue in stale if cue.picture.name == "2")
    assert reel2.current_version is not None
    assert reel2.current_version.tc_in is not None
    assert reel2.tc_in is not None
    # Reel 2's head moved 2 s 12 f (60 frames at 23.976).
    assert reel2.tc_in - reel2.current_version.tc_in == 60


def test_demo_is_deterministic() -> None:
    first = _snapshot(demo.build_demo().project)
    second = _snapshot(demo.build_demo(replace=True).project)
    assert first == second
    assert models.Project.objects.count() == 1


def test_demo_refuses_to_overwrite_without_replace() -> None:
    demo.build_demo()
    with pytest.raises(ValueError, match="--replace"):
        demo.build_demo()


def test_command_prints_a_summary_and_refuses_twice() -> None:
    out = io.StringIO()
    call_command("seed_demo", stdout=out)
    assert "40 cues" in out.getvalue()
    with pytest.raises(CommandError, match="--replace"):
        call_command("seed_demo")
    call_command("seed_demo", "--replace", stdout=io.StringIO())
