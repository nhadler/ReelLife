"""Review links: the page a guest opens, and the cue page's Review section.

The review page (`/review/<token>/`) and what it posts to are outside
sign-in: the link's secret token stands for permission to watch one cue's
bounces and comment on them (cues.services.review). A link can also ask
for a password, which a browser gives once per session. Every one of
these views is rate-limited per client address, password guesses also
per link, and their responses are never cached or indexed.

The rest is for signed-in users: the cue page's Review section with the
link's settings, and resolving or deleting a comment (an editor does
both on the review page).
"""

from collections.abc import Callable
import datetime
import json
from typing import Any, TypeVar, cast
import urllib.parse

from csp import constants as csp
from csp.decorators import csp_update
from django import forms as django_forms
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_POST
from django_ratelimit.core import get_usage
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

from accounts import roles
from accounts.authz import role_required
from accounts.models import Role
from accounts.models import User
from cues import models
from cues import timecode
from cues.services import review
from reeltime import net

ViewT = TypeVar("ViewT", bound=Callable[..., Any])

# What a guest's session remembers: links it gave the password for, and
# the name it comments under.
UNLOCKED = "review_unlocked"
NAME = "review_name"

WRONG_PASSWORD = "That isn't the password for this link."  # noqa: S105  # nosec B105
CLOSED = "Comments are closed on this link."


def _rate(name: str) -> Callable[[str, HttpRequest], str]:
    def rate(group: str, request: HttpRequest) -> str:
        del group, request
        return str(getattr(settings, name))

    return rate


def _token_key(group: str, request: HttpRequest) -> str:
    """Rate-limit key: the link, whoever is asking."""
    del group
    match = request.resolver_match
    return str(match.kwargs.get("token", "")) if match else ""


def _limit(group: str, name: str, **kwargs: Any) -> Callable[[ViewT], ViewT]:
    return cast(
        Callable[[ViewT], ViewT],
        ratelimit(
            group=group,
            key=kwargs.pop("key", net.ratelimit_key),
            rate=_rate(name),
            block=True,
            **kwargs,
        ),
    )


# The review page may load video from Dropbox; nothing else changes.
_media: Callable[[ViewT], ViewT] = cast(
    Callable[[ViewT], ViewT],
    csp_update({"media-src": [csp.SELF, *review.MEDIA_SOURCES]}),
)


def _private(response: HttpResponse) -> HttpResponse:
    """Keeps a review page out of caches and search engines."""
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _member(request: HttpRequest) -> User | None:
    """The signed-in user, if they have a ReelTime role."""
    user = request.user
    if (
        isinstance(user, User)
        and user.is_active
        and roles.at_least(user.role, Role.VIEWER)
    ):
        return user
    return None


def _gone(request: HttpRequest) -> HttpResponse:
    return _private(render(request, "cues/review/gone.html", status=404))


def _locked(request: HttpRequest, link: models.ReviewLink) -> bool:
    """Whether this browser still has to give the link's password."""
    if not link.password or _member(request):
        return False
    kept = request.session.get(UNLOCKED, {})
    return not review.unlocked(link, kept.get(str(link.pk)))


def _rate_config(rate: timecode.Rate, first: int) -> dict[str, Any]:
    """What the player needs to turn its position into a timecode."""
    return {
        "fps": [rate.fps.numerator, rate.fps.denominator],
        "nominal": rate.nominal,
        "dropFrame": rate.drop_frame,
        "firstFrame": first,
    }


def _context(
    request: HttpRequest, link: models.ReviewLink, shown: review.Playable
) -> dict[str, Any]:
    member = _member(request)
    return {
        "link": link,
        "cue": link.cue,
        "shown": shown,
        "comments": review.comments(link, shown.version),
        "member": member,
        "can_resolve": member is not None
        and roles.at_least(member.role, Role.EDITOR),
        "guest_name": request.session.get(NAME, ""),
        "can_comment": link.comments_open,
        "max_name": review.MAX_NAME,
        "max_body": review.MAX_BODY,
        "comments_url": reverse("cues:review_comments", args=[link.token]),
        "stamp": review.stamp(link, shown.version),
    }


