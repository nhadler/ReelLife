/**
 * @fileoverview The editor for Status and other coloured choices (Sub
 * Status, department and review statuses): one
 * click opens the list with colour dots (Status divided by category) and
 * a box that filters it as you type. Arrow keys move, Enter picks, Escape
 * leaves the value as it was. Where the column is `creatable`, text that
 * matches nothing offers to add it.
 */

const CATEGORY_ORDER = [
  'unapproved',
  'approved',
  'inactive',
  'song',
  'license',
];

/** An AG Grid popup cell editor. */
export class StatusEditor {
  /**
   * @param {!Object} params AG Grid cell editor params, plus `options`
   *     (value, label, color and, for Status, category), `creatable` and
   *     `label` (the column's name, for screen readers).
   */
  init(params) {
    this.params = params;
    this.value = params.value;
    // A blank choice ('') reads as None; categories sort Status's list.
    this.options = params.options
      .map((o) => (o.value === '' ? {...o, label: 'None'} : o))
      .sort(
        (a, b) =>
          CATEGORY_ORDER.indexOf(a.category) -
          CATEGORY_ORDER.indexOf(b.category),
      );
    this.gui = document.createElement('div');
    this.gui.className = 'status-editor';
    this.input = document.createElement('input');
    this.input.type = 'text';
    this.input.className = 'status-editor__filter';
    this.input.setAttribute(
      'aria-label',
      `Filter ${params.label || 'choices'}`,
    );
    this.list = document.createElement('ul');
    this.list.className = 'status-editor__list';
    this.list.id = 'choice-list';
    this.list.setAttribute('role', 'listbox');
    this.list.setAttribute('aria-label', params.label || 'Choices');
    // The palette's pattern: a combobox that owns the list it filters.
    this.input.setAttribute('role', 'combobox');
    this.input.setAttribute('aria-controls', this.list.id);
    this.input.setAttribute('aria-expanded', 'true');
    this.input.setAttribute('aria-autocomplete', 'list');
    this.gui.append(this.input, this.list);
    this.active = Math.max(
      0,
      this.options.findIndex((o) => o.value === this.value),
    );
    this.input.addEventListener('input', () => {
      this.active = 0;
      this.render();
    });
    this.input.addEventListener('keydown', (event) => this.onKey(event));
    this.render();
  }

  /**
   * The options matching the filter text.
   * @return {!Array<!Object>}
   */
  shown() {
    const typed = this.input.value.trim();
    const text = typed.toLowerCase();
    const found = this.options.filter((o) =>
      o.label.toLowerCase().includes(text),
    );
    const exact = found.some((o) => o.label.toLowerCase() === text);
    if (this.params.creatable && typed && !exact) {
      // The server adds it to the project's list when the edit saves.
      found.push({value: typed, label: `Add "${typed}"`, isNew: true});
    }
    return found;
  }

  /** Draws the list, with a rule between categories. */
  render() {
    this.list.replaceChildren();
    let category = null;
    this.shown().forEach((option, index) => {
      if (category !== null && option.category !== category) {
        const rule = document.createElement('li');
        rule.className = 'status-editor__rule';
        rule.setAttribute('role', 'separator');
        this.list.append(rule);
      }
      category = option.category;
      const item = document.createElement('li');
      item.className = 'status-editor__item';
      item.setAttribute('role', 'option');
      item.id = `choice-option-${index}`;
      item.setAttribute('aria-selected', String(index === this.active));
      const dot = document.createElement('span');
      dot.className = option.isNew
        ? 'status-editor__dot status-editor__dot--new'
        : 'status-editor__dot';
      if (option.color) {
        dot.style.backgroundColor = option.color;
      }
      item.append(dot, option.label);
      // mousedown, so the pick lands before the editor loses focus.
      item.addEventListener('mousedown', (event) => {
        event.preventDefault();
        this.pick(option);
      });
      this.list.append(item);
      if (index === this.active) {
        this.input.setAttribute('aria-activedescendant', item.id);
      }
    });
  }

  /**
   * Keys inside the filter box.
   * @param {!KeyboardEvent} event
   */
  onKey(event) {
    const shown = this.shown();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopPropagation();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      this.active = (this.active + step + shown.length) % shown.length;
      this.render();
      this.list
        .querySelector('[aria-selected="true"]')
        ?.scrollIntoView({block: 'nearest'});
    } else if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      if (shown[this.active]) {
        this.pick(shown[this.active]);
      }
    }
  }

  /**
   * Chooses a status and closes the editor (which saves it).
   * @param {!Object} option
   */
  pick(option) {
    this.value = option.value;
    this.params.stopEditing();
  }

  /** @return {!HTMLElement} */
  getGui() {
    return this.gui;
  }

  /** Focuses the filter box once shown. */
  afterGuiAttached() {
    this.input.focus();
    this.list
      .querySelector('[aria-selected="true"]')
      ?.scrollIntoView({block: 'nearest'});
  }

  /** @return {?(number|string)} The choice's value, or new text. */
  getValue() {
    return this.value;
  }

  /** @return {boolean} */
  isPopup() {
    return true;
  }
}
