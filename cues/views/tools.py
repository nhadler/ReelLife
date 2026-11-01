"""The Tools menu's dialogs.

GET loads a tool's form into the page's <dialog> for the cues the grid
sends (`?cues=3,5,8`, in the grid's order). The dialog's preview area
posts the form with `preview` whenever a field changes and shows what
would happen; applying answers 204 with the events that reload the grid
and close the dialog, like the new cue dialog.
"""

from django.core.exceptions import ValidationError
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import models
from cues import tool_forms
from cues.services import authoring
from cues.services import projects as project_service
from cues.services import tools
from cues.views.dialogs import _closed

MAX_CUES = 2000


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _selected(
    request: HttpRequest, project: models.Project
) -> list[models.Cue]:
    """The cues named in `cues`, in the order given.

    Raises:
        Http404: An id that isn't a number or isn't a cue of the project.
    """
    raw = request.POST.get("cues") if request.method == "POST" else None
    raw = raw if raw is not None else request.GET.get("cues", "")
    try:
        ids = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise Http404("Bad cue list.") from exc
    if len(ids) > MAX_CUES:
        raise Http404("Too many cues.")
    found = {
        cue.pk: cue
        for cue in project.cues.filter(pk__in=ids).select_related(
            "picture", "status", "current_version", "project"
        )
    }
    if len(found) != len(set(ids)):
        raise Http404("No such cue in this project.")
    return [found[pk] for pk in dict.fromkeys(ids)]


def _initial(
    request: HttpRequest, name: str, project: models.Project
) -> dict[str, object]:
    """The new cues tool starts in a reel, at its next free number."""
    if name != "new":
        return {}
    pictures = project.pictures.exclude(
        name=project_service.PLACEHOLDER_PICTURE
    ).order_by("order", "name")
    picture = pictures.filter(pk=request.GET.get("picture") or 0).first()
    picture = picture or pictures.first()
    if picture is None:
        return {"status": project_service.default_status(project)}
    return {
        "picture": picture,
        "number": authoring.suggested_number(project, picture),
        "status": project_service.default_status(project),
    }


def _errors(form: tool_forms.ToolForm) -> list[str]:
    messages = [str(error) for error in form.non_field_errors()]
    for name, errors in form.errors.items():
        if name != "__all__":
            label = form[name].label
            messages += [f"{label}: {error}" for error in errors]
    return messages


@role_required(Role.EDITOR)
@require_http_methods(["GET", "POST"])
def tool(request: HttpRequest, project_id: int, name: str) -> HttpResponse:
    """A tool's dialog, its preview, and applying it."""
    form_class = tool_forms.FORMS.get(name)
    if form_class is None:
        raise Http404("No such tool.")
    project = get_object_or_404(models.Project, pk=project_id)
    cues = _selected(request, project)
    data = request.POST if request.method == "POST" else None
    form = form_class(
        data,
        project=project,
        cues=cues,
        initial=_initial(request, name, project),
    )
    bump = None
    if data is not None and isinstance(form, tool_forms.RenumberForm):
        index, _, by = data.get("bump", "").partition(":")
        if index.isdigit() and by in ("1", "-1"):
            bump = int(index), int(by)
            form.bumped(*bump)
    context = {
        "project": project,
        "form": form,
        "name": name,
        "action": reverse("cues:tool", args=[project.pk, name]),
        "cue_ids": ",".join(str(cue.pk) for cue in cues),
        "selection_error": form.selection_error(),
    }
    if data is not None and ("preview" in data or bump is not None):
        valid = form.is_valid()
        context["preview"] = form.preview() if valid else None
        quiet = valid or "opening" in data
        context["errors"] = [] if quiet else _errors(form)
        template = (
            "cues/tools/renumber_bump.html"
            if bump
            else "cues/tools/preview.html"
        )
        return render(request, template, context)
    if data is not None and form.is_valid():
        try:
            form.apply(_user(request))
        except tools.ToolError as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            form.add_error(None, exc.messages)
        else:
            return _closed()
    return render(request, form.template_name, context)
