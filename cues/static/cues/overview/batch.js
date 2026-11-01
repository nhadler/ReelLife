/**
 * @fileoverview Batch edit: editing a cell of a selected cue
 * while two or more are selected asks whether to apply the value to all
 * of them. Applying is one change set on the server, so Undo reverts it.
 */

import {keys} from '../keys.js';

/**
 * Asks whether to apply an edit to every selected cue.
 * @param {!Object} input
 * @param {string} input.field The column's name, e.g. 'Status'.
 * @param {string} input.value The new value as shown, e.g. 'Composed'.
 * @param {number} input.count How many cues are selected.
 * @return {!Promise<{apply: boolean, keep: boolean}>} Closing the dialog
 *     any other way keeps the edit on this cue only.
 */
export function askBatch({field, value, count}) {
  const dialog = /** @type {!HTMLDialogElement} */ (
    document.getElementById('app-dialog')
  );
  const head = document.createElement('header');
  head.className = 'dialog__head';
  const title = document.createElement('h2');
  title.id = 'dialog-title';
  title.textContent = 'Apply to Selected Cues?';
  head.append(title);

  const form = document.createElement('form');
  form.className = 'batch-edit';
  form.method = 'dialog';
  const question = document.createElement('p');
  const name = document.createElement('strong');
  name.textContent = `${field} = ${value || '(blank)'}`;
  question.append('Apply ', name, ` to ${count} selected cues?`);
  const undo = document.createElement('p');
  undo.className = 'muted';
  undo.textContent = keys('Undo (Ctrl+Z) reverts all of them at once.');

  const keep = document.createElement('input');
  keep.type = 'checkbox';
  keep.id = 'batch-keep';
  const keepLabel = document.createElement('label');
  keepLabel.htmlFor = 'batch-keep';
  keepLabel.textContent = 'Keep the Selection';
  const keepField = document.createElement('div');
  keepField.className = 'field field--check';
  keepField.append(keep, keepLabel);

  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const apply = document.createElement('button');
  apply.type = 'submit';
  apply.className = 'button';
  apply.textContent = `Apply to ${count} Cues`;
  const single = document.createElement('button');
  single.type = 'button';
  single.className = 'button button--quiet';
  single.textContent = 'Just This Cue';
  actions.append(apply, single);

  form.append(question, undo, keepField, actions);
  dialog.replaceChildren(head, form);

  return new Promise((resolve) => {
    let done = false;
    const finish = (choice) => {
      if (!done) {
        done = true;
        dialog.close();
        resolve({apply: choice, keep: keep.checked});
      }
    };
    single.addEventListener('click', () => finish(false));
    dialog.addEventListener('close', () => finish(false), {once: true});
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      finish(true);
    });
    dialog.showModal();
    apply.focus();
  });
}
