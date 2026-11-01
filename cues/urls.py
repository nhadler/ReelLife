"""URLs for projects and cues."""

from django.urls import path

from cues.api import views as api
from cues.views import activity
from cues.views import backup
from cues.views import calendar_feed
from cues.views import conform
from cues.views import cue_details as details
from cues.views import cue_sheets
from cues.views import dashboard
from cues.views import dialogs
from cues.views import exports
from cues.views import help as help_pages
from cues.views import imports
from cues.views import overview
from cues.views import picture
from cues.views import project_notes
from cues.views import project_settings
from cues.views import projects
from cues.views import schedule
from cues.views import search
from cues.views import settings
from cues.views import shares
from cues.views import stats
from cues.views import tools
from cues.views import trash

app_name = "cues"

urlpatterns = [
    path("", overview.overview, name="overview"),
    path("projects/", dashboard.dashboard, name="dashboard"),
    path("projects/bulk/", dashboard.bulk, name="projects_bulk"),
    path("backup/", backup.backup_page, name="backup"),
    path("backup/download/", backup.backup_download, name="backup_download"),
    path("projects/new/", projects.project_create, name="project_create"),
    path("projects/<int:project_id>/", projects.project_detail, name="project"),
    path(
        "projects/<int:project_id>/cues/new/", dialogs.new_cue, name="new_cue"
    ),
    path(
        "projects/<int:project_id>/export/",
        exports.export_dialog,
        name="export",
    ),
    path(
        "projects/<int:project_id>/export/download/",
        exports.export_download,
        name="export_download",
    ),
    path(
        "projects/<int:project_id>/import/",
        imports.import_dialog,
        name="import",
    ),
    path(
        "projects/<int:project_id>/import/preview/",
        imports.import_preview,
        name="import_preview",
    ),
    path(
        "projects/<int:project_id>/import/apply/",
        imports.import_apply,
        name="import_apply",
    ),
    path(
        "projects/<int:project_id>/cues/suggest-number/",
        dialogs.suggest_number,
        name="suggest_number",
    ),
    path(
        "projects/<int:project_id>/tools/<slug:name>/",
        tools.tool,
        name="tool",
    ),
    path("cues/<int:cue_id>/", details.cue_detail, name="cue"),
    path(
        "cues/<int:cue_id>/thumbnail/",
        details.cue_thumbnail_upload,
        name="cue_thumbnail_upload",
    ),
    path(
        "cues/<int:cue_id>/thumbnail/<str:name>",
        details.cue_thumbnail,
        name="cue_thumbnail",
    ),
    path(
        "cues/<int:cue_id>/fields/<str:field>/",
        details.cue_field,
        name="cue_field",
    ),
    path("cues/<int:cue_id>/compare/", details.cue_compare, name="cue_compare"),
    path(
        "cues/<int:cue_id>/history/",
        details.cue_history_panel,
        name="cue_history",
    ),
    path(
        "cues/<int:cue_id>/history/<int:change_set_id>/revert/",
        details.revert,
        name="cue_revert",
    ),
    path("cues/<int:cue_id>/delete/", details.delete_cue, name="cue_delete"),
    path(
        "versions/<int:version_id>/fields/<str:field>/",
        details.version_field,
        name="version_field",
    ),
    path(
        "versions/<int:version_id>/make-current/",
        details.make_current,
        name="version_make_current",
    ),
    path(
        "versions/<int:version_id>/delete/",
        details.delete_version,
        name="version_delete",
    ),
    path(
        "cues/<int:cue_id>/versions/new/",
        dialogs.new_version,
        name="new_version",
    ),
    path(
        "cues/<int:cue_id>/schedule-on/",
        dialogs.schedule_on,
        name="schedule_on",
    ),
    path(
        "projects/<int:project_id>/thumbnail/<str:name>",
        projects.thumbnail,
        name="thumbnail",
    ),
    path("help/", help_pages.page, name="help"),
    path("help/<slug:slug>/", help_pages.page, name="help_topic"),
    path("projects/<int:project_id>/picture/", picture.picture, name="picture"),
    path(
        "projects/<int:project_id>/picture/place/",
        picture.place_cues,
        name="place_cues",
    ),
    path(
        "projects/<int:project_id>/picture/new/",
        picture.turnover_create,
        name="turnover_create",
    ),
    path(
        "projects/<int:project_id>/picture/<int:turnover_id>/",
        picture.turnover_edit,
        name="turnover_edit",
    ),
    path(
        "projects/<int:project_id>/picture/<int:turnover_id>/delete/",
        picture.turnover_delete,
        name="turnover_delete",
    ),
    path(
        "projects/<int:project_id>/header/",
        api.project_header,
        name="project_header",
    ),
    path(
        "projects/<int:project_id>/notes/",
        project_notes.page,
        name="project_notes",
    ),
    path(
        "projects/<int:project_id>/notes/panel/",
        project_notes.panel,
        name="project_notes_panel",
    ),
    path(
        "projects/<int:project_id>/notes/<str:kind>/",
        project_notes.save,
        name="project_note_save",
    ),
    path(
        "projects/<int:project_id>/stats/",
        stats.panel,
        name="stats_panel",
    ),
    path(
        "projects/<int:project_id>/stats/deadline/",
        stats.choose_deadline,
        name="stats_deadline",
    ),
    path("api/projects/<int:project_id>/grid/", api.grid_data, name="api_grid"),
    path("api/cues/json/", api.cues_json, name="api_cues_json"),
    path("api/search/", search.results, name="api_search"),
    path("api/cues/<int:cue_id>/", api.edit_cue, name="api_cue"),
    path(
        "api/projects/<int:project_id>/cues/edit/",
        api.edit_cues,
        name="api_cues_edit",
    ),
    path("api/projects/<int:project_id>/undo/", api.undo_last, name="api_undo"),
    path(
        "api/projects/<int:project_id>/views/",
        api.create_view,
        name="api_views",
    ),
    path(
        "api/projects/<int:project_id>/views/<int:view_id>/",
        api.change_view,
        name="api_view",
    ),
    path(
        "projects/<int:project_id>/settings/",
        settings.general,
        name="settings",
    ),
    path(
        "projects/<int:project_id>/settings/picture/",
        settings.picture,
        name="settings_picture",
    ),
    path(
        "projects/<int:project_id>/settings/cue-sheet/",
        settings.cue_sheet,
        name="settings_cue_sheet",
    ),
    path(
        "projects/<int:project_id>/settings/lists/",
        settings.lists,
        name="settings_lists",
    ),
    path(
        "projects/<int:project_id>/settings/departments/",
        settings.departments,
        name="settings_departments",
    ),
    path(
        "projects/<int:project_id>/settings/views/",
        project_settings.views,
        name="settings_views",
    ),
    path(
        "projects/<int:project_id>/settings/views/change/",
        project_settings.view_action,
        name="settings_view_action",
    ),
    path(
        "projects/<int:project_id>/settings/views/copy/",
        project_settings.copy_from,
        name="settings_copy_from",
    ),
    path(
        "projects/<int:project_id>/settings/project/",
        project_settings.project_page,
        name="settings_project",
    ),
    path(
        "projects/<int:project_id>/settings/project/duplicate/",
        project_settings.duplicate,
        name="project_duplicate",
    ),
    path(
        "projects/<int:project_id>/settings/project/archive/",
        project_settings.archive,
        name="project_archive",
    ),
    path(
        "projects/<int:project_id>/settings/project/delete/",
        project_settings.delete,
        name="project_delete",
    ),
    path(
        "projects/<int:project_id>/cue-sheet/",
        cue_sheets.dialog,
        name="cue_sheet",
    ),
    path(
        "projects/<int:project_id>/cue-sheet/checklist/",
        cue_sheets.checklist,
        name="cue_sheet_checklist",
    ),
    path(
        "projects/<int:project_id>/cue-sheet/download/",
        cue_sheets.download,
        name="cue_sheet_download",
    ),
    path("projects/<int:project_id>/conform/", conform.page, name="conform"),
    path(
        "projects/<int:project_id>/activity/",
        activity.page,
        name="activity",
    ),
    path(
        "projects/<int:project_id>/activity/<int:change_set_id>/undo/",
        activity.undo,
        name="activity_undo",
    ),
    path("projects/<int:project_id>/trash/", trash.page, name="trash"),
    path(
        "projects/<int:project_id>/trash/<int:cue_id>/restore/",
        trash.restore,
        name="trash_restore",
    ),
    path(
        "projects/<int:project_id>/conform/shift/",
        conform.shift,
        name="conform_shift",
    ),
    path(
        "projects/<int:project_id>/conform/mark/",
        conform.mark,
        name="conform_mark",
    ),
    path("settings/", project_settings.app_settings, name="app_settings"),
    path("days-off/", project_settings.your_days_off, name="days_off"),
    path("schedule/", schedule.schedule, name="schedule"),
    path("calendar-feed/", calendar_feed.feed_page, name="calendar_feed"),
    path(
        "calendar-feed/schedule.ics",
        calendar_feed.download,
        name="calendar_download",
    ),
    path(
        "calendar/<str:token>.ics",
        calendar_feed.feed,
        name="calendar",
    ),
    path("credits/", shares.credits_page, name="credits"),
    path("credits/undo/", shares.credits_undo, name="credits_undo"),
    path(
        "credits/export.<str:kind>",
        shares.credits_export,
        name="credits_export",
    ),
    path("cues/<int:cue_id>/shares/", shares.cue_shares, name="cue_shares"),
    path(
        "projects/<int:project_id>/settings/cue-sheet/apply-shares/",
        shares.apply_defaults,
        name="apply_default_shares",
    ),
    path(
        "projects/<int:project_id>/settings/statuses/",
        settings.statuses,
        name="settings_statuses",
    ),
    path(
        "projects/<int:project_id>/settings/deadlines/",
        settings.deadlines,
        name="settings_deadlines",
    ),
]
