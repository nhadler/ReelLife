"""Forms that edit timecodes as text in the project's frame rate."""

from typing import Any, ClassVar, cast

from django import forms
from django.core.exceptions import ObjectDoesNotExist

from cues import defaults
from cues import models
from cues import numbering
from cues import timecode
from cues.services import thumbnails

TIMECODE_HELP = "H:MM:SS:FF, e.g. 1:00:10:00 (';' before frames for DF)."


class TimecodeModelForm(forms.ModelForm):  # type: ignore[type-arg]
    """A ModelForm whose frame-count fields are typed as timecodes.

    Subclasses list the fields in `timecode_fields` and say where the
    frame rate comes from in `project_for`.
    """

    timecode_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Swaps integer fields for text fields showing timecodes."""
        super().__init__(*args, **kwargs)
        rate = self._rate(self._instance_values())
        for name in self.timecode_fields:
            if name not in self.fields:
                continue
            original = self.fields[name]
            self.fields[name] = forms.CharField(
                label=original.label,
                required=False,
                help_text=TIMECODE_HELP,
            )
            frames = getattr(self.instance, name)
            if frames is not None and rate is not None:
                self.initial[name] = timecode.format_timecode(frames, rate)

    def _instance_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in self.instance._meta.concrete_fields:  # noqa: SLF001 pylint: disable=protected-access
            if field.is_relation:
                try:
                    values[field.name] = getattr(self.instance, field.name)
                except ObjectDoesNotExist:
                    values[field.name] = None
        return values

    def project_for(self, values: dict[str, Any]) -> models.Project | None:
        """Returns the project whose rate applies, from field values.

        Args:
            values: Cleaned data, or the instance's related objects.
        """
        raise NotImplementedError

    def _rate(self, values: dict[str, Any]) -> timecode.Rate | None:
        project = self.project_for(values)
        return None if project is None else project.rate

    def clean(self) -> dict[str, Any]:
        """Parses each timecode field into frames.

        Returns:
            Cleaned data with frame counts (or None) for timecode fields.
        """
        cleaned: dict[str, Any] = super().clean() or {}
        values = {**self._instance_values(), **cleaned}
        rate = self._rate(values)
        for name in self.timecode_fields:
            text = cleaned.get(name)
            if not text:
                cleaned[name] = None
                continue
            if rate is None:
                self.add_error(name, "Choose the project first.")
                continue
            try:
                cleaned[name] = timecode.parse(text, rate)
            except timecode.TimecodeError as exc:
                self.add_error(name, str(exc))
        return cleaned


class CueForm(TimecodeModelForm):
    """Cue with IN, OUT and Seq. Start as timecodes."""

    timecode_fields = ("tc_in", "tc_out", "seq_start")

    class Meta:
        model = models.Cue
        fields = "__all__"

    def project_for(self, values: dict[str, Any]) -> models.Project | None:
        """The cue's project."""
        project = values.get("project")
        return project if isinstance(project, models.Project) else None


class CueVersionForm(TimecodeModelForm):
    """Cue version with its timecodes as text."""

    timecode_fields = ("tc_in", "tc_out", "seq_start")

    class Meta:
        model = models.CueVersion
        fields = "__all__"

    def project_for(self, values: dict[str, Any]) -> models.Project | None:
        """The project of the version's cue."""
        cue = values.get("cue")
        return cue.project if isinstance(cue, models.Cue) else None


class PictureVersionForm(TimecodeModelForm):
    """Picture version with picture IN and OUT as text."""

    timecode_fields = ("tc_in", "tc_out")

    class Meta:
        model = models.PictureVersion
        fields = "__all__"

    def project_for(self, values: dict[str, Any]) -> models.Project | None:
        """The project of the picture or, failing that, the turnover."""
        for name in ("picture", "turnover"):
            owner = values.get(name)
            if isinstance(owner, models.Picture | models.Turnover):
                return owner.project
        return None


# Project pages.


