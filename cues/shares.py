"""Writers, publishers and their shares of each cue.

One directory of parties is shared by every project (Nick, Oct 31). A
cue's shares say who wrote and published it, in what role and for what
percentage; they feed the PRO cue sheet. A project's default shares (or
its series', when it has none) are given to each new composition, and
to existing cues on request. The parties and every kind of share are
tracked by history, so editing them can be undone. Re-exported from
cues.models.
"""

from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.core.validators import MinValueValidator
from django.core.validators import RegexValidator
from django.db import models

from cues.model_base import Timestamped
from cues.model_base import _history

_PERCENT = [MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal(100))]


class Party(Timestamped):
    """A writer or publisher, as their PRO knows them."""

    class Kind(models.TextChoices):
        WRITER = "writer", "Writer"
        PUBLISHER = "publisher", "Publisher"

    # A publisher's name; for a writer, "first last", kept from the two
    # name fields below (cue sheets list writers by first and last name).
    name = models.CharField(max_length=200)
    first_name = models.CharField(
        "first name", max_length=100, blank=True, help_text="And middle names."
    )
    last_name = models.CharField("last name", max_length=100, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    society = models.CharField(
        "PRO",
        max_length=30,
        blank=True,
        help_text="ASCAP, BMI, SESAC, GMR, SOCAN, PRS …",
    )
    ipi = models.CharField(
        "IPI/CAE #",
        max_length=11,
        blank=True,
        validators=[
            RegexValidator(
                r"^\d{9,11}$", "An IPI/CAE number is 9 to 11 digits."
            )
        ],
    )
    is_mine = models.BooleanField(
        "the composer's own",
        default=False,
        help_text="One of the composer's own writer or publishing accounts.",
    )

    history = _history()

    class Meta:
        ordering = ["kind", "name"]
        verbose_name = "writer or publisher"
        verbose_name_plural = "writers and publishers"

    def __str__(self) -> str:
        """Returns e.g. 'Jane Doe (BMI)'."""
        return f"{self.name} ({self.society})" if self.society else self.name

    def clean(self) -> None:
        """A writer needs a last name; a publisher, a name."""
        if self.kind == self.Kind.WRITER:
            self.first_name = " ".join(self.first_name.split())
            self.last_name = " ".join(self.last_name.split())
            if not self.last_name:
                raise ValidationError(
                    {"last_name": "A writer needs a last name."}
                )
            self.name = f"{self.first_name} {self.last_name}".strip()
        elif not self.name.strip():
            raise ValidationError({"name": "A publisher needs a name."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keeps a writer's full name from their first and last names."""
        if self.kind == self.Kind.WRITER and self.last_name:
            self.name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)


class Role(models.TextChoices):
    """A party's role on a cue sheet."""

    COMPOSER = "C", "Composer"
    AUTHOR = "A", "Author/Lyricist"
    COMPOSER_AUTHOR = "CA", "Composer/Author"
    ARRANGER = "AR", "Arranger"
    ADAPTOR = "AD", "Adaptor"
    PUBLISHER = "E", "Original Publisher"
    SUB_PUBLISHER = "SE", "Sub-Publisher"
    ADMINISTRATOR = "AM", "Administrator"


WRITER_ROLES = frozenset({"C", "A", "CA", "AR", "AD"})
PUBLISHER_ROLES = frozenset({"E", "SE", "AM"})


class _Share(Timestamped):
    """A party's role and percentage."""

    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=2, choices=Role.choices)
    share = models.DecimalField(
        "share %", max_digits=5, decimal_places=2, validators=_PERCENT
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["order", "id"]

    def __str__(self) -> str:
        """Returns e.g. 'Jane Doe C 50%'."""
        return f"{self.party.name} {self.role} {self.share.normalize():f}%"

    @property
    def is_writer(self) -> bool:
        """A writer's share (C, A, CA, AR, AD) rather than a publisher's."""
        return self.role in WRITER_ROLES


class CueShare(_Share):
    """One party's share of a cue."""

    cue = models.ForeignKey(
        "cues.Cue", on_delete=models.CASCADE, related_name="shares"
    )

    history = _history()


class DefaultShare(_Share):
    """One party's share of a project's new compositions."""

    project = models.ForeignKey(
        "cues.Project", on_delete=models.CASCADE, related_name="default_shares"
    )

    history = _history()


class SeriesShare(_Share):
    """One party's share of a series' new compositions.

    Every episode uses these unless it has default shares of its own.
    """

    series = models.ForeignKey(
        "cues.Series", on_delete=models.CASCADE, related_name="default_shares"
    )

    history = _history()
