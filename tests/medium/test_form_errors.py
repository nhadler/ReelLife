"""A form sent back with errors sums them up (includes/form_errors.html)."""

from collections.abc import Callable

from django.test import Client
from django.urls import reverse
import pytest

from accounts.models import Role
from tests.medium.test_project_pages import new_project_data


@pytest.fixture(name="editor")
def fixture_editor(role_client: Callable[[Role], Client]) -> Client:
    return role_client(Role.EDITOR)


def test_errors_are_summed_up_above_the_form(editor: Client) -> None:
    one = editor.post(
        reverse("cues:project_create"), new_project_data(title="")
    ).content.decode()
    assert "Fix the field marked below." in one
    assert 'aria-invalid="true"' in one
    two = editor.post(
        reverse("cues:project_create"),
        new_project_data(title="", numbering="custom"),
    ).content.decode()
    assert "Fix the 2 fields marked below." in two
