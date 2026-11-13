"""Forms for the schedule's dialogs: Schedule On and Log Written."""

import datetime
import functools
from typing import Any

from django import forms
from django.utils import timezone

from cues import models
from cues import timecode


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
    """A stretch of a cue written on a day ("Log Written…").

    From and To are timecodes in the cue's picture; cleaned, they're
    frames after the cue's IN (`start`, `end`).
    """

    date = forms.DateField(
        label="Day", widget=forms.DateInput(attrs={"type": "date"})
    )
    start = forms.CharField(
        label="From",
        max_length=20,
        widget=forms.TextInput(attrs={"class": "tc"}),
    )
    end = forms.CharField(
        label="To",
        max_length=20,
        widget=forms.TextInput(attrs={"class": "tc"}),
    )

    def __init__(self, *args: Any, cue: models.Cue, **kwargs: Any) -> None:
        """Sets the cue the timecodes are read against."""
        super().__init__(*args, **kwargs)
        self.cue = cue
        self.rate = cue.project.rate

    def clean_date(self) -> datetime.date:
        """Today or earlier: work not yet done can't be logged."""
        date: datetime.date = self.cleaned_data["date"]
        if date > timezone.localdate():
            raise forms.ValidationError("Pick today or an earlier day.")
        return date

    def _after_in(self, name: str) -> int:
        """A timecode field as frames after the cue's IN."""
        try:
            frames = timecode.parse(self.cleaned_data[name], self.rate)
        except timecode.TimecodeError as exc:
            raise forms.ValidationError(str(exc)) from exc
        # The dialog offers the form only to a cue with an IN and OUT.
        return frames - (self.cue.tc_in or 0)

    def clean_start(self) -> int:
        """From, as frames after IN."""
        return self._after_in("start")

    def clean_end(self) -> int:
        """To, as frames after IN."""
        return self._after_in("end")

    def clean(self) -> dict[str, Any]:
        """From before To, both inside the cue."""
        cleaned: dict[str, Any] = super().clean() or {}
        start, end = cleaned.get("start"), cleaned.get("end")
        tc_in, tc_out = self.cue.tc_in, self.cue.tc_out
        if start is None or end is None or tc_in is None or tc_out is None:
            return cleaned
        if not 0 <= start < end <= tc_out - tc_in:
            show = functools.partial(timecode.format_timecode, rate=self.rate)
            raise forms.ValidationError(
                f"Pick a stretch from {show(tc_in)} to {show(tc_out)}, "
                "From before To."
            )
        return cleaned
