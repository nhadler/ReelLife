"""Serialises a project's cues, columns and views for the grid.

Rows are flat: cue fields and the current version's fields side by side,
keyed by column id (cues/columns.py). Timecodes are {frames, display}
objects; select columns hold the stored code and list their options in
the column metadata, so one response carries everything the grid needs.
"""

import dataclasses
from typing import Any

from django.db.models import OuterRef
from django.db.models import Subquery
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from cues import columns
from cues import defaults
from cues import models
from cues import timecode
from cues.services import progress
from cues.services import view_presets

Json = dict[str, Any]

# Computed columns.
READ_ONLY_EDITORS = {columns.Editor.NONE}
EDITABLE = frozenset(
    c.id for c in columns.COLUMNS if c.editor not in READ_ONLY_EDITORS
)

CHOICES: dict[str, list[tuple[str, str]]] = {
    "cue_type": list(models.Cue.CueType.choices),
    "usage": list(models.Cue.Usage.choices),
    "usage_theme": [("", "")] + list(models.Cue.UsageTheme.choices),
    "priority": [("", "")] + list(models.Priority.choices),
    "difficulty": [("", "")] + list(models.Difficulty.choices),
    "review_status": list(models.CueVersion.ReviewStatus.choices),
}
# Chip columns and the project list each draws from.
LISTS = {"themes": "themes", "instrumentation": "instruments"}
# Option colours for the department statuses, drawn as pills like Status.
COLORS: dict[str, dict[str, str]] = {
    "review_status": {
        value: defaults.TAILWIND[color]
        for value, color in defaults.REVIEW_STATUS_COLORS.items()
    },
}


def _picture_label(version: models.PictureVersion) -> str:
    """A picture version's name: 'Reel 1 - c1023'."""
    return f"Reel {version.picture.name} - {version.turnover.label}"


@dataclasses.dataclass(frozen=True)
class Context:
    """Per-request lookups shared by every row."""

    project: models.Project
    rate: timecode.Rate
    leading_zero: bool
    current_versions: dict[int, int]  # picture id -> picture version id
    version_labels: dict[int, str]  # picture version id -> turnover label
    picture_options: dict[int, list[Json]]  # picture id -> written-to options
    sequence: dict[int, int]
    users: dict[int | None, str]  # user id -> display name

    @classmethod
    def build(cls, project: models.Project) -> "Context":
        """Loads the lookups for a project (a few queries)."""
        settings = models.AppSettings.load()
        versions = list(
            models.PictureVersion.objects.filter(picture__project=project)
            .select_related("turnover", "picture")
            .order_by("-turnover__received_date", "-turnover_id")
        )
        options: dict[int, list[Json]] = {}
        for version in versions:
            options.setdefault(version.picture_id, []).append(
                {"value": version.pk, "label": _picture_label(version)}
            )
        return cls(
            project=project,
            rate=project.rate,
            leading_zero=settings.leading_zero,
            current_versions=progress.current_picture_versions(project),
            version_labels={v.pk: _picture_label(v) for v in versions},
            picture_options=options,
            sequence=progress.sequence_numbers(project),
            users={u.pk: str(u) for u in User.objects.all()},
        )

    def tc(self, frames: int | None) -> Json | None:
        """A timecode value for the grid."""
        if frames is None:
            return None
        return {
            "frames": frames,
            "display": timecode.format_timecode(
                frames, self.rate, leading_zero=self.leading_zero
            ),
        }

    def duration(self, tc_in: int | None, tc_out: int | None) -> Json | None:
        """A duration value, or None while IN or OUT is unset."""
        if tc_in is None or tc_out is None:
            return None
        frames = tc_out - tc_in
        return {
            "frames": frames,
            "display": timecode.format_duration(max(frames, 0), self.rate),
        }

    def offset(self, frames: int | None) -> Json | None:
        """A signed offset value."""
        if frames is None:
            return None
        return {
            "frames": frames,
            "display": timecode.format_offset(frames, self.rate),
        }


def cue_thumbnail_url(cue: models.Cue) -> str:
    """The cue's thumbnail, else the project's (unless turned off), else ''.

    Nick, Oct 31: cues inherit the project thumbnail, per project.
    """
    if cue.thumbnail:
        return reverse("cues:cue_thumbnail", args=[cue.pk, cue.thumbnail_name])
    project = cue.project
    if project.cue_thumbnail_fallback and project.thumbnail:
        return reverse(
            "cues:thumbnail", args=[project.pk, project.thumbnail_name]
        )
    return ""


def _chip(option: Any, seconds: int | None) -> Json:
    """A theme or instrument on a cue, for the grid's chips."""
    return {
        "id": option.pk,
        "name": option.name,
        "color": option.color,
        "seconds": seconds,
    }


