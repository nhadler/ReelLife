/**
 * @fileoverview Copy buttons: `data-copy` names the field whose text a
 * click puts on the clipboard. The button says "Copied" for a moment;
 * where the browser blocks the clipboard the field is selected instead,
 * ready for Ctrl+C.
 */

const SHOWN_MS = 1500;

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy]');
  const field = button && document.querySelector(button.dataset.copy);
  if (!field) {
    return;
  }
  try {
    await navigator.clipboard.writeText(field.value);
  } catch {
    field.focus();
    field.select();
    return;
  }
  const label = button.textContent;
  button.textContent = 'Copied';
  setTimeout(() => {
    button.textContent = label;
  }, SHOWN_MS);
});
