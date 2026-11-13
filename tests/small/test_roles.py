"""Tests for accounts.roles."""

import pytest

from accounts import roles
from accounts.models import Role

GROUPS = {
    "admin_group": "reeltime-admins",
    "editor_group": "reeltime-editors",
    "viewer_group": "reeltime-viewers",
}


@pytest.mark.parametrize(
    ("groups", "role"),
    [
        (["reeltime-admins", "reeltime-editors"], Role.ADMIN),
        (["reeltime-editors"], Role.EDITOR),
        (["other", "reeltime-viewers"], Role.VIEWER),
        (["other"], Role.NONE),
        ([], Role.NONE),
        ("reeltime-editors", Role.EDITOR),
        (None, Role.NONE),
        ({"reeltime-admins": True}, Role.NONE),
        ([1, None, "reeltime-viewers"], Role.VIEWER),
        (["REELTIME-ADMINS"], Role.NONE),
    ],
)
def test_role_from_groups(groups: object, role: Role) -> None:
    assert roles.role_from_groups(groups, **GROUPS) == role


@pytest.mark.parametrize(
    ("role", "minimum", "allowed"),
    [
        (Role.ADMIN, Role.EDITOR, True),
        (Role.EDITOR, Role.EDITOR, True),
        (Role.VIEWER, Role.EDITOR, False),
        (Role.NONE, Role.VIEWER, False),
        (Role.NONE, Role.NONE, True),
    ],
)
def test_at_least(role: Role, minimum: Role, allowed: bool) -> None:
    assert roles.at_least(role, minimum) is allowed
