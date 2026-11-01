/**
 * @fileoverview The days-off calendar (cues/includes/days_off.html).
 *
 * Two months of days to click: a click adds the day as a day off or
 * removes it, Shift-click does the same for every day back to the last
 * one clicked, and arrow keys move between days. The formset rows under
 * it stay the record: adding a day fills a hidden blank row (or a new
 * one from the template), removing one ticks a saved row's Remove box or
 * clears a new row. Each row's × does the same (again, on a saved row,
 * keeps it). Editing the rows redraws the calendar.
 */

import {iso, keepTabStop, month, monthOf, parse, step} from './calendar.js';
import {enhance} from './date-picker.js';

const MONTHS_SHOWN = 2;

/**
 * Sets up one days-off picker.
 * @param {!Element} root The [data-days-off] element.
 */
function setUp(root) {
  const prefix = root.dataset.prefix;
  const total = root.querySelector(`[name="${prefix}-TOTAL_FORMS"]`);
  const rows = root.querySelector('[data-days-rows]');
  const template = root.querySelector('[data-day-template]');
  const calendar = root.querySelector('[data-calendar]');
  const months = root.querySelector('[data-calendar-months]');
  const now = new Date();
  let first = new Date(now.getFullYear(), now.getMonth(), 1);
  /** @type {?string} */
  let anchor = null;
  /** @type {?string} */
  let focusDay = null;

  const dateOf = (row) => row.querySelector('input[name$="-date"]');
  const noteOf = (row) => row.querySelector('input[name$="-note"]');
  const removeOf = (row) => row.querySelector('input[name$="-DELETE"]');
  const allRows = () => [...rows.querySelectorAll('[data-day-row]')];

  /** @return {!Map<string, !Element>} Day off → its row. */
  function daysOff() {
    const found = new Map();
    for (const row of allRows()) {
      const value = dateOf(row).value;
      if (value && !removeOf(row)?.checked) {
        found.set(value, row);
      }
    }
    return found;
  }

  /** Hides blank new rows; marks saved rows set to be removed. */
  function tidy() {
    // With the calendar running, × buttons stand in for the checkboxes.
    for (const button of rows.querySelectorAll('[data-day-remove]')) {
      button.hidden = false;
      const row = button.closest('[data-day-row]');
      const removed = Boolean(removeOf(row)?.checked);
      button.textContent = removed ? '↺' : '×';
      button.setAttribute(
        'aria-label',
        removed ? 'Keep this day off' : 'Remove this day',
      );
    }
    for (const box of rows.querySelectorAll('[data-day-checkbox]')) {
      box.hidden = true;
    }
    rows.closest('table').hidden = !allRows().some(
      (row) => row.hasAttribute('data-saved') || dateOf(row).value,
    );
    for (const row of allRows()) {
      row.hidden = !row.hasAttribute('data-saved') && !dateOf(row).value;
      row.classList.toggle(
        'days-off__row--removed',
        Boolean(removeOf(row)?.checked),
      );
    }
  }

  /**
   * @param {string} day YYYY-MM-DD.
   */
  function add(day) {
    if (daysOff().has(day)) {
      return;
    }
    const saved = allRows().find(
      (row) => dateOf(row).value === day && removeOf(row)?.checked,
    );
    if (saved) {
      removeOf(saved).checked = false;
      return;
    }
    let row = allRows().find(
      (candidate) =>
        !candidate.hasAttribute('data-saved') && !dateOf(candidate).value,
    );
    if (!row) {
      const index = Number(total.value);
      const html = template.innerHTML.replaceAll('__prefix__', String(index));
      rows.insertAdjacentHTML('beforeend', html);
      enhance(rows);
      total.value = String(index + 1);
      row = allRows().at(-1);
    }
    dateOf(row).value = day;
  }

  /**
   * @param {string} day YYYY-MM-DD.
   */
  function remove(day) {
    const row = daysOff().get(day);
    if (!row) {
      return;
    }
    const box = removeOf(row);
    if (box) {
      box.checked = true;
    } else {
      dateOf(row).value = '';
      noteOf(row).value = '';
    }
  }

  /**
   * Turns `day` (and, with Shift, the days back to the last click) on
   * or off, following the clicked day.
   * @param {string} day YYYY-MM-DD.
   * @param {boolean} range Whether Shift was held.
   */
  function toggle(day, range) {
    const on = !daysOff().has(day);
    let days = [day];
    if (range && anchor) {
      const [start, end] = [anchor, day].sort();
      days = [];
      for (
        let at = parse(start);
        iso(at) <= end;
        at.setDate(at.getDate() + 1)
      ) {
        days.push(iso(at));
      }
    }
    for (const each of days) {
      (on ? add : remove)(each);
    }
    anchor = day;
    tidy();
    render();
  }

  /** Draws the shown months. */
  function render() {
    const off = new Set(daysOff().keys());
    const shown = [];
    for (let index = 0; index < MONTHS_SHOWN; index++) {
      shown.push(
        month(monthOf(first, index), {isOn: (day) => off.has(day), focusDay}),
      );
    }
    months.replaceChildren(...shown);
    keepTabStop(months);
  }

  /**
   * Moves focus to another day, changing month if it isn't shown.
   * @param {string} day YYYY-MM-DD.
   */
  function focus(day) {
    focusDay = day;
    let button = months.querySelector(`[data-day="${day}"]`);
    if (!button) {
      // Off the shown months: the target's month comes in on the side
      // the focus moved off.
      const at = parse(day);
      const back = at < first;
      first = new Date(
        at.getFullYear(),
        at.getMonth() - (back ? 0 : MONTHS_SHOWN - 1),
        1,
      );
      render();
      button = months.querySelector(`[data-day="${day}"]`);
    } else {
      for (const other of months.querySelectorAll('.calendar__day')) {
        other.tabIndex = other === button ? 0 : -1;
      }
    }
    button.focus();
  }

  months.addEventListener('click', (event) => {
    const button = event.target.closest('.calendar__day');
    if (button) {
      focusDay = button.dataset.day;
      toggle(button.dataset.day, event.shiftKey);
      months.querySelector(`[data-day="${focusDay}"]`)?.focus();
    }
  });
  months.addEventListener('keydown', (event) => {
    const button = event.target.closest('.calendar__day');
    const next = button && step(button.dataset.day, event.key);
    if (!next) {
      return;
    }
    event.preventDefault();
    focus(next);
  });
  for (const button of calendar.querySelectorAll('[data-calendar-step]')) {
    button.addEventListener('click', () => {
      first = monthOf(first, Number(button.dataset.calendarStep));
      render();
    });
  }
  // Today: back to this month, as the date picker's Today does.
  calendar
    .querySelector('[data-calendar-today]')
    ?.addEventListener('click', () => {
      first = monthOf(new Date());
      focusDay = iso(new Date());
      render();
    });
  // Typing a date or ticking Remove in the list redraws the calendar.
  rows.addEventListener('change', () => {
    tidy();
    render();
  });
  rows.addEventListener('click', (event) => {
    const button = event.target.closest('[data-day-remove]');
    if (!button) {
      return;
    }
    const row = button.closest('[data-day-row]');
    const box = removeOf(row);
    if (box) {
      box.checked = !box.checked;
    } else {
      dateOf(row).value = '';
      noteOf(row).value = '';
    }
    tidy();
    render();
  });

  calendar.hidden = false;
  tidy();
  render();
}

for (const root of document.querySelectorAll('[data-days-off]')) {
  setUp(root);
}
