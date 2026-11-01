"""Creates (or resets) the break-glass local admin."""

import secrets
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts import breakglass
from accounts import models


class Command(BaseCommand):
    """manage.py create_breakglass [--reset]."""

    help = (
        "Create the break-glass local admin and print its password and "
        "TOTP setup URI once. Sign-in also needs "
        "REELLIFE_BREAKGLASS_ENABLED=true."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Adds --reset."""
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Replace the existing password and TOTP device.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        """Creates or resets the account and prints its credentials.

        Raises:
            CommandError: If the account exists and --reset isn't given.
        """
        del args
        user = models.User.objects.filter(username=breakglass.USERNAME).first()
        if user is not None and not options["reset"]:
            raise CommandError(
                "The break-glass admin exists; use --reset to replace its "
                "password and TOTP device."
            )
        if user is None:
            user = models.User(username=breakglass.USERNAME)
        password = secrets.token_urlsafe(24)
        user.oidc_sub = None
        user.role = models.Role.ADMIN
        user.is_staff = user.is_superuser = True
        user.set_password(password)
        user.save()
        TOTPDevice.objects.filter(user=user).delete()
        device = TOTPDevice.objects.create(
            user=user, name="break-glass", confirmed=True
        )
        self.stdout.write(
            "Break-glass admin ready. Store these somewhere safe; they are "
            "shown once.\n"
            f"  username: {user.username}\n"
            f"  password: {password}\n"
            f"  TOTP URI: {device.config_url}\n"
            "Add the TOTP URI to an authenticator app. Enable sign-in at "
            "/auth/local/ with REELLIFE_BREAKGLASS_ENABLED=true, and turn "
            "it off again once PocketID is back."
        )
