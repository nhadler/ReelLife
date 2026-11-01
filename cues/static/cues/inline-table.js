/**
 * @fileoverview Editable lists (cues/templates/cues/includes/
 * inline_table.html): Add, × to remove, and a drag handle to reorder.
 *
 * Add copies the table's blank-row template, numbered as the formset's
 * next form, and focuses its first field. × marks a saved row for
 * removal (struck through; × again keeps it) and takes a new row away.
 * Rows with an order drag by their handle, or move with Alt+↑ and
 * Alt+↓ on it; the hidden order fields are then numbered top to bottom.
 * Listening on the document covers tables HTMX swaps in later.
 */

/**
 * Shows the scripted controls in a part of the page: the handles and ×
 * buttons, with the plain Remove boxes hidden behind them.
 * @param {!(Document|Element)} root
 */
function upgrade(root) {
  for (const table of root.querySelectorAll('[data-inline-table]')) {
    for (const control of table.querySelectorAll(
      '[data-drag-handle], [data-row-remove]',
    )) {
      control.hidden = false;
    }
    for (const box of table.querySelectorAll('[data-delete-box]')) {
      box.hidden = true;
    }
  }
}

/**
 * Numbers a table's order fields top to bottom, and shows its empty note
 * when no rows are left.
 * @param {!Element} table
 */
function renumber(table) {
  const rows = [...table.querySelectorAll('[data-inline-rows] > tr')].filter(
    (row) => row.matches('[data-inline-row]'),
  );
  rows.forEach((row, index) => {
    const order = row.querySelector('input[name$="-order"]');
    if (order) {
      order.value = String(index);
    }
  });
  const empty = table.querySelector('[data-inline-empty]');
  if (empty) {
    empty.hidden = rows.length > 0;
  }
}

/** @param {!Element} button */
function addRow(button) {
  const table = button.closest('[data-inline-table]');
  const total = table.querySelector('input[name$="-TOTAL_FORMS"]');
  const template = table.querySelector('template[data-row-template]');
  const index = Number(total.value);
  const body = table.querySelector('[data-inline-rows]');
  body.insertAdjacentHTML(
    'beforeend',
    template.innerHTML.replaceAll('__prefix__', String(index)),
  );
  total.value = String(index + 1);
  upgrade(table);
  renumber(table);
  body.lastElementChild
    .querySelector('input:not([type="hidden"]), select, textarea')
    ?.focus();
}

/** @param {!Element} button */
function removeRow(button) {
  const row = button.closest('[data-inline-row]');
  const table = row.closest('[data-inline-table]');
  const box = row.querySelector('input[name$="-DELETE"]');
  if (box) {
    box.checked = !box.checked;
    row.classList.toggle('inline-table__row--removed', box.checked);
    button.textContent = box.checked ? '↺' : '×';
    button.setAttribute('aria-label', box.checked ? 'Keep row' : 'Remove row');
    button.title = box.checked ? 'Keep' : 'Remove';
  } else {
    row.remove(); // a new row: its blank form is ignored on save
  }
  renumber(table);
}

document.addEventListener('click', (event) => {
  const add = event.target.closest('[data-add-row]');
  const remove = event.target.closest('[data-row-remove]');
  if (add) {
    addRow(add);
  } else if (remove) {
    removeRow(remove);
  }
});

// Alt+↑ and Alt+↓ on a handle move its row.
document.addEventListener('keydown', (event) => {
  const handle = event.target.closest?.('[data-drag-handle]');
  if (!handle || !event.altKey) {
    return;
  }
  const row = handle.closest('[data-inline-row]');
  const sibling =
    event.key === 'ArrowUp'
      ? row.previousElementSibling
      : event.key === 'ArrowDown'
        ? row.nextElementSibling
        : null;
  if (!sibling?.matches('[data-inline-row]')) {
    return;
  }
  event.preventDefault();
  sibling.insertAdjacentElement(
    event.key === 'ArrowUp' ? 'beforebegin' : 'afterend',
    row,
  );
  handle.focus();
  renumber(row.closest('[data-inline-table]'));
});

// Dragging by the handle: the row becomes draggable while it's held.
/** @type {?Element} */
let dragged = null;

document.addEventListener('pointerdown', (event) => {
  const handle = event.target.closest('[data-drag-handle]');
  if (handle) {
    handle.closest('[data-inline-row]').draggable = true;
  }
});

document.addEventListener('dragstart', (event) => {
  const row = event.target.closest?.('[data-inline-row]');
  if (!row?.draggable) {
    return;
  }
  dragged = row;
  row.classList.add('inline-table__row--dragging');
  event.dataTransfer.effectAllowed = 'move';
});

document.addEventListener('dragover', (event) => {
  const over = event.target.closest?.('[data-inline-row]');
  if (
    !dragged ||
    !over ||
    over === dragged ||
    over.parentNode !== dragged.parentNode
  ) {
    return;
  }
  event.preventDefault();
  const box = over.getBoundingClientRect();
  const after = event.clientY > box.top + box.height / 2;
  over.insertAdjacentElement(after ? 'afterend' : 'beforebegin', dragged);
});

document.addEventListener('dragend', () => {
  if (!dragged) {
    return;
  }
  dragged.classList.remove('inline-table__row--dragging');
  dragged.draggable = false;
  renumber(dragged.closest('[data-inline-table]'));
  dragged = null;
});

upgrade(document);
document.addEventListener('htmx:afterSwap', (event) => upgrade(event.target));
