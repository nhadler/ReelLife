"""Project settings, a page per section.

General, Picture and Cue Sheet edit parts of the project itself;
Statuses, Lists and Departments its lists; Deadlines the dates. Views and
Project live in cues.views.project_settings.
"""

from typing import Any

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import change_labels
from cues import day_off_forms
from cues import forms
from cues import models
from cues import share_forms
from cues.services import department_lists
from cues.services import history
from cues.services import projects as project_service
from cues.services import summaries

# The sidebar: (section, label, URL name), in order.
SECTIONS = (
    ("general", "General", "cues:settings"),
    ("picture", "Picture", "cues:settings_picture"),
    ("cue_sheet", "Cue Sheet", "cues:settings_cue_sheet"),
    ("statuses", "Statuses", "cues:settings_statuses"),
    ("lists", "Lists", "cues:settings_lists"),
    ("departments", "Departments", "cues:settings_departments"),
    ("deadlines", "Deadlines", "cues:settings_deadlines"),
    ("views", "Views", "cues:settings_views"),
    ("project", "Project", "cues:settings_project"),
)
_LABELS = {key: label for key, label, _ in SECTIONS}
_URLS = {key: url for key, _, url in SECTIONS}

# The project fields each of the project's own pages edits.
_FIELDS = {
    "general": (
        "title",
        "sub_title",
        "production_number",
        "kind",
        "series",
        "season",
        "episode_number",
        "thumbnail_upload",
        "remove_thumbnail",
        "cue_thumbnail_fallback",
    ),
    "picture": ("frame_rate", "drop_frame", "picture_type", "lanes"),
    "cue_sheet": (
        "aka",
        "episode_title",
        "production_company",
        "company_address",
        "distributor",
        "network",
        "release_date",
        "country",
        "cue_sheet_category",
        "cue_sheet_version",
        "preparer",
        "preparer_email",
        "cue_number_pattern",
        "numbering",
    ),
}


def page(
    request: HttpRequest, project: models.Project, section: str, **context: Any
) -> HttpResponse:
    """Renders a settings page with the project bar and sidebar."""
    return render(
        request,
        f"cues/settings/{section}.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "settings",
            "section": section,
            "sections": SECTIONS,
            "page_title": _LABELS[section],
            "read_only": not roles.at_least(
                getattr(request.user, "role", ""), Role.EDITOR
            ),
            **context,
        },
    )


def saved(
    request: HttpRequest, project: models.Project, section: str, what: str
) -> HttpResponseRedirect:
    """Says so and shows the page again."""
    messages.success(request, f"Saved {what}.")
    return HttpResponseRedirect(reverse(_URLS[section], args=[project.pk]))


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def frame_rate_locked(project: models.Project) -> bool:
    """Whether changing the frame rate would reinterpret stored timecodes."""
    return (
        project.cues.exists()
        or models.PictureVersion.objects.filter(
            picture__project=project
        ).exists()
    )


def _project_page(
    request: HttpRequest, project_id: int, section: str
) -> HttpResponse:
    project = get_object_or_404(models.Project, pk=project_id)
    form = forms.ProjectForm(
        request.POST if request.method == "POST" else None,
        request.FILES or None,
        instance=project,
        only=_FIELDS[section],
    )
    if section == "picture" and frame_rate_locked(project):
        for name in ("frame_rate", "drop_frame"):
            form.fields[name].disabled = True
        # Said once, under the rate: it covers drop frame too.
        form.fields[
            "frame_rate"
        ].help_text = "Locked: the project has timecodes in this rate."
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            upload = form.cleaned_data.get("thumbnail_upload")
            if upload:
                project_service.set_thumbnail(project, upload)
            elif (
                form.cleaned_data.get("remove_thumbnail") and project.thumbnail
            ):
                project_service.set_thumbnail(project, None)
        return saved(request, project, section, "the project settings")
    return page(request, project, section, form=form)


@require_http_methods(["GET", "POST"])
def general(request: HttpRequest, project_id: int) -> HttpResponse:
    """Title, numbers and thumbnail."""
    return _project_page(request, project_id, "general")


@require_http_methods(["GET", "POST"])
def picture(request: HttpRequest, project_id: int) -> HttpResponse:
    """Frame rate, picture type and timeline lanes."""
    return _project_page(request, project_id, "picture")


@require_http_methods(["GET", "POST"])
def cue_sheet(request: HttpRequest, project_id: int) -> HttpResponse:
    """Cue sheet details, valid cue numbers and the default shares."""
    project = get_object_or_404(models.Project, pk=project_id)
    data = request.POST if request.method == "POST" else None
    form = forms.ProjectForm(data, instance=project, only=_FIELDS["cue_sheet"])
    defaults = share_forms.DefaultShareFormSet(
        data, instance=project, prefix="defaults"
    )
    # An episode can also set its series' defaults, for every episode.
    series = (
        share_forms.SeriesShareFormSet(
            data, instance=project.series, prefix="series"
        )
        if project.series
        else None
    )
    if (
        data is not None
        and form.is_valid()
        and defaults.is_valid()
        and (series is None or series.is_valid())
    ):
        label = change_labels.describe(
            "Edit cue sheet settings",
            defaults,
            *([series] if series is not None else []),
            also=["details"] if form.has_changed() else [],
        )
        if label:
            with history.recording(project, label, _user(request)):
                form.save()
                defaults.save()
                if series is not None:
                    series.save()
        return saved(request, project, "cue_sheet", "the cue sheet settings")
    return page(
        request,
        project,
        "cue_sheet",
        form=form,
        defaults=defaults,
        series_defaults=series,
    )


