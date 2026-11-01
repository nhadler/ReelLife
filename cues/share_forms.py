"""Forms for writers, publishers and shares."""

from typing import Any, cast

from django import forms

from cues import models
from cues.services import shares

# Societies offered as you type a PRO (any other can be typed).
SOCIETIES = (
    "ASCAP",
    "BMI",
    "SESAC",
    "GMR",
    "SOCAN",
    "PRS",
    "GEMA",
    "SACEM",
    "APRA",
    "IMRO",
    "JASRAC",
)


def _party_widgets(form: forms.ModelForm) -> None:  # type: ignore[type-arg]
    form.fields["society"].widget.attrs.update(
        {"list": "societies", "placeholder": "PRO"}
    )
    form.fields["ipi"].widget.attrs.update(
        {"inputmode": "numeric", "placeholder": "9–11 digits"}
    )
    for name, hint in (
        ("first_name", "First name"),
        ("last_name", "Last name"),
        ("name", "Company name"),
    ):
        if name in form.fields:
            form.fields[name].widget.attrs["placeholder"] = hint


class WriterForm(forms.ModelForm):  # type: ignore[type-arg]
    """One writer, by first and last name as cue sheets list them."""

    class Meta:
        model = models.Party
        fields = ["first_name", "last_name", "society", "ipi", "is_mine"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Rows in the Writers table are writers."""
        super().__init__(*args, **kwargs)
        self.instance.kind = models.Party.Kind.WRITER
        _party_widgets(self)


class PublisherForm(forms.ModelForm):  # type: ignore[type-arg]
    """One publisher, by its company name."""

    class Meta:
        model = models.Party
        fields = ["name", "society", "ipi", "is_mine"]
        labels = {"name": "Company name"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Rows in the Publishers table are publishers."""
        super().__init__(*args, **kwargs)
        self.instance.kind = models.Party.Kind.PUBLISHER
        _party_widgets(self)


class _PartyFormSet(forms.BaseModelFormSet):  # type: ignore[type-arg]
    """Refuses to delete a party that a share still names."""

    def clean(self) -> None:
        """Checks deletions."""
        super().clean()
        for form in self.deleted_forms:
            party = form.instance
            if party.pk and (
                models.CueShare.objects.filter(party=party).exists()
                or models.DefaultShare.objects.filter(party=party).exists()
                or models.SeriesShare.objects.filter(party=party).exists()
            ):
                raise forms.ValidationError(
                    f"{party.name} has shares in cues or project defaults; "
                    "remove those first."
                )


WriterFormSet = forms.modelformset_factory(
    models.Party,
    form=WriterForm,
    formset=_PartyFormSet,
    extra=1,
    can_delete=True,
)
PublisherFormSet = forms.modelformset_factory(
    models.Party,
    form=PublisherForm,
    formset=_PartyFormSet,
    extra=1,
    can_delete=True,
)


class ShareForm(forms.ModelForm):  # type: ignore[type-arg]
    """One party's role and percentage."""

    class Meta:
        fields = ["party", "role", "share", "order"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Words the empty choices as prompts rather than dashes."""
        super().__init__(*args, **kwargs)
        party = cast(forms.ModelChoiceField, self.fields["party"])  # type: ignore[type-arg]
        party.empty_label = "Choose…"
        role = cast(forms.ChoiceField, self.fields["role"])
        options = cast(list[tuple[str, str]], role.choices)
        role.choices = [
            ("", "Choose…") if value == "" else (value, label)
            for value, label in options
        ]


class _ShareFormSet(forms.BaseInlineFormSet):  # type: ignore[type-arg]
    """Shares that must add up: writers to 100%, publishers too."""

    def clean(self) -> None:
        """Checks the shares together (services.shares.problems)."""
        super().clean()
        if any(self.errors):
            return
        lines = [
            shares.Line(
                form.cleaned_data["party"],
                form.cleaned_data["role"],
                form.cleaned_data["share"],
            )
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("party")
        ]
        found = shares.problems(lines)
        if found:
            raise forms.ValidationError(found)

    def lines(self) -> list[shares.Line]:
        """The kept shares in order (after is_valid())."""
        kept = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("party")
        ]
        kept.sort(key=lambda data: data.get("order") or 0)
        return [
            shares.Line(data["party"], data["role"], data["share"])
            for data in kept
        ]


def _share_formset(model: type[Any], parent: type[Any]) -> Any:
    form = type(
        "ShareForm",
        (ShareForm,),
        {"Meta": type("Meta", (ShareForm.Meta,), {"model": model})},
    )
    return forms.inlineformset_factory(
        parent,
        model,
        form=form,
        formset=_ShareFormSet,
        fields=["party", "role", "share", "order"],
        extra=0,
        can_delete=True,
    )


DefaultShareFormSet = _share_formset(models.DefaultShare, models.Project)
CueShareFormSet = _share_formset(models.CueShare, models.Cue)
SeriesShareFormSet = _share_formset(models.SeriesShare, models.Series)
