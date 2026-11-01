/**
 * @fileoverview Right-click menus for the grid's cells and headers.
 * AG Grid's own context and column menus are Enterprise
 * features; this draws a small menu at the pointer instead. Escape, a
 * click elsewhere or scrolling closes it; arrow keys move between items.
 */

import {keys} from '../keys.js';

/** @type {?HTMLElement} */
let open = null;
/** @type {number} When the menu opened (ms). */
let openedAt = 0;
// The grid scrolls the clicked cell into view just after a right-click;
// scrolls this soon after opening don't close the menu.
const SETTLE_MS = 300;

/** Closes the open menu, if any. */
export function closeMenu() {
  open?.remove();
  open = null;
}

/**
 * Shows a menu at a point.
 * @param {number} x Client X.
 * @param {number} y Client Y.
 * @param {!Array<?Object>} items {label, shortcut?, action, disabled?};
 *     null draws a rule.
 */
export function showMenu(x, y, items) {
  closeMenu();
  // Escape puts focus back where the menu was opened from.
  const opener = document.activeElement;
  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.setAttribute('role', 'menu');
  for (const item of items) {
    if (item === null) {
      const rule = document.createElement('hr');
      rule.className = 'context-menu__rule';
      menu.append(rule);
      continue;
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'menu__item';
    button.setAttribute('role', 'menuitem');
    button.disabled = Boolean(item.disabled);
    button.append(item.label);
    if (item.shortcut) {
      const kbd = document.createElement('kbd');
      kbd.textContent = keys(item.shortcut);
      button.append(kbd);
    }
    button.addEventListener('click', () => {
      closeMenu();
      item.action();
    });
    menu.append(button);
  }
  menu.addEventListener('keydown', (event) => {
    const buttons = [...menu.querySelectorAll('button:not([disabled])')];
    const at = buttons.indexOf(document.activeElement);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      buttons[(at + step + buttons.length) % buttons.length].focus();
    } else if (event.key === 'Escape') {
      event.stopPropagation();
      closeMenu();
      if (opener instanceof HTMLElement && opener.isConnected) {
        opener.focus();
      }
    }
  });
  document.body.append(menu);
  // Keep it on screen.
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, window.innerWidth - box.width - 8)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - box.height - 8)}px`;
  open = menu;
  openedAt = performance.now();
  menu.querySelector('button:not([disabled])')?.focus();
}

document.addEventListener('mousedown', (event) => {
  if (open && !open.contains(event.target)) {
    closeMenu();
  }
});
document.addEventListener(
  'scroll',
  () => {
    if (performance.now() - openedAt > SETTLE_MS) {
      closeMenu();
    }
  },
  true,
);