class ProjectForm(forms.ModelForm):  # type: ignore[type-arg]
    """Create or edit a project's general settings and thumbnail."""

    thumbnail_upload = forms.FileField(
        label="Thumbnail",
        required=False,
        help_text="A still from the film: JPEG, PNG, WebP or GIF under 2 MB.",
        widget=forms.FileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp,image/gif"}
        ),
    )
    remove_thumbnail = forms.BooleanField(
        label="Remove the thumbnail", required=False
    )
    # The pattern itself is a regular expression; most people should
    # never see one, so it hides behind "Custom".
    numbering = forms.ChoiceField(
        label="Valid cue numbers",
        choices=[
            ("standard", "Standard: 1m01, 1m04a, 1s01, 1m01s, 101m01"),
            ("any", "Any cue number"),
            ("custom", "Custom pattern (advanced)"),
        ],
        initial="standard",
        required=False,
        widget=forms.RadioSelect,
        help_text="Imports can skip rows whose cue number doesn't fit.",
    )

    def __init__(
        self, *args: Any, only: tuple[str, ...] = (), **kwargs: Any
    ) -> None:
        """Shows the project's pattern as one of the numbering choices.

        Args:
            *args: As for a ModelForm.
            only: The fields a settings page shows (all when empty).
            **kwargs: As for a ModelForm.
        """
        super().__init__(*args, **kwargs)
        if only:
            for name in list(self.fields):
                if name not in only:
                    del self.fields[name]
        if "numbering" not in self.fields:
            return
        pattern = self.instance.cue_number_pattern
        self.initial["numbering"] = {
            defaults.DEFAULT_CUE_NUMBER_PATTERN: "standard",
            defaults.ANY_CUE_NUMBER_PATTERN: "any",
        }.get(pattern, "custom" if self.instance.pk else "standard")
        field = self.fields["cue_number_pattern"]
        field.required = False
        field.label = "Custom pattern"
        field.help_text = (
            "A regular expression, e.g. ^\\d+m\\d+$. "
            "Only for unusual numbering."
        )

    def clean(self) -> dict[str, Any]:
        """Checks a new picture type, and turns numbering into a pattern."""
        cleaned: dict[str, Any] = super().clean() or {}
        self._check_picture_type(cleaned.get("picture_type"))
        if "numbering" not in self.fields:
            return cleaned
        # Without a choice (an older form or a script), a posted pattern
        # is kept as custom.
        choice = cleaned.get("numbering") or (
            "custom" if cleaned.get("cue_number_pattern") else "standard"
        )
        if choice == "standard":
            cleaned["cue_number_pattern"] = defaults.DEFAULT_CUE_NUMBER_PATTERN
        elif choice == "any":
            cleaned["cue_number_pattern"] = defaults.ANY_CUE_NUMBER_PATTERN
        elif not cleaned.get("cue_number_pattern"):
            self.add_error(
                "cue_number_pattern", "Enter a pattern, or pick another choice."
            )
        self.instance.cue_number_pattern = cleaned.get(
            "cue_number_pattern", self.instance.cue_number_pattern
        )
        return cleaned

    def _check_picture_type(self, kind: str | None) -> None:
        """Refuses a picture type the project's pictures don't fit.

        Reels and acts need numbered pictures, clips letters and numbers,
        and long play a single picture (Picture.clean).
        """
        project = self.instance
        if not kind or not project.pk or kind == project.picture_type:
            return
        names = [
            name
            for name in project.pictures.values_list("name", flat=True)
            if name != models.PLACEHOLDER_PICTURE
        ]
        probe = models.Project(picture_type=kind)
        problems = sorted({probe.picture_name_problem(n) for n in names} - {""})
        if probe.long_play and len(names) > 1:
            problems.append(
                f"Long play has one picture, and this project has {len(names)}."
            )
        if problems:
            self.add_error("picture_type", " ".join(problems))

    def clean_thumbnail_upload(self) -> Any:
        """Re-encodes the upload now, so a bad image is a field error."""
        upload = self.cleaned_data.get("thumbnail_upload")
        if not upload:
            return None
        try:
            return thumbnails.save_upload(upload)
        except thumbnails.ThumbnailError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def model_fields(self) -> dict[str, Any]:
        """Cleaned data for the model only (no thumbnail fields)."""
        extra = {"thumbnail_upload", "remove_thumbnail", "numbering"}
        return {k: v for k, v in self.cleaned_data.items() if k not in extra}

    class Meta:
        model = models.Project
        fields = [
            "title",
            "sub_title",
            "production_number",
            "kind",
            "series",
            "season",
            "episode_number",
            "frame_rate",
            "drop_frame",
            "picture_type",
            "lanes",
            "production_company",
            "distributor",
            "release_date",
            "country",
            "aka",
            "episode_title",
            "company_address",
            "network",
            "cue_sheet_category",
            "cue_sheet_version",
            "preparer",
            "preparer_email",
            "cue_number_pattern",
            "sync_review_status",
            "cue_thumbnail_fallback",
        ]
        widgets = {
            "release_date": forms.DateInput(attrs={"type": "date"}),
            "company_address": forms.Textarea(attrs={"rows": 3}),
        }


