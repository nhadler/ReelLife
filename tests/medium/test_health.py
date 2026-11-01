"""Tests for the /healthz and /readyz probes."""

from unittest import mock

from django.db import DatabaseError
from django.test import Client
import pytest


def test_healthz_says_only_ok(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_healthz_refuses_post(client: Client) -> None:
    assert client.post("/healthz").status_code == 405


@pytest.mark.django_db
def test_readyz_ok_when_migrated(client: Client) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.content == b"ok"


@pytest.mark.django_db
def test_readyz_unavailable_with_pending_migrations(client: Client) -> None:
    with mock.patch(
        "django.db.migrations.executor.MigrationExecutor.migration_plan",
        return_value=[("pending", False)],
    ):
        response = client.get("/readyz")
    assert response.status_code == 503


@pytest.mark.django_db
def test_readyz_unavailable_when_database_fails(client: Client) -> None:
    with mock.patch(
        "django.db.migrations.executor.MigrationExecutor.migration_plan",
        side_effect=DatabaseError("locked"),
    ):
        response = client.get("/readyz")
    assert response.status_code == 503
    assert b"locked" not in response.content
