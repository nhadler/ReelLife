"""Admin for every cues model, for admins only.

Timecodes are shown and typed in the project's frame rate; projects
created here get the default statuses and sub statuses.
"""

from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from simple_history.admin import SimpleHistoryAdmin

from cues import forms
from cues import models
from cues import timecode
from cues.services import projects


def _tc(frames: int | None, project: models.Project) -> str:
    if frames is None:
        return ""
    return timecode.format_timecode(frames, project.rate)


class DeadlineInline(admin.TabularInline):  # type: ignore[type-arg]
    """Deadlines on the project page."""

    model = models.Deadline
    extra = 0


class PictureInline(admin.TabularInline):  # type: ignore[type-arg]
    """Pictures on the project page."""

    model = models.Picture
    extra = 0
    fields = ("name", "order", "archived")


class StatusOptionInline(admin.TabularInline):  # type: ignore[type-arg]
    """Status list on the project page."""

    model = models.StatusOption
    extra = 0
    fields = ("label", "kind", "color", "order")


class SubStatusInline(admin.TabularInline):  # type: ignore[type-arg]
    """The Sub Status list on the project page."""

    model = models.SubStatusOption
    extra = 0
    fields = ("label", "color", "order")


@admin.register(models.Series)
class SeriesAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Series."""

    list_display = ("title", "production_number")
    search_fields = ("title", "production_number")


@admin.register(models.Project)
class ProjectAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Projects, with pictures, deadlines, statuses and sub statuses inline."""

    list_display = (
        "title",
        "production_number",
        "kind",
        "frame_rate",
        "drop_frame",
        "archived",
    )
    list_filter = ("kind", "archived")
    search_fields = ("title", "production_number")
    inlines = [
        PictureInline,
        DeadlineInline,
        StatusOptionInline,
        SubStatusInline,
    ]

    def save_model(
        self, request: HttpRequest, obj: models.Project, form: Any, change: bool
    ) -> None:
        """Saves, then seeds default statuses and sub statuses on creation."""
        super().save_model(request, obj, form, change)
        if not change:
            projects.seed_project(obj)


class PictureVersionInline(admin.TabularInline):  # type: ignore[type-arg]
    """Each picture's IN/OUT on the turnover page."""

    model = models.PictureVersion
    form = forms.PictureVersionForm
    extra = 0
    fields = ("picture", "tc_in", "tc_out", "notes")


@admin.register(models.Turnover)
class TurnoverAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Turnovers, with each picture's IN/OUT inline."""

    list_display = ("label", "project", "received_date")
    list_filter = ("project",)
    inlines = [PictureVersionInline]


@admin.register(models.Picture)
class PictureAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Pictures (reels, acts, clips)."""

    list_display = ("name", "project", "order", "archived")
    list_filter = ("project", "archived")


@admin.register(models.PictureVersion)
class PictureVersionAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """A picture's IN/OUT in one turnover."""

    form = forms.PictureVersionForm
    list_display = ("picture", "turnover", "in_tc", "out_tc")
    list_filter = ("turnover__project",)

    @admin.display(description="IN")
    def in_tc(self, obj: models.PictureVersion) -> str:
        """IN as a timecode."""
        return _tc(obj.tc_in, obj.picture.project)

    @admin.display(description="OUT")
    def out_tc(self, obj: models.PictureVersion) -> str:
        """OUT as a timecode."""
        return _tc(obj.tc_out, obj.picture.project)


@admin.register(models.StatusOption)
class StatusOptionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Status list entries."""

    list_display = ("label", "project", "kind", "color", "order")
    list_filter = ("project", "kind")


@admin.register(models.SubStatusOption)
class SubStatusAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Sub statuses."""

    list_display = ("label", "project", "color", "order")
    list_filter = ("project",)


@admin.register(models.ThemeOption, models.InstrumentOption)
class ListOptionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Themes and instruments."""

    list_display = ("name", "project", "color", "order")
    list_filter = ("project",)


@admin.register(models.DepartmentStatus)
class DepartmentStatusAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """The department status lists."""

    list_display = ("name", "field", "project", "color", "order")
    list_filter = ("project", "field")


@admin.register(models.Party)
class PartyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Writers and publishers."""

    list_display = ("name", "kind", "society", "ipi", "is_mine")
    list_filter = ("kind", "society", "is_mine")
    search_fields = ("name", "ipi")


@admin.register(models.CueShare)
class CueShareAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Cues' writer and publisher shares."""

    list_display = ("cue", "party", "role", "share")
    list_filter = ("cue__project", "role")


@admin.register(models.SeriesShare)
class SeriesShareAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Series' default shares."""

    list_display = ("series", "party", "role", "share")
    list_filter = ("series",)


@admin.register(models.DefaultShare)
class DefaultShareAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Projects' default shares."""

    list_display = ("project", "party", "role", "share")
    list_filter = ("project",)


