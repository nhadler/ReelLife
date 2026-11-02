"""Prompts an MCP client offers its user, such as Claude's + menu.

Each prompt names the tools to use and the actions that need user
approval. Prompts don't read data; the tools check the caller's role.
"""

from collections.abc import Callable
from typing import Any


def spotting_notes_to_cues(project_id: str, notes: str) -> str:
    """Turn spotting session notes into new cues."""
    return f"""\
These are my spotting notes for ReelLife project {project_id}:

{notes}

Turn these notes into cues. Use get_project to read the reels, frame
rate and timecode format, list_cues to check for existing cues, and
cue_fields to read the available fields. Show the proposed cues in a
table with number, title, reel, IN, OUT and spotting notes. Follow the
project's existing numbering.
Ask me before adding anything. Once I agree, add each cue with new_cue."""


def whats_left(project_id: str) -> str:
    """What's left to write before the next deadline."""
    return f"""\
What's left before the next deadline on ReelLife project {project_id}?
Read it with get_project (progress and the next deadline) and list_cues.
Report the remaining cue count and minutes of music, then list those
cues by reel in picture order. Identify cues on older picture that need
conforming, and assess whether the current pace meets the deadline.
Don't change anything."""


def cue_sheet_check(project_id: str) -> str:
    """Get the cue sheet ready to file with the PRO."""
    return f"""\
Get the cue sheet for ReelLife project {project_id} ready for the PRO.
Run cue_sheet_export and go through its checklist with me: for each
item, say what's wrong and what to fix (missing writers or publishers,
shares that don't add up, missing durations or usage). Suggest the
fixes, but ask me before changing anything. When no checklist issues remain,
export the RapidCue file."""


PROMPTS: tuple[tuple[Callable[..., Any], str], ...] = (
    (spotting_notes_to_cues, "Spotting Notes to Cues"),
    (whats_left, "What's Left Before the Deadline"),
    (cue_sheet_check, "Check the Cue Sheet"),
)
