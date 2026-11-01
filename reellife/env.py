"""Reads configuration from environment variables.

Any variable can instead be read from a file by setting NAME_FILE to its
path, for Docker secrets and systemd credentials.
"""

from collections.abc import Mapping
import os
import pathlib
import zoneinfo

from django.core.exceptions import ImproperlyConfigured

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def get(
    name: str,
    default: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Returns a setting from NAME or the file named by NAME_FILE.

    Args:
        name: The variable name.
        default: Returned when neither variable is set. None makes the
            setting required.
        environ: The environment to read; defaults to os.environ.

    Returns:
        The value, with surrounding whitespace stripped when read from a
        file.

    Raises:
        ImproperlyConfigured: If both NAME and NAME_FILE are set, or the
            setting is required and missing.
    """
    env = os.environ if environ is None else environ
    file_name = f"{name}_FILE"
    if name in env and file_name in env:
        raise ImproperlyConfigured(f"set {name} or {file_name}, not both")
    if file_name in env:
        return pathlib.Path(env[file_name]).read_text(encoding="utf-8").strip()
    if name in env:
        return env[name]
    if default is None:
        raise ImproperlyConfigured(f"{name} is required")
    return default


def get_bool(
    name: str,
    default: bool,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Returns a boolean setting ('true'/'false', '1'/'0', 'yes'/'no').

    Raises:
        ImproperlyConfigured: If the value is not a recognised boolean.
    """
    value = get(name, str(default), environ=environ).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name} must be true or false, not {value!r}")


def get_list(
    name: str,
    default: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Returns a comma-separated setting as a list, dropping blanks."""
    value = get(name, default, environ=environ)
    return [item.strip() for item in value.split(",") if item.strip()]


def get_time_zone(
    name: str,
    default: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Returns an IANA time zone name, e.g. 'America/Los_Angeles'.

    Raises:
        ImproperlyConfigured: If the value isn't a known time zone.
    """
    value = get(name, default, environ=environ).strip()
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ImproperlyConfigured(
            f"{name} must be an IANA time zone such as "
            f"America/Los_Angeles, not {value!r}"
        ) from None
    return value