@login_not_required
@require_GET
@_limit("review", "RATELIMIT_REVIEW", method="GET")
@_media
def page(request: HttpRequest, token: str) -> HttpResponse:
    """The review page: a version's video and its comments."""
    link = review.find(token)
    if link is None:
        return _gone(request)
    cue = link.cue
    if _locked(request, link):
        return _private(
            render(request, "cues/review/locked.html", {"link": link})
        )
    found = review.playable(cue)
    shown = review.choose(found, request.GET.get("v", ""))
    if shown is None and "v" in request.GET:
        return _gone(request)
    link.last_opened_at = timezone.now()
    link.save(update_fields=["last_opened_at"])
    context: dict[str, Any] = {
        "link": link,
        "cue": cue,
        "project": cue.project,
        "versions": found,
        "shown": shown,
    }
    if shown is not None:
        context |= _context(request, link, shown)
        context["player"] = _rate_config(
            cue.project.rate, review.first_frame(link, shown.version)
        )
    return _private(render(request, "cues/review/page.html", context))


def _too_many_tries(request: HttpRequest, *, count: bool) -> bool:
    """Whether a link has had its fill of wrong passwords this hour.

    Counted per link, whoever tries, on top of the limit per address.
    Only wrong tries count (`count` adds one), so reviewers opening the
    link don't use it up; at the limit nobody gets in until the hour
    turns.
    """
    usage = get_usage(
        request,
        group="review-unlock-link",
        key=_token_key,
        rate=settings.RATELIMIT_REVIEW_UNLOCK_LINK,
        method=["POST"],
        increment=count,
    )
    return usage is not None and usage["count"] >= usage["limit"]


@login_not_required
@require_POST
@_limit("review-unlock", "RATELIMIT_REVIEW_UNLOCK")
def unlock(request: HttpRequest, token: str) -> HttpResponse:
    """Takes a link's password; right, the session remembers it.

    Raises:
        Ratelimited: The link has had too many wrong passwords.
    """
    link = review.find(token)
    if link is None:
        return _gone(request)
    if link.password:
        if _too_many_tries(request, count=False):
            raise Ratelimited
        if not review.check_password(link, request.POST.get("password", "")):
            _too_many_tries(request, count=True)
            return _private(
                render(
                    request,
                    "cues/review/locked.html",
                    {"link": link, "error": WRONG_PASSWORD},
                )
            )
    # A new session id for the browser that now holds the key.
    request.session.cycle_key()
    kept = dict(request.session.get(UNLOCKED, {}))
    kept[str(link.pk)] = review.unlock_key(link)
    request.session[UNLOCKED] = kept
    return _private(redirect("cues:review", link.token))


def _frame(text: str) -> int | None:
    """The posted frame: digits, or None for the whole version.

    Raises:
        ReviewError: It's something else.
    """
    text = text.strip()
    if not text:
        return None
    if not text.isascii() or not text.isdigit() or len(text) > 9:
        raise review.ReviewError("That time isn't in the video.")
    return int(text)


def _parent(
    version: models.CueVersion, text: str
) -> models.ReviewComment | None:
    """The note a posted comment replies to, if it names one.

    Raises:
        ReviewError: It isn't one of this version's notes.
    """
    text = text.strip()
    if not text:
        return None
    found = (
        version.review_comments.filter(pk=int(text), parent=None).first()
        if text.isascii() and text.isdigit() and len(text) <= 18
        else None
    )
    if found is None:
        raise review.ReviewError(GONE)
    return found


GONE = "That comment isn't there any more."


