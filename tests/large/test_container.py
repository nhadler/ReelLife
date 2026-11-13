"""The image and Compose deployment (large).

Needs a Docker engine; skipped without one. Run with `just test-container`.
"""

from collections.abc import Iterator
import http.client
import json
import os
import pathlib
import secrets
import shutil
import socket
import subprocess  # noqa: S404  # nosec B404 - drives the docker CLI
import time
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
IMAGE = "reeltime:test"
HOST = "reeltime.example.com"
DOCKER = shutil.which("docker") or "/usr/local/bin/docker"


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(  # noqa: S603  # nosec B603
                [DOCKER, "info"], capture_output=True, check=False, timeout=20
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="needs a running Docker engine"
)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603  # nosec B603
        [DOCKER, *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        timeout=600,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)}:\n{result.stderr}")
    return result


def _env() -> dict[str, str]:
    return {
        "REELTIME_SECRET_KEY": secrets.token_urlsafe(50),
        "REELTIME_ALLOWED_HOSTS": HOST,
        "REELTIME_BASE_URL": f"https://{HOST}",
        "REELTIME_TRUST_PROXY": "true",
        "OIDC_ISSUER": "https://id.example.com",
        "OIDC_CLIENT_ID": "reeltime",
        "OIDC_CLIENT_SECRET": secrets.token_urlsafe(32),
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(
    port: int, path: str, *, https: bool = True
) -> tuple[int, dict[str, str], str]:
    headers = {"Host": HOST}
    if https:
        headers["X-Forwarded-Proto"] = "https"
    request = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 URL
        f"http://127.0.0.1:{port}{path}", headers=headers
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=5) as response:
            return (
                response.status,
                dict(response.headers),
                response.read().decode(),
            )
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), ""


