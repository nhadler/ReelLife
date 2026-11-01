/**
 * @fileoverview The page's modal dialog for htmx-loaded forms.
 *
 * htmx swaps a form into #app-dialog; once it has settled this opens it as
 * a modal and focuses its first field (or the first one with an error),
 * closes it on Cancel or when the server sends a dialog:close event
 * (HX-Trigger), and empties it once closed. A step swapped in inside the
 * dialog (the import preview) gets focus too, and Escape asks before
 * throwing away anything typed.
 */

import {actionOf, ask} from './confirm.js';

const FIELDS = 'input:not([type=hidden]):not([disabled]), select, textarea';
const TYPED =
  'textarea, input[type=text], input[type=number], input:not([type])';

/**
 * The field to focus in `root`: the first with an error (errors follow
 * their field, includes/field.html), else the first.
 * @param {!Element} root
 * @return {?HTMLElement}
 */
function firstField(root) {
  const error = root.querySelector('.errorlist');
  return (
    error?.closest('.field')?.querySelector(FIELDS) ??
    root.querySelector(FIELDS)
  );
}

const dialog = document.getElementById('app-dialog');

if (dialog) {
  // After settling, when htmx has wired up the new form: a quick Enter
  // before then would submit it natively.
  document.body.addEventListener('htmx:afterSettle', (event) => {
    const target = event.detail.target;
    if (target !== dialog && dialog.contains(target)) {
      // A later step, e.g. the import preview: start at its heading.
      const heading = target.querySelector('h2, h3, [role=alert]');
      if (heading) {
        heading.tabIndex = -1;
        heading.focus();
      } else {
        firstField(target)?.focus();
      }
      return;
    }
    if (target !== dialog) {
      return;
    }
    delete dialog.dataset.typed;
    if (!dialog.open) {
      dialog.showModal();
    }
    const field = firstField(dialog);
    if (field) {
      field.focus();
      // Suggested values (cue number, version label) are meant to be
      // typed over.
      if (field.type === 'text') {
        field.select();
      }
    }
  });
  document.body.addEventListener('dialog:close', () => dialog.close());
  // Only typing counts: choices (radios, selects) are quick to redo.
  dialog.addEventListener('input', (event) => {
    if (event.target.matches(TYPED)) {
      dialog.dataset.typed = 'true';
    }
  });
  // Escape closes a modal; ask first when something was typed.
  dialog.addEventListener('cancel', (event) => {
    if (dialog.dataset.typed) {
      event.preventDefault();
      ask('Discard what you typed?', 'Discard').then((yes) => {
        if (yes) {
          dialog.close();
        }
      });
    }
  });
  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-dialog-close]')) {
      dialog.close();
    }
  });
  dialog.addEventListener('close', () => dialog.replaceChildren());
}

// A submit button with data-confirm asks first, as hx-confirm does for
// htmx requests (Settings' view Reset and Delete, like the grid's).
document.addEventListener('submit', (event) => {
  const submitter = event.submitter;
  const question = submitter?.dataset.confirm;
  if (!question || submitter.dataset.confirmed) {
    return;
  }
  event.preventDefault();
  ask(question, actionOf(submitter)).then((yes) => {
    if (yes) {
      submitter.dataset.confirmed = 'true';
      event.target.requestSubmit(submitter);
    }
  });
});
