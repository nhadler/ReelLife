"""Addresses and a guest's post, shared by the review link tests."""

from django.test import Client
from django.urls import reverse

from cues import models

FILE_LINK = "https://www.dropbox.com/scl/fi/abc/1m01_v1.mp4?rlkey=k&dl=0"
HX = {"HX-Request": "true"}


def guest(name: str = "Dana") -> Client:
    """A browser that has said who is watching."""
    client = Client()
    session = client.session
    session["review_name"] = name
    session.save()
    return client


def page_url(link: models.ReviewLink) -> str:
    return reverse("cues:review", args=[link.token])


def comments_url(link: models.ReviewLink) -> str:
    return reverse("cues:review_comments", args=[link.token])


def post(
    client: Client, link: models.ReviewLink, **data: str
) -> tuple[int, str]:
    values = {"v": "v1", "name": "Dana", "body": "Too busy", **data}
    response = client.post(comments_url(link), values, headers=HX)
    return response.status_code, response.content.decode()


def panel_url(cue: models.Cue) -> str:
    return reverse("cues:cue_review", args=[cue.pk])