class DeadlinesForm(forms.Form):
    """The new project's deadlines: start and end for each stage."""

    _DATE = forms.DateInput(attrs={"type": "date"})
    composition_start = forms.DateField(
        label="Start", required=False, widget=_DATE
    )
    composition_end = forms.DateField(label="End", required=False, widget=_DATE)
    orchestration_start = forms.DateField(
        label="Start", required=False, widget=_DATE
    )
    orchestration_end = forms.DateField(
        label="End", required=False, widget=_DATE
    )
    mix_start = forms.DateField(label="Start", required=False, widget=_DATE)
    mix_end = forms.DateField(label="End", required=False, widget=_DATE)
    delivery = forms.DateField(
        label="Delivery date", required=False, widget=_DATE
    )

    KINDS = (
        models.Deadline.Kind.COMPOSITION,
        models.Deadline.Kind.ORCHESTRATION,
        models.Deadline.Kind.MIX,
    )

    def clean(self) -> dict[str, Any]:
        """Each start needs an end on or after it."""
        cleaned: dict[str, Any] = super().clean() or {}
        for kind in self.KINDS:
            start, end = (
                cleaned.get(f"{kind}_start"),
                cleaned.get(f"{kind}_end"),
            )
            if start and not end:
                self.add_error(f"{kind}_end", "Add an end date.")
            elif start and end and end < start:
                self.add_error(f"{kind}_end", "The end is before the start.")
        return cleaned

    def deadlines(self) -> list[dict[str, Any]]:
        """Deadline fields for each one filled in."""
        found = [
            {
                "kind": kind,
                "start_date": self.cleaned_data[f"{kind}_start"],
                "end_date": self.cleaned_data[f"{kind}_end"],
            }
            for kind in self.KINDS
            if self.cleaned_data.get(f"{kind}_end")
        ]
        if self.cleaned_data.get("delivery"):
            found.append(
                {
                    "kind": models.Deadline.Kind.DELIVERY,
                    "start_date": None,
                    "end_date": self.cleaned_data["delivery"],
                }
            )
        return found


class _ReelTimesForm(forms.Form):
    """IN/OUT typed as timecodes, parsed in a given frame rate."""

    tc_in = forms.CharField(label="IN", required=False)
    tc_out = forms.CharField(label="OUT", required=False)

    def __init__(
        self, *args: Any, rate: timecode.Rate | None = None, **kwargs: Any
    ) -> None:
        """Takes the frame rate to parse timecodes with."""
        super().__init__(*args, **kwargs)
        self.rate = rate
        for name in ("tc_in", "tc_out"):
            self.fields[name].widget.attrs.update(
                {"class": "tc", "placeholder": "1:00:00:00"}
            )

    def wants_times(self, cleaned: dict[str, Any]) -> bool:
        """Whether this row must have IN and OUT."""
        raise NotImplementedError

    def _frames(self, name: str) -> int | None:
        text = self.cleaned_data.get(name, "")
        if not text or self.rate is None:
            return None
        try:
            return timecode.parse(text, self.rate)
        except timecode.TimecodeError as exc:
            self.add_error(name, str(exc))
            return None

    def clean(self) -> dict[str, Any]:
        """Parses IN and OUT; a row in use needs both, OUT after IN."""
        cleaned: dict[str, Any] = super().clean() or {}
        tc_in, tc_out = self._frames("tc_in"), self._frames("tc_out")
        cleaned["in_frames"], cleaned["out_frames"] = tc_in, tc_out
        if (
            self.rate is not None
            and not self.errors
            and self.wants_times(cleaned)
        ):
            if tc_in is None or tc_out is None:
                self.add_error("tc_in", "Give its IN and OUT.")
            elif tc_out <= tc_in:
                self.add_error("tc_out", "OUT must be after IN.")
        return cleaned


