/**
 * @fileoverview Notices when one of the page's scripts can't be fetched.
 *
 * A dropped request for one script makes the browser skip it, and every
 * module that imports it, without a word: the cue grid would stay empty.
 * This reloads the page once to fetch them again, and if a script still
 * fails within half a minute, says so instead. A classic script loaded
 * before the others, so it's listening before any of them can fail.
 */

(() => {
  const KEY = 'reellife.reloadedForScript';
  const RETRY_WINDOW = 30000;
  const MESSAGE = "Part of this page didn't load. Reload the page.";

  /**
   * Remembers this reload; false when the browser won't let us, so the
   * page can't reload over and over.
   * @return {boolean}
   */
  function markReload() {
    try {
      const last = Number(sessionStorage.getItem(KEY)) || 0;
      if (Date.now() - last < RETRY_WINDOW) {
        return false;
      }
      sessionStorage.setItem(KEY, String(Date.now()));
      return true;
    } catch {
      return false;
    }
  }

  /** Shows the message at the top of the page, once. */
  function tell() {
    const main = document.getElementById('main');
    if (!main || document.getElementById('load-guard')) {
      return;
    }
    const list = document.createElement('ul');
    list.className = 'messages';
    list.id = 'load-guard';
    const item = document.createElement('li');
    item.className = 'error';
    item.setAttribute('role', 'alert');
    item.textContent = MESSAGE;
    list.append(item);
    main.prepend(list);
  }

  window.addEventListener(
    'error',
    (event) => {
      if (!(event.target instanceof HTMLScriptElement)) {
        return;
      }
      if (markReload()) {
        window.location.reload();
      } else if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tell, {once: true});
      } else {
        tell();
      }
    },
    true,
  );
})();
