"""Text filters, e.g. {% load text %} {{ field.label|title_case }}."""

import datetime

from django import forms
from django import template

from cues import colors
from cues import dates
from cues import text

register = template.Library()


@register.filter(name="title_case")
def title_case(value: object) -> str:
    """A name or label in Title Case (cues.text.title_case)."""
    return text.title_case(str(value))


_SMALL_WORDS = frozenset({"the", "a", "an"})


@register.filter(name="initials")
def initials(value: object) -> str:
    """Up to two initials, e.g. 'The Lighthouse Keeper' -> 'LK'."""
    words = [w for w in str(value).split() if w.lower() not in _SMALL_WORDS]
    words = words or str(value).split()
    return "".join(w[0] for w in words[:2]).upper()


@register.filter(name="readout")
def readout(bound: forms.BoundField) -> str:
    """A field's value as a viewer reads it.

    A choice's label, Yes for a ticked box, and '' for nothing.
    """
    value = bound.value()
    field = bound.field
    if isinstance(field, forms.BooleanField):
        return "Yes" if value else ""
    if value in (None, "", []):
        return ""
    for key, label in getattr(field, "choices", ()):
        if str(key) == str(value):
            return str(label)
    return str(value)


@register.filter(name="stamp")
def stamp(value: datetime.datetime | None, short_format: str) -> str:
    """When something happened, e.g. 'Today 16:59' (cues.dates.stamp).

    short_format is the context's ('M j' or 'j M'), which sets the order.
    """
    if value is None:
        return ""
    return dates.stamp(value, short_format.startswith("M"))


@register.filter(name="invalid_fields")
def invalid_fields(form: forms.BaseForm) -> int:
    """How many of the form's visible fields have errors."""
    return sum(1 for field in form.visible_fields() if field.errors)


@register.filter(name="tone_of")
def tone_of(color: object, project: object = None) -> int:
    """A colour choice's tone (colors.tone).

    Automatic shows the one the project gets, or the first for a project
    not made yet.
    """
    project_id = getattr(project, "pk", None)
    return colors.tone(project_id, str(color or ""))