def _comments(
    request: HttpRequest,
    link: models.ReviewLink,
    shown: review.Playable,
    *,
    posted: bool = False,
    error: str = "",
) -> HttpResponse:
    """The comment list; after a post, with the form's error and name."""
    context = _context(request, link, shown)
    context |= {"posted": posted, "error": error}
    response = render(request, "cues/review/list.html", context)
    if posted:
        # review.js clears the comment box, or drops a reply to a note
        # that has gone.
        event = "review:reply-gone" if error == GONE else "review:posted"
        if not error or error == GONE:
            response["HX-Trigger"] = json.dumps({event: True})
    return _private(response)


@login_not_required
@require_http_methods(["GET", "POST"])
@_limit("review", "RATELIMIT_REVIEW", method="GET")
@_limit("review-comment", "RATELIMIT_REVIEW_COMMENTS", method="POST")
@_limit(
    "review-comment-link",
    "RATELIMIT_REVIEW_COMMENTS_LINK",
    key=_token_key,
    method="POST",
)
def comments(request: HttpRequest, token: str) -> HttpResponse:
    """A version's comments (the page refreshes them), or adds one.

    A refresh sends the list's stamp and gets 204 while nothing changed,
    so the page isn't redrawn under someone reading or about to click.
    """
    link = review.find(token)
    if link is None or _locked(request, link):
        return _gone(request)
    values = request.POST if request.method == "POST" else request.GET
    shown = review.choose(review.playable(link.cue), values.get("v", "") or "-")
    if shown is None:
        return _gone(request)
    if request.method == "GET":
        if request.GET.get("stamp") == review.stamp(link, shown.version):
            return _private(HttpResponse(status=204))
        return _comments(request, link, shown)
    if not link.comments_open:
        return _comments(request, link, shown, posted=True, error=CLOSED)
    member = _member(request)
    try:
        comment = review.add_comment(
            shown.version,
            name=request.POST.get("name", ""),
            body=request.POST.get("body", ""),
            frame=_frame(request.POST.get("frame", "")),
            user=member,
            parent=_parent(shown.version, request.POST.get("parent", "")),
        )
    except review.ReviewError as exc:
        return _comments(request, link, shown, posted=True, error=str(exc))
    if member is None:
        request.session[NAME] = comment.name
    return _comments(request, link, shown, posted=True)


# The cue page's side, for signed-in users.


def _user(request: HttpRequest) -> User | None:
    return request.user if isinstance(request.user, User) else None


def _can_edit(request: HttpRequest) -> bool:
    return roles.at_least(getattr(request.user, "role", ""), Role.EDITOR)


def _cue(cue_id: int) -> models.Cue:
    return get_object_or_404(
        models.Cue.objects.select_related("project", "current_version"),
        pk=cue_id,
    )


def _web_link(text: str) -> str:
    """The text if it's an http(s) URL (safe to link to), else ""."""
    try:
        parts = urllib.parse.urlsplit(text.strip())
    except ValueError:
        return ""
    return (
        text.strip()
        if parts.scheme in ("http", "https") and parts.netloc
        else ""
    )


def panel_context(
    request: HttpRequest,
    cue: models.Cue,
    version: models.CueVersion | None,
) -> dict[str, Any]:
    """The Review section's context, for the cue page and its refreshes."""
    link = models.ReviewLink.objects.filter(cue=cue).first()
    return {
        "cue": cue,
        "review_link": link,
        "review_expired": link is not None and review.expired(link),
        "review_url": settings.BASE_URL
        + reverse("cues:review", args=[link.token])
        if link
        else "",
        "selected": version,
        "review_playable": bool(version and review.play_url(version.file_link)),
        "file_link": version.file_link.strip() if version else "",
        "audio_link": version.audio_link.strip() if version else "",
        "audio_playable": bool(version and review.play_url(version.audio_link)),
        "file_url": _web_link(version.file_link) if version else "",
        "review_notes": review.note_counts(version) if version else (0, 0),
        "review_form": LinkForm(link=link, version=version)
        if link and _can_edit(request)
        else None,
        "review_preroll": preroll_text(link.preroll, cue.project.rate)
        if link
        else "",
        "can_edit": _can_edit(request),
    }