def _wait_healthy(container: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = json.loads(
            _run("inspect", "--format", "{{json .State}}", container).stdout
        )
        if state.get("Health", {}).get("Status") == "healthy":
            return
        if not state["Running"]:
            logs = _run("logs", container, check=False)
            raise AssertionError(
                f"container exited:\n{logs.stdout}{logs.stderr}"
            )
        time.sleep(1)
    raise AssertionError(f"{container} not healthy after {timeout}s")


@pytest.fixture(scope="module", name="image")
def fixture_image() -> str:
    _run("build", "-t", IMAGE, ".")
    return IMAGE


def test_runs_as_a_fixed_non_root_user(image: str) -> None:
    result = _run("run", "--rm", "--entrypoint", "id", image, "-u")
    assert result.stdout.strip() == "10001"


def test_app_code_is_read_only_to_the_app(image: str) -> None:
    result = _run(
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        image,
        "-c",
        "touch /app/x",
        check=False,
    )
    assert result.returncode != 0


def test_image_has_no_build_tools(image: str) -> None:
    result = _run(
        "run",
        "--rm",
        "--entrypoint",
        "sh",
        image,
        "-c",
        "command -v gcc cc make uv pip3 git",
        check=False,
    )
    assert result.stdout.strip() == ""


def test_pdfs_render_with_the_app_fonts(image: str) -> None:
    script = (
        "from cues.exporters import pdf;"
        "import sys;"
        "sys.stdout.write(pdf.render('cues/pdf/header.html',"
        " {'what': 'Wall Chart'})[:5].decode())"
    )
    result = _run(
        "run",
        "--rm",
        "-e",
        "DJANGO_SETTINGS_MODULE=reeltime.settings.build",
        "--entrypoint",
        "python",
        image,
        "-c",
        f"import django; django.setup(); {script}",
    )
    assert result.stdout == "%PDF-"


@pytest.fixture(name="hardened")
def fixture_hardened(image: str) -> Iterator[int]:
    """The web container with Compose's hardening; yields its port."""
    name = f"reeltime-test-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    env_args = [arg for k, v in _env().items() for arg in ("-e", f"{k}={v}")]
    _run(
        "run",
        "-d",
        "--name",
        name,
        "--read-only",
        "--tmpfs",
        "/tmp:size=64m",  # noqa: S108 - a tmpfs mount inside the container
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-p",
        f"127.0.0.1:{port}:8000",
        *env_args,
        image,
    )
    try:
        _wait_healthy(name)
        yield port
    finally:
        _run("rm", "-f", "-v", name, check=False)


def test_hardened_container_serves_sign_in(hardened: int) -> None:
    status, headers, body = _get(hardened, "/auth/login/")
    assert status == 200
    assert "Sign In with PocketID" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert "/static/cues/app." in body  # hashed static file from WhiteNoise


def test_uploads_spool_to_the_data_volume(image: str) -> None:
    script = "import tempfile; print(tempfile.gettempdir())"
    env_args = [arg for k, v in _env().items() for arg in ("-e", f"{k}={v}")]
    result = _run(
        "run",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp:size=64m",  # noqa: S108 - a tmpfs mount inside the container
        *env_args,
        image,
        "shell",
        "-c",
        f"{script}; import os; print(oct(os.stat('/data/tmp').st_mode))",
    )
    assert result.stdout.split()[-2:] == ["/data/tmp", "0o40700"]


def test_oversized_bodies_are_refused_unread(hardened: int) -> None:
    # Only the headers are sent: the answer comes before any body.
    connection = http.client.HTTPConnection("127.0.0.1", hardened, timeout=10)
    try:
        connection.putrequest("POST", "/projects/new/", skip_host=True)
        connection.putheader("Host", HOST)
        connection.putheader("X-Forwarded-Proto", "https")
        connection.putheader("Content-Length", str(13 * 1024 * 1024))
        connection.endheaders()
        assert connection.getresponse().status == 413
    finally:
        connection.close()


def test_hardened_container_redirects_plain_http(hardened: int) -> None:
    status, headers, _ = _get(hardened, "/auth/login/", https=False)
    assert status == 301
    assert headers["Location"] == f"https://{HOST}/auth/login/"


def test_health_probes_answer_over_plain_http(hardened: int) -> None:
    assert _get(hardened, "/healthz", https=False)[:3:2] == (200, "ok")
    assert _get(hardened, "/readyz", https=False)[:3:2] == (200, "ok")


def test_static_files_are_served_with_long_caching(hardened: int) -> None:
    _, _, body = _get(hardened, "/auth/login/")
    css = body.split('href="', 2)[1].split('"', 1)[0]
    status, headers, _ = _get(hardened, css)
    assert status == 200
    assert "max-age=315360000" in headers["Cache-Control"]


def test_compose_up_from_a_clean_checkout(
    image: str, tmp_path: pathlib.Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{k}={v}\n" for k, v in _env().items()), encoding="utf-8"
    )
    port = _free_port()
    project = f"reeltime-test-{uuid.uuid4().hex[:8]}"
    compose = [
        "compose",
        "-f",
        "deploy/docker-compose.yml",
        "-p",
        project,
    ]
    overrides = {
        "REELTIME_ENV_FILE": str(env_file),
        "REELTIME_PORT": str(port),
        "REELTIME_IMAGE": image,
    }
    env = {**os.environ, **overrides}

    def compose_run(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [DOCKER, *compose, *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
            env=env,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr
        return result

    def wait_for_web() -> None:
        # Only web has a healthcheck: some Compose versions refuse --wait
        # for a service (the scheduler) that has none.
        compose_run("up", "-d", "--wait", "--wait-timeout", "120", "web")

    try:
        wait_for_web()
        compose_run("up", "-d", "scheduler")
        status, _, body = _get(port, "/auth/login/")
        assert status == 200
        assert "Sign In with PocketID" in body

        # A restart with nothing to migrate takes no snapshot ...
        compose_run("restart", "web")
        wait_for_web()
        data = compose_run("exec", "web", "ls", "/data").stdout.split()
        assert "snapshots" not in data
        # ... but one with migrations pending (unapplying one stands in
        # for an upgrade that adds it) snapshots the database first.
        compose_run("exec", "web", "reeltime", "migrate", "sessions", "zero")
        compose_run("restart", "web")
        wait_for_web()
        listing = compose_run("exec", "web", "ls", "/data/snapshots").stdout
        assert listing.startswith("before-migrate-")

        # The scheduler stops cleanly on SIGTERM.
        compose_run("stop", "scheduler")
        container = compose_run("ps", "-a", "-q", "scheduler").stdout.strip()
        exit_code = _run(
            "inspect", "--format", "{{.State.ExitCode}}", container
        ).stdout.strip()
        assert exit_code == "0"
    finally:
        compose_run("down", "-v", "--remove-orphans")
