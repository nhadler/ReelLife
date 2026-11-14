"""Domain model.

Two rules carry the design:

1. Timecodes are integer frames at the project's nominal rate, never
   strings (cues.timecode converts).
2. A cue describes a moment in the picture; a version describes one
   delivery.

Cross-row rules a database constraint can't express (a cue's picture
belongs to the same project, and so on) live in each model's clean(),
which forms and services call through full_clean().
"""

import re

from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from cues import colors
from cues import columns as grid_columns
from cues import defaults
from cues import timecode
from cues.model_base import _FRAMES
from cues.model_base import _HEX_COLOR
from cues.model_base import FrameCheck
from cues.model_base import Timestamped
from cues.model_base import _history
from cues.model_base import _past_the_end
from cues.model_base import _timecode_constraints
from cues.services import thumbnails


class Series(Timestamped):
    """A TV series grouping episode projects."""

    title = models.CharField(max_length=200)
    production_number = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name_plural = "series"
        ordering = ["title"]

    def __str__(self) -> str:
        """Returns the title."""
        return self.title


class Project(Timestamped):
    """A film, or one episode of a series."""

    class Kind(models.TextChoices):
        FILM = "film", "Film"
        EPISODE = "episode", "Series Episode"

    class PictureType(models.TextChoices):
        LONG_PLAY = "long_play", "Long Play"
        REELS = "reels", "Reels"
        ACTS = "acts", "Acts"
        CLIPS = "clips", "Clips"

    FRAME_RATES = [(label, label) for label in timecode.RATES]
    DROP_FRAME_RATES = ("29.97", "59.94")

    kind = models.CharField(
        max_length=10, choices=Kind.choices, default=Kind.FILM
    )
    title = models.CharField(max_length=200)
    sub_title = models.CharField(max_length=200, blank=True)
    production_number = models.CharField(
        max_length=50, blank=True, help_text="e.g. BH101"
    )
    series = models.ForeignKey(
        Series,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="episodes",
    )
    season = models.CharField(max_length=20, blank=True)
    episode_number = models.CharField(max_length=20, blank=True)
    frame_rate = models.CharField(
        max_length=6, choices=FRAME_RATES, default="23.976"
    )
    drop_frame = models.BooleanField(
        default=False, help_text="Only for 29.97 and 59.94."
    )
    picture_type = models.CharField(
        max_length=10, choices=PictureType.choices, default=PictureType.REELS
    )
    lanes = models.PositiveSmallIntegerField(
        default=2,
        validators=[validators.MaxValueValidator(8)],
        help_text="Timeline lanes, 0 to 8.",
    )
    production_company = models.CharField(max_length=200, blank=True)
    distributor = models.CharField(max_length=200, blank=True)
    release_date = models.DateField(null=True, blank=True)
    country = models.CharField(max_length=100, blank=True)
    # More of the cue sheet header (RapidCue's).
    aka = models.CharField("program title AKA(s)", max_length=200, blank=True)
    episode_title = models.CharField(max_length=200, blank=True)
    company_address = models.TextField("production company address", blank=True)
    network = models.CharField("network/source", max_length=200, blank=True)
    cue_sheet_category = models.CharField(
        "category",
        max_length=40,
        blank=True,
        choices=[(name, name) for name in defaults.CUE_SHEET_CATEGORIES],
    )
    cue_sheet_version = models.CharField(
        "version",
        max_length=20,
        blank=True,
        choices=[(name, name) for name in defaults.CUE_SHEET_VERSIONS],
        help_text="Where this cut is shown, e.g. Theatrical.",
    )
    preparer = models.CharField(
        "cue sheet prepared by", max_length=200, blank=True
    )
    preparer_email = models.EmailField("preparer's email", blank=True)
    cue_number_pattern = models.CharField(
        max_length=200,
        default=defaults.DEFAULT_CUE_NUMBER_PATTERN,
        help_text="Regular expression a valid cue number matches.",
    )
    archived = models.BooleanField(default=False)
    # Tints its pages and colours it on the Schedule; blank picks one.
    color = models.CharField(
        max_length=10, choices=colors.Color.choices, blank=True, default=""
    )
    daw_start = models.IntegerField(
        "DAW project start",
        null=True,
        blank=True,
        validators=_FRAMES,
        help_text=(
            "The timecode at the start of the DAW project, for Cubase and "
            "Nuendo imports; remembered from the last one."
        ),
    )
    sync_review_status = models.BooleanField(
        "sync status with review status",
        default=False,
        help_text=(
            "Approving a composed cue's current version sets the cue to the "
            "first Approved status."
        ),
    )
    cue_thumbnail_fallback = models.BooleanField(
        "use the project thumbnail for cues",
        default=True,
        help_text="Cues without a thumbnail of their own show this one.",
    )
    # The deadline the daily pace counts down to, chosen in the stats
    # panel; None means the next composition deadline.
    pace_deadline = models.ForeignKey(
        "Deadline",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
    )
    # Stored only after cues.services.thumbnails re-encodes the upload;
    # served by a signed-in view, never directly.
    thumbnail = models.FileField(
        upload_to="thumbnails/",
        storage=thumbnails.storage,
        blank=True,
        editable=False,
    )

    history = _history()

    class Meta:
        ordering = ["title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(drop_frame=False)
                | Q(frame_rate__in=("29.97", "59.94")),
                name="project_drop_frame_rate",
            ),
            models.CheckConstraint(
                condition=Q(lanes__lte=8), name="project_lanes_max_8"
            ),
        ]

    def __str__(self) -> str:
        """Returns e.g. 'BH101 The Big Heist'."""
        return f"{self.production_number} {self.title}".strip()

    @property
    def tone(self) -> int:
        """Its tone, 0-7 (colors.tone)."""
        return colors.tone(self.pk, self.color)

    @property
    def long_play(self) -> bool:
        """One continuous picture, with no reels, acts or clips."""
        return self.picture_type == self.PictureType.LONG_PLAY

    @property
    def reel_word(self) -> str:
        """What a picture is called here: Reel, Act, Clip, or Picture."""
        return _PICTURE_WORDS[self.picture_type][0]

    @property
    def reels_word(self) -> str:
        """The plural: Reels, Acts, Clips, or Pictures."""
        return _PICTURE_WORDS[self.picture_type][1]

    def picture_name_problem(self, name: str) -> str:
        """Why a picture can't be called `name` here, or ''.

        The rules: reels and acts have numbers (1, 2), clips letters
        and numbers (A12); long play has one picture, whatever its name.
        """
        if self.picture_type in (self.PictureType.REELS, self.PictureType.ACTS):
            if not (name.isascii() and name.isdigit()):
                return f"{self.reel_word} names are numbers, like 1 or 2."
        if self.picture_type == self.PictureType.CLIPS:
            if not (name.isascii() and name.isalnum()):
                return "Clip names are letters and numbers, like A12."
        return ""

    def picture_title(self, name: str) -> str:
        """A picture as the app names it: 'Reel 1', 'Act 2', or 'Picture'.

        The placeholder for cues not yet placed reads 'No Reel Yet'.
        """
        if name == PLACEHOLDER_PICTURE:
            return f"No {self.reel_word} Yet"
        if self.long_play:
            return "Picture"
        return f"{self.reel_word} {name}"

    @property
    def thumbnail_name(self) -> str:
        """The thumbnail's file name, part of its URL so caches refresh."""
        name = self.thumbnail.name or ""
        return name.rsplit("/", 1)[-1]

    @property
    def rate(self) -> timecode.Rate:
        """The project's frame rate for timecode maths."""
        return timecode.rate_for(self.frame_rate, drop_frame=self.drop_frame)

    def clean(self) -> None:
        """Checks drop-frame and the cue number pattern.

        Raises:
            ValidationError: On drop-frame at a rate without it, or a cue
                number pattern that isn't a valid regular expression.
        """
        errors = {}
        if self.drop_frame and self.frame_rate not in self.DROP_FRAME_RATES:
            errors["drop_frame"] = "Drop-frame is only used at 29.97 or 59.94."
        try:
            re.compile(self.cue_number_pattern)
        except re.error as exc:
            errors["cue_number_pattern"] = f"Not a valid pattern: {exc}"
        if errors:
            raise ValidationError(errors)


