"""The Ctrl+K palette's search results.

Read-only, so viewers may use it. The palette asks as the user types;
the answer is JSON for it to draw.
"""

import dataclasses

from django.http import HttpRequest
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from cues.services import search


@require_GET
def results(request: HttpRequest) -> JsonResponse:
    """Projects and cues for ?q=, ranking ?project='s cues first."""
    query = request.GET.get("q", "")
    project = request.GET.get("project", "")
    project_id = int(project) if project.isdigit() else None
    hits = [*search.projects(query), *search.cues(query, project_id)]
    return JsonResponse({"results": [dataclasses.asdict(hit) for hit in hits]})
