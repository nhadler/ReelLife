"""Forms for app settings and the project settings' Project page."""

from decimal import Decimal
from typing import Any, cast

from django import forms

from cues import models


class AppSettingsForm(forms.ModelForm):  # type: ignore[type-arg]
    """How ReelLife looks and behaves for everyone (App Settings).

    The schedule's daily maximum and smallest share are stored in
    weighted seconds and edited in minutes of Easy music.
    """

    # Minutes of Easy music; Help explains both.
    daily_max = forms.DecimalField(
        label="Most work a day (Min)",
        required=False,
        min_value=Decimal("0.5"),
        max_value=Decimal(1440),
        decimal_places=1,
        widget=forms.NumberInput(attrs={"placeholder": "As Deadlines Need"}),
    )
    min_part = forms.DecimalField(
        label="Smallest share (Min)",
        min_value=Decimal("0.5"),
        max_value=Decimal(240),
        decimal_places=1,
    )

    class Meta:
        model = models.AppSettings
        fields = [
            "theme",
            "date_format",
            "leading_zero",
            "subframes",
            "single_click_edit",
            "enter_behaviour",
            "version_increment",
            "music_totals",
            "schedule_easy",
            "schedule_moderate",
            "schedule_hard",
            "schedule_revise",
            "schedule_unrated",
            "schedule_projects",
        ]
        labels = {
            "leading_zero": "Leading zero (01:00:10:00)",
            "subframes": "Show subframes",
            "single_click_edit": "Edit cells with one click",
            "enter_behaviour": "Enter in a notes cell",
            "music_totals": "Progress counts",
            "schedule_easy": "Easy",
            "schedule_moderate": "Moderate",
            "schedule_hard": "Hard",
            "schedule_revise": "Revisions",
            "schedule_unrated": "No difficulty counts as",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Shows the stored seconds as minutes (see `clean`)."""
        super().__init__(*args, **kwargs)
        self.fields["leading_zero"].help_text = ""  # the label shows it
        settings = self.instance
        if settings.schedule_daily_max is not None:
            self.fields["daily_max"].initial = _minutes(
                settings.schedule_daily_max
            )
        self.fields["min_part"].initial = _minutes(settings.schedule_min_part)

    def clean(self) -> dict[str, Any]:
        """Keeps the minutes as weighted seconds on the settings."""
        data = super().clean() or {}
        daily_max = data.get("daily_max")
        self.instance.schedule_daily_max = (
            None if daily_max is None else round(daily_max * 60)
        )
        if data.get("min_part") is not None:
            self.instance.schedule_min_part = round(data["min_part"] * 60)
        return data


def _minutes(seconds: int) -> Decimal:
    """Seconds as minutes, to a tenth."""
    minutes = (Decimal(seconds) / 60).quantize(Decimal("0.1"))
    whole = minutes.to_integral_value()
    return whole if minutes == whole else minutes  # 10, not 1E+1


class DuplicateForm(forms.Form):
    """The duplicate's title."""

    title = forms.CharField(label="Title of the copy", max_length=200)


class DeleteForm(forms.Form):
    """Typed confirmation before a project is deleted."""

    confirm = forms.CharField(label="Type the project's title to confirm")

    def __init__(
        self, *args: Any, project: models.Project, **kwargs: Any
    ) -> None:
        """Remembers the title to compare.

        The page keeps Delete disabled until the title is typed
        (confirm.js, data-confirm-text); the server checks it too.
        """
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields["confirm"].widget.attrs["data-confirm-text"] = project.title

    def clean_confirm(self) -> str:
        """Must match the title exactly."""
        value: str = self.cleaned_data["confirm"]
        if value.strip() != self.project.title:
            raise forms.ValidationError("That isn't the project's title.")
        return value


class CopyFromForm(forms.Form):
    """Bring views or lists over from another project."""

    source = forms.ModelChoiceField(
        label="From project", queryset=models.Project.objects.none()
    )
    views = forms.BooleanField(label="Views", required=False, initial=True)
    lists = forms.BooleanField(
        label="Statuses and lists",
        required=False,
        help_text="Adds the entries this project doesn't have yet.",
    )

    def __init__(
        self, *args: Any, project: models.Project, **kwargs: Any
    ) -> None:
        """Offers every other project."""
        super().__init__(*args, **kwargs)
        source = cast(forms.ModelChoiceField, self.fields["source"])  # type: ignore[type-arg]
        source.queryset = models.Project.objects.exclude(
            pk=project.pk
        ).order_by("title")

    def clean(self) -> dict[str, Any]:
        """Needs at least one thing to copy."""
        cleaned: dict[str, Any] = super().clean() or {}
        if not cleaned.get("views") and not cleaned.get("lists"):
            raise forms.ValidationError("Choose what to copy.")
        return cleaned