# Picture type -> (word, plural) for the app's labels.
_PICTURE_WORDS = {
    "reels": ("Reel", "Reels"),
    "acts": ("Act", "Acts"),
    "clips": ("Clip", "Clips"),
    "long_play": ("Picture", "Pictures"),
}

# The picture that holds cues not yet placed (services.projects).
PLACEHOLDER_PICTURE = "N/A"


class Turnover(Timestamped):
    """One picture delivery (`c1030`, `LOCK`), covering one or more reels."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="turnovers"
    )
    label = models.CharField(max_length=50)
    received_date = models.DateField()
    notes = models.TextField(blank=True)

    history = _history()

    class Meta:
        ordering = ["received_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "label"], name="turnover_label_unique"
            ),
        ]

    def __str__(self) -> str:
        """Returns the label."""
        return self.label


class Picture(Timestamped):
    """A reel, act, clip or the long-play timeline."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="pictures"
    )
    name = models.CharField(max_length=50, help_text='e.g. "1" or "A12"')
    order = models.PositiveIntegerField(default=0)
    archived = models.BooleanField(default=False)

    history = _history()

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], name="picture_name_unique"
            ),
        ]

    @property
    def title(self) -> str:
        """As the app names it: 'Reel 1', 'Act 2', 'Clip A12' or 'Picture'."""
        return self.project.picture_title(self.name)

    def clean(self) -> None:
        """Checks the name fits the project's picture type.

        Reels and acts are numbered, clips are letters and numbers, and a
        long-play project has one picture. The "N/A" placeholder is exempt.

        Raises:
            ValidationError: On the name.
        """
        if not self.project_id or self.name == PLACEHOLDER_PICTURE:
            return
        problem = self.project.picture_name_problem(self.name)
        if not problem and self.project.long_play:
            others = self.project.pictures.exclude(pk=self.pk).exclude(
                name=PLACEHOLDER_PICTURE
            )
            if others.exists():
                problem = (
                    "A long-play project has one picture; log its new IN "
                    "and OUT instead."
                )
        if problem:
            raise ValidationError({"name": problem})

    def __str__(self) -> str:
        """Returns the name."""
        return self.name

    def current_version(self) -> "PictureVersion | None":
        """Returns the version from the latest turnover that includes it."""
        return (
            self.versions.order_by("-turnover__received_date", "-turnover_id")
            .select_related("turnover")
            .first()
        )


