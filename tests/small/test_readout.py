"""How a viewer reads a field's value (templatetags.text.readout)."""

from django import forms

from cues.templatetags import text


class _Form(forms.Form):
    kind = forms.ChoiceField(choices=[("a", "Alpha"), ("b", "Beta")])
    done = forms.BooleanField(required=False)
    note = forms.CharField(required=False)


def test_readout() -> None:
    form = _Form(initial={"kind": "b", "done": True, "note": "Hi"})
    assert text.readout(form["kind"]) == "Beta"
    assert text.readout(form["done"]) == "Yes"
    assert text.readout(form["note"]) == "Hi"
    empty = _Form(initial={"kind": "", "done": False})
    assert text.readout(empty["kind"]) == ""
    assert text.readout(empty["done"]) == ""
    assert text.readout(_Form(initial={"note": "x"})["note"]) == "x"
    unknown = _Form(initial={"kind": "zzz"})
    assert text.readout(unknown["kind"]) == "zzz"
