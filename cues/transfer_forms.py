"""The import and export dialogs' forms (7.8)."""

from typing import Any, cast

from django import forms

from cues import columns as columns_registry
from cues import models
from cues import timecode
from cues.exporters.cue_sheet import model as cue_sheet
from cues.importers import apply
from cues.importers import merge
from cues.importers import protools
from cues.importers import tabular
from cues.services import imports
from cues.services import long_play


def _choices(form: forms.BaseForm, name: str) -> forms.ModelChoiceField:  # type: ignore[type-arg]
    return cast(forms.ModelChoiceField, form.fields[name])  # type: ignore[type-arg]


class PictureChoiceField(forms.ModelChoiceField):  # type: ignore[type-arg]
    """Pictures by their titles: 'Reel 1', 'Clip A12', 'No Act Yet'."""

    def label_from_instance(self, obj: Any) -> str:
        """The picture's title (its project is selected with it)."""
        return str(obj.title)


class ImportForm(forms.Form):
    """The import dialog's choices.

    The file comes with every step, so `source()` and `options()` can
    rebuild the same preview each time.
    """

    file = forms.FileField(
        label="File", error_messages={"required": "Choose a file to import."}
    )
    kind = forms.ChoiceField(
        label="Import from",
        choices=[
            (imports.Kind.DETECT, "Detect from the file"),
            (imports.Kind.SPREADSHEET, "Excel or CSV"),
            (imports.Kind.PROTOOLS, "Pro Tools session text"),
            (imports.Kind.CUBASE, "Cubase or Nuendo XML"),
            (imports.Kind.MIDI, "MIDI file markers"),
        ],
        initial=imports.Kind.DETECT,
        widget=forms.RadioSelect,
    )
    method = forms.ChoiceField(
        label="Pro Tools cues are",
        choices=[
            (protools.Method.CLIPS, "Clips on cue tracks"),
            (protools.Method.MARKERS, "IN and OUT markers"),
        ],
        initial=protools.Method.CLIPS,
        required=False,
        widget=forms.RadioSelect,
    )
    prefixes = forms.CharField(
        label="Cue tracks are named",
        initial="Cues",
        required=False,
        help_text="Track names start with this; separate several with "
        "commas, e.g. Cues, MX.",
    )
    start = forms.CharField(
        label="Project start",
        required=False,
        help_text="The timecode where the DAW project starts. A MIDI file "
        "with its own SMPTE offset uses that instead.",
    )
    mapping = forms.ChoiceField(
        label="Clip and marker names",
        choices=[
            (
                merge.Mapping.NUMBER_AND_TITLE,
                "Cue # and title (1m01 Main Title)",
            ),
            (merge.Mapping.NUMBER_ONLY, "Cue # only"),
            (merge.Mapping.TITLE_ONLY, "Titles; number the cues in order"),
        ],
        initial=merge.Mapping.NUMBER_AND_TITLE,
        required=False,
    )
    start_number = forms.CharField(
        label="First cue #", initial="1m01", required=False
    )
    picture = PictureChoiceField(
        label="New cues go to",
        queryset=models.Picture.objects.none(),
        required=False,
    )
    update_titles = forms.BooleanField(label="Title", required=False)
    missing = forms.ChoiceField(
        label="Cues not in the file",
        choices=[
            (apply.MissingAction.LEAVE, "Leave them"),
            (apply.MissingAction.INACTIVE, "Make them inactive"),
            (apply.MissingAction.OMIT, "Set them to Omit"),
        ],
        initial=apply.MissingAction.LEAVE,
        required=False,
        widget=forms.RadioSelect,
    )

    def __init__(
        self, *args: Any, project: models.Project, **kwargs: Any
    ) -> None:
        """Offers the project's reels and its remembered Cubase start.

        The reels are named as the picture type has them ('Act 2',
        'No Act Yet'), and so is the choice to place each new cue by its
        IN.
        """
        super().__init__(*args, **kwargs)
        self.project = project
        picture = _choices(self, "picture")
        picture.queryset = project.pictures.select_related("project").order_by(
            "order", "name"
        )
        picture.empty_label = (
            "The picture"
            if project.long_play
            else f"The {project.reel_word.lower()} their IN falls in"
        )
        self.fields["start"].initial = timecode.format_timecode(
            imports.default_start(project), project.rate
        )
        self.fields["start"].widget.attrs["class"] = "tc"

    def clean_file(self) -> Any:
        """Refuses files over the size limit before reading them."""
        upload = self.cleaned_data["file"]
        if upload.size > imports.MAX_BYTES:
            raise forms.ValidationError("Choose a file under 10 MB.")
        return upload

    def clean_start(self) -> int | None:
        """The DAW start as frames."""
        text = self.cleaned_data.get("start", "").strip()
        if not text:
            return None
        try:
            return timecode.parse(text, self.project.rate)
        except timecode.TimecodeError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_start_number(self) -> str:
        """Needs digits to count from, when numbering titles."""
        text = self.cleaned_data.get("start_number", "").strip() or "1m01"
        if self.data.get("mapping") == merge.Mapping.TITLE_ONLY and not any(
            c.isdigit() for c in text
        ):
            raise forms.ValidationError("Use a cue # with a number, e.g. 1m01.")
        return text

    def data_and_kind(self) -> tuple[bytes, imports.Kind]:
        """The uploaded bytes and the kind to read them as."""
        upload = self.cleaned_data["file"]
        upload.seek(0)
        data = upload.read(imports.MAX_BYTES + 1)
        kind = imports.Kind(self.cleaned_data["kind"])
        if kind == imports.Kind.DETECT:
            kind = imports.detect(upload.name or "", data)
        return data, kind

    def source(self, kind: imports.Kind) -> imports.Source:
        """How to read the file, from the source options."""
        prefixes = tuple(
            p.strip()
            for p in self.cleaned_data.get("prefixes", "").split(",")
            if p.strip()
        )
        columns: dict[int, str | None] = {}
        for key, value in self.data.items():
            if key.startswith("column-") and key[7:].isdigit():
                columns[int(key[7:])] = (
                    value if value in tabular.FIELDS else None
                )
        start = self.cleaned_data.get("start")
        return imports.Source(
            kind=kind,
            method=protools.Method(
                self.cleaned_data.get("method") or protools.Method.CLIPS
            ),
            track_prefixes=prefixes or protools.DEFAULT_TRACK_PREFIXES,
            start_frames=imports.default_start(self.project)
            if start is None
            else start,
            columns=columns,
        )

    def options(self, kind: imports.Kind) -> merge.Options:
        """The merge options; spreadsheets have a cue # column."""
        picture = self.cleaned_data.get("picture")
        mapping = merge.Mapping.NUMBER_AND_TITLE
        if kind != imports.Kind.SPREADSHEET:
            mapping = merge.Mapping(
                self.cleaned_data.get("mapping")
                or merge.Mapping.NUMBER_AND_TITLE
            )
        return merge.Options(
            mapping=mapping,
            update_titles=self.cleaned_data.get("update_titles", False),
            start_number=self.cleaned_data.get("start_number") or "1m01",
            picture_id=picture.pk if picture else None,
        )


