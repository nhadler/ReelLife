/**
 * @fileoverview Pop-up menus built from <details class="menu">.
 *
 * <details> opens and closes on its own; this makes it behave like a
 * menu: a click elsewhere closes it, and so does Escape (returning focus
 * to its button). Items that act (buttons and links) close it too, but
 * controls inside a panel (checkboxes, inputs) leave it open. In a panel
 * of .menu__item entries, ↑ and ↓ move between them, as in the grid's
 * context menu. An open panel never runs past the bottom of the window:
 * it gets the height left below it and scrolls inside.
 */

/**
 * Closes open menus, except the one holding `keep`.
 * @param {?Node=} keep
 */
function closeMenus(keep = null) {
  for (const menu of document.querySelectorAll('details.menu[open]')) {
    if (!keep || !menu.contains(keep)) {
      menu.removeAttribute('open');
    }
  }
}

document.addEventListener('click', (event) => {
  const menu = event.target.closest('details.menu');
  closeMenus(menu);
  const acts = event.target.closest('button, a');
  if (menu && acts && !event.target.closest('summary, [data-keep-open]')) {
    menu.removeAttribute('open');
  }
});

// Registered before the pages' own key handlers, so Escape closes a menu
// without also doing what the page does on Escape.
document.addEventListener('keydown', (event) => {
  const menu = document.querySelector('details.menu[open]');
  if (event.key === 'Escape' && menu) {
    menu.removeAttribute('open');
    menu.querySelector('summary').focus();
    event.stopImmediatePropagation();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') {
    return;
  }
  const menu = event.target.closest?.('details.menu[open]');
  if (!menu || event.target.matches('input, select, textarea')) {
    return;
  }
  const items = [
    ...menu.querySelectorAll('.menu__item:not([disabled]):not([hidden])'),
  ];
  if (!items.length) {
    return;
  }
  event.preventDefault();
  const at = items.indexOf(event.target);
  const step = event.key === 'ArrowDown' ? 1 : -1;
  // From the menu's button, ↓ goes to the first item and ↑ to the last.
  const to = at === -1 ? (step === 1 ? 0 : items.length - 1) : at + step;
  items[(to + items.length) % items.length].focus();
});

// Room below the panel, less a margin; never squeezed below this.
const EDGE = 16;
const LEAST = 192;

// toggle doesn't bubble, so listen while it travels down.
document.addEventListener(
  'toggle',
  (event) => {
    const menu = event.target;
    if (!(menu instanceof HTMLDetailsElement) || !menu.open) {
      return;
    }
    const panel = menu.querySelector(':scope > .menu__panel');
    if (!panel) {
      return;
    }
    const room = window.innerHeight - panel.getBoundingClientRect().top - EDGE;
    panel.style.setProperty('--panel-room', `${Math.max(room, LEAST)}px`);
  },
  true,
);
