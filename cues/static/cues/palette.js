/**
 * @fileoverview The Ctrl+K palette: search projects and
 * cues, and run the page's commands.
 *
 * Commands are the page's elements marked data-palette="Label" (with an
 * optional data-palette-keys hint); choosing one clicks it, so a command
 * exists only where the page offers it and for roles that see it.
 * Projects and cues come from the search endpoint as the user types.
 */

const palette = document.getElementById('palette');

// Wait this long after a keystroke before asking the server.
const DEBOUNCE_MS = 120;
// With nothing typed, show this many commands before the projects.
const IDLE_COMMANDS = 5;

if (palette) {
  const input = palette.querySelector('.palette__input');
  const list = palette.querySelector('.palette__results');
  const searchUrl = palette.dataset.searchUrl;
  const projectId = palette.dataset.project || '';
  /** @type {!Array<!Object>} */
  let items = [];
  let active = 0;
  let timer = 0;
  /** @type {?AbortController} */
  let pending = null;

  /**
   * The page's commands whose label has every typed word.
   * @param {!Array<string>} words Lowercased query words.
   * @return {!Array<!Object>} Command items.
   */
  function commands(words) {
    const seen = new Set();
    const found = [];
    // The page's own actions first, then the top bar's pages.
    const elements = [...document.querySelectorAll('[data-palette]')].sort(
      (a, b) => Number(!!a.closest('.topbar')) - Number(!!b.closest('.topbar')),
    );
    for (const element of elements) {
      const label = element.dataset.palette;
      const lowered = label.toLowerCase();
      if (
        seen.has(label) ||
        element.disabled ||
        element.getAttribute('aria-disabled') === 'true' ||
        !words.every((word) => lowered.includes(word))
      ) {
        continue;
      }
      seen.add(label);
      found.push({
        kind: 'command',
        label,
        detail: element.dataset.paletteKeys || '',
        run: () => element.click(),
      });
    }
    return found;
  }

  /**
   * One option in the list.
   * @param {!Object} item A command, project or cue.
   * @param {number} index Its position.
   * @return {!Element} The option.
   */
  function option(item, index) {
    const li = document.createElement('li');
    li.className = `palette__item palette__item--${item.kind}`;
    li.id = `palette-option-${index}`;
    li.setAttribute('role', 'option');
    if (item.color) {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'palette__swatch');
      svg.setAttribute('viewBox', '0 0 10 10');
      svg.setAttribute('aria-hidden', 'true');
      const rect = document.createElementNS(
        'http://www.w3.org/2000/svg',
        'rect',
      );
      rect.setAttribute('width', '10');
      rect.setAttribute('height', '10');
      rect.setAttribute('rx', '2');
      rect.setAttribute('fill', item.color);
      svg.append(rect);
      li.append(svg);
    }
    if (item.number) {
      const number = document.createElement('span');
      number.className = 'palette__number';
      number.textContent = item.number;
      li.append(number);
    }
    const label = document.createElement('span');
    label.className = 'palette__label';
    label.textContent = item.label;
    li.append(label);
    if (item.tag) {
      const tag = document.createElement('span');
      tag.className = 'palette__tag';
      tag.textContent = item.tag;
      li.append(tag);
    }
    const detail = document.createElement(
      item.kind === 'command' ? 'kbd' : 'span',
    );
    detail.className = 'palette__detail';
    detail.textContent = item.detail;
    if (item.detail) {
      li.append(detail);
    }
    if (item.snippet) {
      // A cue found by its notes: where it matched, on a line of its own.
      li.classList.add('palette__item--snippet');
      const where = document.createElement('span');
      where.className = 'palette__snippet';
      const field = document.createElement('span');
      field.className = 'palette__snippet-field';
      field.textContent = `${item.field}:`;
      where.append(field, ` ${item.snippet}`);
      li.append(where);
    }
    li.addEventListener('mousedown', (event) => event.preventDefault());
    li.addEventListener('click', () => choose(index));
    return li;
  }

  /**
   * A section heading in the list.
   * @param {string} text The heading.
   * @return {!Element} A presentational list item.
   */
  function heading(text) {
    const li = document.createElement('li');
    li.className = 'palette__heading';
    li.setAttribute('role', 'presentation');
    li.textContent = text;
    return li;
  }

  /**
   * Draws the items, grouped, with the active one marked.
   * @param {!Array<!Object>} next Commands, then projects, then cues.
   * @param {boolean} searching Whether a server answer is still to come.
   */
  function render(next, searching) {
    items = next;
    active = Math.min(active, Math.max(items.length - 1, 0));
    const titles = {command: 'Commands', project: 'Projects', cue: 'Cues'};
    const nodes = [];
    let kind = '';
    items.forEach((item, index) => {
      if (item.kind !== kind) {
        kind = item.kind;
        nodes.push(heading(titles[kind]));
      }
      nodes.push(option(item, index));
    });
    if (!items.length) {
      const empty = heading(searching ? 'Searching…' : 'Nothing matches.');
      empty.classList.add('palette__empty');
      nodes.push(empty);
    }
    list.replaceChildren(...nodes);
    highlight();
  }

  /** Marks the active option and keeps it in view. */
  function highlight() {
    for (const node of list.querySelectorAll('[aria-selected]')) {
      node.removeAttribute('aria-selected');
    }
    const node = document.getElementById(`palette-option-${active}`);
    if (node) {
      node.setAttribute('aria-selected', 'true');
      node.scrollIntoView({block: 'nearest'});
      input.setAttribute('aria-activedescendant', node.id);
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  /**
   * Runs or opens an item.
   * @param {number} index Its position.
   */
  function choose(index) {
    const item = items[index];
    if (!item) {
      return;
    }
    palette.close();
    if (item.run) {
      item.run();
    } else {
      window.location.assign(item.url);
    }
  }

  /** Shows the commands at once, then asks the server for the rest. */
  function search() {
    const query = input.value.trim();
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const local = commands(words).slice(
      0,
      words.length ? undefined : IDLE_COMMANDS,
    );
    active = 0;
    render(local, true);
    window.clearTimeout(timer);
    pending?.abort();
    timer = window.setTimeout(async () => {
      pending = new AbortController();
      const params = new URLSearchParams({q: query, project: projectId});
      try {
        const response = await fetch(`${searchUrl}?${params}`, {
          signal: pending.signal,
          headers: {Accept: 'application/json'},
        });
        if (!response.ok) {
          throw new Error(`search failed: ${response.status}`);
        }
        const body = await response.json();
        render([...local, ...body.results], false);
      } catch (error) {
        if (error.name !== 'AbortError') {
          render(local, false);
        }
      }
    }, DEBOUNCE_MS);
  }

  /** Opens the palette, empty, unless another dialog is up. */
  function open() {
    if (palette.open || document.querySelector('dialog[open]')) {
      return;
    }
    input.value = '';
    palette.showModal();
    input.focus();
    search();
  }

  input.addEventListener('input', search);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (items.length) {
        const step = event.key === 'ArrowDown' ? 1 : -1;
        active = (active + step + items.length) % items.length;
        highlight();
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      choose(active);
    }
  });
  // A click on the backdrop (the dialog itself, outside its content).
  palette.addEventListener('click', (event) => {
    if (event.target === palette) {
      palette.close();
    }
  });
  palette.addEventListener('close', () => {
    window.clearTimeout(timer);
    pending?.abort();
    list.replaceChildren();
  });
  for (const button of document.querySelectorAll('[data-palette-open]')) {
    button.addEventListener('click', open);
  }
  document.addEventListener('keydown', (event) => {
    const modifier = event.ctrlKey || event.metaKey;
    if (
      modifier &&
      !event.shiftKey &&
      !event.altKey &&
      event.key.toLowerCase() === 'k'
    ) {
      event.preventDefault();
      if (palette.open) {
        palette.close();
      } else {
        open();
      }
    }
  });
}
