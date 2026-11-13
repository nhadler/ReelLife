"""The MCP server and its tools.

Tools use the same services and validation as the web app. Each acts
as the signed-in user, with role checks and undoable history. Timecodes
are returned as text in the project's format, matching the grid.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.urls import reverse
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import AnyHttpUrl

from accounts.models import Role
from cues import models
from cues.services import guide
from cues.services import search as search_service
from cues.services import summaries
from mcp_server import auth
from mcp_server import common
from mcp_server import cue_tools
from mcp_server import prompts
from mcp_server import schedule
from mcp_server import setup
from mcp_server import transfers
from mcp_server import writes

INSTRUCTIONS = """\
ReelTime tracks a film or TV composer's music cues. A project is a film
or an episode; its picture comes in reels, delivered as turnovers. A cue
is a moment in the picture (number like 1m01, title, IN and OUT
timecodes); a version is one delivery of that cue. Find things with
`search`, then read them by id.

Changes require the editor role and are recorded in History as made
through MCP. `undo_last_change` reverts the latest change. Bulk tools
(cue_tool, conform_shift, import_cues) first return a preview and a
`confirm` code. Show the preview to the user and call again with the
code only after they approve it. Exports are returned as files.

`schedule` answers "what should I write today?" (or for a range of
days), `pin_cue` fixes a cue to a day, the rest flowing around it, and
`log_written` records a stretch of a cue written (from and to
timecodes) or an amount, which the schedule counts as done.

When someone asks how to do something in ReelTime, read `user_guide`
(the topic list first, then the relevant topic). Use the app's field and
menu names in your answer; offer to use the tools when appropriate.

Projects, their cue lists and cue sheets are also resources:
reeltime://projects, reeltime://projects/{id}, .../{id}/cues and
.../{id}/cue-sheet."""

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, open_world_hint=False
)


def search(query: str) -> list[dict[str, Any]]:
    """Projects and cues matching every word of the query.

    Words match a cue's number, title or project, else its scene or
    notes in any version; a cue found by its notes says where
    (`matched_in`, e.g. 'Spotting Notes (v1)') with a `snippet`.
    """
    common.user(Role.VIEWER)
    return [
        {
            "kind": hit.kind,
            "id": hit.id,
            "number": hit.number,
            "title": hit.label,
            "project" if hit.kind == "cue" else "type": hit.detail,
            "inactive_or_archived": bool(hit.tag),
            "url": common.url(hit.url),
        }
        | (
            {"matched_in": hit.field, "snippet": hit.snippet}
            if hit.field
            else {}
        )
        for hit in [
            *search_service.projects(query),
            *search_service.cues(query),
        ]
    ]


def list_projects(include_archived: bool = False) -> list[dict[str, Any]]:
    """Every project, latest changed first."""
    common.user(Role.VIEWER)
    projects = models.Project.objects.order_by("archived", "-updated_at")
    if not include_archived:
        projects = projects.filter(archived=False)
    return [common.project_brief(project) for project in projects]


def get_project(project_id: int) -> dict[str, Any]:
    """A project's details, reels, progress and next deadline."""
    common.user(Role.VIEWER)
    project = common.project(project_id)
    rate = project.rate
    summary = summaries.for_project(project)
    reels = []
    for picture in project.pictures.order_by("order", "name"):
        current = picture.current_version()
        reels.append(
            {
                "id": picture.pk,
                "name": picture.name,
                "turnover": str(current.turnover) if current else "",
                "in": common.tc(current.tc_in if current else None, rate),
                "out": common.tc(current.tc_out if current else None, rate),
            }
        )
    deadline = summary.next_deadline
    return {
        **common.project_brief(project),
        "frame_rate": rate.label,
        "reels": reels,
        "composed": summary.composed,
        "remaining": summary.remaining,
        "percent_composed": summary.percent,
        "cues_on_older_picture": summary.needs_conform,
        "next_deadline": f"{deadline.label}, {deadline.date:%Y-%m-%d}"
        if deadline
        else "",
        "url": common.url(reverse("cues:project", args=[project.pk])),
    }


def list_cues(
    project_id: int, include_inactive: bool = False
) -> list[dict[str, Any]]:
    """A project's cues in picture order."""
    common.user(Role.VIEWER)
    project = common.project(project_id)
    cues = project.cues.select_related(
        "picture", "status", "current_version"
    ).order_by("picture__order", "picture__name", "tc_in", "number")
    if not include_inactive:
        cues = cues.filter(active=True)
    return [common.cue_row(cue, project.rate) for cue in cues]


