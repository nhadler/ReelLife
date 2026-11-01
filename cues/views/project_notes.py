"""Project notes: a page, a side panel and per-kind dialogs.

Each note saves itself when it changes, like the cue page's fields: the
textarea posts with the `updated_at` it was shown with, so an edit made
elsewhere meanwhile is refused rather than overwritten; the refused
text stays in the box beside the saved one. Saves are change sets, so
Undo reverts them.
"""

from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import models
from cues.services import history
from cues.services import summaries

STALE = (
    "These notes changed elsewhere while you were typing. Your text is "
    "still in the box; the saved text is below it. Change the box again "
    "to save yours over it."
)


def _notes(project: models.Project) -> list[models.ProjectNote]:
    """Every kind, in order, saved or not (unsaved ones aren't stored)."""
    saved = {note.kind: note for note in project.notes.all()}
    return [
        saved.get(kind) or models.ProjectNote(project=project, kind=kind)
        for kind in models.NoteKind.values
    ]


def _kind(value: str) -> str:
    if value not in models.NoteKind.values:
        raise Http404
    return value


def _can_edit(request: HttpRequest) -> bool:
    return roles.at_least(getattr(request.user, "role", ""), Role.EDITOR)


@require_GET
def page(request: HttpRequest, project_id: int) -> HttpResponse:
    """All of a project's notes on one page."""
    project = get_object_or_404(models.Project, pk=project_id)
    return render(
        request,
        "cues/notes/page.html",
        {
            "nav": "projects",
            "project": project,
            "summary": summaries.for_project(project),
            "tab": "notes",
            "notes": _notes(project),
            "can_edit": _can_edit(request),
        },
    )


@require_GET
def panel(request: HttpRequest, project_id: int) -> HttpResponse:
    """The notes for the side panel, or one kind (?kind=) for a dialog."""
    project = get_object_or_404(models.Project, pk=project_id)
    kind = request.GET.get("kind")
    notes = _notes(project)
    if kind:
        notes = [n for n in notes if n.kind == _kind(kind)]
    return render(
        request,
        "cues/notes/dialog.html" if kind else "cues/notes/panel.html",
        {"project": project, "notes": notes, "can_edit": _can_edit(request)},
    )


@role_required(Role.EDITOR)
@require_POST
def save(request: HttpRequest, project_id: int, kind: str) -> HttpResponse:
    """Saves one kind's notes; answers with that note, saved or refused."""
    project = get_object_or_404(models.Project, pk=project_id)
    kind = _kind(kind)
    note = project.notes.filter(kind=kind).first() or models.ProjectNote(
        project=project, kind=kind
    )
    shown = request.POST.get("updated_at", "")
    text = request.POST.get("text", "")
    current = note.updated_at.isoformat() if note.pk else ""
    error = ""
    draft = None
    if shown != current:
        # Keep the draft in the box, now marked as seen against the saved
        # text, so saving again is a deliberate overwrite.
        error, draft = STALE, text
    elif text != note.text:
        user = request.user if isinstance(request.user, User) else None
        label = f"Edit project notes: {models.NoteKind(kind).label}"
        with history.recording(project, label, user):
            note.text = text
            note.full_clean()
            note.save()
    return render(
        request,
        "cues/notes/note.html",
        {
            "project": project,
            "note": note,
            "can_edit": True,
            "error": error,
            "saved": not error,
            "draft": draft,
        },
    )
