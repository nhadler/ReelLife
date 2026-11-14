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
3. Paste the link into **File Link**, at the top of **Review** on the
   cue's page. The line under it says whether the link will play.

The page plays the file you uploaded, not the lighter copy Dropbox's
own site streams, so keep bounces small: 1080p at about 10 Mbit/s
plays smoothly, a 4K master doesn't.

Links to a Dropbox folder, and links to anywhere else, aren't played.

**Audio Link**, under File Link, is optional: a Dropbox link to the
cue's music alone, such as a WAV of the mix. It gets a small player of
its own under the video, for listening without picture. Playing one
pauses the other. Comments are about the video.

## Making a Link

Under File Link, **Make a Review Link**. A cue has one link. It always
shows the current version first and offers the cue's other versions
that have video, so the same link keeps working when you deliver a new
version. **Copy Link** copies it; **Open** shows the page.

The link's settings are under it and save as you change them:

- **Password**: asked once in each browser before the page shows. Send
  it to your reviewer separately from the link. **Remove Password**
  takes it off.
- **Last Day**: the link stops working after that day.
- **Pre-Roll**: how much video plays before the cue's IN, such as a
  count-in. Comments' timecodes allow for it.
- **Allow Comments**: untick to let reviewers watch and read only.
- **Revoke Link** ends the link for everyone who has it. Comments stay
  on the cue, and a new link has a new address.

A password, a last day and revoking protect the review page. The video
itself is on Dropbox: anyone who opened the page while it worked could
have kept the file's Dropbox address. To cut that off as well, stop
sharing the file in Dropbox.

A reviewer's browser has to reach ReelTime itself, so a link only works
from where your ReelTime can be opened.

Editors and admins make and change links. Deleting a cue ends its link;
restoring the cue doesn't bring the link back.

## What a Reviewer Sees

First the password, if the link has one, then **Who's Watching?**: a
guest gives their name once, before anything of the cue shows. The name
goes with their comments and with when they watched.

Then the video with its timecode, and the comments beside it. Space plays
and pauses, the arrow keys step one frame, J and L jump five seconds, F
is full screen and M mutes.

Typing a comment pauses the video. The comment is about the frame on
screen, shown as **At** with its timecode; untick **At** for a comment
on the whole cue. Enter sends it. Reviewers see each other's comments, and **Reply**
on a comment answers it. A guest can type any name; comments from
someone signed in to ReelTime are marked **Team**, and only those are
sure to be from who they say.

Each timed comment is a mark on the video's timeline. Clicking a
comment's timecode, or its mark, goes to that frame.

## Working Through the Comments

Open the link yourself while signed in to ReelTime: you skip the
password and comment under your own name (viewers too), and editors get
two more buttons on each comment.

- **Resolve** marks a comment dealt with. It stays in the list, faded,
  and its mark on the timeline turns grey. **Reopen** undoes that.
- **Delete** removes a comment with its replies.

The cue's page shows how many comments the chosen version has and how
many are open. In the grid, the **Open Notes** column counts the
comments on each cue's current version that aren't resolved yet; add it
with **View**. Comments belong to the version they were made on, so
a new version starts with none.

Every comment, reply, resolve and deletion is in the project's
**Activity**, where **Undo** reverses it.

## Who Watched

Under the link, **Views** lists each visit to the chosen version's
review page, newest first: who, when, how long the video or audio
actually played (**Watched**), the furthest timecode they got to
(**Reached**) and how long the page was open. A reload within half an
hour counts as the same visit. A version keeps its newest 200 visits.

Guests are known only by the name they gave; no address or other detail
about them is kept. Editors and admins opening the link aren't counted;
a viewer is, marked **Team**.