def _panel(
    request: HttpRequest,
    cue: models.Cue,
    version: models.CueVersion | None,
) -> HttpResponse:
    return render(
        request,
        "cues/details/review.html",
        panel_context(request, cue, version),
    )


def _version(cue: models.Cue, text: str) -> models.CueVersion | None:
    """The version a panel is about: the one named, else the current."""
    if text.isascii() and text.isdigit():
        return get_object_or_404(cue.versions, pk=int(text))
    return cue.current_version or cue.versions.order_by("-created_at").first()


@require_GET
def panel(request: HttpRequest, cue_id: int) -> HttpResponse:
    """The cue page's Review section: the link and a version's comments."""
    cue = _cue(cue_id)
    return _panel(request, cue, _version(cue, request.GET.get("version", "")))


@role_required(Role.EDITOR)
@require_POST
def create(request: HttpRequest, cue_id: int) -> HttpResponse:
    """Makes the cue's review link and shows it."""
    cue = _cue(cue_id)
    review.create(cue, _user(request))
    return _panel(request, cue, _version(cue, request.POST.get("version", "")))


class LinkForm(django_forms.Form):
    """A review link's settings."""

    password = django_forms.CharField(
        required=False,
        max_length=100,
        # A code to pass on, not the user's own password: shown as typed,
        # and password managers are asked to leave it alone.
        widget=django_forms.TextInput(
            attrs={
                "autocomplete": "off",
                "spellcheck": "false",
                "data-bwignore": "true",
                "data-1p-ignore": "true",
                "data-lpignore": "true",
            }
        ),
    )
    expires_on = django_forms.DateField(
        label="Last Day",
        required=False,
        widget=django_forms.DateInput(attrs={"type": "date"}),
    )
    comments_open = django_forms.BooleanField(
        label="Allow Comments", required=False
    )
    preroll = django_forms.CharField(
        label="Pre-Roll",
        required=False,
        max_length=11,
        widget=django_forms.TextInput(
            attrs={
                "placeholder": "0:00",
                "title": "Video before the cue's IN, as m:ss or a timecode",
            }
        ),
    )

    def __init__(
        self,
        *args: Any,
        link: models.ReviewLink,
        version: models.CueVersion | None = None,
        **kwargs: Any,
    ) -> None:
        """Starts from the link's settings.

        `version` is the one the cue page is showing, sent back with
        each save so the answer is about the same version.
        """
        self.link = link
        self.rate = link.cue.project.rate
        kwargs["initial"] = {
            "expires_on": link.expires_on,
            "comments_open": link.comments_open,
            "preroll": preroll_text(link.preroll, self.rate),
        }
        super().__init__(*args, **kwargs)
        self.fields["password"].widget.attrs["placeholder"] = (
            "Set. Type a new one to change it." if link.password else "None"
        )
        # Each field posts itself on change, alone: see link_settings.
        for name, field in self.fields.items():
            sent = {"field": name}
            if version is not None:
                sent["version"] = str(version.pk)
            field.widget.attrs |= {
                "hx-post": reverse("cues:review_link", args=[link.cue_id]),
                # A date typed digit by digit is a date at each digit.
                "hx-trigger": "change delay:600ms"
                if name == "expires_on"
                else "change",
                "hx-vals": json.dumps(sent),
                "hx-target": "#review-status",
                "hx-swap": "outerHTML",
                "hx-sync": "#cue-review:queue all",
            }

    def clean_password(self) -> str:
        """Checks a new password's length."""
        password = str(self.cleaned_data["password"])
        if password and len(password) < review.MIN_PASSWORD:
            raise django_forms.ValidationError(
                f"Use at least {review.MIN_PASSWORD} characters."
            )
        return password

    def clean_expires_on(self) -> datetime.date | None:
        """A last day already past would end the link at once."""
        day = cast(datetime.date | None, self.cleaned_data["expires_on"])
        if day is not None and day < timezone.localdate():
            raise django_forms.ValidationError("Choose today or a later day.")
        return day

    def clean_preroll(self) -> int:
        """The pre-roll in frames."""
        text = str(self.cleaned_data["preroll"]).strip()
        if not text:
            return 0
        try:
            return timecode.parse_duration(text, self.rate)
        except timecode.TimecodeError:
            raise django_forms.ValidationError(
                "Enter a length like 0:02, or a timecode like 00:00:02:00."
            ) from None

    def save(self, field: str) -> None:
        """Writes one setting to the link: the one that was changed."""
        link, value = self.link, self.cleaned_data[field]
        if field == "password":
            if value:
                review.set_password(link, value)
        else:
            setattr(link, field, value)
        link.save()


