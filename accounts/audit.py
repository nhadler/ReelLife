"""Writes audit events to the database and as JSON log lines."""

import datetime
import ipaddress
import logging
from typing import Any

from django.http import HttpRequest

from accounts import models
from reeltime import net

_LOGGER = logging.getLogger("reeltime.audit")


def _valid_ip(text: str) -> str | None:
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def record(
    action: models.AuditAction,
    *,
    request: HttpRequest | None = None,
    user: models.User | None = None,
    actor: str = "",
    **detail: Any,
) -> models.AuditEvent:
    """Records an audit event.

    Args:
        action: What happened.
        request: The request it happened in, for the client IP.
        user: Who did it, if known.
        actor: A label for who did it when there is no user (e.g. the
            OIDC sub of a refused login). Defaults to the username.
        **detail: JSON-serialisable context, e.g. reason='no_group'. Never
            pass tokens, passwords or cookies.

    Returns:
        The saved event.
    """
    ip = _valid_ip(net.client_ip(request)) if request is not None else None
    event = models.AuditEvent.objects.create(
        action=action,
        user=user,
        actor=actor or (user.username if user is not None else ""),
        ip=ip,
        detail=detail,
    )
    _LOGGER.info(
        "audit %s",
        action.value,
        extra={
            "audit": {
                "action": action.value,
                "actor": event.actor,
                "ip": ip,
                "detail": detail,
            }
        },
    )
    return event


def purge_before(cutoff: datetime.datetime) -> int:
    """Deletes audit events older than `cutoff` (one-year retention).

    Returns:
        How many events were deleted.
    """
    deleted, _ = models.AuditEvent.objects.filter(
        created_at__lt=cutoff
    ).delete()
    return deleted
