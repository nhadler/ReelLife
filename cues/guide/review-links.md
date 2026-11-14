# Review Links

A review link lets someone without a ReelTime account watch a cue's
bounce and comment on it: a director, a producer, a music editor. They
see that one cue and nothing else of the app.

## The Video

ReelTime doesn't hold video. The review page plays each version's
**File Link** when it's a Dropbox link to a single file:

1. Bounce the cue to picture as H.264 video with AAC audio, in an .mp4
   or a .mov: every browser plays that. With uncompressed (PCM) audio
   it plays in Chrome but not in Firefox.
2. Put it in Dropbox and copy its share link.
3. Paste the link into the version's **File Link** on the cue's page.

Links to a Dropbox folder, and links to anywhere else, aren't played.
The cue's page says so under **Review** when the chosen version has no
video. An audio file's link plays too, without picture.

## Making a Link

On the cue's page, **Review** has **Make a Review Link**. A cue has one
link. It always shows the current version first and offers the cue's
other versions that have video, so the same link keeps working when you
deliver a new version. **Copy Link** copies it; **Open** shows the page
as your reviewer sees it.

**Settings…** changes the link:

- **Password**: asked once in each browser before the page shows. Send
  it to your reviewer separately from the link.
- **Last Day**: the link stops working after that day.
- **Pre-Roll**: how much video plays before the cue's IN, such as a
  count-in. Comments' timecodes allow for it.
- **Allow Comments**: untick to let reviewers watch and read only.
- **Revoke Link** ends the link for everyone who has it. Comments stay
  on the cue, and a new link has a new address.

A reviewer's browser has to reach ReelTime itself, so a link only works
from where your ReelTime can be opened.

Editors and admins make and change links. Deleting a cue ends its link;
restoring the cue doesn't bring the link back.

## What a Reviewer Sees

The video with its timecode, and the comments beside it. Space plays
and pauses, the arrow keys step one frame, J and L jump five seconds, F
is full screen and M mutes.

Typing a comment pauses the video. The comment is about the frame on
screen, shown as **At** with its timecode; untick **At** for a comment
on the whole cue. Enter sends it. A reviewer gives their name with
their first comment. Reviewers see each other's comments.

Each timed comment is a mark on the video's timeline. Clicking a
comment's timecode, or its mark, goes to that frame.

## Reading the Comments

A cue's page lists the chosen version's comments under **Review**, the
ones on the whole cue first, then by timecode. Comments belong to the
version they were made on.

Editors can **Delete** a comment. Each comment and each deletion is in
the project's **Activity**, where **Undo** reverses it.
