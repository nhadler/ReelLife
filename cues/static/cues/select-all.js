/**
 * @fileoverview Select All checkboxes for lists of checkboxes.
 *
 * A checkbox with data-select-all="name" ticks or clears every enabled
 * checkbox named `name` in its form, and shows mixed when only some are
 * ticked. Used by the conform page's cues on older picture.
 */

/**
 * @param {!HTMLInputElement} all
 * @return {!Array<!HTMLInputElement>}
 */
function boxesFor(all) {
  const name = all.dataset.selectAll;
  return [...all.form.querySelectorAll('input[type=checkbox]')].filter(
    (box) => box.name === name && !box.disabled,
  );
}

document.addEventListener('change', (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || !target.form) {
    return;
  }
  if (target.dataset.selectAll) {
    for (const box of boxesFor(target)) {
      box.checked = target.checked;
    }
    target.indeterminate = false;
    return;
  }
  const all = target.form.querySelector(
    `[data-select-all="${CSS.escape(target.name)}"]`,
  );
  if (all) {
    const boxes = boxesFor(all);
    const ticked = boxes.filter((box) => box.checked).length;
    all.checked = ticked === boxes.length;
    all.indeterminate = ticked > 0 && ticked < boxes.length;
  }
});
