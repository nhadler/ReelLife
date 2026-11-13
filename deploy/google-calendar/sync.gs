/**
 * @fileoverview Keeps a Google calendar in step with ReelTime's schedule.
 *
 * Google Calendar reads a subscribed iCal feed only every few hours.
 * This Google Apps Script, run in your own Google account every few
 * minutes, reads ReelTime's Calendar Feed and adds, changes and removes
 * events in a calendar of its own to match. See README.md beside it.
 *
 * It only touches events it made (each carries the feed event's UID),
 * and only within the days the feed covers.
 */

// The calendar the events go in; made on the first run if missing.
const CALENDAR_NAME = 'ReelTime';
// The days ReelTime's feed covers (cues/services/feed.py).
const PAST_DAYS = 30;
const AHEAD_DAYS = 90;
const UID_TAG = 'reeltimeUid';

/** Reads the feed and brings the calendar in line with it. */
function sync() {
  const url = PropertiesService.getScriptProperties().getProperty('FEED_URL');
  if (!url) {
    throw new Error('Set the FEED_URL script property (see README.md).');
  }
  const reply = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
  if (reply.getResponseCode() !== 200) {
    throw new Error(`The feed answered ${reply.getResponseCode()}.`);
  }
  const wanted = new Map();
  for (const event of parseFeed(reply.getContentText())) {
    wanted.set(event.uid, event);
  }

  const calendar = targetCalendar();
  // Strictly inside the feed's days (its edges may fall on another day
  // in ReelTime's time zone), so older events stay as history.
  const today = startOfDay(new Date());
  const from = addDays(today, -PAST_DAYS + 1);
  const to = addDays(today, AHEAD_DAYS);
  const seen = new Set();
  for (const existing of calendar.getEvents(from, to)) {
    const uid = existing.getTag(UID_TAG);
    if (!uid || startOfDay(existing.getAllDayStartDate()) < from) {
      continue; // someone's own event, or one from before the window
    }
    const event = wanted.get(uid);
    if (!event || seen.has(uid)) {
      existing.deleteEvent();
      continue;
    }
    seen.add(uid);
    if (existing.getTitle() !== event.title) {
      existing.setTitle(event.title);
    }
    if (existing.getDescription() !== event.description) {
      existing.setDescription(event.description);
    }
    if (
      startOfDay(existing.getAllDayStartDate()).getTime() !==
      event.date.getTime()
    ) {
      existing.setAllDayDate(event.date);
    }
  }
  for (const [uid, event] of wanted) {
    if (seen.has(uid) || event.date < from || event.date >= to) {
      continue;
    }
    const made = calendar.createAllDayEvent(event.title, event.date, {
      description: event.description,
    });
    made.setTag(UID_TAG, uid);
    made.setTransparency(CalendarApp.EventTransparency.TRANSPARENT);
  }
}

/** Runs sync() every 10 minutes from now on, and once straight away. */
function install() {
  for (const trigger of ScriptApp.getProjectTriggers()) {
    if (trigger.getHandlerFunction() === 'sync') {
      ScriptApp.deleteTrigger(trigger);
    }
  }
  ScriptApp.newTrigger('sync').timeBased().everyMinutes(10).create();
  sync();
}

/** The calendar to keep in step, made if it doesn't exist yet. */
function targetCalendar() {
  const found = CalendarApp.getCalendarsByName(CALENDAR_NAME);
  return found.length ? found[0] : CalendarApp.createCalendar(CALENDAR_NAME);
}

/**
 * The feed's events: uid, title, description and day.
 * @param {string} text An iCalendar file.
 * @return {!Array<{uid: string, title: string, description: string, date: !Date}>}
 */
function parseFeed(text) {
  // Lines starting with a space or tab continue the line before.
  const lines = text
    .replace(/\r\n/g, '\n')
    .replace(/\n[ \t]/g, '')
    .split('\n');
  const events = [];
  let current = null;
  for (const line of lines) {
    if (line === 'BEGIN:VEVENT') {
      current = {};
    } else if (line === 'END:VEVENT') {
      if (current && current.uid && current.date) {
        events.push({
          uid: current.uid,
          title: current.title || '',
          description: current.description || '',
          date: current.date,
        });
      }
      current = null;
    } else if (current) {
      const colon = line.indexOf(':');
      if (colon < 0) {
        continue;
      }
      const name = line.slice(0, colon).split(';')[0].toUpperCase();
      const value = line.slice(colon + 1);
      if (name === 'UID') {
        current.uid = value;
      } else if (name === 'SUMMARY') {
        current.title = unescapeText(value);
      } else if (name === 'DESCRIPTION') {
        current.description = unescapeText(value);
      } else if (name === 'DTSTART') {
        const match = /^(\d{4})(\d{2})(\d{2})/.exec(value);
        if (match) {
          current.date = new Date(+match[1], +match[2] - 1, +match[3]);
        }
      }
    }
  }
  return events;
}

/**
 * An iCalendar text value as plain text.
 * @param {string} value
 * @return {string}
 */
function unescapeText(value) {
  return value.replace(/\\([\\;,nN])/g, (_, c) =>
    c === 'n' || c === 'N' ? '\n' : c,
  );
}

/**
 * Midnight at the start of a day, in the script's time zone.
 * @param {!Date} date
 * @return {!Date}
 */
function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/**
 * A day some days away.
 * @param {!Date} date
 * @param {number} days
 * @return {!Date}
 */
function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}