def _shares(cue: models.Cue, *, writers: bool) -> str:
    """'Casey Composer (C 100%)', one per writer (or publisher)."""
    return "; ".join(
        f"{share.party.name} ({share.role} {share.share.normalize():f}%)"
        for share in cue.shares.all()
        if share.is_writer == writers
    )


def row(cue: models.Cue, ctx: Context) -> Json:
    """One cue as a grid row."""
    version = cue.current_version
    current = ctx.current_versions.get(cue.picture_id)
    conform = progress.conform(
        written_to_id=version.written_to_id if version else None,
        current_picture_version_id=current,
        cue_in=cue.tc_in,
        cue_out=cue.tc_out,
        version_in=version.tc_in if version else None,
        version_out=version.tc_out if version else None,
    )
    needs = conform.needs_conform
    local_updated = timezone.localtime(cue.updated_at)
    return {
        "id": cue.pk,
        "_updated_at": cue.updated_at.isoformat(),
        "_active": cue.active,
        "_needs_conform": needs,
        "_picture": {"id": cue.picture_id, "name": cue.picture.name},
        "_picture_order": cue.picture.order,
        "_lane": cue.lane,
        "lane": cue.lane,
        "_updated_by": ctx.users.get(getattr(cue, "updated_by", None), ""),
        "sequence_number": ctx.sequence.get(cue.pk),
        "thumbnail": cue_thumbnail_url(cue),
        "_thumbnail_inherited": not cue.thumbnail
        and bool(cue_thumbnail_url(cue)),
        "status": cue.status_id,
        "number": cue.number,
        "title": cue.title,
        "version": version.label if version else "",
        "sub_status": version.sub_status_id if version else None,
        "seq_start": ctx.tc(cue.seq_start),
        "tc_in": ctx.tc(cue.tc_in),
        "tc_out": ctx.tc(cue.tc_out),
        "duration": ctx.duration(cue.tc_in, cue.tc_out),
        "written_to": version.written_to_id if version else None,
        "current_picture": ctx.version_labels.get(current or -1, ""),
        "start_offset": ctx.offset(conform.start_offset) if needs else None,
        "duration_difference": ctx.offset(conform.duration_difference)
        if needs
        else None,
        "version_seq_start": ctx.tc(version.seq_start) if version else None,
        "version_in": ctx.tc(version.tc_in) if version else None,
        "version_out": ctx.tc(version.tc_out) if version else None,
        "version_duration": ctx.duration(version.tc_in, version.tc_out)
        if version
        else None,
        "change_notes": version.change_notes if version else "",
        "review_status": version.review_status if version else None,
        "usage_theme": cue.usage_theme,
        "usage": cue.usage,
        "scene": cue.scene,
        "scene_description": version.scene_description if version else "",
        "temp": version.temp if version else "",
        "in_note": version.in_note if version else "",
        "out_note": version.out_note if version else "",
        "spotting_notes": version.spotting_notes if version else "",
        "notes": version.notes if version else "",
        "revision_notes": version.revision_notes if version else "",
        "themes": [
            _chip(link.theme, link.seconds)
            for link in (version.themes.all() if version else [])
        ],
        "instrumentation": [
            _chip(link.instrument, link.seconds)
            for link in cue.instrumentation.all()
        ],
        "key": version.key if version else "",
        "bpm": str(version.bpm)
        if version and version.bpm is not None
        else None,
        "meter": version.meter if version else "",
        "priority": cue.priority,
        "difficulty": cue.difficulty,
        "cue_type": cue.cue_type,
        "plays_as_source": cue.plays_as_source,
        "interrupted_use": cue.interrupted_use,
        "artist": cue.artist,
        "cue_sheet_title": cue.cue_sheet_title,
        "writers": _shares(cue, writers=True),
        "publishers": _shares(cue, writers=False),
        "cue_sheet_notes": cue.cue_sheet_notes,
        **{
            field: getattr(cue, field)
            for field in (
                *models.DEPARTMENT_STATUS_FIELDS,
                *models.DEPARTMENT_FLAG_FIELDS,
                *models.DEPARTMENT_TEXT_FIELDS,
            )
        },
        "updated_at": local_updated.strftime("%-d %b %H:%M"),
    }


def cues_queryset(project: models.Project) -> Any:
    """Every cue with what its row needs, in reel then IN order."""
    return (
        project.cues.select_related(
            "status", "picture", "current_version__written_to"
        )
        .prefetch_related(
            "current_version__themes__theme",
            "instrumentation__instrument",
            "shares__party",
        )
        .annotate(
            # Who saved the latest change (the history's user), for Upd.
            updated_by=Subquery(
                getattr(models.Cue, "history")  # noqa: B009 pylint: disable=no-member
                .filter(id=OuterRef("pk"))
                .order_by("-history_id")
                .values("history_user_id")[:1]
            )
        )
        .order_by("picture__order", "picture__name", "tc_in", "number")
    )


