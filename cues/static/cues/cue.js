/**
 * @fileoverview The cue details page.
 *
 * Fields save themselves through htmx; this adds what htmx doesn't:
 * keyboard shortcuts (Esc closes, Alt+Up/Down move between cues, Ctrl+N
 * and Ctrl+Shift+V open the dialogs, Ctrl+Shift+J copies JSON), a spoken "Saved." after each save,
 * and a reload once a dialog has added a version or cue.
 */

import {copyJson} from './copyjson.js';

const config = JSON.parse(document.getElementById('cue-config').textContent);
const dialog = document.getElementById('app-dialog');
const status = document.getElementById('cue-status');
let reloadWhenClosed = false;

/**
 * Whether the event is a letter key: by physical key (any layout), or by
 * character for synthesized events that carry no code.
 * @param {!KeyboardEvent} event
 * @param {string} letter Lowercase letter.
 * @return {boolean}
 */
function isLetter(event, letter) {
  return (
    event.code === `Key${letter.toUpperCase()}` ||
    event.key.toLowerCase() === letter
  );
}

/**
 * Clicks the element with this id, if the page has it.
 * @param {string} id
 * @param {!Event} event The key event, cancelled when something is clicked.
 */
function press(id, event) {
  const element = document.getElementById(id);
  if (element) {
    event.preventDefault();
    element.click();
  }
}

/**
 * Keyboard shortcuts.
 * @param {!KeyboardEvent} event
 */
function onKey(event) {
  if (document.querySelector('dialog[open]')) {
    return;
  }
  const inField = event.target.closest('input, textarea, select');
  if (event.key === 'Escape') {
    // An open menu has already closed itself (menus.js).
    if (inField) {
      event.target.blur(); // saves a changed field
    } else {
      window.location.assign(config.projectUrl);
    }
    return;
  }
  const modifier = event.ctrlKey || event.metaKey;
  if (modifier && event.shiftKey && isLetter(event, 'j')) {
    event.preventDefault();
    copyJson(config.jsonUrl).then((message) => {
      status.textContent = message;
    });
  } else if (modifier && event.shiftKey && isLetter(event, 'v')) {
    press('new-version', event);
  } else if (modifier && !event.shiftKey && isLetter(event, 'n')) {
    press('new-cue', event);
  } else if (event.altKey && !inField && event.key === 'ArrowUp') {
    press('cue-previous', event);
  } else if (event.altKey && !inField && event.key === 'ArrowDown') {
    press('cue-next', event);
  }
}

document.addEventListener('keydown', onKey);

document.body.addEventListener('htmx:afterRequest', (event) => {
  const source = event.detail.elt;
  const isSave = event.detail.requestConfig.verb === 'post';
  // The review link's settings say what happened under themselves. (By
  // address: once its button is replaced, htmx names the section here.)
  const path = event.detail.requestConfig.path || '';
  if (
    !isSave ||
    !source.closest ||
    !source.closest('.details-fields') ||
    path.includes('/review-link/')
  ) {
    return;
  }
  // The server sends cues:changed only when it saved something.
  const trigger = event.detail.xhr.getResponseHeader('HX-Trigger') || '';
  const saved = event.detail.successful && trigger.includes('cues:changed');
  status.textContent = saved ? 'Saved.' : 'Not saved.';
});

// A dialog that added a cue or version asks the page to reload; wait
// until it closes so "Save and add another" keeps working.
document.body.addEventListener('cues:reload', () => {
  if (dialog && dialog.open) {
    reloadWhenClosed = true;
  } else {
    window.location.reload();
  }
});

if (dialog) {
  dialog.addEventListener('close', () => {
    if (reloadWhenClosed) {
      window.location.reload();
    }
  });
}

// The section index opens a folded department when it jumps there.
document.addEventListener('click', (event) => {
  const link = event.target.closest('.cue-index a');
  const target = link && document.querySelector(link.hash);
  if (target instanceof HTMLDetailsElement) {
    target.open = true;
  }
});
