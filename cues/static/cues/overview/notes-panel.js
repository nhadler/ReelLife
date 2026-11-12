/**
 * @fileoverview One cue's notes (Revision Notes, say) in the panel beside
 * the grid, edited as formatted text (TipTap) and stored as Markdown:
 * a toolbar, check boxes that tick, and saving as you go. Clicking
 * another row's cell shows that cue's notes; Alt+↑/↓ steps through the
 * shown cues.
 */

import {checklist} from './markdown.js';
import {
  Editor,
  Markdown,
  Marked,
  Placeholder,
  StarterKit,
  TaskItem,
  TaskList,
} from '../vendor/tiptap.min.js';

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

const SAFE_LINK = /^(https?:\/\/|mailto:)/i;
// A ballot-box line pasted from elsewhere: "☐ item", maybe after a dash.
const BALLOT = /^(\s*)(?:[-*+]\s+)?([☐☑☒])\s?(.*)$/u;
// A list item's marker after its indent.
const LIST_LINE = /^( +)([-*+]|\d{1,9}[.)])\s/;
const ENTITIES = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"'};

// A [label](address) link's address.
const LINK_ADDRESS = /^!?\[(?:\\.|[^\]\\])*\]\(\s*<?([^\s)>]*)/;

/**
 * The editor's Markdown parser, reading notes as the grid does: HTML is
 * text, never markup (a note saying <img onerror=…> shows those
 * characters), a link to anything but a web or mail address is text,
 * and line breaks within a paragraph stay line breaks.
 * @return {!Object} A marked instance.
 */
function parser() {
  const marked = new Marked({gfm: true, breaks: true});
  const noMatch = () => undefined;
  marked.use({
    tokenizer: {
      html: noMatch,
      tag: noMatch,
      link(src) {
        const address = LINK_ADDRESS.exec(src)?.[1];
        // undefined: not a link, so text; false: marked's own reading.
        return address !== undefined && !SAFE_LINK.test(address)
          ? undefined
          : false;
      },
    },
  });
  return marked;
}

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
 * A stored note as the editor reads it. Ballot boxes become check-list
 * items, and list lines indented under a paragraph (which Markdown would
 * run into the paragraph) become a list, as the grid shows them.
 * @param {string} text
 * @return {string}
 */
export function toEditor(text) {
  const lines = (text || '').split('\n');
  let levels = [];
  return lines
    .map((line) => {
      const ballot = BALLOT.exec(line);
      if (ballot) {
        const mark = ballot[2] === '☐' ? ' ' : 'x';
        line = `${ballot[1]}- [${mark}] ${ballot[3]}`;
      }
      const list = LIST_LINE.exec(line);
      if (!list) {
        if (line.trim()) {
          levels = [];
        }
        return line;
      }
      // Each deeper indent in this list is one level in.
      const indent = list[1].length;
      levels = levels.filter((level) => level <= indent);
      if (!levels.includes(indent)) {
        levels.push(indent);
      }
      return '    '.repeat(levels.length - 1) + line.trimStart();
    })
    .join('\n');
}

/**
 * The editor's Markdown as stored: characters it wrote as HTML entities
 * go back to themselves, so exports and the grid read plainly.
 * @param {string} markdown
 * @return {string}
 */
