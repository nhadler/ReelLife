"""The project Settings menu's list of settings pages (project_menu.html)."""

from django import template

from cues.views import settings

register = template.Library()


@register.simple_tag
def settings_sections() -> tuple[tuple[str, str, str], ...]:
    """(key, label, URL name) for each project settings page, in order."""
    return settings.SECTIONS
