/**
 * @fileoverview Theme and Instrumentation cells: chips
 * in their colours, and a popup editor. The editor lists the cue's
 * chips, each with an optional duration (m:ss) and a remove button, and
 * a box to add more: it filters the project's list as you type, and a
 * name that isn't on it can be added (the server adds it to the list).
 * Enter adds the highlighted entry; Enter in an empty box, or a click
 * elsewhere, saves; Escape leaves the cell as it was.
 */

/**
 * Seconds as m:ss.
 * @param {?number} seconds
 * @return {string}
 */
export function minutes(seconds) {
  if (seconds === null || seconds === undefined) {
    return '';
  }
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/**
 * Reads m:ss (or plain seconds) as seconds; null when blank or unclear.
 * @param {string} text
 * @return {?number}
 */
export function parseMinutes(text) {
  const match = text.trim().match(/^(?:(\d+):)?(\d+)$/);
  if (!match) {
    return null;
  }
  return Number(match[1] || 0) * 60 + Number(match[2]);
}

/**
 * Draws a cell's chips.
 * @param {function(string): string} ink Text colour for a fill colour.
 * @return {function(!Object): !HTMLElement}
 */
export function chipsRenderer(ink) {
  return (params) => {
    const list = document.createElement('span');
    list.className = 'chip-list';
    for (const chip of params.value || []) {
      const item = document.createElement('span');
      item.className = 'chip-list__chip';
      if (chip.color) {
        item.style.backgroundColor = chip.color;
        item.style.color = ink(chip.color);
      }
      item.textContent =
        chip.seconds !== null && chip.seconds !== undefined
          ? `${chip.name} ${minutes(chip.seconds)}`
          : chip.name;
      list.append(item);
    }
    if (!list.childElementCount) {
      list.classList.add('chip-list--empty');
      list.textContent = '–';
    }
    return list;
  };
}

/** An AG Grid popup cell editor for a chip list. */
export class ChipsEditor {
  /**
   * @param {!Object} params AG Grid cell editor params, plus `options`
   *     (the project's list: value, label, color) and `label`.
   */
  init(params) {
    this.params = params;
    this.original = params.value || [];
    this.chips = this.original.map((chip) => ({...chip}));
    this.active = 0;
    this.gui = document.createElement('div');
    this.gui.className = 'chips-editor';
    this.current = document.createElement('ul');
    this.current.className = 'chips-editor__current';
    this.current.setAttribute('aria-label', params.label);
    this.input = document.createElement('input');
    this.input.type = 'text';
    this.input.className = 'status-editor__filter';
    this.input.placeholder = `Add ${params.label.toLowerCase()}…`;
    this.input.setAttribute('aria-label', `Add ${params.label.toLowerCase()}`);
    this.list = document.createElement('ul');
    this.list.className = 'status-editor__list';
    this.list.id = 'chips-list';
    this.list.setAttribute('role', 'listbox');
    this.list.setAttribute('aria-label', `${params.label} to add`);
    this.input.setAttribute('role', 'combobox');
    this.input.setAttribute('aria-controls', this.list.id);
    this.input.setAttribute('aria-expanded', 'true');
    this.input.setAttribute('aria-autocomplete', 'list');
    this.gui.append(this.current, this.input, this.list);
    this.input.addEventListener('input', () => {
      this.active = 0;
      this.renderChoices();
    });
    this.input.addEventListener('keydown', (event) => this.onKey(event));
    this.renderCurrent();
    this.renderChoices();
  }

  /** Draws the chips already on the cue. */
  renderCurrent() {
    this.current.replaceChildren();
    this.chips.forEach((chip, index) => {
      const row = document.createElement('li');
      row.className = 'chips-editor__chip';
      const dot = document.createElement('span');
      dot.className = 'status-editor__dot';
      if (chip.color) {
        dot.style.backgroundColor = chip.color;
      }
      const name = document.createElement('span');
      name.className = 'chips-editor__name';
      name.textContent = chip.name;
      const time = document.createElement('input');
      time.type = 'text';
      time.className = 'chips-editor__time tc';
      time.placeholder = 'm:ss';
      time.value = minutes(chip.seconds);
      time.setAttribute('aria-label', `${chip.name} duration`);
      time.addEventListener('change', () => {
        chip.seconds = parseMinutes(time.value);
        time.value = minutes(chip.seconds);
      });
      time.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          event.stopPropagation();
          chip.seconds = parseMinutes(time.value);
          this.input.focus();
        }
      });
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'chips-editor__remove';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${chip.name}`);
      // mousedown keeps the editor from losing focus to the button; click
      // also comes from Enter and Space, so the keyboard can remove too.
      remove.addEventListener('mousedown', (event) => event.preventDefault());
      remove.addEventListener('click', () => {
        this.chips.splice(index, 1);
        this.renderCurrent();
        this.renderChoices();
        this.input.focus();
      });
      row.append(dot, name, time, remove);
      this.current.append(row);
    });
  }

  /**
   * The list entries matching the box, not yet on the cue, and an entry
   * to add what's typed when nothing matches it exactly.
   * @return {!Array<!Object>}
   */
  shown() {
    const typed = this.input.value.trim();
    const text = typed.toLowerCase();
    const taken = new Set(this.chips.map((chip) => chip.name.toLowerCase()));
    const found = this.params.options.filter(
      (o) =>
        !taken.has(o.label.toLowerCase()) &&
        o.label.toLowerCase().includes(text),
    );
    const known = this.params.options.some(
      (o) => o.label.toLowerCase() === text,
    );
    if (typed && !known && !taken.has(text)) {
      found.push({value: null, label: typed, isNew: true});
    }
    return found;
  }

  /** Draws the choices for the box. */
  renderChoices() {
    this.list.replaceChildren();
    this.input.removeAttribute('aria-activedescendant');
    this.shown().forEach((option, index) => {
      const item = document.createElement('li');
      item.className = 'status-editor__item';
      item.setAttribute('role', 'option');
      item.id = `chips-option-${index}`;
      item.setAttribute('aria-selected', String(index === this.active));
      if (index === this.active) {
        this.input.setAttribute('aria-activedescendant', item.id);
      }
      const dot = document.createElement('span');
      dot.className = option.isNew
        ? 'status-editor__dot status-editor__dot--new'
        : 'status-editor__dot';
      if (option.color) {
        dot.style.backgroundColor = option.color;
      }
      item.append(dot, option.isNew ? `Add "${option.label}"` : option.label);
      item.addEventListener('mousedown', (event) => {
        event.preventDefault();
        this.add(option);
      });
      this.list.append(item);
    });
  }

  /**
   * Puts an entry on the cue.
   * @param {!Object} option
   */
  add(option) {
    this.chips.push(
      option.isNew
        ? {name: option.label, seconds: null}
        : {
            id: option.value,
            name: option.label,
            color: option.color,
            seconds: null,
          },
    );
    this.input.value = '';
    this.active = 0;
    this.renderCurrent();
    this.renderChoices();
    this.input.focus();
  }

  /**
   * Keys in the add box.
   * @param {!KeyboardEvent} event
   */
  onKey(event) {
    const shown = this.shown();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopPropagation();
      if (shown.length) {
        const step = event.key === 'ArrowDown' ? 1 : -1;
        this.active = (this.active + step + shown.length) % shown.length;
        this.renderChoices();
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      if (this.input.value.trim() && shown[this.active]) {
        this.add(shown[this.active]);
      } else {
        this.params.stopEditing();
      }
    }
  }

  /** @return {!HTMLElement} */
  getGui() {
    return this.gui;
  }

  /** Focuses the add box once shown. */
  afterGuiAttached() {
    this.input.focus();
  }

  /**
   * The new list, or the original when nothing changed (no save then).
   * @return {!Array<!Object>}
   */
  getValue() {
    const same =
      this.chips.length === this.original.length &&
      this.chips.every(
        (chip, i) =>
          chip.name === this.original[i].name &&
          chip.seconds === this.original[i].seconds,
      );
    return same ? this.original : this.chips;
  }

  /** @return {boolean} */
  isPopup() {
    return true;
  }
}