export function fromEditor(markdown) {
  return markdown
    .replace(/&(amp|lt|gt|quot);/g, (entity) => ENTITIES[entity])
    .replace(/\s+$/, '');
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
  let editor = null;
  let status = null;
  let count = null;
  let linkForm = null;
  // The text the link field applies to, kept while focus is in the field.
  let linkRange = null;
  let timer = 0;
  let sent = '';
  // The note as the editor reads it on opening; saved only once edits
  // change that (TipTap's own tidying on load isn't an edit).
  let baseline = '';
  let ready = false;
  let edited = false;

  const showing = () => panel.dataset.showing === 'cue-notes' && !panel.hidden;
  const current = () => (editor ? fromEditor(editor.getMarkdown()) : sent);

  /** Saves the text if it was edited and changed since it was last sent. */
  async function flush() {
    clearTimeout(timer);
    const cue = cueId === null ? null : row(cueId);
    if (!canEdit || !editor || !cue || !edited) {
      return;
    }
    const text = current();
    if (text === sent) {
      return;
    }
    sent = text;
    status.textContent = 'Saving…';
    await save(cue, field, text);
    const saved = row(cue.id)?.[field] ?? '';
    if (editor && cueId === cue.id) {
      status.textContent =
        saved === text ? 'Saved' : 'Not saved: see the message under the grid';
      status.classList.toggle('notes-panel__status--error', saved !== text);
    }
  }

  /** Shows how many check-list items are ticked, when there are any. */
  function recount() {
    const {done, total} = checklist(edited ? current() : sent);
    count.hidden = !total;
    count.textContent = `${done} of ${total} done`;
  }

  /** Marks the toolbar's buttons that apply where the cursor is. */
  function markTools() {
    for (const button of panel.querySelectorAll('[data-tool]')) {
      const active = {
        bold: () => editor.isActive('bold'),
        italic: () => editor.isActive('italic'),
        heading: () => editor.isActive('heading'),
        bullets: () => editor.isActive('bulletList'),
        checks: () => editor.isActive('taskList'),
        link: () => editor.isActive('link'),
      }[button.dataset.tool];
      button.setAttribute('aria-pressed', String(Boolean(active?.())));
    }
  }

  /**
   * The selected text's range. Read from the browser: a selection made
   * with the keyboard reaches the editor's state a moment later, and
   * Ctrl+K can come first.
   * @return {{from: number, to: number}}
   */
  function selected() {
    const dom = window.getSelection();
    const view = editor.view;
    const {from, to} = editor.state.selection;
    if (dom?.anchorNode && view.dom.contains(dom.anchorNode)) {
      const anchor = view.posAtDOM(dom.anchorNode, dom.anchorOffset);
      const head = view.posAtDOM(dom.focusNode, dom.focusOffset);
      // The browser's, unless it lost a selection the editor still has.
      if (anchor !== head || from === to) {
        return {from: Math.min(anchor, head), to: Math.max(anchor, head)};
      }
    }
    return {from, to};
  }

  /** Opens the link field, filled with the link at the cursor if any. */
  function askLink() {
    linkRange = selected();
    linkForm.hidden = false;
    const input = linkForm.querySelector('input');
    input.value = editor.getAttributes('link').href || '';
    input.focus();
    input.select();
  }

  /** Closes the link field and goes back to the text. */
  function closeLink() {
    linkForm.hidden = true;
    editor.commands.focus();
  }

  /**
   * Links the selection (or the word at the cursor's link) to an address;
   * with nothing selected, adds the address itself as a link.
   * @param {string} address
   */
  function setLink(address) {
    const href = address.trim();
    const chain = editor.chain().focus();
    if (linkRange) {
      chain.setTextSelection(linkRange);
    }
    chain.extendMarkRange('link');
    if (!href) {
      chain.unsetLink().run();
    } else if (!SAFE_LINK.test(href)) {
      status.textContent = 'Links start with https://, http:// or mailto:';
      return;
    } else if (linkRange?.from === linkRange?.to && !editor.isActive('link')) {
      chain
        .insertContent({
          type: 'text',
          text: href,
          marks: [{type: 'link', attrs: {href}}],
        })
        .run();
    } else {
      chain.setLink({href}).run();
    }
    linkForm.hidden = true;
  }

  /**
   * Applies a toolbar action.
   * @param {string} name A TOOLS name.
   */
  function apply(name) {
    const chain = editor.chain().focus();
    if (name === 'bold') {
      chain.toggleBold().run();
    } else if (name === 'italic') {
      chain.toggleItalic().run();
    } else if (name === 'heading') {
      chain.toggleHeading({level: 2}).run();
    } else if (name === 'bullets') {
      chain.toggleBulletList().run();
    } else if (name === 'checks') {
      chain.toggleTaskList().run();
    } else if (name === 'link') {
      askLink();
    }
  }

  /** Builds the panel for the current cue and field. */
  function build() {
    editor?.destroy();
    ready = false;
    const cue = row(cueId);
    sent = cue?.[field] ?? '';
    edited = false;
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

    const body = make('div', 'side-panel__body notes-panel');
    if (canEdit) {
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
      linkForm = make('form', 'notes-panel__link');
      linkForm.hidden = true;
      const input = make('input');
      input.type = 'url';
      input.placeholder = 'https://';
      input.setAttribute('aria-label', 'Link Address');
      const apply = make('button', 'button button--small', 'Link');
      apply.type = 'submit';
      const remove = make(
        'button',
        'button button--small button--quiet',
        'Remove',
      );
      remove.type = 'button';
      remove.dataset.linkRemove = '';
      linkForm.append(input, apply, remove);
      body.append(tools, linkForm);
    }
    const host = make('div', 'notes-panel__editor');
    body.append(host);
    status = make('p', 'notes-panel__status');
    status.setAttribute('role', 'status');
    panel.classList.add('side-panel--notes');
    panel.replaceChildren(head, bar, body, status);

    editor = new Editor({
      element: host,
      injectCSS: false,
      editable: canEdit,
      extensions: [
        // Only what the grid's Markdown shows too (overview/markdown.js).
        StarterKit.configure({
          codeBlock: false,
          strike: false,
          underline: false,
          heading: {levels: [1, 2, 3]},
          link: {
            openOnClick: !canEdit,
            autolink: true,
            defaultProtocol: 'https',
            isAllowedUri: (url) => SAFE_LINK.test(url),
            HTMLAttributes: {rel: 'noopener noreferrer', target: '_blank'},
          },
        }),
        TaskList,
        TaskItem.configure({
          nested: true,
          a11y: {checkboxLabel: (node) => node.textContent || 'Item'},
        }),
        Placeholder.configure({
          placeholder: canEdit ? 'Write notes…' : 'No notes yet.',
        }),
        Markdown.configure({marked: parser()}),
      ],
      content: toEditor(sent),
      contentType: 'markdown',
      editorProps: {
        attributes: {
          class: 'md notes-panel__doc',
          'aria-label': label(field),
          'aria-multiline': 'true',
          role: 'textbox',
          spellcheck: 'true',
        },
      },
      onUpdate: () => {
        if (!ready) {
          return; // TipTap tidying the note while it loads
        }
        edited = current() !== baseline;
        if (!edited) {
          return;
        }
        recount();
        status.textContent = 'Editing…';
        status.classList.remove('notes-panel__status--error');
        clearTimeout(timer);
        timer = setTimeout(flush, SAVE_DELAY_MS);
      },
      onSelectionUpdate: markTools,
      onTransaction: markTools,
      onBlur: ({event}) => {
        // Not when focus moves to the toolbar or link field.
        if (!panel.contains(event.relatedTarget)) {
          flush();
        }
      },
    });
    baseline = current();
    ready = true;
    if (!canEdit) {
      for (const box of host.querySelectorAll('input[type="checkbox"]')) {
        box.disabled = true;
      }
    }
    recount();
    markTools();
  }

  /**
   * Shows a cue's notes.
   * @param {!Object} cue The cue's row.
   * @param {string} name The notes field, e.g. 'revision_notes'.
   * @param {boolean=} focus Put the cursor in the notes.
   */
  async function open(cue, name, focus = true) {
    if (showing() && cueId === cue.id && field === name) {
      if (focus) {
        editor?.commands.focus();
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
    if (focus && canEdit) {
      editor.commands.focus('end');
    }
  }

  panel.addEventListener('mousedown', (event) => {
    // Keep the text's selection when a tool is clicked.
    if (showing() && event.target.closest('[data-tool]')) {
      event.preventDefault();
    }
  });

  panel.addEventListener('click', (event) => {
    if (!showing() || !editor) {
      return;
    }
    const tool = event.target.closest('[data-tool]');
    if (tool) {
      apply(tool.dataset.tool);
    } else if (event.target.closest('[data-link-remove]')) {
      setLink('');
    }
  });

  panel.addEventListener('change', (event) => {
    if (
      canEdit &&
      editor?.view.dom.contains(event.target) &&
      event.target.matches('input[type="checkbox"]')
    ) {
      // Ticked: a cursor at the end of the item's text once TipTap has
      // taken the change, so typing goes on there rather than to the
      // grid's shortcuts, and Enter doesn't replace the whole item.
      const text = event.target.closest('li')?.querySelector(':scope > div');
      setTimeout(() => {
        if (!editor || !text?.isConnected) {
          editor?.commands.focus();
          return;
        }
        const end = editor.view.posAtDOM(text, text.childNodes.length) - 1;
        editor.commands.focus(end);
      });
    }
  });

  panel.addEventListener('submit', (event) => {
    if (event.target === linkForm) {
      event.preventDefault();
      setLink(linkForm.querySelector('input').value);
    }
  });

  panel.addEventListener('focusout', (event) => {
    // Leaving the link field for somewhere outside the panel.
    if (
      event.target.closest?.('.notes-panel__link') &&
      !panel.contains(event.relatedTarget)
    ) {
      flush();
    }
  });

  // Capture: these keys mean something to the panel before the editor
  // turns them into new lines.
  panel.addEventListener(
    'keydown',
    (event) => {
      if (!showing() || !editor) {
        return;
      }
      if (
        event.altKey &&
        (event.key === 'ArrowUp' || event.key === 'ArrowDown')
      ) {
        event.preventDefault();
        event.stopPropagation();
        step(event.key === 'ArrowUp' ? -1 : 1);
        return;
      }
      if (linkForm && !linkForm.hidden && linkForm.contains(event.target)) {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          closeLink();
        }
        return;
      }
      if (!editor.view.dom.contains(event.target)) {
        return;
      }
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && !event.shiftKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        event.stopPropagation();
        askLink();
      } else if (
        event.key === 'Enter' &&
        (modifier || (enterSaves() && !event.shiftKey))
      ) {
        // Ctrl+Enter, or Enter when App Settings say so: done writing.
        event.preventDefault();
        event.stopPropagation();
        flush();
        onDone(cueId, field);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        flush();
        onDone(cueId, field);
      }
    },
    true,
  );

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
      const focused = editor?.view.dom.contains(document.activeElement);
      await open(next, field, Boolean(focused));
    }
  }

  return {open, follow, step, flush, showing};
}