class PictureVersion(Timestamped):
    """A picture's IN/OUT as delivered in one turnover."""

    picture = models.ForeignKey(
        Picture, on_delete=models.CASCADE, related_name="versions"
    )
    turnover = models.ForeignKey(
        Turnover, on_delete=models.CASCADE, related_name="picture_versions"
    )
    tc_in = models.IntegerField(null=True, blank=True, validators=_FRAMES)
    tc_out = models.IntegerField(null=True, blank=True, validators=_FRAMES)
    notes = models.TextField(blank=True)
    # Set when the conform assistant shifts the reel's cues to this
    # picture, so the shift can't be applied twice; undo clears it.
    cues_shifted = models.BooleanField(default=False, editable=False)

    history = _history()

    class Meta:
        ordering = ["picture", "turnover"]
        constraints = [
            models.UniqueConstraint(
                fields=["picture", "turnover"],
                name="picture_version_one_per_turnover",
            ),
            *_timecode_constraints("picture_version"),
        ]

    def __str__(self) -> str:
        """Returns e.g. '1 · c1030'."""
        return f"{self.picture} · {self.turnover}"

    def clean(self) -> None:
        """Checks the turnover's project and the timecodes' range.

        Raises:
            ValidationError: Naming each field that's wrong.
        """
        errors = (
            _past_the_end(self, self.picture.project.rate, "tc_in", "tc_out")
            if self.picture_id
            else {}
        )
        if (
            self.picture_id
            and self.turnover_id
            and self.picture.project_id != self.turnover.project_id
        ):
            errors["turnover"] = "Turnover is from a different project."
        if errors:
            raise ValidationError(errors)


