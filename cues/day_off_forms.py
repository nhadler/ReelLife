"""Days off: shared by every project, or one project's.

A clickable calendar (days-off.js) edits these formsets, which stay the
source of truth and work without JavaScript.
"""

import datetime
from typing import Any

from django import forms
from django.utils import timezone

from cues import models


class _DayOffForm(forms.ModelForm):  # type: ignore[type-arg]
    """One day off; a repeat of a day already off isn't an error."""

    class Meta:
        model = models.DayOff
        fields = ["date", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def validate_unique(self) -> None:
        """The formset skips repeats instead (see _DaysOff.clean)."""


class _DaysOff(forms.BaseModelFormSet):  # type: ignore[type-arg]
    """Days off: a day added twice, or already off, is simply skipped.

    Shared days off, or one project's (`project`), never both at once.
    """

    def __init__(
        self, *args: Any, project: models.Project | None = None, **kwargs: Any
    ) -> None:
        """Takes the project whose days off these are; None for shared."""
        self.project = project
        super().__init__(*args, **kwargs)

    def _scope(self) -> Any:
        return models.DayOff.objects.filter(project=self.project)

    def save_new(self, form: Any, commit: bool = True) -> Any:
        """New days belong to this formset's project (or none)."""
        form.instance.project = self.project
        return super().save_new(form, commit=commit)

    def validate_unique(self) -> None:
        """Repeats are handled in clean()."""

    def clean(self) -> None:
        """Marks new rows for days that are already off as not to save."""
        super().clean()
        removed = {
            form.instance.pk
            for form in self.forms
            if form.instance.pk
            and getattr(form, "cleaned_data", {}).get("DELETE")
        }
        taken = set(
            self._scope().exclude(pk__in=removed).values_list("date", flat=True)
        )
        for form in self.forms:
            date = getattr(form, "cleaned_data", {}).get("date")
            if form.instance.pk or date is None:
                continue
            if date in taken:
                form.cleaned_data["DELETE"] = True  # already a day off
            taken.add(date)


DayOffFormSet = forms.modelformset_factory(
    models.DayOff,
    form=_DayOffForm,
    formset=_DaysOff,
    extra=1,
    can_delete=True,
)


def days_off_formset(data: Any, project: models.Project | None = None) -> Any:
    """Days off, the recent and future ones: shared, or one project's.

    Unbound when the post has no days-off fields, so a form that leaves
    them out changes none (see `days_off_ok`).

    Args:
        data: The POST, or None.
        project: Whose days off; None for the shared ones.
    """
    prefix = "days" if project is None else "project-days"
    if data is not None and f"{prefix}-TOTAL_FORMS" not in data:
        data = None
    since = timezone.localdate() - datetime.timedelta(days=30)
    return DayOffFormSet(
        data,
        prefix=prefix,
        project=project,
        queryset=models.DayOff.objects.filter(
            project=project, date__gte=since
        ).order_by("date"),
    )


def days_off_ok(formset: Any) -> bool:
    """Valid, or not posted at all."""
    return not formset.is_bound or bool(formset.is_valid())


def save_days_off(formset: Any) -> None:
    """Saves the days off, if they were posted."""
    if formset.is_bound:
        formset.save()
