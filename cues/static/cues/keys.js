/**
 * @fileoverview Keyboard hints in the terms of the computer in use.
 *
 * Hints are written as on Windows: "Ctrl+Shift+S" means Ctrl, or ⌘ on
 * a Mac, since the handlers take either. "Control+N" means the Control
 * key on every computer, for the few shortcuts Chrome on a Mac keeps
 * for itself with ⌘ (⌘N, ⌘1–9, ⌘⇧=). On a Mac hints read the Mac way,
 * symbols in the order ⌃⌥⇧⌘ and no plus signs ("⇧⌘S", "⌃N"); elsewhere
 * "Control" reads "Ctrl".
 *
 * Page load and every htmx swap rewrite title attributes, <kbd> text,
 * data-palette-keys and the text of elements marked data-keys (server
 * sentences such as "Undo (Ctrl+Z) brings them back."); code that
 * builds hints calls `keys()` itself.
 */

/** Whether this is a Mac (or iPad with a keyboard). */
// Chrome reports "macOS"; Safari and Firefox "MacIntel".
export const IS_MAC = /mac|iphone|ipad/i.test(
  navigator.userAgentData?.platform || navigator.platform || '',
);

// "Ctrl+Shift+S", "Control+1–9", "Ctrl+Shift+=", "Ctrl+Shift+⌫".
const SHORTCUT = /\b(?:(?:Control|Ctrl|Shift|Alt)\+)+(?:\+|[^\s)+,;:]+)/g;

const MAC_SYMBOLS = {Control: '⌃', Alt: '⌥', Shift: '⇧', Ctrl: '⌘'};
const MAC_ORDER = ['Control', 'Alt', 'Shift', 'Ctrl'];

/**
 * One shortcut in this computer's terms.
 * @param {string} shortcut E.g. "Ctrl+Shift+S".
 * @return {string} E.g. "⇧⌘S" on a Mac.
 */
function one(shortcut) {
  const parts = shortcut.split('+');
  // "Ctrl+Shift++" splits into a trailing empty pair: the key is "+".
  const key = shortcut.endsWith('++') ? '+' : parts.pop();
  const modifiers = parts.filter(Boolean);
  if (!IS_MAC) {
    return [...modifiers.map((m) => (m === 'Control' ? 'Ctrl' : m)), key].join(
      '+',
    );
  }
  const symbols = MAC_ORDER.filter((m) => modifiers.includes(m)).map(
    (m) => MAC_SYMBOLS[m],
  );
  return symbols.join('') + key;
}

/**
 * Rewrites the shortcuts in a piece of text.
 * @param {string} text E.g. "New cue (Control+N)".
 * @return {string} E.g. "New cue (⌃N)" on a Mac, "New cue (Ctrl+N)" else.
 */
export function keys(text) {
  return text.replace(SHORTCUT, one);
}

/**
 * Rewrites the hints under an element.
 * @param {!Document|!Element} root The page, or newly swapped-in content.
 */
export function localize(root) {
  const scope = root.querySelectorAll ? root : document;
  for (const element of scope.querySelectorAll('[title]')) {
    element.title = keys(element.title);
  }
  for (const element of scope.querySelectorAll('[data-palette-keys]')) {
    element.dataset.paletteKeys = keys(element.dataset.paletteKeys);
  }
  for (const element of scope.querySelectorAll('kbd')) {
    element.textContent = keys(element.textContent);
  }
  for (const element of scope.querySelectorAll('[data-keys]')) {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      node.data = keys(node.data);
    }
  }
}

localize(document);
document.body?.addEventListener('htmx:afterSettle', (event) =>
  localize(event.target),
);
