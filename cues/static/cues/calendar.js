/**
 * @fileoverview A month of clickable days, shared by the date picker
 * (date-picker.js) and the days-off calendar (days-off.js).
 *
 * Days are buttons carrying data-day="YYYY-MM-DD", in weeks starting on
 * Sunday; weekends are shaded, today outlined, and "on" days pressed.
 * Only the focused day is in the tab order; arrow keys move between days
 * (see `step`).
 */

/**
 * @param {!Date} date
 * @return {string} YYYY-MM-DD in local time.
 */
export function iso(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * @param {string} text YYYY-MM-DD.
 * @return {!Date} Local midnight.
 */
export function parse(text) {
  const [year, month, day] = text.split('-').map(Number);
  return new Date(year, month - 1, day);
}

/**
 * @param {!Date} date Any day.
 * @param {number=} offset Months later (or earlier, negative).
 * @return {!Date} The first of that day's month, moved by `offset`.
 */
export function monthOf(date, offset = 0) {
  return new Date(date.getFullYear(), date.getMonth() + offset, 1);
}

/**
 * The day an arrow key moves to, or null for other keys.
 * @param {string} day YYYY-MM-DD.
 * @param {string} key The key pressed.
 * @return {?string} YYYY-MM-DD.
 */
export function step(day, key) {
  const days = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[key];
  if (!days) {
    return null;
  }
  const at = parse(day);
  at.setDate(at.getDate() + days);
  return iso(at);
}

/**
 * One month's grid.
 * @param {!Date} start The month's first day.
 * @param {{isOn: function(string): boolean, focusDay: ?string}} options
 *     Which days are pressed, and which day takes the tab stop.
 * @return {!Element} The month: a title and a grid of day buttons.
 */
export function month(start, {isOn, focusDay}) {
  const today = iso(new Date());
  const box = document.createElement('div');
  box.className = 'calendar__month';
  const title = document.createElement('div');
  title.className = 'calendar__title';
  title.textContent = start.toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  });
  const grid = document.createElement('div');
  grid.className = 'calendar__grid';
  grid.setAttribute('role', 'group');
  grid.setAttribute('aria-label', title.textContent);
  for (let weekday = 0; weekday < 7; weekday++) {
    const name = document.createElement('span');
    name.className = 'calendar__weekday';
    name.setAttribute('aria-hidden', 'true');
    // 2023-01-01 was a Sunday.
    name.textContent = new Date(2023, 0, 1 + weekday).toLocaleDateString(
      undefined,
      {weekday: 'narrow'},
    );
    grid.append(name);
  }
  for (let blank = 0; blank < start.getDay(); blank++) {
    grid.append(document.createElement('span'));
  }
  const last = new Date(start.getFullYear(), start.getMonth() + 1, 0);
  for (let date = 1; date <= last.getDate(); date++) {
    const at = new Date(start.getFullYear(), start.getMonth(), date);
    const day = iso(at);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'calendar__day';
    button.dataset.day = day;
    button.textContent = String(date);
    button.setAttribute('aria-pressed', String(isOn(day)));
    button.setAttribute(
      'aria-label',
      at.toLocaleDateString(undefined, {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
      }),
    );
    button.tabIndex = day === focusDay ? 0 : -1;
    button.classList.toggle(
      'calendar__day--weekend',
      [0, 6].includes(at.getDay()),
    );
    button.classList.toggle('calendar__day--today', day === today);
    grid.append(button);
  }
  box.append(title, grid);
  return box;
}

/**
 * Makes sure one day in `container` takes the tab stop.
 * @param {!Element} container The drawn months.
 */
export function keepTabStop(container) {
  const buttons = [...container.querySelectorAll('.calendar__day')];
  if (buttons.length && !buttons.some((button) => button.tabIndex === 0)) {
    buttons[0].tabIndex = 0;
  }
}
