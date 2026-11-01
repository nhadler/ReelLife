"""Creating cues and versions from the dialogs (7.5).

Each runs as one undoable change set.
"""

from typing import TYPE_CHECKING, Any

from accounts.models import User
from cues import models
from cues import numbering
from cues.services import history
from cues.services import shares

if TYPE_CHECKING:
    from django.core.files.base import ContentFile


def suggested_number(project: models.Project, picture: models.Picture) -> str:
    """The next free cue number for a picture."""
    taken = {
        n.lower()
        for n in project.cues.filter(active=True).values_list(
            "number", flat=True
        )
    }
    in_picture = list(picture.cues.values_list("number", flat=True))
    return numbering.suggest_number(in_picture, picture.name, taken)


def next_version_label(cue: models.Cue) -> str:
    """The cue's next version label, by the version increment setting."""
    minor = (
        models.AppSettings.load().version_increment
        == models.AppSettings.VersionIncrement.MINOR
    )
    labels = list(cue.versions.values_list("label", flat=True))
    return numbering.next_version_label(labels, minor=minor)


def create_cue(
    cue: models.Cue,
    *,
    notes: dict[str, Any] | None = None,
    sub_status: models.SubStatusOption | None = None,
    user: User | None = None,
) -> models.Cue:
    """Saves a new cue with its v1 (every cue has a version).

    Args:
        cue: An unsaved cue, already validated (e.g. by NewCueForm).
        notes: v1's note fields (models.VERSION_NOTE_FIELDS), if any.
        sub_status: v1's sub status, if any.
        user: Who created it.

    Returns:
        The saved cue.

    Raises:
        django.core.exceptions.ValidationError: Invalid fields; nothing
            is saved.
    """
    with history.recording(cue.project, f"New cue {cue.number}", user):
        save_with_v1(cue, notes=notes, sub_status=sub_status)
    return cue


def save_with_v1(
    cue: models.Cue,
    *,
    notes: dict[str, Any] | None = None,
    sub_status: models.SubStatusOption | None = None,
) -> models.Cue:
    """Validates and saves a new cue and its v1.

    Runs inside the caller's change set (create_cue's, or a tool's).

    Raises:
        django.core.exceptions.ValidationError: Invalid fields.
    """
    cue.full_clean()
    cue.save()
    version = models.CueVersion(
        cue=cue,
        label="v1",
        written_to=cue.picture.current_version(),
        tc_in=cue.tc_in,
        tc_out=cue.tc_out,
        seq_start=cue.seq_start,
        sub_status=sub_status,
        **(notes or {}),
    )
    version.full_clean()
    version.save()
    cue.current_version = version
    cue.save(update_fields=["current_version", "updated_at"])
    shares.give_defaults(cue)
    return cue


def create_version(
    version: models.CueVersion,
    *,
    update_cue: bool,
    status: models.StatusOption | None = None,
    user: User | None = None,
) -> models.CueVersion:
    """Saves a new version and makes it current.

    Args:
        version: An unsaved version of a cue, already validated.
        update_cue: Also set the cue's current timecodes to the version's.
        status: The cue's status from now on (e.g. Revisions); None
            leaves it as it is.
        user: Who added it.

    Returns:
        The saved, current version.

    Raises:
        django.core.exceptions.ValidationError: Invalid fields; nothing
            is saved.
    """
    cue = version.cue
    label = f"New version {cue.number} {version.label}"
    with history.recording(cue.project, label, user):
        save_version(version, update_cue=update_cue, status=status)
    return version


def save_version(
    version: models.CueVersion,
    *,
    update_cue: bool,
    status: models.StatusOption | None = None,
) -> models.CueVersion:
    """create_version's work, inside the caller's change set (a tool's).

    Raises:
        django.core.exceptions.ValidationError: Invalid fields.
    """
    cue = version.cue
    previous = cue.current_version
    version.full_clean()
    version.save()
    if previous is not None:
        # Themes carry over to the new version.
        for link in previous.themes.all():
            models.VersionTheme.objects.create(
                version=version,
                theme_id=link.theme_id,
                seconds=link.seconds,
                order=link.order,
            )
    cue.current_version = version
    if update_cue:
        for name in ("tc_in", "tc_out", "seq_start"):
            setattr(cue, name, getattr(version, name))
    if status is not None:
        cue.status = status
    sync_review_status(cue, version)
    cue.full_clean()
    cue.save()
    return version


def make_current(
    version: models.CueVersion, *, user: User | None = None
) -> models.CueVersion:
    """Makes an existing version the cue's current one.

    The cue's own timecodes stay as they are: they describe where the cue
    sits in today's picture, whichever version is current.
    """
    cue = version.cue
    with history.recording(
        cue.project, f"Make {cue.number} {version.label} current", user
    ):
        cue.current_version = version
        cue.save()
    return version


def delete_version(
    version: models.CueVersion, *, user: User | None = None
) -> None:
    """Deletes a version; the newest remaining one becomes current."""
    cue = version.cue
    with history.recording(
        cue.project, f"Delete {cue.number} {version.label}", user
    ):
        if cue.current_version_id == version.pk:
            cue.current_version = (
                cue.versions.exclude(pk=version.pk)
                .order_by("-created_at", "-id")
                .first()
            )
            cue.save()
        version.delete()


def delete_cue(cue: models.Cue, *, user: User | None = None) -> None:
    """Deletes a cue and its versions, as one undoable change."""
    with history.recording(cue.project, f"Delete cue {cue.number}", user):
        cue.current_version = None
        cue.save()
        for version in cue.versions.all():
            version.delete()
        cue.delete()


def set_thumbnail(
    cue: models.Cue,
    content: "ContentFile[bytes] | None",
    *,
    user: User | None = None,
) -> models.Cue:
    """Replaces (or with None, removes) a cue's thumbnail, undoably.

    The old file stays on disk: undoing this change points back at it.

    Args:
        cue: The cue.
        content: A processed image from thumbnails.save_upload, or None.
        user: Who changed it.

    Returns:
        The cue.
    """
    label = f"Edit {cue.number} Thumbnail"
    with history.recording(cue.project, label, user):
        if content is None:
            cue.thumbnail = None
            cue.save()
        else:
            cue.thumbnail.save(content.name or "thumbnail.webp", content)
    return cue


def reopened_status(cue: models.Cue) -> models.StatusOption | None:
    """The status a cue takes with a new version (Nick, Oct 31).

    Approved means the current version was approved, so a new version
    reopens review: an Approved cue goes back to the project's Done-kind
    status (Composed). Other statuses carry over.
    """
    if cue.status is not None and cue.status.kind == models.StatusKind.APPROVED:
        composed = cue.project.statuses.filter(
            kind=models.StatusKind.DONE
        ).first()
        if composed is not None:
            return composed
    return cue.status


def sync_review_status(cue: models.Cue, version: models.CueVersion) -> None:
    """Moves a composed cue to Approved when its current version is.

    Only with the project's "sync status with review status" setting on
    The caller saves the cue.
    """
    if (
        cue.project.sync_review_status
        and version.review_status == models.CueVersion.ReviewStatus.APPROVED
        and cue.current_version_id == version.pk
        and cue.status is not None
        and cue.status.kind == models.StatusKind.DONE
    ):
        approved = cue.project.statuses.filter(
            kind=models.StatusKind.APPROVED
        ).first()
        if approved is not None:
            cue.status = approved