class ExportForm(forms.Form):
    """The export dialog (9): a cue log or a PDF report."""

    CUSTOM = "custom"

    report = forms.ChoiceField(
        label="Export",
        choices=[
            ("cue_log", "Cue Log"),
            ("wall_chart", "Wall Chart"),
            ("version_notes", "Version Notes"),
        ],
        initial="cue_log",
        required=False,
        widget=forms.RadioSelect,
    )
    format = forms.ChoiceField(
        label="Format",
        choices=[("xlsx", "Excel"), ("csv", "CSV"), ("pdf", "PDF")],
        initial="xlsx",
        required=False,
        widget=forms.RadioSelect,
    )
    preset = forms.ChoiceField(
        label="Columns", initial=CUSTOM, widget=forms.RadioSelect
    )
    columns = forms.MultipleChoiceField(
        label="Columns to include",
        choices=[(c.id, c.label) for c in columns_registry.COLUMNS],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    totals = forms.BooleanField(label="Music totals", required=False)
    progress = forms.BooleanField(label="Progress", required=False)
    exclude_empty = forms.BooleanField(
        label="Leave out empty columns", required=False
    )
    all_versions = forms.BooleanField(label="All versions", required=False)
    scope = forms.ChoiceField(
        choices=[("shown", "Shown"), ("all", "All")],
        initial="all",
        required=False,
        widget=forms.RadioSelect,
    )
    ids = forms.CharField(required=False, widget=forms.HiddenInput)
    # Reels joined into one timeline (services/long_play.py); not
    # offered for long-play projects, which have one picture already.
    long_play = forms.BooleanField(label="Long Play Time", required=False)
    start_hour = forms.ChoiceField(
        label="Start at",
        choices=[("1", "01:00:00:00"), ("0", "00:00:00:00")],
        initial="1",
        required=False,
        widget=forms.RadioSelect,
    )
    leader = forms.CharField(label="Leader", max_length=20, required=False)

    def __init__(
        self,
        *args: Any,
        project: models.Project,
        views: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """Offers each view as a preset, plus a custom list."""
        super().__init__(*args, **kwargs)
        self.project = project
        self.views = {str(view["id"]): view for view in views}
        choices = [(str(v["id"]), v["name"]) for v in views]
        cast(forms.ChoiceField, self.fields["preset"]).choices = [
            *choices,
            (self.CUSTOM, "Choose columns"),
        ]
        leader = self.fields["leader"]
        leader.help_text = (
            f"Time before the first {project.reel_word.lower()}, e.g. "
            "0:00:08:00."
        )
        leader.widget.attrs.update({"class": "tc", "placeholder": "0:00:00:00"})

    def clean(self) -> dict[str, Any]:
        """Reads the leader, when Long Play Time is on."""
        data = super().clean() or {}
        data["long_play_time"] = None
        if data.get("long_play") and not self.project.long_play:
            try:
                data["long_play_time"] = long_play.options(
                    int(data.get("start_hour") or 1),
                    data.get("leader", ""),
                    self.project.rate,
                )
            except ValueError as exc:
                self.add_error("leader", str(exc))
        return data

    def clean_report(self) -> str:
        """The cue log unless another report was asked for."""
        return self.cleaned_data.get("report") or "cue_log"

    def clean_format(self) -> str:
        """Excel unless another format was asked for."""
        return self.cleaned_data.get("format") or "xlsx"

    def clean_ids(self) -> list[int] | None:
        """The grid's shown cue ids, in order, if any came."""
        text = self.cleaned_data.get("ids", "")
        ids = [int(part) for part in text.split(",") if part.strip().isdigit()]
        return ids or None

    def column_ids(self) -> list[str]:
        """The chosen preset's columns, or the custom list."""
        preset = self.cleaned_data["preset"]
        if preset == self.CUSTOM:
            return list(self.cleaned_data["columns"])
        return [entry["id"] for entry in self.views[preset]["columns"]]


class CueSheetForm(forms.Form):
    """The cue sheet export's choices."""

    format = forms.ChoiceField(
        choices=[
            ("rapidcue", "RapidCue (Excel, for ASCAP and BMI)"),
            ("csv", "CSV (one row per writer or publisher)"),
        ],
        initial="rapidcue",
        widget=forms.RadioSelect,
    )
    revision = forms.BooleanField(label="A revised cue sheet", required=False)
    prefix_numbers = forms.BooleanField(
        label="Cue # before each title", required=False
    )
    include_ipi = forms.BooleanField(
        label="IPI/CAE numbers", required=False, initial=True
    )
    include_notes = forms.BooleanField(
        label="Notes (source, interrupted, licensed)", required=False
    )
    include_artist = forms.BooleanField(label="Artist", required=False)
    include_inactive = forms.BooleanField(
        label="Inactive, omitted and on-hold cues", required=False
    )

    def options(self) -> cue_sheet.Options:
        """The choices as cue sheet options (after is_valid())."""
        data = self.cleaned_data
        return cue_sheet.Options(
            revision=data["revision"],
            prefix_numbers=data["prefix_numbers"],
            include_ipi=data["include_ipi"],
            include_notes=data["include_notes"],
            include_inactive=data["include_inactive"],
            include_artist=data["include_artist"],
        )