def _in_use(formset: Any, sub_set: Any) -> bool:
    """Flags statuses and sub statuses marked for deletion still in use.

    Deleting a sub status would clear it from versions outside history,
    and break undoing older changes that set it.
    """
    blocked = False
    for form in formset.deleted_forms:
        status = form.instance
        if status.pk and status.cues.exists():
            count = status.cues.count()
            cues = "cue" if count == 1 else "cues"
            form.add_error(
                None,
                f"{status.label} is used by {count} {cues}; give them another "
                "status first.",
            )
            blocked = True
    for form in sub_set.deleted_forms:
        option = form.instance
        count = (
            models.CueVersion.objects.filter(sub_status=option).count()
            if option.pk
            else 0
        )
        if count:
            versions = "version" if count == 1 else "versions"
            form.add_error(
                None,
                f"{option.label} is used by {count} {versions}; give them "
                "another sub status first.",
            )
            blocked = True
    return blocked


@require_http_methods(["GET", "POST"])
def statuses(request: HttpRequest, project_id: int) -> HttpResponse:
    """The status and sub status lists, and syncing with review status."""
    project = get_object_or_404(models.Project, pk=project_id)
    data = request.POST if request.method == "POST" else None
    status_set = forms.StatusFormSet(data, instance=project, prefix="statuses")
    sub_set = forms.SubStatusFormSet(data, instance=project, prefix="subs")
    sync_form = forms.ProjectForm(
        data, instance=project, only=("sync_review_status",), prefix="project"
    )
    if (
        data is not None
        and status_set.is_valid()
        and sub_set.is_valid()
        and sync_form.is_valid()
        and not _in_use(status_set, sub_set)
    ):
        label = change_labels.describe(
            "Edit statuses",
            status_set,
            sub_set,
            also=["sync with review status"] if sync_form.has_changed() else [],
        )
        if label:
            with history.recording(project, label, _user(request)):
                status_set.save()
                sub_set.save()
                sync_form.save()
        return saved(request, project, "statuses", "the statuses")
    return page(
        request,
        project,
        "statuses",
        status_set=status_set,
        sub_set=sub_set,
        sync_form=sync_form,
    )


@require_http_methods(["GET", "POST"])
def lists(request: HttpRequest, project_id: int) -> HttpResponse:
    """The theme and instrument lists."""
    project = get_object_or_404(models.Project, pk=project_id)
    data = request.POST if request.method == "POST" else None
    theme_set = forms.ThemeFormSet(data, instance=project, prefix="themes")
    instrument_set = forms.InstrumentFormSet(
        data, instance=project, prefix="instruments"
    )
    if data is not None and theme_set.is_valid() and instrument_set.is_valid():
        label = change_labels.describe("Edit lists", theme_set, instrument_set)
        if label:
            with history.recording(project, label, _user(request)):
                theme_set.save()
                instrument_set.save()
        return saved(request, project, "lists", "the lists")
    return page(
        request,
        project,
        "lists",
        theme_set=theme_set,
        instrument_set=instrument_set,
    )


@require_http_methods(["GET", "POST"])
def departments(request: HttpRequest, project_id: int) -> HttpResponse:
    """One department's status list at a time, chosen by tab (?list=)."""
    project = get_object_or_404(models.Project, pk=project_id)
    labels = dict(models.DepartmentStatus.Field.choices)
    # The known name, not the request's string, goes on into the redirect.
    wanted = request.GET.get("list")
    field = next(
        (name for name in labels if name == wanted),
        models.DEPARTMENT_STATUS_FIELDS[0],
    )
    data = request.POST if request.method == "POST" else None
    formset = forms.DepartmentFormSet(data, instance=project, field=field)
    if (
        data is not None
        and formset.is_valid()
        and not department_lists.in_use(project, formset)
    ):
        department_lists.save(
            project,
            {field: formset},
            change_labels.describe(f"Edit {labels[field]}", formset),
            _user(request),
        )
        messages.success(request, f"Saved the {labels[field]} list.")
        url = reverse("cues:settings_departments", args=[project.pk])
        return HttpResponseRedirect(f"{url}?list={field}")
    return page(
        request,
        project,
        "departments",
        tabs=[
            (key, label.removesuffix(" Status"))
            for key, label in labels.items()
        ],
        field=field,
        label=labels[field],
        formset=formset,
    )


@require_http_methods(["GET", "POST"])
def deadlines(request: HttpRequest, project_id: int) -> HttpResponse:
    """Edit deadlines."""
    project = get_object_or_404(models.Project, pk=project_id)
    data = request.POST if request.method == "POST" else None
    deadline_set = forms.DeadlineFormSet(
        data, instance=project, prefix="deadlines"
    )
    project_days = day_off_forms.days_off_formset(data, project)
    if (
        data is not None
        and deadline_set.is_valid()
        and day_off_forms.days_off_ok(project_days)
    ):
        with transaction.atomic():
            deadline_set.save()
            day_off_forms.save_days_off(project_days)
        return saved(request, project, "deadlines", "deadlines and days off")
    return page(
        request,
        project,
        "deadlines",
        deadline_set=deadline_set,
        project_days=project_days,
    )
