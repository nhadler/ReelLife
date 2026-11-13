# ReelTime's schedule in Google Calendar, every 10 minutes

Google Calendar reads a subscribed iCal feed only every few hours, so a
subscribed ReelTime Calendar Feed can lag far behind the schedule.
`sync.gs` is a Google Apps Script that runs in your own Google account,
reads the feed every 10 minutes, and keeps a calendar named **ReelTime**
in step: it adds, changes and removes events to match.

It needs nothing from ReelTime but the feed link, and nothing from
Google but your account: no Cloud project or OAuth app. It only touches
the events it made, and only within the days the feed covers (30 back,
90 ahead), so older days stay as history.

## Set it up

1. In ReelTime, open **Calendar Feed** in the profile menu, make a link
   if you haven't, and copy it. Treat it like a password: anyone with it
   can read your schedule.
2. Go to [script.google.com](https://script.google.com) and click
   **New project**. Name it "ReelTime Calendar Sync".
3. Replace the contents of `Code.gs` with `sync.gs` from this folder.
4. In **Project Settings** (the gear):
   - set **Time zone** to yours, so events land on the right days;
   - under **Script Properties**, add `FEED_URL` with the link from step 1.
5. Back in the editor, choose `install` in the function list and click
   **Run**. Google asks you to allow the script to manage your calendars
   and to connect to an external service (the feed); allow both. Since
   the script is yours, Google may first say it isn't verified: choose
   **Advanced**, then **Go to ReelTime Calendar Sync**.

`install` runs the first sync and sets it to repeat every 10 minutes. A
calendar named ReelTime appears in Google Calendar with your schedule.
If you subscribed to the feed in Google Calendar before, remove that
subscription so the days don't show twice.

## Afterwards

- **New feed link** (you made a new one or revoked the old): update
  `FEED_URL` in Script Properties.
- **Sooner or later than every 10 minutes**: change `everyMinutes(10)`
  in `install` (1, 5, 10, 15 or 30) and run `install` again.
- **Stop**: in **Triggers** (the alarm clock), delete the `sync`
  trigger. Delete the ReelTime calendar too if you no longer want it.
- **Errors**: **Executions** lists each run; a failed one says why (a
  revoked link answers 404).
