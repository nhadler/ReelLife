/**
 * @fileoverview Pop-up menus built from <details class="menu">.
 *
 * <details> opens and closes on its own; this makes it behave like a
 * menu: a click elsewhere closes it, and so does Escape (returning focus
 * to its button). Items that act (buttons and links) close it too, but
 * controls inside a panel (checkboxes, inputs) leave it open. In a panel
 * of .menu__item entries, ↑ and ↓ move between them, as in the grid's
 * context menu.
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
