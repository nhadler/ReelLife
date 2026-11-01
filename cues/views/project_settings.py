"""The Views and Project settings pages (Nick, Oct 31).

Views: rename, reorder, duplicate, reset or delete the project's views,
and copy views or lists from another project. Project: duplicate,
archive or delete it (deleting is for admins, with the title typed).
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import day_off_forms
from cues import models
from cues import settings_forms
from cues.services import history
from cues.services import project_admin
from cues.services import view_presets
from cues.views.settings import page


def _views_url(project: models.Project) -> str:
    return reverse("cues:settings_views", args=[project.pk])


def _view_rows(project: models.Project) -> list[dict[str, object]]:
    """The views with what can be done to each."""
    own = [
        view
        for view in view_presets.for_project(project)
        if view.name not in view_presets.BUILT_IN
    ]
    rows = []
    for view in view_presets.for_project(project):
        standard = view.name in view_presets.BUILT_IN
        rows.append(
            {
                "view": view,
                "standard": standard,
                "changed": standard and view.project_id == project.pk,
                "first": not standard and own and own[0] == view,
                "last": not standard and own and own[-1] == view,
            }
        )
    return rows


@require_GET
def views(request: HttpRequest, project_id: int) -> HttpResponse:
    """The project's views, and copying from another project."""
    project = get_object_or_404(models.Project, pk=project_id)
    return page(
        request,
        project,
        "views",
        rows=_view_rows(project),
        copy_form=settings_forms.CopyFromForm(project=project),
    )


def _project_view(project: models.Project, view_id: str) -> models.View:
    view = next(
        (v for v in view_presets.for_project(project) if str(v.pk) == view_id),
        None,
    )
    if view is None:
        raise Http404("No such view.")
    return view


@require_POST
def view_action(request: HttpRequest, project_id: int) -> HttpResponse:
    """Rename, move, duplicate, reset or delete one view."""
    project = get_object_or_404(models.Project, pk=project_id)
    view = _project_view(project, request.POST.get("view", ""))
    action = request.POST.get("action", "")
    name = view.name
    try:
        if action == "rename":
            view_presets.update(
                project, view, {"name": request.POST.get("name")}
            )
            message = f"Renamed {name}."
        elif action in ("up", "down"):
            view_presets.move(project, view, -1 if action == "up" else 1)
            message = f"Moved {name}."
        elif action == "duplicate":
            copy = view_presets.duplicate(project, view)
            message = f"Made {copy.name}."
        elif action in ("reset", "delete"):
            view_presets.delete(project, view)
            message = (
                f"Reset {name}." if action == "reset" else f"Deleted {name}."
            )
        else:
            raise Http404("No such action.")
    except view_presets.ViewError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, message)
    return HttpResponseRedirect(_views_url(project))


@require_POST
def copy_from(request: HttpRequest, project_id: int) -> HttpResponse:
    """Brings views or lists over from another project."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = settings_forms.CopyFromForm(request.POST, project=project)
    if not form.is_valid():
        return page(
            request,
            project,
            "views",
            rows=_view_rows(project),
            copy_form=form,
        )
    source = form.cleaned_data["source"]
    done = []
    if form.cleaned_data["views"]:
        count = view_presets.copy_from(project, source)
        done.append(f"{count} view{'' if count == 1 else 's'}")
    if form.cleaned_data["lists"]:
        user = request.user if isinstance(request.user, User) else None
        with history.recording(
            project, f"Copy lists from {source.title}", user
        ):
            count = project_admin.copy_lists(project, source)
            if not count:
                transaction.set_rollback(True)  # no empty change set
        done.append(f"{count} list entr{'y' if count == 1 else 'ies'}")
    messages.success(request, f"Copied {' and '.join(done)} from {source}.")
    return HttpResponseRedirect(_views_url(project))


def _project_page(
    request: HttpRequest,
    project: models.Project,
    duplicate_form: settings_forms.DuplicateForm | None = None,
    delete_form: settings_forms.DeleteForm | None = None,
) -> HttpResponse:
    return page(
        request,
        project,
        "project",
        duplicate_form=duplicate_form
        or settings_forms.DuplicateForm(
            initial={"title": f"{project.title} (copy)"}
        ),
        delete_form=delete_form or settings_forms.DeleteForm(project=project),
        can_delete=getattr(request.user, "role", "") == Role.ADMIN,
    )


@require_GET
def project_page(request: HttpRequest, project_id: int) -> HttpResponse:
    """Duplicate, archive or delete the project."""
    project = get_object_or_404(models.Project, pk=project_id)
    return _project_page(request, project)


@require_POST
def duplicate(request: HttpRequest, project_id: int) -> HttpResponse:
    """A new project with this one's setup and no cues."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = settings_forms.DuplicateForm(request.POST)
    if form.is_valid():
        try:
            copy = project_admin.duplicate(project, form.cleaned_data["title"])
        except ValidationError as exc:
            form.add_error("title", exc.messages)
        else:
            messages.success(request, f"Made {copy.title}.")
            return HttpResponseRedirect(
                reverse("cues:settings", args=[copy.pk])
            )
    return _project_page(request, project, duplicate_form=form)


@require_POST
def archive(request: HttpRequest, project_id: int) -> HttpResponse:
    """Archives the project, or brings it back."""
    project = get_object_or_404(models.Project, pk=project_id)
    archived = request.POST.get("archived") == "1"
    project_admin.set_archived(project, archived)
    messages.success(
        request,
        f"Archived {project.title}."
        if archived
        else f"Restored {project.title}.",
    )
    return HttpResponseRedirect(
        reverse("cues:settings_project", args=[project.pk])
    )


@role_required(Role.ADMIN)
@require_POST
def delete(request: HttpRequest, project_id: int) -> HttpResponse:
    """Deletes the project, once its title is typed."""
    project = get_object_or_404(models.Project, pk=project_id)
    form = settings_forms.DeleteForm(request.POST, project=project)
    if not form.is_valid():
        return _project_page(request, project, delete_form=form)
    title = project.title
    project_admin.delete(project)
    messages.success(request, f"Deleted {title}.")
    return HttpResponseRedirect(reverse("cues:dashboard"))


@require_http_methods(["GET", "POST"])
def your_days_off(request: HttpRequest) -> HttpResponse:
    """The composer's own days off, for every project (profile menu)."""
    data = request.POST if request.method == "POST" else None
    days_off = day_off_forms.days_off_formset(data)
    if data is not None and day_off_forms.days_off_ok(days_off):
        day_off_forms.save_days_off(days_off)
        messages.success(request, "Saved your days off.")
        return HttpResponseRedirect(reverse("cues:days_off"))
    return render(
        request,
        "cues/days_off.html",
        {
            "days_off": days_off,
            "can_edit": roles.at_least(
                getattr(request.user, "role", ""), Role.EDITOR
            ),
        },
    )


@require_http_methods(["GET", "POST"])
def app_settings(request: HttpRequest) -> HttpResponse:
    """How ReelLife looks and behaves for everyone (App Settings)."""
    settings = models.AppSettings.load()
    form = settings_forms.AppSettingsForm(
        request.POST if request.method == "POST" else None, instance=settings
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved the app settings.")
        return HttpResponseRedirect(reverse("cues:app_settings"))
    read_only = not roles.at_least(
        getattr(request.user, "role", ""), Role.EDITOR
    )
    return render(
        request,
        "cues/app_settings.html",
        {"nav": "settings", "form": form, "read_only": read_only},
    )
