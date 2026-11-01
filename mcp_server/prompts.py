"""Prompts an MCP client offers its user, such as Claude's + menu.

Each is a starting message that tells the assistant which tools to use
and where to stop and ask. They read nothing themselves; the tools they
name check the caller's role as usual.
"""

from collections.abc import Callable
from typing import Any


def spotting_notes_to_cues(project_id: str, notes: str) -> str:
    """Turn spotting session notes into new cues."""
    return f"""\
These are my spotting notes for ReelLife project {project_id}:

{notes}

Turn them into cues. First read the project with get_project (its reels,
frame rate and timecode format), list_cues (so you don't repeat a cue
that's already there) and cue_fields (what a cue can hold). Then show me
the cues you'd add as a table: number, title, reel, IN, OUT and the
spotting notes for each. Number them the way the project already does.
Ask me before adding anything; once I agree, add each with new_cue."""


def whats_left(project_id: str) -> str:
    """What's left to write before the next deadline."""
    return f"""\
What's left before the next deadline on ReelLife project {project_id}?
Read it with get_project (progress and the next deadline) and list_cues.
Tell me how many cues and how many minutes of music are still to write,
which those are (by reel, in picture order), the cues sitting on older
picture that need conforming, and whether the pace so far gets there in
time. Don't change anything."""


def cue_sheet_check(project_id: str) -> str:
    """Get the cue sheet ready to file with the PRO."""
    return f"""\
Get the cue sheet for ReelLife project {project_id} ready for the PRO.
Run cue_sheet_export and go through its checklist with me: for each
item, say what's wrong and what to fix (missing writers or publishers,
shares that don't add up, missing durations or usage). Suggest the
fixes, but ask me before changing anything. When the checklist is clear,
export the RapidCue file."""


PROMPTS: tuple[tuple[Callable[..., Any], str], ...] = (
    (spotting_notes_to_cues, "Spotting Notes to Cues"),
    (whats_left, "What's Left Before the Deadline"),
    (cue_sheet_check, "Check the Cue Sheet"),
)
