"""End-to-end fixtures: ReelTime, a fake OIDC server and axe.

Runs the app with Django's live server (test settings, every middleware,
the strict CSP) and signs in through the fake OIDC provider served over
HTTP, driven by Playwright. Container deployment is tested separately.
"""

from collections.abc import Callable, Iterator
import copy
import os
import pathlib
import threading
from typing import Any, cast
from wsgiref import simple_server

from django.conf import settings as django_settings
from django.core.cache import cache
import pytest

from tests.fakes import oidc_provider

# Playwright's sync API runs an event loop on the test thread; the test
# only touches the database between browser steps, never concurrently.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

ROOT = pathlib.Path(__file__).resolve().parents[3]
AXE = ROOT / "node_modules" / "axe-core" / "axe.min.js"
CLIENT_ID = "reeltime-e2e"
CLIENT_SECRET = "reeltime-e2e-secret-long-enough-for-hs256"  # noqa: S105  # nosec B105


@pytest.fixture(scope="session")
def django_db_modify_db_settings(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A database file rather than memory, as in production.

    The live server answers requests in threads; Django shares a single
    connection between them for an in-memory database, so two requests at
    once (a grid reload and a header refresh) would collide.
    """
    path = tmp_path_factory.mktemp("e2e-db") / "reeltime.sqlite3"
    test_settings = cast(
        dict[str, Any], django_settings.DATABASES["default"]["TEST"]
    )
    test_settings["NAME"] = str(path)


class _QuietHandler(simple_server.WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 pylint: disable=redefined-builtin
        del format, args


@pytest.fixture(name="provider")
def fixture_provider() -> Iterator[oidc_provider.FakeProvider]:
    """The fake provider on a real port, as `just dev` runs it."""
    server = simple_server.make_server(
        "127.0.0.1", 0, lambda e, s: [], handler_class=_QuietHandler
    )
    port = server.server_port
    provider = oidc_provider.FakeProvider(
        f"http://127.0.0.1:{port}", CLIENT_ID, CLIENT_SECRET
    )
    server.set_app(oidc_provider.wsgi_app(provider))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield provider
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session", name="server")
def fixture_server(django_db_setup: None, live_server: Any) -> Any:
    """The live server, started after the test database exists.

    Both are session fixtures; started first, the server would see the
    in-memory database and share one connection between its threads.
    """
    del django_db_setup
    return live_server


@pytest.fixture(name="app")
def fixture_app(
    server: Any,
    provider: oidc_provider.FakeProvider,
    settings: Any,
    tmp_path: pathlib.Path,
) -> str:
    """The live app, pointed at the provider. Returns its base URL."""
    settings.OIDC_ISSUER = provider.issuer
    settings.OIDC_RP_CLIENT_ID = CLIENT_ID
    settings.OIDC_RP_CLIENT_SECRET = CLIENT_SECRET
    settings.BASE_URL = server.url
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
    settings.DATA_DIR = tmp_path
    policy = copy.deepcopy(settings.CONTENT_SECURITY_POLICY)
    policy["DIRECTIVES"]["form-action"].append(provider.issuer)
    settings.CONTENT_SECURITY_POLICY = policy
    cache.clear()  # no discovery document from another test
    return str(server.url)


@pytest.fixture(name="csp_violations")
def fixture_csp_violations(page: Any) -> Iterator[list[str]]:
    """Every CSP violation the browser reports; the test must leave none."""
    found: list[str] = []
    page.on(
        "console",
        lambda m: (
            found.append(m.text)
            if "Content Security Policy" in m.text
            else None
        ),
    )
    yield found
    assert not found, found


# check(where, within=None): `within` limits it to one CSS selector.
Check = Callable[..., None]


@pytest.fixture(name="axe")
def fixture_axe(page: Any) -> Check:
    """Runs axe-core on the page: no serious or critical WCAG A/AA issues.

    axe is evaluated through Playwright, not added to the page, so the
    page's strict CSP stays on while it runs.
    """
    source = AXE.read_text(encoding="utf-8")

    def check(where: str, within: str | None = None) -> None:
        page.evaluate(source)
        problems = page.evaluate(
            """async (within) => {
              const root = within ? document.querySelector(within) : document;
              const result = await axe.run(root, {
                runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'],
              });
              return result.violations
                .filter((v) => ['serious', 'critical'].includes(v.impact))
                .map((v) => v.id + ': ' +
                  v.nodes.map((n) => n.target).join(' | '));
            }""",
            within,
        )
        assert not problems, f"{where}: {problems}"

    return check


SignInAs = Callable[[str], None]


@pytest.fixture(name="sign_in_as")
def fixture_sign_in_as(page: Any, app: str) -> SignInAs:
    """Signs in through the fake provider's picker, e.g. 'Casey Composer'."""

    def run(name: str) -> None:
        page.goto(f"{app}/")
        page.get_by_role("link", name="Sign In with PocketID").click()
        page.get_by_role("link", name=name).click()
        page.wait_for_url(f"{app}/")

    return run


@pytest.fixture(autouse=True)
def fixture_diagnostics(page: Any) -> Iterator[None]:
    """Prints page errors and failed requests; pytest shows them on failure."""
    seen: list[str] = []
    page.on("pageerror", lambda error: seen.append(f"page error: {error}"))
    page.on(
        "console",
        lambda m: (
            seen.append(f"console {m.type}: {m.text}")
            if m.type == "error"
            else None
        ),
    )
    page.on(
        "response",
        lambda r: (
            seen.append(f"HTTP {r.status} {r.request.method} {r.url}")
            if r.status >= 400
            else None
        ),
    )
    yield
    for line in seen:
        print(line)  # noqa: T201
