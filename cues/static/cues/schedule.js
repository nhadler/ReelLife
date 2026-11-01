/**
 * @fileoverview The Schedule's Projects list applies as soon as a box is
 * ticked; its Show button is for browsers without scripts. A pin set or
 * cleared from the page ("Schedule On…") redraws it.
 */

for (const form of document.querySelectorAll('form[data-autosubmit]')) {
  form.querySelector('[data-autosubmit-button]')?.setAttribute('hidden', '');
  form.addEventListener('change', () => form.requestSubmit());
}

document.body.addEventListener('schedule:changed', () => {
  window.location.reload();
});
