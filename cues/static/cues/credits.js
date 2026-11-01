/**
 * @fileoverview The Credits page (cues/templates/cues/credits.html).
 *
 * Each table's Add button adds a blank row from its template (blank
 * spare rows stay hidden until then), and each row's × removes it: a new
 * row is cleared, a saved one marked for removal (× again keeps it). A
 * party still named in shares can't be removed. The search box shows
 * only the rows whose names, PRO or IPI hold every word typed. Without
 * JavaScript the tables work as plain forms.
 */

const search = document.querySelector('[data-credits-search]');
const tables = [...document.querySelectorAll('[data-party-table]')];

/**
 * @param {!Element} row
 * @return {!Array<!HTMLInputElement>} The row's text fields.
 */
function textFields(row) {
  return [...row.querySelectorAll('input[type="text"], input:not([type])')];
}

/**
 * Whether a row is a spare: new and still blank.
 * @param {!Element} row
 * @return {boolean}
 */
function spare(row) {
  return (
    !row.hasAttribute('data-saved') &&
    textFields(row).every((field) => !field.value.trim())
  );
}

/**
 * Brings a table's rows, count and empty note up to date.
 * @param {!Element} table
 */
function tidy(table) {
  const rows = [...table.querySelectorAll('[data-party-row]')];
  const words = (search?.value || '')
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  let shown = 0;
  for (const row of rows) {
    const remove = row.querySelector('[data-party-remove]');
    const box = row.querySelector('input[name$="-DELETE"]');
    remove.hidden = false;
    row.querySelector('[data-party-checkbox]')?.setAttribute('hidden', '');
    row.classList.toggle('party-table__row--removed', Boolean(box?.checked));
    if (box && !remove.disabled) {
      remove.textContent = box.checked ? '↺' : '×';
      remove.setAttribute(
        'aria-label',
        box.checked ? 'Keep this row' : 'Remove this row',
      );
    }
    const text = textFields(row)
      .map((field) => field.value)
      .join(' ')
      .toLowerCase();
    const isSpare = spare(row) && !row.hasAttribute('data-added');
    row.hidden = isSpare || !words.every((word) => text.includes(word));
    shown += row.hidden ? 0 : 1;
  }
  const saved = rows.filter((row) => row.hasAttribute('data-saved')).length;
  const section = table.closest('[data-party-section]');
  const count = section?.querySelector('[data-party-count]');
  if (count) {
    count.textContent = String(saved);
  }
  table.querySelector('table').hidden = !shown;
  table.querySelector('[data-party-empty]').hidden = Boolean(
    shown || words.length,
  );
}

/**
 * Adds a blank row to a table (reusing a hidden spare) and focuses it.
 * @param {!Element} table
 */
function addRow(table) {
  const prefix = table.dataset.prefix;
  const rows = table.querySelector('[data-party-rows]');
  let row = [...rows.querySelectorAll('[data-party-row]')].find(
    (candidate) => spare(candidate) && !candidate.hasAttribute('data-added'),
  );
  if (!row) {
    const total = table.querySelector(`[name="${prefix}-TOTAL_FORMS"]`);
    const index = Number(total.value);
    const template = table.querySelector('[data-party-template]');
    rows.insertAdjacentHTML(
      'beforeend',
      template.innerHTML.replaceAll('__prefix__', String(index)),
    );
    total.value = String(index + 1);
    row = rows.lastElementChild;
  }
  row.setAttribute('data-added', '');
  if (search) {
    search.value = '';
  }
  tidy(table);
  textFields(row)[0]?.focus();
}

for (const table of tables) {
  table.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-party-remove]');
    if (!remove || remove.disabled) {
      return;
    }
    const row = remove.closest('[data-party-row]');
    const box = row.querySelector('input[name$="-DELETE"]');
    if (box) {
      box.checked = !box.checked;
    } else {
      for (const field of row.querySelectorAll('input')) {
        if (field.type === 'checkbox') {
          field.checked = false;
        } else if (field.type !== 'hidden') {
          field.value = '';
        }
      }
      row.removeAttribute('data-added');
    }
    tidy(table);
  });
  table.addEventListener('input', () => tidy(table));
  tidy(table);
}

for (const button of document.querySelectorAll('[data-party-add]')) {
  const table = document.querySelector(
    `[data-party-table][data-prefix="${button.dataset.partyAdd}"]`,
  );
  button.hidden = false;
  button.addEventListener('click', () => addRow(table));
}

if (search && tables.length) {
  search.closest('[data-credits-search-field]')?.removeAttribute('hidden');
  search.addEventListener('input', () => tables.forEach(tidy));
}

// Says when there's something to save.
const form = document.querySelector('[data-credits-form]');
const status = document.querySelector('[data-credits-status]');
if (form && status) {
  const changed = () => {
    form.setAttribute('data-dirty', '');
    status.textContent = 'Unsaved changes';
  };
  form.addEventListener('input', changed);
  form.addEventListener('click', (event) => {
    if (event.target.closest('[data-party-remove]')) {
      changed();
    }
  });
}
