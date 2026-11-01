"""Liveness and readiness probes for Docker and systemd."""

from django.db import DEFAULT_DB_ALIAS
from django.db import DatabaseError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest
from django.http import HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request: HttpRequest) -> HttpResponse:  # pylint: disable=unused-argument
    """Reports that the process is up. Says nothing else, by design."""
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def readyz(request: HttpRequest) -> HttpResponse:  # pylint: disable=unused-argument
    """Reports whether the database is reachable and fully migrated."""
    connection = connections[DEFAULT_DB_ALIAS]
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        pending = executor.migration_plan(targets)
    except DatabaseError:
        return HttpResponse(
            "unavailable", status=503, content_type="text/plain"
        )
    if pending:
        return HttpResponse(
            "unavailable", status=503, content_type="text/plain"
        )
    return HttpResponse("ok", content_type="text/plain")
