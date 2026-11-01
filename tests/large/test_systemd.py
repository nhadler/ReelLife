"""The bare-metal systemd deployment, on a real systemd (large).

Boots Debian with systemd as PID 1 in a privileged container, installs
ReelLife with deploy/systemd/install.sh as the README describes, and
checks the units. Needs Docker; skipped without it.

systemd can't mount LoadCredential directories inside a container, so a
test-only drop-in points the services at readable copies of the secrets.
Everything else (dynamic user, sandboxing, state directory, snapshot and
migrate on start, the timer) runs as shipped.
"""

from collections.abc import Iterator
import io
import subprocess  # noqa: S404  # nosec B404 - drives the docker CLI
import tarfile
import time
import uuid

import pytest

from tests.large import test_container

ROOT = test_container.ROOT
DOCKER = test_container.DOCKER
HOST_IMAGE = "reellife-systemd-host:test"

pytestmark = test_container.pytestmark

_DROP_IN = """[Service]
LoadCredential=
Environment=REELLIFE_SECRET_KEY_FILE=/etc/reellife-test/secret_key
Environment=OIDC_CLIENT_SECRET_FILE=/etc/reellife-test/oidc_client_secret
"""


_ENV = """REELLIFE_ALLOWED_HOSTS=reellife.example.com
REELLIFE_BASE_URL=https://reellife.example.com
REELLIFE_TRUST_PROXY=true
OIDC_ISSUER=https://id.example.com
OIDC_CLIENT_ID=reellife
"""


def _write(container: str, path: str, content: str, mode: int) -> None:
    subprocess.run(  # noqa: S603  # nosec B603
        [
            DOCKER,
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            f'mkdir -p "$(dirname {path})" && cat > {path} '
            f"&& chmod {mode:o} {path}",
        ],
        input=content.encode(),
        check=True,
        capture_output=True,
    )


def _checkout_tar() -> bytes:
    """The working tree (tracked and new files) as a tar stream."""
    files = subprocess.run(  # noqa: S603  # nosec B603
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],  # noqa: S607
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout.split(b"\0")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name in filter(None, files):
            path = ROOT / name.decode()
            if path.is_file():
                archive.add(path, arcname=name.decode())
    return buffer.getvalue()


