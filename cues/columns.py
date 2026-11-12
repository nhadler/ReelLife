"""The grid's column registry.

Every column a view can show, whether it reads the cue or the cue's
current version, and how it is edited. Pure data with no Django imports,
so saved views, migrations and the grid endpoint all share one list.
"""

import dataclasses
import enum


class Source(enum.StrEnum):
    """Where a column's value comes from."""

    CUE = "cue"
    VERSION = "version"  # the cue's current version
    COMPUTED = "computed"  # derived; read-only


class Editor(enum.StrEnum):
    """How a column is edited in the grid."""

    NONE = "none"
    TEXT = "text"
    MARKDOWN = "markdown"
    TIMECODE = "timecode"
    DURATION = "duration"
    SELECT = "select"
    STATUS = "status"
    TAGS = "tags"
    CHECKBOX = "checkbox"
    NUMBER = "number"


@dataclasses.dataclass(frozen=True)
class Column:
    """One grid column.

    Attributes:
        id: Stable identifier stored in saved views.
        label: Header text.
        source: Cue, current version or computed.
        editor: Inline editor type; NONE for read-only columns.
    """

    id: str
    label: str
    source: Source
    editor: Editor


_C, _V, _X = Source.CUE, Source.VERSION, Source.COMPUTED

COLUMNS: tuple[Column, ...] = (
    Column("thumbnail", "Thumbnail", _C, Editor.NONE),
    Column("sequence_number", "#", _X, Editor.NONE),
    Column("status", "Status", _C, Editor.STATUS),
    Column("percent_written", "% Written", _X, Editor.NONE),
    Column("number", "Cue #", _C, Editor.TEXT),
    Column("title", "Title", _C, Editor.TEXT),
    Column("version", "Cue Version", _V, Editor.NONE),
    Column("sub_status", "Sub Status", _V, Editor.SELECT),
    Column("seq_start", "Seq. Start", _C, Editor.TIMECODE),
    Column("tc_in", "IN", _C, Editor.TIMECODE),
    Column("tc_out", "OUT", _C, Editor.TIMECODE),
    Column("duration", "DUR", _C, Editor.DURATION),
    Column("written_to", "Written to Picture", _V, Editor.SELECT),
    Column("current_picture", "Current Picture", _X, Editor.NONE),
    Column("start_offset", "Start Offset", _X, Editor.NONE),
    Column("duration_difference", "Dur. Diff.", _X, Editor.NONE),
    Column("version_seq_start", "Version Seq. Start", _V, Editor.TIMECODE),
    Column("version_in", "Version IN", _V, Editor.TIMECODE),
    Column("version_out", "Version OUT", _V, Editor.TIMECODE),
    Column("version_duration", "Version Duration", _V, Editor.NONE),
    Column("change_notes", "Conform Notes", _V, Editor.MARKDOWN),
    Column("review_status", "Review Status", _V, Editor.SELECT),
    Column("usage_theme", "Usage Theme", _C, Editor.SELECT),
    Column("usage", "Usage", _C, Editor.SELECT),
    Column("scene", "Scene", _C, Editor.TEXT),
    Column("scene_description", "Scene Description", _V, Editor.MARKDOWN),
    Column("temp", "Temp", _V, Editor.TEXT),
    Column("in_note", "IN Notes", _V, Editor.TEXT),
    Column("out_note", "OUT Notes", _V, Editor.TEXT),
    Column("spotting_notes", "Spotting Notes", _V, Editor.MARKDOWN),
    Column("notes", "Notes", _V, Editor.MARKDOWN),
    Column("revision_notes", "Revision Notes", _V, Editor.MARKDOWN),
    Column("themes", "Theme", _V, Editor.TAGS),
    Column("key", "Key", _V, Editor.TEXT),
    Column("bpm", "BPM", _V, Editor.NUMBER),
    Column("meter", "Meter", _V, Editor.TEXT),
    Column("instrumentation", "Instrumentation", _C, Editor.TAGS),
    Column("orchestration_status", "Orchestration Status", _C, Editor.SELECT),
    Column("score", "Score", _C, Editor.CHECKBOX),
    Column("parts", "Parts", _C, Editor.CHECKBOX),
    Column("printed", "Printed", _C, Editor.CHECKBOX),
    Column("orchestration_version", "Orch. Version", _C, Editor.TEXT),
    Column("orchestration_notes", "Orchestration Notes", _C, Editor.MARKDOWN),
    Column("recording_status", "Recording Status", _C, Editor.SELECT),
    Column("clicks", "Clicks", _C, Editor.CHECKBOX),
    Column("prelays", "Prelays", _C, Editor.CHECKBOX),
    Column("recording_notes", "Recording Notes", _C, Editor.MARKDOWN),
    Column("mix_status", "Mix Status", _C, Editor.SELECT),
    Column("mix_version", "Mix Version", _C, Editor.TEXT),
    Column("mix_notes", "Mix Notes", _C, Editor.MARKDOWN),
    Column("priority", "Priority", _C, Editor.SELECT),
    Column("difficulty", "Difficulty", _C, Editor.SELECT),
    Column("music_edit_status", "Music Edit Status", _C, Editor.SELECT),
    Column("licensing_status", "Licensing Status", _C, Editor.SELECT),
    Column("cue_type", "Cue Type", _C, Editor.SELECT),
    Column("plays_as_source", "Plays as Source", _C, Editor.CHECKBOX),
    Column("interrupted_use", "Interrupted Use", _C, Editor.CHECKBOX),
    Column("artist", "Artist", _C, Editor.TEXT),
    Column("cue_sheet_title", "Cue Sheet Title", _C, Editor.TEXT),
    # From the cue's shares, edited on its page (Credits).
    Column("writers", "Writers", _X, Editor.NONE),
    Column("publishers", "Publishers", _X, Editor.NONE),
    Column("cue_sheet_notes", "Cue Sheet Notes", _C, Editor.MARKDOWN),
    Column("lane", "Lane", _C, Editor.NUMBER),
    Column("id", "ID", _X, Editor.NONE),
    Column("updated_at", "Updated", _X, Editor.NONE),
)

BY_ID: dict[str, Column] = {column.id: column for column in COLUMNS}

# Scene and note fields searched by the grid and Ctrl+K. Ids are model
# field names; their order determines which match Ctrl+K uses for a snippet.
SEARCHED_NOTES: tuple[str, ...] = (
    "scene",
    "scene_description",
    "spotting_notes",
    "notes",
    "revision_notes",
    "change_notes",
    "temp",
    "in_note",
    "out_note",
    "orchestration_notes",
    "recording_notes",
    "mix_notes",
    "cue_sheet_notes",
)
