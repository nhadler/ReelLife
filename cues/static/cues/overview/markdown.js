/**
 * @fileoverview A small Markdown renderer for cue notes: headings,
 * paragraphs, bulleted, numbered and check lists, quotes, bold, italic,
 * code and links. It builds DOM nodes and never parses HTML, so a note's
 * text can't become markup; links go only to http(s) and mailto.
 */

const HEADING = /^(#{1,3})\s+(.*)$/;
const CHECK = /^\s*[-*+]\s+\[([ xX])\]\s?(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBER = /^\s*\d{1,9}[.)]\s+(.*)$/;
const QUOTE = /^>\s?(.*)$/;
// Code, bold, italic, a [link](url), or a bare URL, in that order.
const INLINE =
  /`([^`]+)`|\*\*(.+?)\*\*|__(.+?)__|\*([^*\s][^*]*?)\*|_([^_\s][^_]*?)_|\[([^\]]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/;
const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);

/**
 * A link's address, if it's a web or mail address.
 * @param {string} text
 * @return {?string} The address as the browser reads it, else null.
 */
function safeUrl(text) {
  if (!/^(https?:\/\/|mailto:)/i.test(text)) {
    return null;
  }
  try {
    const url = new URL(text);
    return SAFE_PROTOCOLS.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

/**
 * A note's check-list items: how many there are and how many are ticked.
 * @param {?string} text
 * @return {{done: number, total: number}}
 */
export function checklist(text) {
  let done = 0;
  let total = 0;
  for (const line of (text || '').split('\n')) {
    const match = CHECK.exec(line);
    if (match) {
      total += 1;
      done += match[1] === ' ' ? 0 : 1;
    }
  }
  return {done, total};
}

/**
 * Ticks or unticks the check-list item on one line.
 * @param {string} text
 * @param {number} index The line's index.
 * @return {string} The text with that box flipped.
 */
export function toggle(text, index) {
  const lines = text.split('\n');
  const match = CHECK.exec(lines[index] ?? '');
  if (match) {
    const mark = match[1] === ' ' ? 'x' : ' ';
    lines[index] = lines[index].replace(/\[[ xX]\]/, `[${mark}]`);
  }
  return lines.join('\n');
}

/**
 * Appends a line's inline formatting to an element.
 * @param {!Node} parent
 * @param {string} text
 */
function inline(parent, text) {
  let rest = text;
  while (rest) {
    const match = INLINE.exec(rest);
    if (!match) {
      parent.append(rest);
      return;
    }
    parent.append(rest.slice(0, match.index));
    const [whole, code, bold, bold2, em, em2, label, href, bare] = match;
    if (code !== undefined) {
      const node = document.createElement('code');
      node.textContent = code;
      parent.append(node);
    } else if (bold !== undefined || bold2 !== undefined) {
      const node = document.createElement('strong');
      inline(node, bold ?? bold2);
      parent.append(node);
    } else if (em !== undefined || em2 !== undefined) {
      const node = document.createElement('em');
      inline(node, em ?? em2);
      parent.append(node);
    } else {
      const url = safeUrl(href ?? bare);
      if (url) {
        const link = document.createElement('a');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = label ?? bare;
        parent.append(link);
      } else {
        parent.append(whole);
      }
    }
    rest = rest.slice(match.index + whole.length);
  }
}

/**
 * Renders a note.
 * @param {?string} text The note's Markdown.
 * @param {{checkable: (boolean|undefined)}=} options checkable: the
 *     check-list boxes can be clicked (they carry data-line, the item's
 *     line index); otherwise they are disabled.
 * @return {!DocumentFragment}
 */
export function render(text, options = {}) {
  const out = document.createDocumentFragment();
  let list = null;
  let paragraph = null;
  const close = () => {
    list = null;
    paragraph = null;
  };
  const listOf = (tag, className) => {
    if (list?.tagName !== tag.toUpperCase() || list.className !== className) {
      paragraph = null;
      list = document.createElement(tag);
      list.className = className;
      out.append(list);
    }
    return list;
  };
  (text || '').split('\n').forEach((line, index) => {
    let match;
    if (!line.trim()) {
      close();
    } else if ((match = HEADING.exec(line))) {
      close();
      const node = document.createElement('p');
      node.className = `md-heading md-heading--${match[1].length}`;
      inline(node, match[2]);
      out.append(node);
    } else if ((match = CHECK.exec(line))) {
      const item = document.createElement('li');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = match[1] !== ' ';
      box.disabled = !options.checkable;
      box.dataset.line = String(index);
      box.setAttribute('aria-label', match[2] || 'Item');
      const label = document.createElement('span');
      inline(label, match[2]);
      item.append(box, label);
      item.classList.toggle('md-check--done', box.checked);
      listOf('ul', 'md-checklist').append(item);
    } else if ((match = BULLET.exec(line))) {
      const item = document.createElement('li');
      inline(item, match[1]);
      listOf('ul', 'md-list').append(item);
    } else if ((match = NUMBER.exec(line))) {
      const item = document.createElement('li');
      inline(item, match[1]);
      listOf('ol', 'md-list').append(item);
    } else if ((match = QUOTE.exec(line))) {
      close();
      const node = document.createElement('blockquote');
      inline(node, match[1]);
      out.append(node);
    } else if (paragraph) {
      paragraph.append(document.createElement('br'));
      inline(paragraph, line);
    } else {
      list = null;
      paragraph = document.createElement('p');
      inline(paragraph, line);
      out.append(paragraph);
    }
  });
  return out;
}
