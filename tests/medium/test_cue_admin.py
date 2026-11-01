"""The admin can build a whole project."""

from collections.abc import Callable
from typing import Any

from django.apps import apps
from django.contrib import admin
from django.test import Client
from django.test import RequestFactory
from django.urls import reverse
import pytest

from accounts.models import Role
from cues import forms
from cues import models
from tests import factories

pytestmark = pytest.mark.django_db

# The app's own models, without the Historical* ones simple_history adds.
CUE_MODELS = [
    model
    for model in apps.get_app_config("cues").get_models()
    if not model.__name__.startswith("Historical")
]


def _inlines(*prefixes: str, **rows: list[dict[str, str]]) -> dict[str, Any]:
    """Management-form data for admin inlines, with optional rows."""
    data: dict[str, Any] = {}
    for prefix in prefixes:
        forms_data = rows.get(prefix, [])
        data |= {
            f"{prefix}-TOTAL_FORMS": str(len(forms_data)),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for i, row in enumerate(forms_data):
            data |= {f"{prefix}-{i}-{key}": value for key, value in row.items()}
    return data


@pytest.fixture(name="admin_client_")
def _admin_client(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.ADMIN)


def test_every_cues_model_is_registered() -> None:
    assert all(admin.site.is_registered(model) for model in CUE_MODELS)
    names = {model.__name__ for model in CUE_MODELS}
    assert names == {
        "AppSettings",
        "CalendarFeed",
        "ChangeSet",
        "Cue",
        "CueShare",
        "DefaultShare",
        "DepartmentStatus",
        "CueVersion",
        "DayOff",
        "Deadline",
        "Party",
        "Picture",
        "PictureVersion",
        "Project",
        "ScheduleEntry",
        "SchedulePin",
        "Series",
        "SeriesShare",
        "StatusOption",
        "SubStatusOption",
        "ThemeOption",
        "InstrumentOption",
        "VersionTheme",
        "CueInstrument",
        "ProjectNote",
        "Turnover",
        "View",
    }


@pytest.mark.parametrize("model", CUE_MODELS, ids=lambda m: m.__name__)
def test_changelist_and_add_pages_render(
    admin_client_: Client, model: type[Any]
) -> None:
    # Rows for every list, including timecodes that are unset.
    version = factories.CueVersionFactory.create()
    factories.PictureVersionFactory.create(picture=version.cue.picture)
    factories.CueFactory.create(
        project=version.cue.project, tc_in=None, tc_out=None
    )
    name = f"admin:cues_{model._meta.model_name}"  # noqa: SLF001 pylint: disable=protected-access
    assert admin_client_.get(reverse(f"{name}_changelist")).status_code == 200
    # Settings refuses "add" once its single row exists; change sets
    # are never added by hand.
    add = admin_client_.get(reverse(f"{name}_add"))
    assert add.status_code in (200, 403)


def test_whole_project_can_be_built_in_the_admin(admin_client_: Client) -> None:
    # Project with two reels.
    response = admin_client_.post(
        reverse("admin:cues_project_add"),
        {
            "kind": "film",
            "title": "The Big Heist",
            "production_number": "BH",
            "frame_rate": "23.976",
            "picture_type": "reels",
            "lanes": "2",
            "cue_number_pattern": r"^\d+m\d+$",
            **_inlines(
                "pictures",
                "deadlines",
                "statuses",
                "sub_statuses",
                pictures=[
                    {"name": "1", "order": "1"},
                    {"name": "2", "order": "2"},
                ],
            ),
        },
    )
    assert response.status_code == 302, response.content
    project = models.Project.objects.get(title="The Big Heist")
    assert project.statuses.count() == 17
    reel1, reel2 = project.pictures.all()

    # Turnover with each reel's IN/OUT typed as timecodes.
    response = admin_client_.post(
        reverse("admin:cues_turnover_add"),
        {
            "project": str(project.pk),
            "label": "c1030",
            "received_date": "2026-10-30",
            **_inlines(
                "picture_versions",
                picture_versions=[
                    {
                        "picture": str(reel1.pk),
                        "tc_in": "1:00:00:00",
                        "tc_out": "1:20:00:00",
                    },
                    {
                        "picture": str(reel2.pk),
                        "tc_in": "2:00:00:00",
                        "tc_out": "2:18:30:12",
                    },
                ],
            ),
        },
    )
    assert response.status_code == 302, response.content
    pv1 = reel1.current_version()
    assert pv1 is not None
    assert (pv1.tc_in, pv1.tc_out) == (86_400, 115_200)

    # Cue with a version written to that turnover.
    status = project.statuses.get(label="In Progress")
    response = admin_client_.post(
        reverse("admin:cues_cue_add"),
        {
            "project": str(project.pk),
            "picture": str(reel1.pk),
            "number": "1m01",
            "title": "Main Title",
            "cue_type": "SCORE",
            "status": str(status.pk),
            "tc_in": "1:00:10:00",
            "tc_out": "1:01:35:00",
            "active": "on",
            "lane": "0",
            "usage": "BI",
            **_inlines(
                "versions",
                versions=[
                    {
                        "label": "v1",
                        "written_to": str(pv1.pk),
                        "tc_in": "1:00:10:00",
                        "tc_out": "1:01:35:00",
                        "review_status": "no_status",
                    }
                ],
            ),
        },
    )
    assert response.status_code == 302, response.content
    cue = models.Cue.objects.get(number="1m01")
    assert (cue.tc_in, cue.tc_out, cue.duration) == (86_640, 88_680, 2_040)
    version = cue.versions.get()
    assert version.written_to == pv1
    assert version.tc_in == 86_640

    # Make the version current; the history records who did it.
    edit = admin_client_.get(reverse("admin:cues_cue_change", args=[cue.pk]))
    assert b"1:00:10:00" in edit.content
    choices = edit.context["adminform"].form.fields["current_version"].queryset
    assert list(choices) == [version]
    assert cue.history.latest().history_user.role == "admin"

    listing = admin_client_.get(reverse("admin:cues_cue_changelist"))
    assert b"1:01:35:00" in listing.content


def test_bad_timecode_is_reported_on_the_field() -> None:
    cue = factories.CueFactory.create()
    form = forms.CueForm(
        data={
            "project": cue.project.pk,
            "picture": cue.picture.pk,
            "number": "1m99",
            "cue_type": "SCORE",
            "usage": "BI",
            "lane": 0,
            "tc_in": "1:00:00:24",
        }
    )
    assert not form.is_valid()
    assert "frames must be < 24" in form.errors["tc_in"][0]


def test_timecode_needs_a_project() -> None:
    form = forms.CueForm(data={"number": "1m01", "tc_in": "1:00:00:00"})
    assert not form.is_valid()
    assert form.errors["tc_in"] == ["Choose the project first."]


def test_blank_timecodes_are_none() -> None:
    cue = factories.CueFactory.create()
    form = forms.CueForm(
        instance=cue,
        data={
            "project": cue.project.pk,
            "picture": cue.picture.pk,
            "number": cue.number,
            "cue_type": "SCORE",
            "usage": "BI",
            "lane": 0,
            "active": True,
            "tc_in": "",
            "tc_out": "",
        },
    )
    assert form.is_valid(), form.errors
    assert form.save().tc_in is None


def test_drop_frame_project_uses_drop_frame_labels() -> None:
    project = factories.ProjectFactory.create(
        frame_rate="29.97", drop_frame=True
    )
    cue = factories.CueFactory.create(
        project=project, tc_in=1_800, tc_out=1_900
    )
    form = forms.CueForm(instance=cue)
    assert form.initial["tc_in"] == "0:01:00;02"


def test_picture_version_rate_falls_back_to_turnover() -> None:
    turnover = factories.TurnoverFactory.create()
    form = forms.PictureVersionForm()
    assert form.project_for({"turnover": turnover}) == turnover.project
    assert form.project_for({}) is None


def test_cue_version_form_shows_timecodes() -> None:
    version = factories.CueVersionFactory.create()
    form = forms.CueVersionForm(instance=version)
    assert form.initial["tc_in"] == "1:00:10:00"


def test_base_form_needs_a_project_source() -> None:
    class Bare(forms.TimecodeModelForm):  # pylint: disable=abstract-method
        class Meta:
            model = models.Cue
            fields = ["number"]

    with pytest.raises(NotImplementedError):
        Bare()


def test_settings_admin_allows_one_row() -> None:
    model_admin = admin.site.get_model_admin(models.AppSettings)
    request = RequestFactory().get("/")
    assert model_admin.has_add_permission(request)
    models.AppSettings.load()
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_delete_permission(request)


def test_editing_a_project_keeps_its_statuses(admin_client_: Client) -> None:
    project = factories.ProjectFactory.create(title="Old")
    project.sub_statuses.all().delete()
    project.statuses.exclude(label="To Write").delete()
    status = project.statuses.get()
    response = admin_client_.post(
        reverse("admin:cues_project_change", args=[project.pk]),
        {
            "kind": "film",
            "title": "New",
            "frame_rate": "23.976",
            "picture_type": "reels",
            "lanes": "2",
            "cue_number_pattern": project.cue_number_pattern,
            **_inlines("pictures", "deadlines", "sub_statuses"),
            "statuses-TOTAL_FORMS": "1",
            "statuses-INITIAL_FORMS": "1",
            "statuses-0-id": str(status.pk),
            "statuses-0-project": str(project.pk),
            "statuses-0-label": "To Write",
            "statuses-0-kind": "to_do",
            "statuses-0-color": "#e5e7eb",
            "statuses-0-order": "0",
        },
    )
    assert response.status_code == 302, response.content
    project.refresh_from_db()
    assert project.title == "New"
    # Editing doesn't re-seed the lists the user trimmed.
    assert project.statuses.count() == 1
    assert not project.sub_statuses.exists()


def test_forms_skip_timecode_fields_they_leave_out() -> None:
    class InOnly(forms.CueForm):
        class Meta:
            model = models.Cue
            fields = ["project", "tc_in"]

    cue = factories.CueFactory.create()
    form = InOnly(instance=cue)
    assert set(form.fields) == {"project", "tc_in"}
    assert form.initial["tc_in"] == "1:00:10:00"