def _sh(container: str, script: str, *, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603  # nosec B603
        [DOCKER, "exec", container, "sh", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"{script}\n{result.stdout}\n{result.stderr}")
    return result.stdout


@pytest.fixture(scope="module", name="host")
def fixture_host() -> Iterator[str]:
    """A systemd host with ReelLife installed and started."""
    test_container._run(  # noqa: SLF001 pylint: disable=protected-access
        "build", "-t", HOST_IMAGE, "tests/large/systemd"
    )
    name = f"reellife-host-{uuid.uuid4().hex[:8]}"
    test_container._run(  # noqa: SLF001 pylint: disable=protected-access
        "run",
        "-d",
        "--name",
        name,
        "--privileged",
        "--cgroupns=private",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/run/lock",
        HOST_IMAGE,
    )
    try:
        _sh(name, "mkdir -p /opt/reellife")
        subprocess.run(  # noqa: S603  # nosec B603
            [DOCKER, "cp", "-", f"{name}:/opt/reellife"],
            input=_checkout_tar(),
            check=True,
            capture_output=True,
        )
        _sh(name, "sh /opt/reellife/deploy/systemd/install.sh")
        # What an admin does next (deploy/README.md), plus the drop-in.
        _write(name, "/etc/reellife/reellife.env", _ENV, 0o600)
        _write(name, "/etc/reellife/oidc_client_secret", "test-secret", 0o600)
        _sh(
            name,
            "install -d -m 0755 /etc/reellife-test && "
            "install -m 0644 /etc/reellife/secret_key "
            "/etc/reellife/oidc_client_secret /etc/reellife-test/",
        )
        for unit in ("reellife", "reellife-snapshot"):
            _write(
                name,
                f"/etc/systemd/system/{unit}.service.d/test.conf",
                _DROP_IN,
                0o644,
            )
        _sh(
            name,
            "systemctl daemon-reload && systemctl enable --now "
            "reellife.service reellife-snapshot.timer",
        )
        deadline = time.monotonic() + 120
        while (
            _sh(
                name, "systemctl is-active reellife.service", check=False
            ).strip()
            != "active"
        ):
            assert time.monotonic() < deadline, _sh(
                name, "journalctl -u reellife.service --no-pager", check=False
            )
            time.sleep(1)
        yield name
    finally:
        test_container._run("rm", "-f", "-v", name, check=False)  # noqa: SLF001 pylint: disable=protected-access


def _curl(host: str, path: str) -> str:
    return _sh(
        host,
        "for i in $(seq 30); do "
        "curl -sf -H 'Host: reellife.example.com' "
        "-H 'X-Forwarded-Proto: https' "
        f"http://127.0.0.1:8000{path} && exit 0; sleep 1; done; exit 1",
    )


def test_service_serves_sign_in(host: str) -> None:
    assert "Sign In with PocketID" in _curl(host, "/auth/login/")
    assert _curl(host, "/healthz") == "ok"


def test_service_runs_as_a_dynamic_user(host: str) -> None:
    owner = _sh(host, "stat -c %U /var/lib/private/reellife").strip()
    assert owner not in ("root", "0")
    mode = _sh(host, "stat -c %a /var/lib/private/reellife/reellife.sqlite3")
    assert mode.strip() == "600"


def test_service_is_sandboxed(host: str) -> None:
    report = _sh(host, "systemd-analyze security reellife.service --no-pager")
    score = float(report.rsplit("reellife.service: ", 1)[1].split()[0])
    assert score <= 2.0, report


def test_uploads_spool_to_the_cache_directory(host: str) -> None:
    env = _sh(host, "systemctl show reellife.service -p Environment")
    assert "TMPDIR=/var/cache/reellife" in env
    mode = _sh(host, "stat -c %a /var/cache/private/reellife")
    assert mode.strip() == "700"


def test_units_verify(host: str) -> None:
    out = _sh(
        host,
        "systemd-analyze verify /etc/systemd/system/reellife*.service "
        "/etc/systemd/system/reellife-snapshot.timer 2>&1",
    )
    assert out.strip() == ""


def test_nightly_service_writes_a_snapshot(host: str) -> None:
    _sh(host, "systemctl start reellife-snapshot.service")
    listing = _sh(host, "ls /var/lib/private/reellife/snapshots")
    assert listing.startswith("reellife-")


def test_timer_is_scheduled(host: str) -> None:
    timers = _sh(
        host, "systemctl list-timers reellife-snapshot.timer --no-pager"
    )
    assert "reellife-snapshot.service" in timers


def _manage(host: str, command: str) -> str:
    """Runs a management command through deploy/systemd/reellife-manage."""
    # Same test-only swap as the drop-in: no credential mounts here.
    _sh(
        host,
        "sed -e '/LoadCredential=/d' "
        "-e 's#$CREDENTIALS/#/etc/reellife-test/#' "
        "/opt/reellife/deploy/systemd/reellife-manage "
        "> /usr/local/bin/reellife-manage-test && "
        "chmod +x /usr/local/bin/reellife-manage-test",
    )
    return _sh(host, f"reellife-manage-test {command}")


def test_snapshot_taken_before_migrating_on_restart(host: str) -> None:
    snapshots = "/var/lib/private/reellife/snapshots"
    _sh(host, f"rm -rf {snapshots}")
    _sh(host, "systemctl restart reellife.service")
    # Nothing to migrate: no snapshot.
    assert "No such file" in _sh(host, f"ls {snapshots} 2>&1", check=False)
    # Unapplying a migration stands in for an upgrade that adds one.
    _manage(host, "migrate sessions zero")
    _sh(host, "systemctl restart reellife.service")
    listing = _sh(host, f"ls {snapshots}")
    assert listing.startswith("before-migrate-")


def test_manage_wrapper_runs_commands_as_the_service_user(host: str) -> None:
    out = _manage(host, "create_breakglass")
    assert "username: breakglass" in out
    owners = _sh(
        host, "stat -c %U /var/lib/private/reellife/reellife.sqlite3*"
    ).split()
    assert "root" not in owners


def test_reinstall_keeps_configuration(host: str) -> None:
    before = _sh(
        host, "sha256sum /etc/reellife/secret_key /etc/reellife/reellife.env"
    )
    _sh(host, "sh /opt/reellife/deploy/systemd/install.sh")
    after = _sh(
        host, "sha256sum /etc/reellife/secret_key /etc/reellife/reellife.env"
    )
    assert before == after
