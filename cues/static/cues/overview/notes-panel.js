/**
 * @fileoverview One cue's notes (Revision Notes, say) in the panel beside
 * the grid: a Markdown text box with a toolbar, a preview whose check
 * boxes can be ticked, and saving as you go. Clicking another row's cell
 * shows that cue's notes; Alt+↑/↓ steps through the shown cues.
 */

import {checklist, render, toggle} from './markdown.js';

// How long typing pauses before the text is saved.
const SAVE_DELAY_MS = 800;

const TOOLS = [
  {name: 'bold', label: 'Bold', key: 'b', text: 'B'},
  {name: 'italic', label: 'Italic', key: 'i', text: 'I'},
  {name: 'heading', label: 'Heading', text: 'H'},
  {name: 'bullets', label: 'Bulleted List', text: '•'},
  {name: 'checks', label: 'Check List', text: '☐'},
  {name: 'link', label: 'Link', key: 'k', text: '🔗'},
];

// A list item's marker, carried on to the next line by Enter.
const LIST_ITEM = /^(\s*)([-*+] \[[ xX]\] |[-*+] |(\d{1,9})([.)]) )(.*)$/;

/**
 * Makes an element.
 * @param {string} tag
 * @param {string=} className
 * @param {string=} text
 * @return {!HTMLElement}
 */
function make(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text) {
    node.textContent = text;
  }
  return node;
}

/**
 * Wraps the selection in a marker, or puts the marker either side of the
 * cursor.
 * @param {!HTMLTextAreaElement} box
 * @param {string} before
 * @param {string=} after
 * @param {string=} placeholder Selected when nothing was.
 */
function wrap(box, before, after = before, placeholder = '') {
  const {selectionStart: start, selectionEnd: end} = box;
  const chosen = box.value.slice(start, end) || placeholder;
  box.setRangeText(`${before}${chosen}${after}`, start, end, 'end');
  box.setSelectionRange(
    start + before.length,
    start + before.length + chosen.length,
  );
}

/**
 * Puts a prefix at the start of each selected line (or takes it off when
 * every line has it).
 * @param {!HTMLTextAreaElement} box
 * @param {string} prefix
 */
function prefixLines(box, prefix) {
  const value = box.value;
  const start = value.lastIndexOf('\n', box.selectionStart - 1) + 1;
  const found = value.indexOf('\n', box.selectionEnd);
  const end = found === -1 ? value.length : found;
  const lines = value.slice(start, end).split('\n');
  const all = lines.every((line) => line.startsWith(prefix));
  const changed = lines
    .map((line) => (all ? line.slice(prefix.length) : prefix + line))
    .join('\n');
  box.setRangeText(changed, start, end, 'select');
}

/**
 * Applies a toolbar action to the text box.
 * @param {!HTMLTextAreaElement} box
 * @param {string} name A TOOLS name.
 */
function apply(box, name) {
  if (name === 'bold') {
    wrap(box, '**', '**', 'bold');
  } else if (name === 'italic') {
    wrap(box, '*', '*', 'italic');
  } else if (name === 'heading') {
    prefixLines(box, '## ');
  } else if (name === 'bullets') {
    prefixLines(box, '- ');
  } else if (name === 'checks') {
    prefixLines(box, '- [ ] ');
  } else if (name === 'link') {
    const {selectionStart: start, selectionEnd: end} = box;
    const label = box.value.slice(start, end) || 'link';
    box.setRangeText(`[${label}](https://)`, start, end, 'end');
    const url = start + label.length + 3;
    box.setSelectionRange(url, url + 'https://'.length);
  }
  box.dispatchEvent(new Event('input', {bubbles: true}));
}

/**
 * Enter in a list item starts the next item; on an empty item it ends
 * the list.
 * @param {!HTMLTextAreaElement} box
 * @return {boolean} Whether Enter was handled.
 */
function continueList(box) {
  if (box.selectionStart !== box.selectionEnd) {
    return false;
  }
  const at = box.selectionStart;
  const start = box.value.lastIndexOf('\n', at - 1) + 1;
  const match = LIST_ITEM.exec(box.value.slice(start, at));
  if (!match) {
    return false;
  }
  const [line, indent, marker, number, dot, rest] = match;
  if (!rest.trim()) {
    box.setRangeText('', start, start + line.length, 'end');
  } else {
    let next = marker.replace(/\[[xX]\]/, '[ ]');
    if (number) {
      next = `${Number(number) + 1}${dot} `;
    }
    box.setRangeText(`\n${indent}${next}`, at, at, 'end');
  }
  box.dispatchEvent(new Event('input', {bubbles: true}));
  return true;
}