class StatusKind(models.TextChoices):
    """The 11 global status slots; drive totals and colours."""

    TO_DO = "to_do", "To Do"
    IN_PROGRESS = "in_progress", "In Progress"
    REDO = "redo", "Redo"
    CHANGES = "changes", "Changes"
    DONE = "done", "Done"
    APPROVED = "approved", "Approved"
    COMPLETED = "completed", "Completed"
    ON_HOLD = "on_hold", "On Hold"
    OMIT = "omit", "Omit"
    SONG = "song", "Song"
    LICENSE = "license", "License"


# kind -> (category, counts as composed, in totals)
STATUS_KIND_INFO: dict[str, tuple[str, bool, bool]] = {
    StatusKind.TO_DO: ("unapproved", False, True),
    StatusKind.IN_PROGRESS: ("unapproved", False, True),
    StatusKind.REDO: ("unapproved", False, True),
    StatusKind.CHANGES: ("unapproved", True, True),
    StatusKind.DONE: ("unapproved", True, True),
    StatusKind.APPROVED: ("approved", True, True),
    StatusKind.COMPLETED: ("approved", True, True),
    StatusKind.ON_HOLD: ("inactive", False, False),
    StatusKind.OMIT: ("inactive", False, False),
    StatusKind.SONG: ("song", False, True),
    StatusKind.LICENSE: ("license", False, True),
}


class StatusOption(Timestamped):
    """One entry in a project's editable Status list."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="statuses"
    )
    label = models.CharField(max_length=50)
    kind = models.CharField(max_length=15, choices=StatusKind.choices)
    color = models.CharField(max_length=7, validators=[_HEX_COLOR])
    order = models.PositiveIntegerField(default=0)

    history = _history()

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "status"
        verbose_name_plural = "statuses"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "label"], name="status_label_unique"
            ),
        ]

    def __str__(self) -> str:
        """Returns the label."""
        return self.label

    @property
    def category(self) -> str:
        """unapproved, approved, inactive, song or license."""
        return STATUS_KIND_INFO[self.kind][0]

    @property
    def counts_as_composed(self) -> bool:
        """Whether cues with this status count toward 'composed'."""
        return STATUS_KIND_INFO[self.kind][1]

    @property
    def in_totals(self) -> bool:
        """Whether cues with this status count in project totals."""
        return STATUS_KIND_INFO[self.kind][2]


class SubStatusOption(Timestamped):
    """One entry in a project's Sub Status list, with its colour.

    A version has at most one; typing a new
    name in the grid adds it to the list.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="sub_statuses"
    )
    label = models.CharField(max_length=50)
    color = models.CharField(max_length=7, validators=[_HEX_COLOR])
    order = models.PositiveIntegerField(default=0)

    history = _history()

    class Meta:
        ordering = ["order", "label"]
        verbose_name = "sub status"
        verbose_name_plural = "sub statuses"
        constraints = [
            models.UniqueConstraint(
                "project",
                Lower("label"),
                name="sub_status_label_unique",
                violation_error_message=(
                    "This project already has that sub status."
                ),
            ),
        ]

    def __str__(self) -> str:
        """Returns the label."""
        return self.label


# Fields of a cue's department tracking, each status with the default list
# a new project starts with (its own lists: DepartmentStatus).
DEPARTMENT_STATUSES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "orchestration_status": defaults.ORCHESTRATION_STATUSES,
    "recording_status": defaults.RECORDING_STATUSES,
    "mix_status": defaults.MIX_STATUSES,
    "music_edit_status": defaults.MUSIC_EDIT_STATUSES,
    "licensing_status": defaults.LICENSING_STATUSES,
}
DEPARTMENT_STATUS_FIELDS = tuple(DEPARTMENT_STATUSES)
DEPARTMENT_FLAG_FIELDS = ("score", "parts", "printed", "clicks", "prelays")
DEPARTMENT_TEXT_FIELDS = (
    "orchestration_version",
    "orchestration_notes",
    "recording_notes",
    "mix_version",
    "mix_notes",
)