def column_meta(project: models.Project, *, can_edit: bool) -> list[Json]:
    """Every column with its source, editor, editability and options."""
    statuses = [
        {
            "value": s.pk,
            "label": s.label,
            "color": s.color,
            "kind": s.kind,
            "category": s.category,
        }
        for s in project.statuses.all()
    ]
    department: dict[str, list[models.DepartmentStatus]] = {
        field: [] for field in models.DEPARTMENT_STATUS_FIELDS
    }
    for option in project.department_statuses.all():
        department[option.field].append(option)
    meta = []
    for column in columns.COLUMNS:
        entry: Json = {
            "id": column.id,
            "label": column.label,
            "source": column.source.value,
            "editor": column.editor.value,
            "editable": can_edit and column.id in EDITABLE,
        }
        if column.id == "status":
            entry["options"] = statuses
        elif column.id == "sub_status":
            # The project's list; typing a new label adds to it.
            entry["options"] = [
                {"value": o.pk, "label": o.label, "color": o.color}
                for o in project.sub_statuses.all()
            ]
            entry["creatable"] = True
        elif column.id in LISTS:
            entry["options"] = [
                {"value": o.pk, "label": o.name, "color": o.color}
                for o in getattr(project, LISTS[column.id]).all()
            ]
            entry["creatable"] = True
        elif column.id in models.DEPARTMENT_STATUS_FIELDS:
            # The project's own list for that department, as pills.
            entry["options"] = [{"value": "", "label": ""}] + [
                {"value": o.name, "label": o.name, "color": o.color}
                for o in department[column.id]
            ]
        elif column.id in CHOICES:
            colors = COLORS.get(column.id, {})
            entry["options"] = [
                {"value": value, "label": label}
                | ({"color": colors[value]} if value in colors else {})
                for value, label in CHOICES[column.id]
            ]
        meta.append(entry)
    return meta


def view_json(view: models.View) -> Json:
    """A view for the grid: its layout, and what can be done to it."""
    built_in = view.name in view_presets.BUILT_IN
    return {
        "id": view.pk,
        "name": view.name,
        "columns": view.columns,
        "sort": view.sort,
        # Seeded views keep their names and can't be deleted; a project's
        # changed copy of one can be reset.
        "builtIn": built_in,
        "changed": built_in and view.project_id is not None,
    }


def views_for(project: models.Project) -> list[Json]:
    """The seeded views (the project's copies where changed), then its own."""
    return [view_json(view) for view in view_presets.for_project(project)]


VIEWER_VIEW = "Viewer"


def _visible_views(views: list[Json], *, can_edit: bool) -> list[Json]:
    """Viewers get only the Viewer view."""
    if can_edit:
        return views
    return [v for v in views if v["name"] == VIEWER_VIEW] or views


def pictures(project: models.Project, ctx: Context) -> list[Json]:
    """The reels, in order, for group rows and the timeline.

    Each has its current picture version: the turnover's label, the
    version's notes (else the turnover's) and its IN and OUT in frames.
    """
    versions = {
        v.pk: v
        for v in models.PictureVersion.objects.filter(
            pk__in=ctx.current_versions.values()
        ).select_related("turnover")
    }
    result = []
    for picture in project.pictures.filter(archived=False).order_by(
        "order", "name"
    ):
        version = versions.get(ctx.current_versions.get(picture.pk, -1))
        result.append(
            {
                "id": picture.pk,
                "name": picture.name,
                "title": project.picture_title(picture.name),
                "label": version.turnover.label if version else "",
                "notes": (version.notes or version.turnover.notes)
                if version
                else "",
                "tc_in": version.tc_in if version else None,
                "tc_out": version.tc_out if version else None,
            }
        )
    return result


def payload(project: models.Project, *, can_edit: bool) -> Json:
    """The grid's whole initial state."""
    ctx = Context.build(project)
    settings = models.AppSettings.load()
    return {
        "project": {
            "id": project.pk,
            "title": project.title,
            "rate": ctx.rate.label,
        },
        "columns": column_meta(project, can_edit=can_edit),
        "views": _visible_views(views_for(project), can_edit=can_edit),
        "pictures": pictures(project, ctx),
        # Each reel's "written to" choices, once rather than on every row.
        "written_to": {
            str(picture_id): options
            for picture_id, options in ctx.picture_options.items()
        },
        "rows": [row(cue, ctx) for cue in cues_queryset(project)],
        "settings": {
            "single_click_edit": settings.single_click_edit,
            "enter_behaviour": settings.enter_behaviour,
        },
    }
