"""What every MCP tool shares: the caller, lookups, and running in Django.

Tools are written as plain functions over the app's services. `off_loop`
turns one into the async tool the SDK registers: it runs in Django's
sync thread (as Django's own ASGI handler runs views), and labels any
change it records as made through MCP and which client, so History
shows it and Undo reverts it.
"""

from collections.abc import Callable, Iterable, Mapping
import functools
import hashlib
import json
from typing import Any
import urllib.parse

from asgiref.sync import sync_to_async
from django import forms
from django.conf import settings
from django.db import close_old_connections
from django.http import QueryDict
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError

from accounts import roles
from accounts.models import Role
from accounts.models import User
from cues import models
from cues import timecode
from cues.services import history


def user(minimum: Role) -> User:
    """The user the request acts for, if their role is high enough.

    Raises:
        ToolError: No signed-in user, an inactive one, or their role is
            too low.
    """
    token = get_access_token()
    claims = (token.claims or {}) if token else {}
    user_id = claims.get("user_id")
    found = (
        User.objects.filter(pk=user_id).first()
        if isinstance(user_id, int)
        else None
    )
    if (
        found is None
        or not found.is_active
        or not roles.at_least(found.role, minimum)
    ):
        raise ToolError(f"This needs the {minimum.label.lower()} role.")
    return found


def client_label() -> str:
    """The MCP client, for History: "MCP, claude.ai" or just "MCP"."""
    token = get_access_token()
    client = token.client_id if token else ""
    host = urllib.parse.urlsplit(client).hostname if "://" in client else ""
    name = host or client
    return f"MCP, {name[:40]}" if name else "MCP"


def off_loop[**P, T](fn: Callable[P, T]) -> Callable[P, Any]:
    """An async tool that runs `fn` in a worker thread, as MCP.

    Not Django's one thread-sensitive thread: the MCP app has no
    per-request thread context, so every call would queue behind the
    slowest (an import, a PDF) on that worker. Each call instead gets a
    pool thread, with the database connection handled as Django does
    around a request: stale ones closed before, and after.
    """

    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        close_old_connections()
        try:
            with history.via(client_label()):
                return fn(*args, **kwargs)
        finally:
            close_old_connections()

    @functools.wraps(fn)
    async def tool(*args: P.args, **kwargs: P.kwargs) -> T:
        return await sync_to_async(run, thread_sensitive=False)(*args, **kwargs)

    return tool


def tc(frames: int | None, rate: timecode.Rate) -> str:
    """Frames as timecode text, or ''."""
    return "" if frames is None else timecode.format_timecode(frames, rate)


def url(path: str) -> str:
    """An app path as a full link."""
    return settings.BASE_URL.rstrip("/") + path


def project(project_id: int) -> models.Project:
    """The project, or a ToolError."""
    found = models.Project.objects.filter(pk=project_id).first()
    if found is None:
        raise ToolError(f"No project {project_id}.")
    return found


def cue(cue_id: int) -> models.Cue:
    """The cue, or a ToolError."""
    found = (
        models.Cue.objects.select_related(
            "project", "picture", "status", "current_version"
        )
        .filter(pk=cue_id)
        .first()
    )
    if found is None:
        raise ToolError(f"No cue {cue_id}.")
    return found


def cues(project_: models.Project, cue_ids: Iterable[int]) -> list[models.Cue]:
    """The project's cues with these ids, in the order given.

    Raises:
        ToolError: An id that isn't a cue of the project.
    """
    ids = list(dict.fromkeys(cue_ids))
    found = {
        c.pk: c
        for c in project_.cues.filter(pk__in=ids).select_related(
            "picture", "status", "current_version", "project"
        )
    }
    missing = [pk for pk in ids if pk not in found]
    if missing:
        raise ToolError(f"Not cues of this project: {missing}.")
    return [found[pk] for pk in ids]


def project_brief(found: models.Project) -> dict[str, Any]:
    """A project's id, names and state."""
    return {
        "id": found.pk,
        "title": found.title,
        "production_number": found.production_number,
        "kind": found.get_kind_display(),
        "archived": found.archived,
    }


def cue_row(found: models.Cue, rate: timecode.Rate) -> dict[str, Any]:
    """A cue as the grid shows it."""
    version = found.current_version
    return {
        "id": found.pk,
        "number": found.number,
        "title": found.title,
        "reel": found.picture.name,
        "status": found.status.label if found.status else "",
        "type": found.get_cue_type_display(),
        "in": tc(found.tc_in, rate),
        "out": tc(found.tc_out, rate),
        "duration": timecode.format_duration(max(found.duration or 0, 0), rate)
        if found.duration is not None
        else "",
        "version": version.label if version else "",
        "active": found.active,
    }


def form_errors(form: forms.BaseForm) -> str:
    """A form's errors as one sentence per problem."""
    lines = [str(error) for error in form.non_field_errors()]
    for name, errors in form.errors.items():
        if name != "__all__":
            label = form.fields[name].label if name in form.fields else name
            lines += [f"{label or name}: {error}" for error in errors]
    return " ".join(lines) or "Those values aren't valid."


def fingerprint(*parts: Any) -> str:
    """A short digest of a preview, which applying must quote back."""
    payload = json.dumps(parts, default=str, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def as_form_data(values: Mapping[str, Any]) -> QueryDict:
    """Tool arguments as form data: true as 'on', false left out."""
    data = QueryDict(mutable=True)
    for key, value in values.items():
        if isinstance(value, bool):
            if value:
                data[key] = "on"
        elif isinstance(value, list | tuple):
            data.setlist(key, [str(item) for item in value])
        elif value is not None:
            data[key] = str(value)
    return data
