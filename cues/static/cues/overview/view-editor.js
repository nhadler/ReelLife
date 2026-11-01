/**
 * @fileoverview The Edit view dialog: a view's name and its
 * columns as tiles. Active fields are in order; click one to drop it,
 * drag it (or Alt+Left/Right) to move it. Inactive fields: click to add
 * at the end. A search box narrows both; "Start from" loads a standard
 * view's columns. Widths and pinning are kept from the view, and set in
 * the grid.
 */

import {keys} from '../keys.js';

/**
 * Opens the dialog and resolves with the result, or null on Cancel.
 * @param {!Object} input
 * @param {string} input.title The dialog's heading.
 * @param {string} input.name The view's name.
 * @param {boolean} input.fixedName Seeded views keep their names.
 * @param {!Array<!Object>} input.columns The view's layout ({id, ...}).
 * @param {!Array<!Object>} input.fields Every column: {id, label}.
 * @param {!Array<!Object>} input.presets Views to start from.
 * @return {!Promise<?{name: string, columns: !Array<!Object>}>}
 */
export function editView(input) {
  const dialog = /** @type {!HTMLDialogElement} */ (
    document.getElementById('app-dialog')
  );
  const labels = new Map(input.fields.map((f) => [f.id, f.label]));
  const layout = new Map(input.columns.map((c) => [c.id, c]));
  let active = input.columns.map((c) => c.id).filter((id) => labels.has(id));

  const head = document.createElement('header');
  head.className = 'dialog__head';
  const title = document.createElement('h2');
  title.id = 'dialog-title';
  title.textContent = input.title;
  // The × every dialog has; closing resolves as Cancel does.
  const close = document.createElement('button');
  close.className = 'dialog__close';
  close.type = 'button';
  close.dataset.dialogClose = '';
  close.setAttribute('aria-label', 'Close');
  close.textContent = '×';
  head.append(title, close);

  const form = document.createElement('form');
  form.className = 'view-editor';
  form.method = 'dialog';

  const name = document.createElement('input');
  name.type = 'text';
  name.id = 'view-name';
  name.maxLength = 50;
  name.required = true;
  name.value = input.name;
  name.disabled = input.fixedName;
  const nameLabel = document.createElement('label');
  nameLabel.htmlFor = 'view-name';
  nameLabel.textContent = 'Name';

  const start = document.createElement('select');
  start.id = 'view-start';
  start.append(new Option('Keep These Columns', ''));
  for (const preset of input.presets) {
    start.append(new Option(preset.name, String(preset.id)));
  }
  const startLabel = document.createElement('label');
  startLabel.htmlFor = 'view-start';
  startLabel.textContent = 'Start From';
  start.addEventListener('change', () => {
    const preset = input.presets.find((p) => String(p.id) === start.value);
    if (preset) {
      active = preset.columns.map((c) => c.id).filter((id) => labels.has(id));
      for (const column of preset.columns) {
        layout.set(column.id, column);
      }
      render();
    }
  });

  const search = document.createElement('input');
  search.type = 'search';
  search.placeholder = 'Search fields';
  search.setAttribute('aria-label', 'Search fields');
  search.addEventListener('input', () => render());

  const hint = document.createElement('p');
  hint.className = 'muted';
  hint.textContent = keys(
    'Click a field to add or drop it; drag active fields (or Alt+arrow keys) to reorder. Widths and pinning are set in the grid.',
  );

  const activeList = document.createElement('ol');
  activeList.className = 'view-editor__tiles view-editor__tiles--active';
  activeList.setAttribute('aria-label', 'Active Fields');
  const inactiveList = document.createElement('ul');
  inactiveList.className = 'view-editor__tiles';
  inactiveList.setAttribute('aria-label', 'Inactive Fields');

  const all = document.createElement('button');
  all.type = 'button';
  all.className = 'button button--quiet button--small';
  all.textContent = 'Select All';
  all.addEventListener('click', () => {
    active = [
      ...active,
      ...input.fields.map((f) => f.id).filter((id) => !active.includes(id)),
    ];
    render();
  });

  const error = document.createElement('p');
  error.className = 'form-error';
  error.setAttribute('role', 'alert');

  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'button button--quiet';
  cancel.textContent = 'Cancel';
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'button';
  save.textContent = 'Save View';
  actions.append(save, cancel);

  const heading = (text) => {
    const h = document.createElement('h3');
    h.className = 'view-editor__heading';
    h.textContent = text;
    return h;
  };
  form.append(
    nameLabel,
    name,
    startLabel,
    start,
    search,
    hint,
    heading('Active Fields'),
    activeList,
    heading('Inactive Fields'),
    all,
    inactiveList,
    error,
    actions,
  );
  dialog.replaceChildren(head, form);

  let dragged = null;

  /**
   * One field's tile.
   * @param {string} id
   * @param {boolean} isActive
   * @return {!HTMLElement}
   */
  function tile(id, isActive) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = isActive ? 'view-tile view-tile--active' : 'view-tile';
    button.textContent = labels.get(id);
    button.dataset.id = id;
    button.title = isActive ? 'Click to drop; drag to move' : 'Click to add';
    button.addEventListener('click', () => {
      active = isActive ? active.filter((a) => a !== id) : [...active, id];
      render();
      const again = (isActive ? inactiveList : activeList).querySelector(
        `[data-id="${id}"]`,
      );
      again?.focus();
    });
    if (isActive) {
      item.draggable = true;
      item.addEventListener('dragstart', () => {
        dragged = id;
      });
      item.addEventListener('dragover', (event) => event.preventDefault());
      item.addEventListener('drop', (event) => {
        event.preventDefault();
        if (dragged && dragged !== id) {
          const without = active.filter((a) => a !== dragged);
          without.splice(without.indexOf(id), 0, dragged);
          active = without;
          render();
        }
      });
      button.addEventListener('keydown', (event) => {
        const step = {ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1}[
          event.key
        ];
        if (!event.altKey || !step) {
          return;
        }
        event.preventDefault();
        const at = active.indexOf(id);
        const to = at + step;
        if (to >= 0 && to < active.length) {
          active.splice(at, 1);
          active.splice(to, 0, id);
          render();
          activeList.querySelector(`[data-id="${id}"]`)?.focus();
        }
      });
    }
    item.append(button);
    return item;
  }

  /** Draws both lists, narrowed by the search. */
  function render() {
    const text = search.value.trim().toLowerCase();
    const shown = (id) => !text || labels.get(id).toLowerCase().includes(text);
    activeList.replaceChildren(
      ...active.filter(shown).map((id) => tile(id, true)),
    );
    inactiveList.replaceChildren(
      ...input.fields
        .map((f) => f.id)
        .filter((id) => !active.includes(id) && shown(id))
        .map((id) => tile(id, false)),
    );
  }
  render();

  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (!done) {
        done = true;
        dialog.close();
        resolve(value);
      }
    };
    cancel.addEventListener('click', () => finish(null));
    dialog.addEventListener('close', () => finish(null), {once: true});
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!active.length) {
        error.textContent = 'Pick at least one field.';
        return;
      }
      if (!name.value.trim()) {
        error.textContent = 'Give the view a name.';
        name.focus();
        return;
      }
      finish({
        name: name.value.trim(),
        columns: active.map((id) => ({...(layout.get(id) || {}), id})),
      });
    });
    dialog.showModal();
    (input.fixedName ? search : name).focus();
  });
}
