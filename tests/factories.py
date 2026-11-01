"""factory_boy factories for every model."""

import datetime
from typing import Any

import factory
from factory.django import DjangoModelFactory

from accounts import models as account_models
from cues import models
from cues.services import projects


class UserFactory(DjangoModelFactory[account_models.User]):
    class Meta:
        model = account_models.User

    username = factory.Sequence(lambda n: f"user{n}")
    oidc_sub = factory.Sequence(lambda n: f"sub-{n}")
    role = account_models.Role.EDITOR


class ProjectFactory(DjangoModelFactory[models.Project]):
    """A film at 23.976 with the default statuses and tags."""

    class Meta:
        model = models.Project

    title = factory.Sequence(lambda n: f"Film {n}")
    production_number = factory.Sequence(lambda n: f"F{n:03d}")
    frame_rate = "23.976"

    @classmethod
    def _after_postgeneration(
        cls, instance: models.Project, create: bool, results: Any = None
    ) -> None:
        """Seeds statuses and tags, as projects.create_project does."""
        del results
        if create:
            projects.seed_project(instance)


class PictureFactory(DjangoModelFactory[models.Picture]):
    class Meta:
        model = models.Picture

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: str(n + 1))
    order = factory.Sequence(lambda n: n)


class TurnoverFactory(DjangoModelFactory[models.Turnover]):
    class Meta:
        model = models.Turnover

    project = factory.SubFactory(ProjectFactory)
    label = factory.Sequence(lambda n: f"c{n:04d}")
    received_date = factory.Sequence(
        lambda n: datetime.date(2026, 1, 1) + datetime.timedelta(days=n)
    )


class PictureVersionFactory(DjangoModelFactory[models.PictureVersion]):
    class Meta:
        model = models.PictureVersion

    picture = factory.SubFactory(PictureFactory)
    turnover = factory.SubFactory(
        TurnoverFactory, project=factory.SelfAttribute("..picture.project")
    )
    tc_in = 86_400  # 1:00:00:00 at 23.976
    tc_out = 86_400 + 20 * 60 * 24


class StatusOptionFactory(DjangoModelFactory[models.StatusOption]):
    class Meta:
        model = models.StatusOption

    project = factory.SubFactory(ProjectFactory)
    label = factory.Sequence(lambda n: f"Status {n}")
    kind = models.StatusKind.TO_DO
    color = "#e5e7eb"


class SubStatusFactory(DjangoModelFactory[models.SubStatusOption]):
    class Meta:
        model = models.SubStatusOption

    project = factory.SubFactory(ProjectFactory)
    label = factory.Sequence(lambda n: f"Sub status {n}")
    color = "#6b7280"


class CueFactory(DjangoModelFactory[models.Cue]):
    """A 30-second score cue in reel 1."""

    class Meta:
        model = models.Cue

    project = factory.SubFactory(ProjectFactory)
    picture = factory.SubFactory(
        PictureFactory, project=factory.SelfAttribute("..project")
    )
    number = factory.Sequence(lambda n: f"1m{n + 1:02d}")
    title = factory.Sequence(lambda n: f"Cue {n}")
    tc_in = 86_640
    tc_out = 86_640 + 30 * 24


class CueVersionFactory(DjangoModelFactory[models.CueVersion]):
    class Meta:
        model = models.CueVersion

    cue = factory.SubFactory(CueFactory)
    label = factory.Sequence(lambda n: f"v{n + 1}")
    tc_in = factory.SelfAttribute("cue.tc_in")
    tc_out = factory.SelfAttribute("cue.tc_out")


class DeadlineFactory(DjangoModelFactory[models.Deadline]):
    class Meta:
        model = models.Deadline

    project = factory.SubFactory(ProjectFactory)
    kind = models.Deadline.Kind.COMPOSITION
    end_date = datetime.date(2026, 12, 1)


class ViewFactory(DjangoModelFactory[models.View]):
    class Meta:
        model = models.View

    project = factory.SubFactory(ProjectFactory)
    name = factory.Sequence(lambda n: f"View {n}")
    columns = factory.LazyFunction(lambda: [{"id": "number"}, {"id": "title"}])
