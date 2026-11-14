/**
 * @fileoverview A calendar for every date field, in place of the
 * browser's own picker (which pages can't style).
 *
 * Each <input type="date"> gets a calendar button inside its right edge.
 * The button (or Alt+↓ in the field) opens one shared popover: a month
 * of days (calendar.js), ‹ › to change month, Today and Clear. Picking a
 * day fills the field and fires input and change, as typing would. The
 * popover is in the browser's top layer, and moves into the field's
 * dialog when there is one (a modal dialog makes everything outside it
 * inert); a click outside or Esc closes it. The field itself still takes typing.
 */

import {iso, keepTabStop, month, monthOf, parse, step} from './calendar.js';

const ENHANCED = 'date-field__input';

const popover = document.createElement('div');
popover.className = 'date-picker';
popover.setAttribute('popover', 'auto');
popover.setAttribute('role', 'dialog');
popover.setAttribute('aria-label', 'Choose a date');
popover.innerHTML = `
  <div class="date-picker__head">
    <button class="button button--quiet button--icon" type="button"
            data-step="-1" aria-label="Previous month">‹</button>
    <span class="date-picker__title" data-title></span>
    <button class="button button--quiet button--icon" type="button"
            data-step="1" aria-label="Next month">›</button>
  </div>
  <div data-month></div>
  <div class="date-picker__foot">
    <button class="button button--quiet button--small" type="button"
            data-today>Today</button>
    <button class="button button--quiet button--small" type="button"
            data-clear>Clear</button>
  </div>`;
const monthBox = popover.querySelector('[data-month]');
const titleBox = popover.querySelector('[data-title]');

/** @type {?HTMLInputElement} */
let field = null;
let shown = monthOf(new Date());
/** @type {?string} */
let focusDay = null;

/** Draws the shown month around the field's value. */
function render() {
  const value = field?.value || '';
  const drawn = month(shown, {isOn: (day) => day === value, focusDay});
  // The popover's own head carries the month's name.
  titleBox.textContent = drawn.querySelector('.calendar__title').textContent;
  drawn.querySelector('.calendar__title').remove();
  monthBox.replaceChildren(drawn);
  keepTabStop(monthBox);
}

/**
 * Sets the field's value as typing would.
 * @param {string} value YYYY-MM-DD, or '' to clear.
 */
function choose(value) {
  if (!field) {
    return;
  }
  field.value = value;
  field.dispatchEvent(new Event('input', {bubbles: true}));
  field.dispatchEvent(new Event('change', {bubbles: true}));
  const done = field;
  popover.hidePopover();
  done.focus();
}

/**
 * Puts the popover under `anchor`, kept inside the window.
 * @param {!Element} anchor The field.
 */
function place(anchor) {
  const box = anchor.getBoundingClientRect();
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  const left = Math.min(box.left, window.innerWidth - width - 8);
  const below = box.bottom + 4;
  const top =
    below + height > window.innerHeight ? box.top - height - 4 : below;
  popover.style.left = `${Math.max(8, left)}px`;
  popover.style.top = `${Math.max(8, top)}px`;
}

/**
 * Opens the calendar for a field.
 * @param {!HTMLInputElement} input The date field.
 */
function open(input) {
  field = input;
  (input.closest('dialog') || document.body).append(popover);
  const start = input.value ? parse(input.value) : new Date();
  shown = monthOf(start);
  focusDay = iso(start);
  render();
  popover.showPopover();
  place(input);
  monthBox.querySelector(`[data-day="${focusDay}"]`)?.focus();
}

/**
 * Moves the keyboard focus to another day, changing month if needed.
 * @param {string} day YYYY-MM-DD.
 */
function focus(day) {
  focusDay = day;
  const target = parse(day);
  if (
    target.getMonth() !== shown.getMonth() ||
    target.getFullYear() !== shown.getFullYear()
  ) {
    shown = monthOf(target);
  }
  render();
  monthBox.querySelector(`[data-day="${day}"]`)?.focus();
}

popover.addEventListener('click', (event) => {
  const day = event.target.closest('.calendar__day');
  if (day) {
    choose(day.dataset.day);
    return;
  }
  const move = event.target.closest('[data-step]');
  if (move) {
    shown = monthOf(shown, Number(move.dataset.step));
    render();
  } else if (event.target.closest('[data-today]')) {
    choose(iso(new Date()));
  } else if (event.target.closest('[data-clear]')) {
    choose('');
  }
});
popover.addEventListener('keydown', (event) => {
  const day = event.target.closest('.calendar__day');
  const next = day && step(day.dataset.day, event.key);
  if (next) {
    event.preventDefault();
    focus(next);
  }
});
popover.addEventListener('toggle', (event) => {
  if (event.newState === 'closed') {
    field = null;
  }
});

/**
 * Gives the date fields under `root` a calendar button.
 * @param {!Document|!Element} root The page, or new content.
 */
export function enhance(root) {
  for (const input of root.querySelectorAll('input[type="date"]')) {
    if (input.classList.contains(ENHANCED)) {
      continue;
    }
    input.classList.add(ENHANCED);
    // Moving the field drops its focus (a dialog may have just focused it).
    const focused = input === document.activeElement;
    const wrap = document.createElement('span');
    wrap.className = 'date-field';
    input.replaceWith(wrap);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'date-field__button';
    button.setAttribute('aria-label', 'Choose a date');
    button.innerHTML =
      '<svg class="icon" viewBox="0 0 20 20" aria-hidden="true">' +
      '<path d="M3.5 5h13v11.5h-13z M3.5 8.5h13 M7 3v4 M13 3v4"/></svg>';
    button.addEventListener('click', () => open(input));
    input.addEventListener('keydown', (event) => {
      if (event.altKey && event.key === 'ArrowDown') {
        event.preventDefault();
        open(input);
      }
    });
    wrap.append(input, button);
    if (focused) {
      input.focus();
    }
  }
}

if ('showPopover' in popover) {
  document.body.append(popover);
  enhance(document);
  document.body.addEventListener('htmx:afterSettle', (event) =>
    enhance(event.target),
  );
}
