"""Tests for accounts.audit."""

import logging

from django.contrib import admin
from django.test import RequestFactory
import pytest

from accounts import audit
from accounts import models


@pytest.mark.django_db
def test_record_saves_event_with_ip_and_detail() -> None:
    user = models.User.objects.create(username="ada")
    request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5")
    event = audit.record(
        models.AuditAction.LOGIN, request=request, user=user, method="oidc"
    )
    event.refresh_from_db()
    assert event.actor == "ada"
    assert event.ip == "10.0.0.5"
    assert event.detail == {"method": "oidc"}
    assert str(event).endswith("login ada")


@pytest.mark.django_db
def test_record_without_user_uses_actor_label() -> None:
    event = audit.record(
        models.AuditAction.LOGIN_REFUSED, actor="sub-123", reason="no_group"
    )
    assert event.user is None
    assert event.actor == "sub-123"
    assert event.ip is None


@pytest.mark.django_db
def test_record_ignores_unparseable_ip() -> None:
    request = RequestFactory().get("/", REMOTE_ADDR="not-an-ip")
    event = audit.record(models.AuditAction.LOGOUT, request=request)
    assert event.ip is None


@pytest.mark.django_db
def test_record_emits_structured_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="reeltime.audit"):
        audit.record(models.AuditAction.LOGOUT, actor="ada")
    (log,) = caplog.records
    assert log.audit == {  # type: ignore[attr-defined]
        "action": "logout",
        "actor": "ada",
        "ip": None,
        "detail": {},
    }


@pytest.mark.django_db
def test_admin_cannot_edit_audit_log() -> None:
    model_admin = admin.site.get_model_admin(models.AuditEvent)
    request = RequestFactory().get("/")
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