def preroll_text(frames: int, rate: timecode.Rate) -> str:
    """A pre-roll as it's typed: m:ss for whole seconds, else a timecode."""
    if not frames:
        return ""
    short = timecode.format_duration(frames, rate)
    if timecode.parse_duration(short, rate) == frames:
        return short
    return timecode.format_timecode(frames, rate, leading_zero=True)


@role_required(Role.EDITOR)
@require_POST
def link_settings(request: HttpRequest, cue_id: int) -> HttpResponse:
    """Saves one of the link's settings, or revokes the link.

    Each setting posts itself when it changes, naming itself in `field`,
    and only that one is checked and saved: a mistake in one, or a value
    another tab shows, never touches the rest. The answer is the status
    under the settings: what the link is set to now, or the error.
    """
    cue = _cue(cue_id)
    version = _version(cue, request.POST.get("version", ""))
    link = get_object_or_404(models.ReviewLink, cue=cue)
    if "revoke" in request.POST:
        review.revoke(link)
        return _panel(request, cue, version)
    if "remove_password" in request.POST:
        review.set_password(link, "")
        link.save()
        return _panel(request, cue, version)
    form = LinkForm(request.POST, link=link)
    field = request.POST.get("field", "")
    if field not in form.fields:
        return HttpResponse(status=400)
    form.is_valid()  # cleans every field; only this one's result is used
    error = " ".join(str(e) for e in form.errors.get(field, []))
    if not error:
        form.save(field)
    context = panel_context(request, cue, version)
    context["review_error"] = (
        f"{form.fields[field].label or 'Password'}: {error}" if error else ""
    )
    # Only the status is redrawn, so the other fields stay as typed. A
    # saved password's field is emptied again.
    context["empty_field"] = field == "password" and not error
    return render(request, "cues/details/review_status.html", context)


def _review_changed() -> HttpResponse:
    """Tells the review page (and a cue page) to reload its comments."""
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"review:changed": True})
    return response


def _comment(comment_id: int) -> models.ReviewComment:
    return get_object_or_404(
        models.ReviewComment.objects.select_related("version__cue__project"),
        pk=comment_id,
    )


@role_required(Role.EDITOR)
@require_POST
def delete_comment(request: HttpRequest, comment_id: int) -> HttpResponse:
    """Deletes a comment and any replies to it (undoable)."""
    review.delete_comment(_comment(comment_id), _user(request))
    return _review_changed()


@role_required(Role.EDITOR)
@require_POST
def resolve(request: HttpRequest, comment_id: int) -> HttpResponse:
    """Marks a note resolved, or open again with `resolved=0`."""
    try:
        review.resolve(
            _comment(comment_id),
            _user(request),
            resolved=request.POST.get("resolved", "1") != "0",
        )
    except review.ReviewError:
        return HttpResponse(status=400)
    return _review_changed()