class Priority(models.TextChoices):
    """Cue priority."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Difficulty(models.TextChoices):
    """Cue difficulty."""

    EASY = "Easy"
    MODERATE = "Moderate"
    HARD = "Hard"


class Cue(Timestamped):
    """A moment in the picture that gets music.

    Timecodes here are the cue's current position against the current
    picture; each version keeps the timecodes it was written to.
    """

    class CueType(models.TextChoices):
        SCORE = "SCORE", "Score"
        SONG = "SONG", "Song"
        SOURCE = "SOURCE", "Source"

    class Usage(models.TextChoices):
        BI = "BI", "Background Instrumental"
        BV = "BV", "Background Vocal"
        VI = "VI", "Visual Instrumental"
        VV = "VV", "Visual Vocal"
        VD = "VD", "Visual Dance"

    class UsageTheme(models.TextChoices):
        LOGO = "LOGO", "Logo"
        MT = "MT", "Main Title Theme"
        ET = "ET", "End Title Theme"
        OT = "OT", "Opening Theme"
        CT = "CT", "Closing Theme"
        ST = "ST", "Segment Theme"
        BUMP = "BUMP", "Bumper"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="cues"
    )
    picture = models.ForeignKey(
        Picture, on_delete=models.PROTECT, related_name="cues"
    )
    number = models.CharField("cue #", max_length=20)
    title = models.CharField(max_length=200, blank=True)
    cue_type = models.CharField(
        max_length=6, choices=CueType.choices, default=CueType.SCORE
    )
    status = models.ForeignKey(
        StatusOption,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cues",
    )
    tc_in = models.IntegerField("IN", null=True, blank=True, validators=_FRAMES)
    tc_out = models.IntegerField(
        "OUT", null=True, blank=True, validators=_FRAMES
    )
    seq_start = models.IntegerField(
        "seq. start", null=True, blank=True, validators=_FRAMES
    )
    current_version = models.ForeignKey(
        "CueVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    active = models.BooleanField(
        default=True, help_text="Inactive cues are greyed and not counted."
    )
    lane = models.PositiveSmallIntegerField(default=0)
    scene = models.CharField(max_length=200, blank=True)
    priority = models.CharField(
        max_length=6, choices=Priority.choices, blank=True
    )
    difficulty = models.CharField(
        max_length=8, choices=Difficulty.choices, blank=True
    )
    usage = models.CharField(
        max_length=2, choices=Usage.choices, default=Usage.BI
    )
    usage_theme = models.CharField(
        max_length=4, choices=UsageTheme.choices, blank=True
    )
    plays_as_source = models.BooleanField(default=False)
    interrupted_use = models.BooleanField(default=False)
    artist = models.CharField(max_length=200, blank=True)
    cue_sheet_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Overrides the title on cue sheets.",
    )
    cue_sheet_notes = models.TextField(blank=True)
    # Re-encoded like the project thumbnail. Replaced files are kept
    # so undo can restore them.
    thumbnail = models.FileField(
        upload_to="thumbnails/",
        storage=thumbnails.storage,
        blank=True,
        editable=False,
    )
    # Department tracking, as the Orchestration, Recording and Mixing
    # views show it.
    # The department statuses hold a label from the project's list for
    # that department (DepartmentStatus), checked where cues are edited.
    orchestration_status = models.CharField(max_length=30, blank=True)
    score = models.BooleanField(default=False)
    parts = models.BooleanField(default=False)
    printed = models.BooleanField(default=False)
    orchestration_version = models.CharField(max_length=20, blank=True)
    orchestration_notes = models.TextField(blank=True)
    recording_status = models.CharField(max_length=30, blank=True)
    clicks = models.BooleanField(default=False)
    prelays = models.BooleanField(default=False)
    recording_notes = models.TextField(blank=True)
    mix_status = models.CharField(max_length=30, blank=True)
    mix_version = models.CharField(max_length=20, blank=True)
    mix_notes = models.TextField(blank=True)
    music_edit_status = models.CharField(max_length=30, blank=True)
    licensing_status = models.CharField(max_length=30, blank=True)

    history = _history()

    class Meta:
        ordering = ["picture__order", "tc_in", "number"]
        constraints = [
            # Cue numbers repeat only on inactive cues (e.g. after a merge).
            models.UniqueConstraint(
                "project",
                Lower("number"),
                condition=Q(active=True),
                name="cue_number_unique_among_active",
                violation_error_message=(
                    "Another active cue already has this number."
                ),
            ),
            *_timecode_constraints("cue"),
            FrameCheck(
                condition=Q(seq_start__isnull=True) | Q(seq_start__gte=0),
                name="cue_seq_start_non_negative",
            ),
        ]

    def __str__(self) -> str:
        """Returns e.g. '1m01 Main Title'."""
        return f"{self.number} {self.title}".strip()

    @property
    def thumbnail_name(self) -> str:
        """The thumbnail's file name, part of its URL so caches refresh."""
        return (self.thumbnail.name or "").rsplit("/", 1)[-1]

    @property
    def duration(self) -> int | None:
        """OUT minus IN in frames, or None while either is unset."""
        if self.tc_in is None or self.tc_out is None:
            return None
        return self.tc_out - self.tc_in

    def clean(self) -> None:
        """Checks related rows belong to this cue's project, and timecodes.

        Raises:
            ValidationError: Naming each field that points elsewhere or is
                past the last timecode.
        """
        errors = (
            _past_the_end(
                self, self.project.rate, "tc_in", "tc_out", "seq_start"
            )
            if self.project_id
            else {}
        )
        if self.picture_id and self.picture.project_id != self.project_id:
            errors["picture"] = "Picture is from a different project."
        if (
            self.status is not None
            and self.status.project_id != self.project_id
        ):
            errors["status"] = "Status is from a different project."
        if (
            self.current_version is not None
            and self.current_version.cue_id != self.pk
        ):
            errors["current_version"] = "Version belongs to another cue."
        if errors:
            raise ValidationError(errors)


