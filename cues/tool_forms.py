"""Forms for the overview's cue tools.

Each form knows its cues (the grid's selection, in the grid's order),
checks the user's choices by planning the change with cues.services.tools
(a ToolError becomes a form error), describes the plan as a preview
table, and applies it as one undoable change set.
"""

from collections.abc import Sequence
import dataclasses
from typing import Any, ClassVar, cast

from django import forms

from accounts.models import User
from cues import models
from cues import timecode
from cues.services import projects as project_service
from cues.services import tools

# Preview columns of cue numbers and timecodes, set in mono.
_MONO_COLUMNS = frozenset(
    {"Cue #", "IN", "OUT", "DUR", "IN Now", "New IN", "New OUT"}
)


@dataclasses.dataclass(frozen=True)
class Preview:
    """What a tool would do, as a table under the dialog's fields."""

    columns: tuple[str, ...]
    rows: Sequence[tuple[str, ...]]
    summary: str = ""

    @property
    def table(self) -> list[list[tuple[str, bool]]]:
        """The rows, each cell paired with whether it is set in mono."""
        mono = [column in _MONO_COLUMNS for column in self.columns]
        return [list(zip(row, mono, strict=False)) for row in self.rows]


def _noun(count: int) -> str:
    return "1 cue" if count == 1 else f"{count} cues"


class ToolForm(forms.Form):
    """A tool's form: its cues, its plan, its preview."""

    title: ClassVar[str] = ""
    apply_label: ClassVar[str] = "Apply"
    template_name: ClassVar[str] = ""
    min_cues: ClassVar[int] = 1
    max_cues: ClassVar[int | None] = None

    def __init__(
        self,
        *args: Any,
        project: models.Project,
        cues: Sequence[models.Cue],
        **kwargs: Any,
    ) -> None:
        """Binds the form to a project and the cues it works on."""
        super().__init__(*args, **kwargs)
        self.project = project
        self.cues = list(cues)
        self.rate = project.rate

    def heading(self) -> str:
        """The dialog's title."""
        return self.title

    def selection_error(self) -> str:
        """Why these cues can't be used, or ''."""
        count = len(self.cues)
        if count < self.min_cues:
            return (
                "Pick a cue in the grid first."
                if self.min_cues == 1
                else f"Pick {self.min_cues} or more cues in the grid."
            )
        if self.max_cues is not None and count > self.max_cues:
            return "Pick exactly one cue."
        return ""

    def tc(self, frames: int | None) -> str:
        """A frame count as timecode, or a dash."""
        if frames is None:
            return "—"
        return timecode.format_timecode(frames, self.rate)

    def parse_tc(self, name: str) -> int | None:
        """A timecode field in frames; adds an error when it won't parse."""
        text = (self.cleaned_data.get(name) or "").strip()
        if not text:
            self.add_error(name, "Enter a timecode.")
            return None
        try:
            return timecode.parse(text, self.rate)
        except timecode.TimecodeError as exc:
            self.add_error(name, str(exc))
            return None

    def clean(self) -> dict[str, Any]:
        """Plans the change; a ToolError becomes a form error."""
        cleaned: dict[str, Any] = super().clean() or {}
        problem = self.selection_error()
        if problem:
            raise forms.ValidationError(problem)
        if not self.errors:
            try:
                self.plan(cleaned)
            except tools.ToolError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return cleaned

    def plan(self, cleaned: dict[str, Any]) -> None:
        """Works out the change, raising ToolError if it can't be made."""

    def preview(self) -> Preview:
        """The planned change; call after is_valid()."""
        raise NotImplementedError

    def apply(self, user: User | None) -> None:
        """Makes the change, as one change set."""
        raise NotImplementedError


def _pictures(project: models.Project) -> Any:
    return project.pictures.exclude(
        name=project_service.PLACEHOLDER_PICTURE
    ).order_by("order", "name")


def _model_choice(form: forms.Form, name: str) -> forms.ModelChoiceField:  # type: ignore[type-arg]
    return cast(forms.ModelChoiceField, form.fields[name])  # type: ignore[type-arg]


class _PictureChoice(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: models.Picture) -> str:
        return str(obj.title)