class ReelRowForm(_ReelTimesForm):
    """A reel by name with its IN/OUT (new project, new reels).

    For a long-play project (`long_play`) the one picture needs no name:
    a row with times is it, named "1".
    """

    name = forms.CharField(max_length=50, required=False)
    field_order = ["name", "tc_in", "tc_out"]

    def __init__(
        self, *args: Any, long_play: bool = False, **kwargs: Any
    ) -> None:
        """Takes whether the project is long play."""
        super().__init__(*args, **kwargs)
        self.long_play = long_play

    def clean(self) -> dict[str, Any]:
        """Names a long-play project's picture when only times are given."""
        if (
            self.long_play
            and not self.cleaned_data.get("name")
            and (
                self.cleaned_data.get("tc_in")
                or self.cleaned_data.get("tc_out")
            )
        ):
            self.cleaned_data["name"] = "1"
        return super().clean()

    def wants_times(self, cleaned: dict[str, Any]) -> bool:
        """A named row is a reel to create."""
        return bool(cleaned.get("name"))


class ReelUpdateForm(_ReelTimesForm):
    """An existing reel in a new turnover: included, with its IN/OUT."""

    include = forms.BooleanField(required=False, initial=True)
    field_order = ["include", "tc_in", "tc_out"]

    def wants_times(self, cleaned: dict[str, Any]) -> bool:
        """Only reels that changed in this turnover."""
        return bool(cleaned.get("include"))


NewReelFormSet = forms.formset_factory(ReelRowForm, extra=3, max_num=40)
ReelUpdateFormSet = forms.formset_factory(ReelUpdateForm, extra=0, max_num=200)


