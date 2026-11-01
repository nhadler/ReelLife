/**
 * @fileoverview Folds that stay open or shut: a <details data-remember=
 * "name"> keeps its state in the browser, also across htmx swaps of the
 * part of the page it sits in (the project header is re-fetched after
 * edits).
 */

const PREFIX = 'reellife.open.';

/**
 * @param {string} name
 * @return {?string}
 */
function stored(name) {
  try {
    return window.localStorage.getItem(PREFIX + name);
  } catch {
    return null; // storage blocked: every fold starts shut
  }
}

/** Opens or shuts every remembered fold as it was last left. */
function restore() {
  for (const fold of document.querySelectorAll('details[data-remember]')) {
    fold.open = stored(fold.dataset.remember) === '1';
  }
}

document.addEventListener(
  'toggle',
  (event) => {
    const fold = event.target;
    if (!(fold instanceof HTMLDetailsElement) || !fold.dataset.remember) {
      return;
    }
    try {
      window.localStorage.setItem(
        PREFIX + fold.dataset.remember,
        fold.open ? '1' : '0',
      );
    } catch {
      // storage blocked: the fold just won't be remembered
    }
  },
  true, // toggle doesn't bubble
);
document.body.addEventListener('htmx:afterSettle', restore);
restore();