class NewCuesForm(ToolForm):
    """Several new cues in one reel, numbered up from the first."""

    title = "New Cues"
    apply_label = "Create Cues"
    template_name = "cues/tools/new_cues.html"
    min_cues = 0

    count = forms.IntegerField(
        label="Number of new cues", min_value=1, max_value=200, initial=5
    )
    picture = _PictureChoice(
        label="Reel", queryset=models.Picture.objects.none()
    )
    number = forms.CharField(label="First cue #", max_length=20)
    status = forms.ModelChoiceField(
        queryset=models.StatusOption.objects.none(), required=False
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Limits reels and statuses to the project."""
        super().__init__(*args, **kwargs)
        _model_choice(self, "picture").queryset = _pictures(self.project)
        _model_choice(self, "status").queryset = self.project.statuses.all()
        self.numbers: list[str] = []

    def plan(self, cleaned: dict[str, Any]) -> None:
        """The new numbers."""
        self.numbers = tools.plan_new_cues(
            cleaned["picture"], cleaned["number"], cleaned["count"]
        )

    def preview(self) -> Preview:
        """Each new cue's number."""
        reel = self.cleaned_data["picture"].name
        return Preview(
            ("Cue #", "Reel"),
            [(number, reel) for number in self.numbers],
            f"Creates {_noun(len(self.numbers))}, each with a v1.",
        )

    def apply(self, user: User | None) -> None:
        """Creates them."""
        tools.new_cues(
            self.cleaned_data["picture"],
            self.numbers,
            status=self.cleaned_data["status"],
            user=user,
        )


class AddVersionsForm(ToolForm):
    """A new version for each cue."""

    title = "Add Versions"
    apply_label = "Add Versions"
    template_name = "cues/tools/add_versions.html"

    increment = forms.ChoiceField(
        label="Next label",
        choices=[("major", "Major (v1 → v2)"), ("minor", "Minor (v1 → v1.1)")],
        widget=forms.RadioSelect,
    )
    keep_notes = forms.BooleanField(
        label="Keep notes", required=False, initial=True
    )
    keep_sub_status = forms.BooleanField(
        label="Keep sub status", required=False, initial=True
    )
    mark_composed = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Counts the compositions the last checkbox would mark."""
        super().__init__(*args, **kwargs)
        settings = models.AppSettings.load()
        minor = (
            settings.version_increment
            == models.AppSettings.VersionIncrement.MINOR
        )
        self.fields["increment"].initial = "minor" if minor else "major"
        self.to_mark = tools.compositions_to_mark(self.cues)
        count = len(self.to_mark)
        self.fields[
            "mark_composed"
        ].label = (
            f'Mark {count} composition{"" if count == 1 else "s"} as "Composed"'
        )

    def options(self) -> tools.VersionOptions:
        """The checked options."""
        data = self.cleaned_data
        return tools.VersionOptions(
            minor=data["increment"] == "minor",
            keep_notes=data["keep_notes"],
            keep_sub_status=data["keep_sub_status"],
            mark_composed=data["mark_composed"],
        )

    def preview(self) -> Preview:
        """Each cue's new label, and its status change."""
        options = self.options()
        marked = (
            {cue.pk for cue in self.to_mark} if options.mark_composed else set()
        )
        rows = []
        for cue in self.cues:
            picture = cue.picture.current_version()
            rows.append(
                (
                    cue.number,
                    cue.current_version.label if cue.current_version else "—",
                    tools.next_label(cue, minor=options.minor),
                    str(picture.turnover) if picture else "—",
                    "Composed" if cue.pk in marked else "",
                )
            )
        return Preview(
            ("Cue #", "Now", "New", "Written To", "Status"),
            rows,
            f"Adds a version to {_noun(len(self.cues))}.",
        )

    def apply(self, user: User | None) -> None:
        """Adds them."""
        tools.add_versions(self.cues, self.options(), user=user)


class SplitForm(ToolForm):
    """One cue split in two."""

    title = "Split Cue"
    apply_label = "Split"
    template_name = "cues/tools/split.html"
    max_cues = 1

    at = forms.CharField(label="Split at", max_length=20)
    number = forms.CharField(label="New cue #", max_length=20)
    new_takes = forms.ChoiceField(
        label="The new cue takes",
        choices=[("second", "The Second Half"), ("first", "The First Half")],
        initial="second",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Suggests the midpoint and the next free letter."""
        super().__init__(*args, **kwargs)
        self._plan: tools.SplitPlan | None = None
        if len(self.cues) == 1:
            try:
                at, number = tools.split_defaults(self.cues[0])
            except tools.ToolError:
                return
            self.fields["at"].initial = self.tc(at)
            self.fields["number"].initial = number

    def facts(self) -> str:
        """The cue's IN, OUT and duration, for the dialog's intro."""
        cue = self.cues[0]
        duration = (
            "—"
            if cue.duration is None
            else timecode.format_duration(cue.duration, self.rate)
        )
        return (
            f"{cue}: IN {self.tc(cue.tc_in)}, OUT {self.tc(cue.tc_out)}, "
            f"DUR {duration}."
        )

    @property
    def split_plan(self) -> tools.SplitPlan:
        """The checked split; only after is_valid()."""
        if self._plan is None:
            raise RuntimeError("The form isn't valid.")
        return self._plan

    def plan(self, cleaned: dict[str, Any]) -> None:
        """Checks the point and number."""
        at = self.parse_tc("at")
        if at is None:
            return
        self._plan = tools.plan_split(
            self.cues[0],
            at,
            cleaned["number"],
            new_takes_second=cleaned["new_takes"] == "second",
        )

    def preview(self) -> Preview:
        """The two cues afterwards."""
        plan = self.split_plan
        halves = [(plan.cue.number, plan.first), (plan.number, plan.second)]
        if not plan.new_takes_second:
            halves = [(plan.number, plan.first), (plan.cue.number, plan.second)]
        rate = self.rate
        return Preview(
            ("Cue #", "IN", "OUT", "DUR"),
            [
                (
                    number,
                    self.tc(span[0]),
                    self.tc(span[1]),
                    timecode.format_duration(span[1] - span[0], rate),
                )
                for number, span in halves
            ],
            f"{plan.number} is new, with a v1; {plan.cue.number} keeps "
            "its versions and history.",
        )

    def apply(self, user: User | None) -> None:
        """Splits it."""
        tools.split(self.split_plan, user=user)


class MergeForm(ToolForm):
    """Two or more cues merged into one."""

    title = "Merge Cues"
    apply_label = "Merge"
    template_name = "cues/tools/merge.html"
    min_cues = 2

    tc_in = forms.CharField(label="IN", max_length=20)
    tc_out = forms.CharField(label="OUT", max_length=20)
    keeper = forms.ChoiceField(label="Keep")
    others = forms.ChoiceField(
        label="The others",
        choices=[
            ("inactive", "Mark Omit and Make Inactive"),
            ("delete", "Delete"),
        ],
        initial="inactive",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Suggests the span and the earliest cue."""
        super().__init__(*args, **kwargs)
        self.fields["keeper"].choices = [  # type: ignore[attr-defined]
            (str(cue.pk), str(cue)) for cue in self.cues
        ]
        self.span: tuple[int, int] = (0, 0)
        try:
            tc_in, tc_out, earliest = tools.merge_defaults(self.cues)
        except tools.ToolError:
            return
        self.fields["tc_in"].initial = self.tc(tc_in)
        self.fields["tc_out"].initial = self.tc(tc_out)
        self.fields["keeper"].initial = str(earliest.pk)

    def keeper_cue(self) -> models.Cue:
        """The cue that stays."""
        return next(
            cue
            for cue in self.cues
            if str(cue.pk) == self.cleaned_data["keeper"]
        )

    def plan(self, cleaned: dict[str, Any]) -> None:
        """Checks the cues and the span."""
        tools.merge_defaults(self.cues)
        tc_in, tc_out = self.parse_tc("tc_in"), self.parse_tc("tc_out")
        if tc_in is None or tc_out is None:
            return
        if tc_out <= tc_in:
            raise tools.ToolError("OUT must be after IN.")
        self.span = (tc_in, tc_out)

    def preview(self) -> Preview:
        """The kept cue's new span and what happens to the others."""
        keeper = self.keeper_cue()
        fate = (
            "Deleted"
            if self.cleaned_data["others"] == "delete"
            else "Omit, inactive"
        )
        rows = [
            (
                keeper.number,
                self.tc(self.span[0]),
                self.tc(self.span[1]),
                timecode.format_duration(
                    self.span[1] - self.span[0], self.rate
                ),
                "Kept",
            )
        ]
        rows += [
            (cue.number, self.tc(cue.tc_in), self.tc(cue.tc_out), "", fate)
            for cue in self.cues
            if cue.pk != keeper.pk
        ]
        return Preview(
            ("Cue #", "IN", "OUT", "DUR", ""),
            rows,
            f"Only {keeper.number}'s IN and OUT change.",
        )

    def apply(self, user: User | None) -> None:
        """Merges them."""
        tools.merge(
            self.cues,
            self.keeper_cue(),
            self.span,
            delete_others=self.cleaned_data["others"] == "delete",
            user=user,
        )


class RenumberForm(ToolForm):
    """New numbers for the cues, edited one by one or counted up."""

    title = "Renumber Cues"
    apply_label = "Renumber"
    template_name = "cues/tools/renumber.html"

    mode = forms.ChoiceField(
        choices=[("renumber", "Renumber"), ("overwrite", "Overwrite")],
        initial="renumber",
        widget=forms.RadioSelect,
    )
    start = forms.CharField(label="Start at", max_length=20, required=False)
    keep_suffix = forms.BooleanField(
        label="Keep each cue's suffix", required=False, initial=True
    )
    reel = forms.ChoiceField(
        label="Reel part",
        choices=tools.ReelPart.choices,
        initial=tools.ReelPart.KEEP,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """One editable number per cue, named new_0, new_1 …."""
        super().__init__(*args, **kwargs)
        for index, cue in enumerate(self.cues):
            self.fields[f"new_{index}"] = forms.CharField(
                label=f"New number for {cue.number}",
                max_length=20,
                required=False,
                initial=cue.number,
            )
        if self.cues:
            self.fields["start"].initial = self.cues[0].number
        self.numbers: list[str] = []

    def rows(self) -> list[tuple[models.Cue, forms.BoundField]]:
        """Each cue with its number field."""
        return [(cue, self[f"new_{i}"]) for i, cue in enumerate(self.cues)]

    def bumped(self, index: int, by: int) -> None:
        """Moves the number at `index` and those after it (−/+)."""
        if not self.is_bound:
            return
        data = self.data.copy()  # type: ignore[attr-defined]
        numbers = [
            data.get(f"new_{i}", cue.number) for i, cue in enumerate(self.cues)
        ]
        for i, number in enumerate(tools.bump(numbers, index, by)):
            data[f"new_{i}"] = number
        self.data = data

    def plan(self, cleaned: dict[str, Any]) -> None:
        """The numbers, from the list or counted from the start."""
        if cleaned["mode"] == "overwrite":
            if not cleaned["start"].strip():
                raise tools.ToolError("Enter a start number.")
            self.numbers = tools.overwrite_numbers(
                self.cues,
                cleaned["start"],
                keep_suffix=cleaned["keep_suffix"],
                reel=tools.ReelPart(cleaned["reel"]),
            )
        else:
            self.numbers = [
                cleaned[f"new_{i}"].strip() for i in range(len(self.cues))
            ]
        tools.check_renumber(self.cues, self.numbers)

    def preview(self) -> Preview:
        """Old and new numbers."""
        rows = [
            (cue.number, number, "" if cue.number == number else "changes")
            for cue, number in zip(self.cues, self.numbers, strict=True)
        ]
        changes = sum(1 for row in rows if row[2])
        return Preview(("Now", "New", ""), rows, f"Renumbers {_noun(changes)}.")

    def apply(self, user: User | None) -> None:
        """Renumbers them."""
        tools.renumber(self.cues, self.numbers, user=user)


class ShiftForm(ToolForm):
    """The cues moved earlier or later in the picture."""

    title = "Shift Cues"
    apply_label = "Shift"
    template_name = "cues/tools/shift.html"

    by = forms.ChoiceField(
        label="Shift by",
        choices=[
            ("offset", "An Offset"),
            ("in", "A New IN for One Cue"),
            ("seq", "A New Seq. Start for One Cue"),
        ],
        initial="offset",
        widget=forms.RadioSelect,
    )
    offset = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "tc"}),
        help_text=(
            "48 is 48 frames, 2.5 is 2 s 5 fr, -1.10.12 moves back "
            "1 min 10 s 12 fr."
        ),
    )
    reference = forms.ChoiceField(label="Cue", required=False)
    new_time = forms.CharField(
        label="Moves to",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "tc"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Lists the cues for the reference choice."""
        super().__init__(*args, **kwargs)
        self.fields["reference"].choices = [  # type: ignore[attr-defined]
            (str(cue.pk), str(cue)) for cue in self.cues
        ]
        self.delta = 0

    def _reference_delta(self, cleaned: dict[str, Any]) -> int | None:
        cue = next(
            (c for c in self.cues if str(c.pk) == cleaned["reference"]), None
        )
        if cue is None:
            raise tools.ToolError("Pick the cue to move.")
        now = cue.tc_in if cleaned["by"] == "in" else cue.seq_start
        if now is None:
            what = "IN" if cleaned["by"] == "in" else "Seq. start"
            raise tools.ToolError(f"{cue.number} has no {what} to move from.")
        target = self.parse_tc("new_time")
        return None if target is None else target - now

    def plan(self, cleaned: dict[str, Any]) -> None:
        """The offset in frames, checked against the cues."""
        if cleaned["by"] == "offset":
            try:
                delta: int | None = timecode.parse_shift(
                    cleaned["offset"], self.rate
                )
            except timecode.TimecodeError:
                self.add_error("offset", "Enter an offset such as 2.5 or -48.")
                return
        else:
            delta = self._reference_delta(cleaned)
        if delta is None:
            return
        tools.check_shift(self.cues, delta)
        self.delta = delta

    def preview(self) -> Preview:
        """Each cue's IN and OUT afterwards."""
        rows = []
        for cue in self.cues:
            tc_in, tc_out, _ = tools.shifted(cue, self.delta)
            rows.append(
                (
                    cue.number,
                    self.tc(cue.tc_in),
                    self.tc(tc_in),
                    self.tc(tc_out),
                )
            )
        offset = timecode.format_offset(self.delta, self.rate)
        return Preview(
            ("Cue #", "IN Now", "New IN", "New OUT"),
            rows,
            f"Moves {_noun(len(self.cues))} by {offset}; versions keep "
            "their timecodes.",
        )

    def apply(self, user: User | None) -> None:
        """Shifts them."""
        tools.shift(self.cues, self.delta, user=user)


class _ReelForm(ToolForm):  # pylint: disable=abstract-method
    """A tool that sends cues to a reel."""

    picture = _PictureChoice(
        label="To reel", queryset=models.Picture.objects.none()
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Limits the reels to the project."""
        super().__init__(*args, **kwargs)
        _model_choice(self, "picture").queryset = _pictures(self.project)


class MoveForm(_ReelForm):
    """The cues moved to another reel."""

    title = "Move Cues"
    apply_label = "Move"
    template_name = "cues/tools/move.html"

    def preview(self) -> Preview:
        """Each cue's reel before and after."""
        picture = self.cleaned_data["picture"]
        return Preview(
            ("Cue #", "Reel Now", "New Reel"),
            [(c.number, c.picture.name, picture.name) for c in self.cues],
            "Timecodes stay as they are.",
        )

    def apply(self, user: User | None) -> None:
        """Moves them."""
        tools.move(self.cues, self.cleaned_data["picture"], user=user)


class CopyForm(_ReelForm):
    """Copies of the cues in a reel."""

    title = "Copy Cues"
    apply_label = "Copy"
    template_name = "cues/tools/copy.html"

    versions = forms.ChoiceField(
        label="Versions",
        choices=[("current", "Current Version Only"), ("all", "All Versions")],
        initial="current",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Starts with no numbers planned."""
        super().__init__(*args, **kwargs)
        self.numbers: list[str] = []

    def plan(self, cleaned: dict[str, Any]) -> None:
        """The copies' numbers, which must fit Valid Cue Numbers."""
        self.numbers = tools.copy_numbers(self.cues, cleaned["picture"])

    def preview(self) -> Preview:
        """Each cue's copy's number."""
        numbers = self.numbers
        return Preview(
            ("Cue #", "Copy"),
            list(zip((c.number for c in self.cues), numbers, strict=True)),
            "History and conform state aren't copied.",
        )

    def apply(self, user: User | None) -> None:
        """Copies them."""
        tools.copy(
            self.cues,
            self.cleaned_data["picture"],
            all_versions=self.cleaned_data["versions"] == "all",
            user=user,
        )


class DeleteForm(ToolForm):
    """The cues deleted."""

    title = "Delete Cues"
    apply_label = "Delete"
    template_name = "cues/tools/delete.html"

    def heading(self) -> str:
        """'Delete 3 cues?'."""
        return f"Delete {_noun(len(self.cues))}?"

    def preview(self) -> Preview:
        """The cues that go."""
        return Preview(
            ("Cue #", "Title"),
            [(cue.number, cue.title) for cue in self.cues],
            "Undo (Ctrl+Z) brings them back.",
        )

    def apply(self, user: User | None) -> None:
        """Deletes them."""
        tools.delete(self.cues, user=user)


FORMS: dict[str, type[ToolForm]] = {
    "new": NewCuesForm,
    "versions": AddVersionsForm,
    "split": SplitForm,
    "merge": MergeForm,
    "renumber": RenumberForm,
    "shift": ShiftForm,
    "move": MoveForm,
    "copy": CopyForm,
    "delete": DeleteForm,
}
