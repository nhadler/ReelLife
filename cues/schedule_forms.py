"""Forms for the schedule's dialogs: Schedule On and Log Written."""

import datetime

from django import forms
from django.utils import timezone

from cues.services import writing


class ScheduleOnForm(forms.Form):
    """Pins a cue to a day of the schedule ("Schedule On…")."""

    date = forms.DateField(
        label="Day", widget=forms.DateInput(attrs={"type": "date"})
    )

    def clean_date(self) -> datetime.date:
        """Today or later: the past holds no work."""
        date: datetime.date = self.cleaned_data["date"]
        if date < timezone.localdate():
            raise forms.ValidationError("Pick today or a later day.")
        return date


class LogWrittenForm(forms.Form):
    """How much of a cue was written on a day ("Log Written…")."""

    date = forms.DateField(
        label="Day", widget=forms.DateInput(attrs={"type": "date"})
    )
    amount = forms.CharField(
        label="Written",
        max_length=10,
        widget=forms.TextInput(attrs={"placeholder": "1:20"}),
    )

    def clean_date(self) -> datetime.date:
        """Today or earlier: work not yet done can't be logged."""
        date: datetime.date = self.cleaned_data["date"]
        if date > timezone.localdate():
            raise forms.ValidationError("Pick today or an earlier day.")
        return date

    def clean_amount(self) -> int:
        """Seconds, from m:ss or minutes."""
        try:
            return writing.parse_amount(self.cleaned_data["amount"])
        except writing.AmountError as exc:
            raise forms.ValidationError(str(exc)) from exc