class TurnoverForm(forms.ModelForm):  # type: ignore[type-arg]
    """A new turnover's label, date and notes."""

    class Meta:
        model = models.Turnover
        fields = ["label", "received_date", "notes"]
        widgets = {
            "received_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {"label": "e.g. c1030 or LOCK"}


class FirstTurnoverForm(TurnoverForm):
    """New Project's optional first turnover (Nick, Nov 1).

    Left blank, the project starts without one, as before. A label or a
    named reel means one is wanted: then the label and date are needed,
    and the reels table (a NewReelFormSet) needs a reel.
    """

    class Meta(TurnoverForm.Meta):
        fields = ["label", "received_date"]  # notes can wait for Picture

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Makes every field optional until the turnover is wanted."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def check_wanted(self, reels: Any) -> bool:
        """Whether a turnover was asked for; adds errors if incomplete.

        Args:
            reels: The bound, valid reels formset.

        Returns:
            True when the turnover should be logged.
        """
        named = [row for row in reels.cleaned_data if row.get("name")]
        if not (self.cleaned_data.get("label") or named):
            return False
        if not self.cleaned_data.get("label"):
            self.add_error("label", "Name the turnover, e.g. LOCK.")
        if not self.cleaned_data.get("received_date"):
            self.add_error("received_date", "Give the date it arrived.")
        if not named:
            self.add_error(None, "Add at least one reel with its IN and OUT.")
        return not self.errors


# Colour inputs can't be empty, so blank "add" rows post this; matching it
# as the initial value lets Django skip rows the user didn't touch.
DEFAULT_COLOR = "#6b7280"


class _ColorForm(forms.ModelForm):  # type: ignore[type-arg]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["color"].initial = DEFAULT_COLOR


class StatusForm(_ColorForm):
    """One status in the project's list."""

    class Meta:
        model = models.StatusOption
        fields = ["label", "kind", "color", "order"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class SubStatusForm(_ColorForm):
    """One entry in the project's Sub Status list."""

    class Meta:
        model = models.SubStatusOption
        fields = ["label", "color", "order"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class ThemeForm(_ColorForm):
    """One theme in the project's list."""

    class Meta:
        model = models.ThemeOption
        fields = ["name", "color", "order"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class InstrumentForm(_ColorForm):
    """One instrument in the project's list."""

    class Meta:
        model = models.InstrumentOption
        fields = ["name", "color", "order"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class DeadlineForm(forms.ModelForm):  # type: ignore[type-arg]
    """One deadline."""

    class Meta:
        model = models.Deadline
        fields = ["kind", "label", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Words the empty kind as a prompt rather than dashes."""
        super().__init__(*args, **kwargs)
        kind = cast(forms.ChoiceField, self.fields["kind"])
        kind.choices = [("", "Choose…"), *models.Deadline.Kind.choices]


class DepartmentStatusForm(_ColorForm):
    """One entry in a department's status list."""

    class Meta:
        model = models.DepartmentStatus
        fields = ["name", "color", "order"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class _DepartmentFormSet(forms.BaseInlineFormSet):  # type: ignore[type-arg]
    """One department's entries; new ones join that department."""

    def __init__(self, *args: Any, field: str, **kwargs: Any) -> None:
        self.field = field
        kwargs["queryset"] = models.DepartmentStatus.objects.filter(field=field)
        kwargs["prefix"] = field
        super().__init__(*args, **kwargs)
        for form in self.forms:  # before validation: the unique check
            form.instance.field = field


DepartmentFormSet = forms.inlineformset_factory(
    models.Project,
    models.DepartmentStatus,
    form=DepartmentStatusForm,
    formset=_DepartmentFormSet,
    extra=0,
    can_delete=True,
)
StatusFormSet = forms.inlineformset_factory(
    models.Project,
    models.StatusOption,
    form=StatusForm,
    extra=0,
    can_delete=True,
)
SubStatusFormSet = forms.inlineformset_factory(
    models.Project,
    models.SubStatusOption,
    form=SubStatusForm,
    extra=0,
    can_delete=True,
)
ThemeFormSet = forms.inlineformset_factory(
    models.Project, models.ThemeOption, form=ThemeForm, extra=0, can_delete=True
)
InstrumentFormSet = forms.inlineformset_factory(
    models.Project,
    models.InstrumentOption,
    form=InstrumentForm,
    extra=0,
    can_delete=True,
)


DeadlineFormSet = forms.inlineformset_factory(
    # No blank row: Add Row adds one (inline-table.js).
    models.Project,
    models.Deadline,
    form=DeadlineForm,
    extra=0,
    can_delete=True,
)


# Dialogs (5 "Version rules").


def _choices(form: forms.BaseForm, name: str) -> forms.ModelChoiceField:  # type: ignore[type-arg]
    """A form's model choice field, typed so its queryset can be set."""
    return cast(forms.ModelChoiceField, form.fields[name])  # type: ignore[type-arg]


def _quiet_blanks(form: forms.BaseForm) -> None:
    """Shows an empty choice as a dash rather than Django's '---------'."""
    for field in form.fields.values():
        if isinstance(field, forms.ModelChoiceField):
            # None (no empty choice) stays None.
            field.empty_label = field.empty_label and "—"
        elif isinstance(field, forms.ChoiceField):
            choices = cast(list[tuple[Any, Any]], field.choices)
            field.choices = [
                (value, "—" if value == "" else label)
                for value, label in choices
            ]


DURATION_HELP = "m:ss or a full timecode; sets OUT from IN."
_NOTE_WIDGETS: dict[str, type[forms.Widget] | forms.Widget] = {
    "spotting_notes": forms.Textarea(attrs={"rows": 3}),
    "scene_description": forms.Textarea(attrs={"rows": 2}),
    "notes": forms.Textarea(attrs={"rows": 2}),
}


def _version_note_fields() -> dict[str, forms.Field]:
    """Form fields for a version's notes, from the CueVersion model."""
    return dict(
        forms.fields_for_model(
            models.CueVersion,
            fields=models.VERSION_NOTE_FIELDS,
            widgets=_NOTE_WIDGETS,
        )
    )


def _duration_field() -> forms.CharField:
    return forms.CharField(label="DUR", required=False, help_text=DURATION_HELP)


def _apply_duration(
    form: TimecodeModelForm, cleaned: dict[str, Any], rate: timecode.Rate
) -> None:
    """Fills OUT from IN + DUR, or checks the three agree."""
    text = cleaned.get("duration", "")
    if not text or form.errors:
        return
    try:
        frames = timecode.parse_duration(text, rate)
    except timecode.TimecodeError as exc:
        form.add_error("duration", str(exc))
        return
    tc_in, tc_out = cleaned.get("tc_in"), cleaned.get("tc_out")
    if tc_in is None:
        form.add_error("duration", "Set IN to use a duration.")
    elif tc_out is None:
        cleaned["tc_out"] = tc_in + frames
        form.instance.tc_out = tc_in + frames
    elif tc_out - tc_in != frames:
        form.add_error("duration", "OUT and DUR disagree; clear one of them.")


class NewCueForm(TimecodeModelForm):
    """The new cue dialog: timing first, then spotting and cue sheet."""

    timecode_fields = ("tc_in", "tc_out", "seq_start")
    duration = _duration_field()

    class Meta:
        model = models.Cue
        fields = [
            "number",
            "title",
            "picture",
            "tc_in",
            "tc_out",
            "seq_start",
            "status",
            "scene",
            "priority",
            "difficulty",
            "cue_type",
            "usage",
            "usage_theme",
            "plays_as_source",
            "interrupted_use",
            "artist",
            "cue_sheet_title",
        ]
        widgets: dict[str, type[forms.Widget] | forms.Widget] = {}

    def __init__(
        self, *args: Any, project: models.Project, **kwargs: Any
    ) -> None:
        """Limits pictures, statuses and sub statuses to the project."""
        self.project = project
        kwargs.setdefault("instance", models.Cue(project=project))
        super().__init__(*args, **kwargs)
        _choices(self, "picture").queryset = project.pictures.order_by(
            "order", "name"
        )
        _choices(self, "status").queryset = project.statuses.all()
        # v1's notes and sub status: the dialog fills them in; create_cue
        # stores them on v1.
        self.fields.update(_version_note_fields())
        self.fields["sub_status"] = forms.ModelChoiceField(
            queryset=project.sub_statuses.all(), required=False
        )
        for name in ("tc_in", "tc_out", "seq_start", "duration"):
            self.fields[name].widget.attrs["class"] = "tc"
        # Placeholders show the format; the long help would crowd the
        # dialog's four columns.
        for name in ("tc_in", "tc_out", "seq_start"):
            self.fields[name].help_text = ""
            self.fields[name].widget.attrs["placeholder"] = "1:00:00:00"
        self.fields["duration"].help_text = ""
        self.fields["duration"].widget.attrs["placeholder"] = "0:00"
        for name, hint in (("in_note", "MX IN"), ("out_note", "MX OUT")):
            self.fields[name].widget.attrs["placeholder"] = hint
        _quiet_blanks(self)

    def project_for(self, values: dict[str, Any]) -> models.Project | None:
        """Always the dialog's project."""
        del values
        return self.project

    def clean_number(self) -> str:
        """Holds a new or renamed cue to Valid Cue Numbers.

        A cue keeping its number isn't checked, so one imported before
        the pattern changed can still be edited.
        """
        number: str = self.cleaned_data["number"]
        if self.instance.pk is None or number != self.instance.number:
            problem = numbering.misfit(number, self.project.cue_number_pattern)
            if problem:
                raise forms.ValidationError(problem)
        return number

    def clean(self) -> dict[str, Any]:
        """Fills OUT from IN + DUR, or checks they agree."""
        cleaned = super().clean()
        _apply_duration(self, cleaned, self.project.rate)
        return cleaned


class _TurnoverChoice(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        return f"{obj.turnover.label} ({obj.turnover.received_date:%-d %b %Y})"


class NewVersionForm(TimecodeModelForm):
    """The new version dialog."""

    timecode_fields = ("tc_in", "tc_out", "seq_start")
    duration = _duration_field()
    status = forms.ModelChoiceField(
        queryset=models.StatusOption.objects.none(),
        required=False,
        label="Cue status",
    )
    update_cue = forms.BooleanField(
        label="Also update the cue's current timecodes",
        required=False,
        initial=True,
    )

    class Meta:
        model = models.CueVersion
        fields = [
            "label",
            "written_to",
            "tc_in",
            "tc_out",
            "seq_start",
            "change_notes",
            "review_status",
            "sub_status",
            "revision_notes",
            "file_link",
            "delivered_at",
            *models.VERSION_NOTE_FIELDS,
        ]
        field_classes = {"written_to": _TurnoverChoice}
        widgets = {
            **_NOTE_WIDGETS,
            "change_notes": forms.Textarea(attrs={"rows": 3}),
            "revision_notes": forms.Textarea(attrs={"rows": 3}),
            "delivered_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        labels = {
            "written_to": "Written to picture",
            "change_notes": "Conform notes",
        }

    def __init__(self, *args: Any, cue: models.Cue, **kwargs: Any) -> None:
        """Offers the cue's picture versions, newest first."""
        self.cue = cue
        kwargs.setdefault("instance", models.CueVersion(cue=cue))
        super().__init__(*args, **kwargs)
        _choices(
            self, "written_to"
        ).queryset = cue.picture.versions.select_related("turnover").order_by(
            "-turnover__received_date", "-turnover_id"
        )
        _choices(self, "status").queryset = cue.project.statuses.all()
        _choices(self, "sub_status").queryset = cue.project.sub_statuses.all()
        for name in ("tc_in", "tc_out", "seq_start", "duration"):
            self.fields[name].widget.attrs["class"] = "tc"
        _quiet_blanks(self)

    def project_for(self, values: dict[str, Any]) -> models.Project | None:
        """The cue's project."""
        del values
        return self.cue.project

    def clean(self) -> dict[str, Any]:
        """Fills OUT from IN + DUR, or checks they agree."""
        cleaned = super().clean()
        _apply_duration(self, cleaned, self.cue.project.rate)
        return cleaned


def _save_on_change(form: forms.BaseForm, url_for: Any) -> None:
    """Makes each field post itself to `url_for(name)` when it changes.

    The details page has no Save button: a field saves when
    it loses focus with a new value, and the response replaces it.
    """
    for name, field in form.fields.items():
        field.widget.attrs.update(
            {
                "hx-post": url_for(name),
                "hx-trigger": "change",
                "hx-target": "closest .field",
                "hx-swap": "outerHTML",
                "hx-include": "#cue-updated-at",
                "hx-sync": "closest .details-fields:queue all",
            }
        )


def _read_only(form: forms.BaseForm) -> None:
    for field in form.fields.values():
        field.disabled = True


def _department_lists(form: forms.BaseForm, cue: models.Cue) -> None:
    """Each department status as a choice from the project's own list.

    A value no longer in the list (renamed elsewhere) stays choosable,
    so showing the cue doesn't change it.
    """
    lists: dict[str, list[str]] = {
        field: [] for field in models.DEPARTMENT_STATUS_FIELDS
    }
    for option in cue.project.department_statuses.all():
        lists[option.field].append(option.name)
    for field, names in lists.items():
        current = getattr(cue, field)
        if current and current not in names:
            names.append(current)
        old = form.fields[field]
        form.fields[field] = forms.ChoiceField(
            label=old.label,
            required=False,
            choices=[("", "—"), *((name, name) for name in names)],
        )


class CueDetailsForm(NewCueForm):
    """The cue fields on the details page, each saved on its own."""

    class Meta(NewCueForm.Meta):
        fields = [
            *NewCueForm.Meta.fields,
            "cue_sheet_notes",
            "active",
            *models.DEPARTMENT_STATUS_FIELDS,
            *models.DEPARTMENT_FLAG_FIELDS,
            *models.DEPARTMENT_TEXT_FIELDS,
        ]
        widgets = {
            **NewCueForm.Meta.widgets,
            "cue_sheet_notes": forms.Textarea(attrs={"rows": 2}),
            "orchestration_notes": forms.Textarea(attrs={"rows": 2}),
            "recording_notes": forms.Textarea(attrs={"rows": 2}),
            "mix_notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"active": "Active cue"}
        help_texts = {"active": "Inactive cues are left out of totals."}

    def __init__(
        self, *args: Any, cue: models.Cue, url_for: Any, can_edit: bool
    ) -> None:
        """Shows the cue, with DUR filled in, saving fields on change."""
        super().__init__(*args, project=cue.project, instance=cue)
        for name in (*models.VERSION_NOTE_FIELDS, "sub_status"):
            del self.fields[name]  # edited on the version
        _choices(self, "picture").empty_label = None
        self.fields["picture"].label = "Reel"
        _department_lists(self, cue)
        if cue.duration is not None:
            self.initial["duration"] = timecode.format_duration(
                cue.duration, cue.project.rate
            )
        if can_edit:
            _save_on_change(self, url_for)
        else:
            _read_only(self)


class VersionDetailsForm(NewVersionForm):
    """One version's fields on the details page, each saved on its own."""

    class Meta(NewVersionForm.Meta):
        labels = {**NewVersionForm.Meta.labels, "label": "Version"}

    def __init__(
        self,
        *args: Any,
        version: models.CueVersion,
        url_for: Any,
        can_edit: bool,
    ) -> None:
        """Shows the version, saving fields on change."""
        # Prefixed so its fields don't share ids with the cue's (both
        # have IN, OUT and Seq. Start).
        super().__init__(
            *args, cue=version.cue, instance=version, prefix="version"
        )
        for name in ("update_cue", "status", "duration"):
            del self.fields[name]
        if can_edit:
            _save_on_change(self, url_for)
        else:
            _read_only(self)