@admin.register(models.ProjectNote)
class ProjectNoteAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Project notes, one per kind."""

    list_display = ("project", "kind", "updated_at")
    list_filter = ("project", "kind")


@admin.register(models.VersionTheme)
class VersionThemeAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Which themes each version uses."""

    list_display = ("version", "theme", "seconds", "order")
    list_select_related = ("version__cue", "theme")


@admin.register(models.CueInstrument)
class CueInstrumentAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Each cue's instrumentation."""

    list_display = ("cue", "instrument", "seconds", "order")
    list_select_related = ("cue", "instrument")


class CueVersionInline(admin.StackedInline):  # type: ignore[type-arg]
    """Versions on the cue page."""

    model = models.CueVersion
    form = forms.CueVersionForm
    extra = 0


@admin.register(models.Cue)
class CueAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Cues, with versions inline."""

    form = forms.CueForm
    list_display = (
        "number",
        "title",
        "project",
        "picture",
        "status",
        "in_tc",
        "out_tc",
        "current_version",
        "active",
    )
    list_filter = ("project", "status__kind", "active")
    list_select_related = ("project", "picture", "status", "current_version")
    search_fields = ("number", "title")
    inlines = [CueVersionInline]

    @admin.display(description="IN", ordering="tc_in")
    def in_tc(self, obj: models.Cue) -> str:
        """IN as a timecode."""
        return _tc(obj.tc_in, obj.project)

    @admin.display(description="OUT", ordering="tc_out")
    def out_tc(self, obj: models.Cue) -> str:
        """OUT as a timecode."""
        return _tc(obj.tc_out, obj.project)

    def formfield_for_foreignkey(
        self, db_field: Any, request: HttpRequest, **kwargs: Any
    ) -> Any:
        """Limits current_version to this cue's own versions."""
        if db_field.name == "current_version":
            match = request.resolver_match
            cue_id = match.kwargs.get("object_id") if match else None
            queryset: QuerySet[models.CueVersion] = (
                models.CueVersion.objects.none()
            )
            if cue_id is not None:
                queryset = models.CueVersion.objects.filter(cue_id=cue_id)
            kwargs["queryset"] = queryset
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(models.CueVersion)
class CueVersionAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Cue versions."""

    form = forms.CueVersionForm
    list_display = ("cue", "label", "written_to", "review_status")
    list_filter = ("cue__project", "review_status")


@admin.register(models.View)
class ViewAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Saved grid views; ones without a project are global."""

    list_display = ("name", "project", "order")


@admin.register(models.Deadline)
class DeadlineAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Deadlines."""

    list_display = ("__str__", "project", "kind", "end_date")


@admin.register(models.DayOff)
class DayOffAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Days off, skipped in pace maths."""

    list_display = ("date", "project", "note")
    list_filter = ("project",)


@admin.register(models.SchedulePin)
class SchedulePinAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Cues fixed to a day of the schedule."""

    list_display = ("cue", "date")


@admin.register(models.WritingLog)
class WritingLogAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """How much of each cue was written on a day."""

    list_display = ("date", "cue", "seconds", "revise")
    list_filter = ("date",)


@admin.register(models.ReviewLink)
class ReviewLinkAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Review links. The token and password hash aren't shown."""

    list_display = ("cue", "expires_on", "comments_open", "last_opened_at")
    exclude = ("token", "password")


@admin.register(models.ReviewComment)
class ReviewCommentAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Comments left on a version's review page."""

    list_display = ("version", "name", "member", "frame", "created_at")


@admin.register(models.ScheduleEntry)
class ScheduleEntryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Saved daily plans: a log, read only."""

    list_display = ("date", "cue", "music", "effort", "revise")
    list_filter = ("date",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Only the schedule writes them."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: models.ScheduleEntry | None = None
    ) -> bool:
        """What was planned isn't edited after the fact."""
        return False


@admin.register(models.CalendarFeed)
class CalendarFeedAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Calendar links; delete one to revoke it."""

    list_display = ("user", "created_at", "last_used_at")
    readonly_fields = ("user", "token_hash", "created_at", "last_used_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Links are made by their user, on the Calendar Feed page."""
        return False


@admin.register(models.AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """The single settings row."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Only one row, created on first use."""
        return not models.AppSettings.objects.exists()

    def has_delete_permission(
        self, request: HttpRequest, obj: models.AppSettings | None = None
    ) -> bool:
        """The row is never deleted."""
        return False


@admin.register(models.ChangeSet)
class ChangeSetAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Imports and tool runs, read-only; undo them from the project."""

    list_display = ("label", "project", "user", "created_at", "undone_at")
    list_filter = ("project",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Change sets are written by imports and tools only."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: models.ChangeSet | None = None
    ) -> bool:
        """They are a record; editing would break undo."""
        return False
