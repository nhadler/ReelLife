"""The "sync status with review status" project setting."""

from collections.abc import Callable

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import models
from cues.api import edits
from cues.services import authoring
from cues.services import demo

pytestmark = pytest.mark.django_db


@pytest.fixture(name="project")
def fixture_project() -> models.Project:
    project = demo.build_demo().project
    project.sync_review_status = True
    project.save()
    return project


def _composed(project: models.Project) -> models.Cue:
    cue = project.cues.get(number="1m02")
    cue.status = project.statuses.get(label="Composed")
    cue.save()
    return cue


def test_approving_in_the_grid(project: models.Project) -> None:
    cue = _composed(project)
    edits.apply(cue, "review_status", "approved")
    cue.refresh_from_db()
    assert cue.status is not None
    assert cue.status.kind == models.StatusKind.APPROVED


def test_approving_on_the_details_page(project: models.Project) -> None:
    cue = _composed(project)
    assert cue.current_version is not None
    edits.apply_version(cue.current_version, "review_status", "approved")
    cue.refresh_from_db()
    assert cue.status is not None
    assert cue.status.label == "Approved"


def test_approving_a_new_version(project: models.Project) -> None:
    cue = _composed(project)
    version = models.CueVersion(
        cue=cue, label="v2", review_status="approved", tc_in=cue.tc_in
    )
    authoring.create_version(version, update_cue=False)
    cue.refresh_from_db()
    assert cue.status is not None
    assert cue.status.label == "Approved"


@pytest.mark.parametrize(
    "case", ["setting off", "not composed", "older version", "other review"]
)
def test_when_nothing_changes(project: models.Project, case: str) -> None:
    cue = _composed(project)
    current = cue.current_version
    assert current is not None
    target, review = current, "approved"
    if case == "setting off":
        project.sync_review_status = False
        project.save()
    elif case == "not composed":
        cue.status = project.statuses.get(label="Revisions")
        cue.save()
    elif case == "older version":
        newer = models.CueVersion.objects.create(cue=cue, label="v9")
        cue.current_version = newer
        cue.save()
    else:
        review = "needs_review"
    before = cue.status
    edits.apply_version(target, "review_status", review)
    cue.refresh_from_db()
    assert cue.status == before


def test_projects_without_an_approved_status(project: models.Project) -> None:
    cue = _composed(project)
    project.statuses.filter(kind=models.StatusKind.APPROVED).update(
        kind=models.StatusKind.COMPLETED
    )
    edits.apply(cue, "review_status", "approved")
    cue.refresh_from_db()
    assert cue.status is not None
    assert cue.status.label == "Composed"


def test_settings_page_has_the_switch(
    role_client: Callable[[Role], Client], project: models.Project
) -> None:
    html = (
        role_client(Role.EDITOR)
        .get(reverse("cues:settings_statuses", args=[project.pk]))
        .content.decode()
    )
    assert "Sync Status with Review Status" in html
