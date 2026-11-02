"""Title Case for names and labels (cues.text)."""

from collections.abc import Iterator
import importlib
import inspect
from typing import Any, cast

from django import forms
import pytest

from cues import models
from cues import text
from cues.templatetags import text as text_tags

_FORM_MODULES = [
    "accounts.forms",
    "cues.day_off_forms",
    "cues.forms",
    "cues.schedule_forms",
    "cues.settings_forms",
    "cues.share_forms",
    "cues.tool_forms",
    "cues.transfer_forms",
]
# A PRO's own category names, written as the PRO writes them.
_AS_GIVEN = {"Play-off Sport", "Team vs. Team Sport"}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("first cue # in the reel", "First Cue # in the Reel"),
        ("start at", "Start At"),  # the last word is capitalised
        ("a new IN for one cue", "A New IN for One Cue"),
        ("The ReelLife app", "The ReelLife App"),
        ("Sign-in is unavailable", "Sign-In Is Unavailable"),
        ("Rewrite/revisions", "Rewrite/Revisions"),
        ("Keep each cue's suffix", "Keep Each Cue's Suffix"),
        ("1m01 and 1m02", "1m01 and 1m02"),
        ("major (v1 → v2)", "Major (v1 → v2)"),
        ("custom pattern (advanced)", "Custom Pattern (Advanced)"),
        ("csv (one row per writer)", "Csv (One Row per Writer)"),
        ("team vs. team", "Team vs. Team"),
        ("", ""),
    ],
)
def test_title_case(given: str, expected: str) -> None:
    assert text.title_case(given) == expected


def test_filter() -> None:
    assert text_tags.title_case("frame rate") == "Frame Rate"


def _fixed_choices() -> Iterator[tuple[str, str]]:
    """Every option label a form fixes in code (not one from the data)."""
    for module in map(importlib.import_module, _FORM_MODULES):
        for name, form in inspect.getmembers(module, inspect.isclass):
            if not issubclass(form, forms.BaseForm) or form is forms.Form:
                continue
            fields: dict[str, forms.Field] = getattr(form, "base_fields", {})
            for field_name, field in fields.items():
                if isinstance(field, forms.ModelChoiceField) or not isinstance(
                    field, forms.ChoiceField
                ):
                    continue
                choices = cast(list[tuple[Any, Any]], field.choices)
                for value, label in choices:
                    if value != "":  # the blank one ('—', set per form)
                        yield f"{name}.{field_name}", str(label)


def test_fixed_option_labels_are_title_case() -> None:
    found = list(_fixed_choices())
    assert ("StatusForm.kind", "To Do") in found
    wrong = [
        (where, label)
        for where, label in found
        if label not in _AS_GIVEN and label != text.title_case(label)
    ]
    assert not wrong


def test_blank_choices_show_a_dash() -> None:
    field = forms.ModelChoiceField(queryset=models.Project.objects.none())
    assert field.empty_label == "—"
