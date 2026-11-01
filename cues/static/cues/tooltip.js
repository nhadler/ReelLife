/**
 * @fileoverview Styled tooltips for every element with a title.
 *
 * The browser's own title tooltip is slow and plain. This takes the
 * title over the first time the pointer or keyboard focus reaches the
 * element (moving it to data-tip, and to aria-label when the element has
 * no other name, so screen readers lose nothing), then shows it in a
 * box after a short delay, with a trailing shortcut such as "(Ctrl+N)"
 * drawn as a key. SVG <title> elements and AG Grid's own tooltips are
 * left alone.
 */

const DELAY_MS = 350;
const GAP_PX = 6;
const EDGE_PX = 8;
// "Find a cue (Ctrl+F)": a label and a shortcut in brackets at the end.
const SHORTCUT = /^(.*?)\s*\(((?:Ctrl|Shift|Alt|[⌘⌃⌥⇧])[^()]*|[A-Z0-9])\)$/s;

let tip = null;
let timer = 0;
let current = null;

/**
 * Whether an element has a name besides its title.
 * @param {!Element} element
 * @return {boolean}
 */
function hasName(element) {
  return Boolean(
    element.getAttribute('aria-label') ||
    element.getAttribute('aria-labelledby') ||
    element.textContent.trim() ||
    element.labels?.length ||
    element.getAttribute('alt'),
  );
}

/**
 * The element's tooltip text, taking over a title attribute if present.
 * @param {!Element} element
 * @return {string}
 */
function adopt(element) {
  const title = element.getAttribute('title');
  if (title !== null) {
    element.removeAttribute('title');
    if (title && !hasName(element)) {
      element.setAttribute('aria-label', title);
    }
    element.dataset.tip = title;
  }
  return element.dataset.tip || '';
}

/**
 * The nearest element with a tooltip, from an event target.
 * @param {?EventTarget} node
 * @return {?Element}
 */
function owner(node) {
  return node instanceof Element
    ? node.closest('[title]:not(svg *), [data-tip]')
    : null;
}

/**
 * Whether the element already shows the tooltip's words, e.g. a labelled
 * link whose label is hidden only in narrow windows.
 * @param {!Element} element
 * @param {string} text
 * @return {boolean}
 */
function saysIt(element, text) {
  return [...element.querySelectorAll('span')].some(
    (span) =>
      span.textContent.trim() === text &&
      span.getBoundingClientRect().width > 1,
  );
}

/**
 * Fills the box: the label, then the shortcut as a key.
 * @param {!HTMLElement} box
 * @param {string} text
 */
function fill(box, text) {
  const match = SHORTCUT.exec(text);
  if (!match || !match[1]) {
    box.textContent = text;
    return;
  }
  const key = document.createElement('kbd');
  key.className = 'tooltip__key';
  key.textContent = match[2];
  box.replaceChildren(match[1], key);
}

/**
 * Keeps a box of `size` inside the window along one axis.
 * @param {number} start
 * @param {number} size
 * @param {number} room
 * @return {number}
 */
function clamp(start, size, room) {
  return Math.min(Math.max(start, EDGE_PX), room - size - EDGE_PX);
}

/**
 * Under the element, centred; above it when there's no room below.
 * @param {!DOMRect} rect The element.
 * @param {!DOMRect} box The tooltip.
 * @return {{top: number, left: number}}
 */
function below(rect, box) {
  let top = rect.bottom + GAP_PX;
  if (top + box.height > window.innerHeight - EDGE_PX) {
    top = rect.top - GAP_PX - box.height;
  }
  return {top, left: rect.left + rect.width / 2 - box.width / 2};
}

/**
 * Beside a menu item, so it doesn't cover the next one.
 * @param {!DOMRect} rect The element.
 * @param {!DOMRect} box The tooltip.
 * @return {{top: number, left: number}}
 */
function beside(rect, box) {
  let left = rect.right + GAP_PX;
  if (left + box.width > window.innerWidth - EDGE_PX) {
    left = rect.left - GAP_PX - box.width;
  }
  return {top: rect.top + rect.height / 2 - box.height / 2, left};
}

/** Hides the box and forgets the element. */
function hide() {
  clearTimeout(timer);
  if (current) {
    current.removeAttribute('aria-describedby');
  }
  current = null;
  if (tip) {
    tip.hidden = true;
  }
}

/**
 * Shows the box under the element (above it near the bottom edge).
 * @param {!Element} element
 */
function show(element) {
  const text = adopt(element);
  if (!text || !element.isConnected || saysIt(element, text)) {
    return;
  }
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'tooltip';
    tip.id = 'app-tooltip';
    tip.setAttribute('role', 'tooltip');
  }
  // Inside a modal dialog, the box must be too, or the dialog covers it.
  const host = element.closest('dialog[open]') || document.body;
  if (tip.parentElement !== host) {
    host.append(tip);
  }
  fill(tip, text);
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  const rect = element.getBoundingClientRect();
  const {top, left} = element.closest('[role="menu"], .menu__panel')
    ? beside(rect, box)
    : below(rect, box);
  tip.style.top = `${clamp(top, box.height, window.innerHeight)}px`;
  tip.style.left = `${clamp(left, box.width, window.innerWidth)}px`;
  if (element.getAttribute('aria-label') !== text) {
    element.setAttribute('aria-describedby', tip.id);
  }
}

/**
 * Shows an element's tooltip after the delay.
 * @param {?Element} element
 */
function schedule(element) {
  if (element === current) {
    return;
  }
  hide();
  if (!element) {
    return;
  }
  adopt(element); // at once, so the browser never shows its own
  current = element;
  timer = setTimeout(() => show(element), DELAY_MS);
}

document.addEventListener('pointerover', (event) => {
  if (event.pointerType !== 'touch') {
    schedule(owner(event.target));
  }
});
document.addEventListener('pointerout', (event) => {
  if (current && !current.contains(event.relatedTarget)) {
    hide();
  }
});
document.addEventListener('focusin', (event) => {
  const element = owner(event.target);
  if (element && element.matches(':focus-visible')) {
    schedule(element);
  }
});
document.addEventListener('focusout', hide);
document.addEventListener('pointerdown', hide, true);
document.addEventListener('scroll', hide, true);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && current) {
    hide();
  }
});
