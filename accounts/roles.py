"""Maps identity-provider groups to ReelLife roles."""

from collections.abc import Iterable

from accounts.models import Role

_RANK = {Role.NONE: 0, Role.VIEWER: 1, Role.EDITOR: 2, Role.ADMIN: 3}


def role_from_groups(
    groups: object,
    *,
    admin_group: str,
    editor_group: str,
    viewer_group: str,
) -> Role:
    """Returns the highest role the groups grant.

    Args:
        groups: The `groups` claim as received: normally a list of names,
            sometimes a single string. Anything else grants nothing.
        admin_group: Group name that grants admin.
        editor_group: Group name that grants editor.
        viewer_group: Group name that grants viewer.

    Returns:
        ADMIN, EDITOR or VIEWER by membership, else NONE.
    """
    if isinstance(groups, str):
        names = {groups}
    elif isinstance(groups, Iterable) and not isinstance(groups, dict):
        names = {name for name in groups if isinstance(name, str)}
    else:
        names = set()
    if admin_group in names:
        return Role.ADMIN
    if editor_group in names:
        return Role.EDITOR
    if viewer_group in names:
        return Role.VIEWER
    return Role.NONE


def at_least(role: str, minimum: Role) -> bool:
    """Returns whether `role` grants everything `minimum` does."""
    return _RANK.get(Role(role), 0) >= _RANK[minimum]