/**
 * Sets up the notes panel.
 * @param {{
 *   panel: !HTMLElement,
 *   label: function(string): string,
 *   row: function(number): ?Object,
 *   save: function(!Object, string, string): !Promise<void>,
 *   neighbour: function(number, number): ?Object,
 *   canEdit: boolean,
 *   enterSaves: function(): boolean,
 *   onShow: function(),
 *   onDone: function(number, string),
 * }} options label: a field's name; row: a cue's current row; save:
 *     sends a cell edit; neighbour: the shown cue before (-1) or after
 *     (+1) a cue; enterSaves: whether App Settings make Enter finish;
 *     onShow: called when the panel takes over the side panel; onDone:
 *     called with the cue and field when writing is finished.
 * @return {{
 *   open: function(!Object, string),
 *   follow: function(!Object),
 *   step: function(number),
 *   flush: function(): !Promise<void>,
 *   showing: function(): boolean,
 * }}
 */
export function setUpNotesPanel(options) {
  const {panel, label, row, save, neighbour, canEdit, enterSaves} = options;
  const {onShow, onDone} = options;
  let cueId = null;
  let field = '';
  let box = null;
  let preview = null;
  let status = null;
  let count = null;
  let timer = 0;
  let sent = '';

  const showing = () => panel.dataset.showing === 'cue-notes' && !panel.hidden;

  /** Saves the text if it changed since it was last sent. */
  async function flush() {
    clearTimeout(timer);
    const cue = cueId === null ? null : row(cueId);
    if (!canEdit || !box || !cue || box.value === sent) {
      return;
    }
    const text = box.value;
    sent = text;
    status.textContent = 'Saving…';
    await save(cue, field, text);
    const saved = row(cue.id)?.[field] ?? '';
    if (box && cueId === cue.id) {
      status.textContent =
        saved === text ? 'Saved' : 'Not saved: see the message under the grid';
      status.classList.toggle('notes-panel__status--error', saved !== text);
    }
  }

  /** Shows how many check-list items are ticked, when there are any. */
  function recount() {
    const {done, total} = checklist(
      box ? box.value : (row(cueId)?.[field] ?? ''),
    );
    count.hidden = !total;
    count.textContent = `${done} of ${total} done`;
  }

  /** Redraws the preview from the text. */
  function draw() {
    const text = box ? box.value : (row(cueId)?.[field] ?? '');
    preview.replaceChildren(render(text, {checkable: canEdit}));
    if (!preview.childNodes.length) {
      preview.append(make('p', 'muted', 'No notes yet.'));
    }
  }

  /**
   * Shows one mode: writing or the preview.
   * @param {string} mode 'write' or 'preview'.
   */
  function show(mode) {
    const writing = mode === 'write' && Boolean(box);
    const editor = panel.querySelector('.notes-panel__editor');
    if (editor) {
      editor.hidden = !writing;
    }
    preview.hidden = writing;
    for (const button of panel.querySelectorAll('[data-notes-mode]')) {
      button.setAttribute(
        'aria-pressed',
        String(button.dataset.notesMode === mode),
      );
    }
    if (!writing) {
      draw();
    }
  }

  /** Builds the panel for the current cue and field. */
  function build() {
    const cue = row(cueId);
    const text = cue?.[field] ?? '';
    sent = text;
    const head = make('div', 'side-panel__head');
    const title = make('h2', 'side-panel__title');
    title.append(
      make('span', 'notes-panel__cue', cue.number),
      ` ${cue.title || ''}`,
    );
    const close = make('button', 'dialog__close', '×');
    close.type = 'button';
    close.dataset.sidePanelClose = '';
    close.setAttribute('aria-label', `Close ${label(field)}`);
    head.append(title, close);

    const bar = make('div', 'notes-panel__bar');
    bar.append(make('span', 'notes-panel__field', label(field)));
    count = make('span', 'notes-panel__count');
    bar.append(count);
    const modes = make('div', 'notes-panel__modes');
    for (const [mode, name] of [
      ['write', 'Write'],
      ['preview', 'Preview'],
    ]) {
      const button = make('button', 'notes-panel__mode', name);
      button.type = 'button';
      button.dataset.notesMode = mode;
      modes.append(button);
    }
    if (canEdit) {
      bar.append(modes);
    }

    const body = make('div', 'side-panel__body notes-panel');
    preview = make('div', 'notes-panel__preview md');
    box = null;
    if (canEdit) {
      const editor = make('div', 'notes-panel__editor');
      const tools = make('div', 'notes-panel__tools');
      tools.setAttribute('role', 'toolbar');
      tools.setAttribute('aria-label', 'Formatting');
      for (const tool of TOOLS) {
        const button = make('button', 'notes-panel__tool', tool.text);
        button.type = 'button';
        button.dataset.tool = tool.name;
        const keys = tool.key ? ` (Ctrl+${tool.key.toUpperCase()})` : '';
        button.title = `${tool.label}${keys}`;
        button.setAttribute('aria-label', tool.label);
        tools.append(button);
      }
      box = make('textarea', 'notes-panel__text');
      box.value = text;
      box.spellcheck = true;
      box.setAttribute('aria-label', label(field));
      editor.append(tools, box);
      body.append(editor);
    }
    body.append(preview);
    status = make('p', 'notes-panel__status');
    status.setAttribute('role', 'status');
    status.textContent = '';
    panel.classList.add('side-panel--notes');
    panel.replaceChildren(head, bar, body, status);
    recount();
    show(canEdit ? 'write' : 'preview');
  }

  /**
   * Shows a cue's notes.
   * @param {!Object} cue The cue's row.
   * @param {string} name The notes field, e.g. 'revision_notes'.
   * @param {boolean=} focus Put the cursor in the text box.
   */
  async function open(cue, name, focus = true) {
    if (showing() && cueId === cue.id && field === name) {
      if (focus) {
        box?.focus();
      }
      return;
    }
    await flush();
    cueId = cue.id;
    field = name;
    onShow();
    panel.hidden = false;
    panel.dataset.showing = 'cue-notes';
    build();
    if (focus) {
      box?.focus();
    }
  }

  panel.addEventListener('click', (event) => {
    if (!showing()) {
      return;
    }
    const tool = event.target.closest('[data-tool]');
    const mode = event.target.closest('[data-notes-mode]');
    const check = event.target.closest('input[type="checkbox"][data-line]');
    if (tool && box) {
      apply(box, tool.dataset.tool);
      box.focus();
    } else if (mode) {
      show(mode.dataset.notesMode);
    } else if (check && canEdit) {
      const text = toggle(box ? box.value : '', Number(check.dataset.line));
      if (box) {
        box.value = text;
      }
      draw();
      recount();
      flush();
    }
  });

  panel.addEventListener('input', (event) => {
    if (event.target !== box) {
      return;
    }
    recount();
    status.textContent = 'Editing…';
    status.classList.remove('notes-panel__status--error');
    clearTimeout(timer);
    timer = setTimeout(flush, SAVE_DELAY_MS);
  });

  panel.addEventListener('focusout', (event) => {
    if (event.target === box) {
      flush();
    }
  });

  panel.addEventListener('keydown', (event) => {
    if (!showing()) {
      return;
    }
    if (
      event.altKey &&
      (event.key === 'ArrowUp' || event.key === 'ArrowDown')
    ) {
      event.preventDefault();
      step(event.key === 'ArrowUp' ? -1 : 1);
      return;
    }
    if (event.target !== box) {
      return;
    }
    const modifier = event.ctrlKey || event.metaKey;
    const tool = TOOLS.find((t) => t.key && t.key === event.key.toLowerCase());
    if (modifier && !event.shiftKey && !event.altKey && tool) {
      event.preventDefault();
      apply(box, tool.name);
    } else if (
      event.key === 'Enter' &&
      (modifier || (enterSaves() && !event.shiftKey))
    ) {
      // Ctrl+Enter, or Enter when App Settings say so: done writing.
      event.preventDefault();
      flush();
      onDone(cueId, field);
    } else if (event.key === 'Enter' && !event.shiftKey) {
      if (continueList(box)) {
        event.preventDefault();
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      flush();
      onDone(cueId, field);
    }
  });

  /**
   * Shows the same notes for another cue, once the panel is open.
   * @param {!Object} cue
   */
  function follow(cue) {
    if (showing() && cue.id !== cueId) {
      open(cue, field, false);
    }
  }

  /**
   * Moves to the shown cue before or after this one.
   * @param {number} delta -1 or +1.
   */
  async function step(delta) {
    const next = cueId === null ? null : neighbour(cueId, delta);
    if (next) {
      await open(next, field, document.activeElement === box);
    }
  }

  return {open, follow, step, flush, showing};
}