# CueVersion fields that a new version copies from the previous one, and
# that the new cue dialog fills in for v1.
VERSION_NOTE_FIELDS = (
    "in_note",
    "out_note",
    "spotting_notes",
    "temp",
    "scene_description",
    "key",
    "bpm",
    "meter",
    "notes",
)


class CueVersion(Timestamped):
    """One delivery of a cue: what was written, to which picture."""

    class ReviewStatus(models.TextChoices):
        NO_STATUS = "no_status", "No Status"
        NEEDS_REVIEW = "needs_review", "Needs Review"
        REVIEWED = "reviewed", "Reviewed"
        IN_PROGRESS = "in_progress", "In Progress"
        APPROVED = "approved", "Approved"
        ON_HOLD = "on_hold", "On Hold"
        OMIT = "omit", "Omit"

    cue = models.ForeignKey(
        Cue, on_delete=models.CASCADE, related_name="versions"
    )
    label = models.CharField(max_length=20, help_text="e.g. v1, v2, v1.1")
    written_to = models.ForeignKey(
        PictureVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cue_versions",
    )
    tc_in = models.IntegerField("IN", null=True, blank=True, validators=_FRAMES)
    tc_out = models.IntegerField(
        "OUT", null=True, blank=True, validators=_FRAMES
    )
    seq_start = models.IntegerField(
        "seq. start", null=True, blank=True, validators=_FRAMES
    )
    change_notes = models.TextField(blank=True)
    review_status = models.CharField(
        max_length=12,
        choices=ReviewStatus.choices,
        default=ReviewStatus.NO_STATUS,
    )
    sub_status = models.ForeignKey(
        SubStatusOption,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    file_link = models.CharField(
        max_length=500, blank=True, help_text="URL or path to the bounce."
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    # Notes that describe this delivery. A new version
    # starts as a copy of the previous one's.
    in_note = models.CharField("IN note", max_length=200, blank=True)
    out_note = models.CharField("OUT note", max_length=200, blank=True)
    spotting_notes = models.TextField(blank=True)
    temp = models.CharField(max_length=200, blank=True)
    scene_description = models.TextField(blank=True)
    key = models.CharField(max_length=30, blank=True)
    bpm = models.DecimalField(
        "BPM", max_digits=6, decimal_places=2, null=True, blank=True
    )
    meter = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    # What to change in this revision. Not carried to the
    # next version: each revision has its own.
    revision_notes = models.TextField(blank=True)

    history = _history()

    class Meta:
        ordering = ["cue", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cue", "label"], name="cue_version_label_unique"
            ),
            *_timecode_constraints("cue_version"),
        ]

    def __str__(self) -> str:
        """Returns e.g. '1m01 v2'."""
        return f"{self.cue.number} {self.label}"

    @property
    def duration(self) -> int | None:
        """OUT minus IN in frames, or None while either is unset."""
        if self.tc_in is None or self.tc_out is None:
            return None
        return self.tc_out - self.tc_in

    def clean(self) -> None:
        """Checks the picture version's project and the timecodes' range.

        Raises:
            ValidationError: Naming each field that's wrong.
        """
        errors = (
            _past_the_end(
                self, self.cue.project.rate, "tc_in", "tc_out", "seq_start"
            )
            if self.cue_id
            else {}
        )
        if (
            self.written_to is not None
            and self.cue_id
            and self.written_to.picture.project_id != self.cue.project_id
        ):
            errors["written_to"] = (
                "Picture version is from a different project."
            )
        if errors:
            raise ValidationError(errors)


class View(Timestamped):
    """A saved grid view: columns, sort and filter.

    A view with no project is global and offered in every project.
    """

    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="views",
    )
    name = models.CharField(max_length=50)
    # [{"id": "tc_in", "width": 110, "pinned": "left"}, ...]
    columns = models.JSONField(default=list)
    # [{"id": "tc_in", "dir": "asc"}, ...]
    sort = models.JSONField(default=list, blank=True)
    filter = models.JSONField(default=dict, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], name="view_name_unique"
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(project__isnull=True),
                name="global_view_name_unique",
            ),
        ]

    def __str__(self) -> str:
        """Returns the name."""
        return self.name

    def clean(self) -> None:
        """Checks every column id is one the grid knows.

        Raises:
            ValidationError: Listing unknown or malformed columns.
        """
        if not isinstance(self.columns, list):
            raise ValidationError({"columns": "Columns must be a list."})
        unknown = [
            str(entry)
            for entry in self.columns
            if not isinstance(entry, dict)
            or entry.get("id") not in grid_columns.BY_ID
        ]
        if unknown:
            raise ValidationError(
                {"columns": f"Unknown columns: {', '.join(unknown)}"}
            )


