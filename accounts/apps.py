"""App config for accounts."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Accounts app."""

    name = "accounts"

    def ready(self) -> None:
        """Connects the sign-in time stamp for session lifetimes."""
        from django.contrib.auth import signals  # pylint: disable=import-outside-toplevel

        from accounts import sessions  # pylint: disable=import-outside-toplevel

        signals.user_logged_in.connect(
            sessions.mark_signed_in, dispatch_uid="reellife.mark_signed_in"
        )
