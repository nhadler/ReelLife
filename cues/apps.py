"""App config for cues."""

from typing import Any

from django.apps import AppConfig
from django.db.models.signals import post_delete
from django.db.models.signals import post_migrate


def _delete_thumbnail_file(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Removes a deleted project's thumbnail file from storage."""
    del sender, kwargs
    if instance.thumbnail:
        instance.thumbnail.storage.delete(instance.thumbnail.name)


class CuesConfig(AppConfig):
    """Cues app."""

    name = "cues"

    def ready(self) -> None:
        """Connects the history and thumbnail signal handlers."""
        # pylint: disable=import-outside-toplevel
        from simple_history import signals

        from cues.services import history

        signals.pre_create_historical_record.connect(
            history.tag_history_row, dispatch_uid="reellife.tag_history_row"
        )
        post_migrate.connect(
            history.keep_ids_unique,
            sender=self,
            dispatch_uid="reellife.keep_ids_unique",
        )
        post_delete.connect(
            _delete_thumbnail_file,
            sender="cues.Project",
            dispatch_uid="reellife.delete_thumbnail_file",
        )
