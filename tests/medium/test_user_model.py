"""Tests for accounts.models.User."""

from accounts import models


def test_str_prefers_full_name() -> None:
    user = models.User(username="u1", first_name="Ada", last_name="Lee")
    assert str(user) == "Ada Lee"


def test_str_falls_back_to_username() -> None:
    assert str(models.User(username="u1")) == "u1"


def test_new_users_have_no_access() -> None:
    assert models.User(username="u1").role == models.Role.NONE
