/**
 * @fileoverview Takes the user to the first field that needs fixing.
 *
 * A form sent back with errors carries a .form-errors summary
 * (includes/form_errors.html). On load, and when htmx swaps one into a
 * dialog, the first invalid field in that form gets the focus.
 */

const INVALID =
  '[aria-invalid="true"], .field:has(> .errorlist) :is(input, select, textarea)';

/** @param {!Document|!Element} root */
function focusFirstProblem(root) {
  const summary = root.querySelector('.form-errors');
  const form = summary?.closest('form');
  const field = form?.querySelector(INVALID);
  if (field instanceof HTMLElement) {
    field.focus();
  }
}

focusFirstProblem(document);
document.addEventListener('htmx:afterSettle', (event) => {
  if (event.target instanceof Element) {
    focusFirstProblem(event.target);
  }
});
