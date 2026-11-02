/**
 * @fileoverview Asking before something can't be taken back, in the
 * app's own dialog rather than the browser's confirm box.
 *
 * `ask` shows the question with a named action (Delete, Reset, Discard)
 * and Cancel. htmx's hx-confirm goes through it too. A field with
 * data-confirm-text keeps its form's danger button disabled until the
 * text is typed exactly (Delete Project, the title).
 */

/** @type {?HTMLDialogElement} */
let dialog = null;

/** @return {!HTMLDialogElement} The confirm dialog, made on first use. */
function confirmDialog() {
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'dialog confirm-dialog';
    dialog.setAttribute('aria-labelledby', 'confirm-title');
    const form = document.createElement('form');
    form.method = 'dialog';
    const title = document.createElement('h2');
    title.id = 'confirm-title';
    title.className = 'confirm-dialog__title';
    const actions = document.createElement('footer');
    actions.className = 'form-actions';
    const yes = document.createElement('button');
    yes.value = 'yes';
    yes.dataset.confirmYes = '';
    const no = document.createElement('button');
    no.value = 'no';
    no.className = 'button button--quiet';
    no.textContent = 'Cancel';
    actions.append(yes, no);
    form.append(title, actions);
    dialog.append(form);
    document.body.append(dialog);
  }
  return dialog;
}

/**
 * Asks a question; resolves true when the action is chosen.
 * @param {string} question
 * @param {string=} action The action's name, e.g. 'Delete Cue'.
 * @return {!Promise<boolean>}
 */
export function ask(question, action = 'OK') {
  const box = confirmDialog();
  box.querySelector('#confirm-title').textContent = question;
  const yes = box.querySelector('[data-confirm-yes]');
  yes.textContent = action;
  const danger = /^(Delete|Reset|Discard|Remove|Revert)/.test(action);
  yes.className = danger ? 'button button--danger' : 'button';
  box.returnValue = '';
  box.showModal();
  yes.focus();
  return new Promise((resolve) => {
    box.addEventListener('close', () => resolve(box.returnValue === 'yes'), {
      once: true,
    });
  });
}

/**
 * The name of the action an element starts: its own words, briefly.
 * @param {?Element} element
 * @return {string}
 */
export function actionOf(element) {
  const words = (element?.textContent || '').trim().replace(/\s+/g, ' ');
  return words && words.length <= 28 ? words : 'Continue';
}

// hx-confirm asks here too.
document.addEventListener('htmx:confirm', (event) => {
  const question = event.detail.question;
  if (!question) {
    return;
  }
  event.preventDefault();
  ask(question, actionOf(event.detail.elt)).then((yes) => {
    if (yes) {
      event.detail.issueRequest(true);
    }
  });
});

/**
 * Enables a form's danger button only once its typed confirmation matches.
 * @param {!HTMLInputElement} input A field with data-confirm-text.
 */
function guard(input) {
  const button = input.form?.querySelector('.button--danger');
  if (button) {
    button.disabled = input.value.trim() !== input.dataset.confirmText;
  }
}

for (const input of document.querySelectorAll('input[data-confirm-text]')) {
  guard(/** @type {!HTMLInputElement} */ (input));
}
document.addEventListener('input', (event) => {
  if (
    event.target instanceof HTMLInputElement &&
    event.target.dataset.confirmText !== undefined
  ) {
    guard(event.target);
  }
});