class ChangeSet(models.Model):
    """One user action that may change many rows: an import, a tool run.

    History rows written while it is recording point at it, so it can be
    undone as a whole (cues.services.history.undo).
    """

    # None for changes to what every project shares (the Credits list).
    project = models.ForeignKey(
        Project, models.CASCADE, null=True, related_name="change_sets"
    )
    label = models.CharField(max_length=200)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    undo_of = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="undone_by",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        """Returns the label."""
        return self.label


# Themes and instrumentation live in cues.lists, project notes in
# cues.notes (both refer to the models above by name); importing them
# here registers them and lets the rest of the app use them as
# models.ThemeOption and so on.
# pylint: disable=wrong-import-position,useless-import-alias,unused-import
from cues.app_settings import AppSettings as AppSettings  # noqa: E402
from cues.lists import CueInstrument as CueInstrument  # noqa: E402
from cues.lists import DepartmentStatus as DepartmentStatus  # noqa: E402
from cues.lists import InstrumentOption as InstrumentOption  # noqa: E402
from cues.lists import ThemeOption as ThemeOption  # noqa: E402
from cues.lists import VersionTheme as VersionTheme  # noqa: E402
from cues.notes import NoteKind as NoteKind  # noqa: E402
from cues.notes import ProjectNote as ProjectNote  # noqa: E402
from cues.review import ReviewComment as ReviewComment  # noqa: E402
from cues.review import ReviewLink as ReviewLink  # noqa: E402
from cues.schedule import CalendarFeed as CalendarFeed  # noqa: E402
from cues.schedule import DayOff as DayOff  # noqa: E402
from cues.schedule import Deadline as Deadline  # noqa: E402
from cues.schedule import ScheduleEntry as ScheduleEntry  # noqa: E402
from cues.schedule import SchedulePin as SchedulePin  # noqa: E402
from cues.schedule import WritingLog as WritingLog  # noqa: E402
from cues.shares import CueShare as CueShare  # noqa: E402
from cues.shares import DefaultShare as DefaultShare  # noqa: E402
from cues.shares import Party as Party  # noqa: E402
from cues.shares import SeriesShare as SeriesShare  # noqa: E402