def get_cue(cue_id: int) -> dict[str, Any]:
    """A cue with its notes and every version."""
    common.user(Role.VIEWER)
    cue = common.cue(cue_id)
    rate = cue.project.rate
    versions = [
        {
            "id": version.pk,
            "label": version.label,
            "current": version.pk == cue.current_version_id,
            "written_to": str(version.written_to.turnover)
            if version.written_to
            else "",
            "in": common.tc(version.tc_in, rate),
            "out": common.tc(version.tc_out, rate),
            "spotting_notes": version.spotting_notes,
            "notes": version.notes,
            "revision_notes": version.revision_notes,
            "change_notes": version.change_notes,
        }
        for version in cue.versions.select_related(
            "written_to__turnover"
        ).order_by("created_at", "pk")
    ]
    return {
        **common.cue_row(cue, rate),
        "project": common.project_brief(cue.project),
        "scene": cue.scene,
        "versions": versions,
        "url": common.url(reverse("cues:cue", args=[cue.pk])),
    }


def user_guide(topic: str = "") -> dict[str, Any]:
    """ReelTime's user guide, organised by topic.

    With no topic (or an unknown one), lists the topics; with a topic's
    name, returns that page as Markdown. "all" returns every page.
    """
    common.user(Role.VIEWER)
    wanted = topic.strip().lower()
    if wanted == "all":
        return {"guide": "\n\n".join(t.text for t in guide.topics())}
    found = guide.topic(wanted) if wanted else None
    if found is None:
        listing: dict[str, Any] = {
            "topics": [
                {"topic": t.slug, "title": t.title} for t in guide.topics()
            ]
        }
        if wanted:
            listing["note"] = f"No topic {topic!r}; pick one of these."
        return listing
    return {"title": found.title, "guide": found.text}


TOOLS: tuple[Callable[..., Any], ...] = (
    user_guide,
    search,
    list_projects,
    get_project,
    list_cues,
    get_cue,
)


def _project_id(text: str) -> int:
    """A resource URI's project id, or a ToolError."""
    if not text.isdigit():
        raise ToolError(f"No project {text!r}.")
    return int(text)


def projects_resource() -> list[dict[str, Any]]:
    """Every current project, latest changed first."""
    return list_projects()


def project_resource(project_id: str) -> dict[str, Any]:
    """A project's details, reels, progress and next deadline."""
    return get_project(_project_id(project_id))


def cues_resource(project_id: str) -> list[dict[str, Any]]:
    """A project's active cues in picture order."""
    return list_cues(_project_id(project_id))


def guide_resource() -> str:
    """The whole user guide, as Markdown."""
    return "\n\n".join(t.text for t in guide.topics())


def cue_sheet_resource(project_id: str) -> str:
    """A project's PRO cue sheet as CSV."""
    return transfers.cue_sheet_csv(_project_id(project_id))


# (URI, function, title, MIME type)
RESOURCES: tuple[tuple[str, Callable[..., Any], str, str], ...] = (
    ("reeltime://projects", projects_resource, "Projects", "application/json"),
    ("reeltime://guide", guide_resource, "User Guide", "text/markdown"),
    (
        "reeltime://projects/{project_id}",
        project_resource,
        "Project",
        "application/json",
    ),
    (
        "reeltime://projects/{project_id}/cues",
        cues_resource,
        "Cue List",
        "application/json",
    ),
    (
        "reeltime://projects/{project_id}/cue-sheet",
        cue_sheet_resource,
        "Cue Sheet",
        "text/csv",
    ),
)


def build() -> MCPServer:
    """The server, configured from settings."""
    server = MCPServer(
        name="ReelTime",
        instructions=INSTRUCTIONS,
        token_verifier=auth.TokenVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.OIDC_ISSUER),
            resource_server_url=AnyHttpUrl(auth.resource_url()),
            # auth.check() requires aud == resource_url() itself.
            validate_token_resource=True,
        ),
    )
    reads: list[Callable[..., Any]] = [
        *TOOLS,
        *writes.READ_TOOLS,
        *cue_tools.READ_TOOLS,
        *transfers.READ_TOOLS,
        *schedule.READ_TOOLS,
    ]
    changes: list[Callable[..., Any]] = [
        *setup.TOOLS,
        *writes.TOOLS,
        *cue_tools.TOOLS,
        *transfers.TOOLS,
        *schedule.TOOLS,
    ]
    for fn in reads:
        server.tool(annotations=READ, structured_output=False)(
            common.off_loop(fn)
        )
    for fn in changes:
        server.tool(annotations=WRITE, structured_output=False)(
            common.off_loop(fn)
        )
    for uri, fn, title, mime_type in RESOURCES:
        server.resource(uri, title=title, mime_type=mime_type)(
            common.off_loop(fn)
        )
    for fn, title in prompts.PROMPTS:
        server.prompt(title=title)(fn)
    return server
