"""Help: the user guide's pages (cues/services/guide.py)."""

from django.conf import settings
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from cues.services import guide
from reellife import updates


@require_GET
def page(request: HttpRequest, slug: str = "") -> HttpResponse:
    """A guide topic; the first one when none is named."""
    topics = guide.topics()
    found = guide.topic(slug) if slug else topics[0]
    if found is None:
        raise Http404("No such help topic.")
    return render(
        request,
        "cues/help.html",
        {
            "topics": topics,
            "topic": found,
            "body": guide.html(found),
            "version": updates.installed(),
            "source_url": settings.REELLIFE_SOURCE_URL,
        },
    )
