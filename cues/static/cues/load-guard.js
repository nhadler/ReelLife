/**
 * @fileoverview Notices when one of the page's scripts can't be fetched.
 *
 * A dropped request for one script makes the browser skip it, and every
 * module that imports it, without a word: the cue grid would stay empty.
 * A request that never gets an answer is worse: nothing fails, the page
 * just never finishes loading (it waits for its scripts). Either way this
 * reloads the page once to fetch them again, and if that happens again
 * within half a minute, says so instead. A classic script loaded before
 * the others, so it's listening before any of them can fail.
 */

(() => {
  const KEY = 'reellife.reloadedForScript';
  const RETRY_WINDOW = 30000;
  const MESSAGE = "Part of this page didn't load. Reload the page.";
  // How long the page may take to run its scripts before one counts as
  // stuck. Generous: a first visit over a slow link fetches them all.
  const STALL_AFTER = 15000;

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

  /** Reloads once, or says the page didn't load. */
  function recover() {
    if (markReload()) {
      window.location.reload();
    } else if (document.readyState === 'loading') {
      // Not parsed yet: say it once it is. A stuck script doesn't hold
      // this up; it only holds up DOMContentLoaded.
      document.addEventListener('readystatechange', tell, {once: true});
    } else {
      tell();
    }
  }

  window.addEventListener(
    'error',
    (event) => {
      if (event.target instanceof HTMLScriptElement) {
        recover();
      }
    },
    true,
  );

  // DOMContentLoaded waits for every script to run; not there after
  // STALL_AFTER means one is stuck.
  const stall = setTimeout(recover, STALL_AFTER);
  document.addEventListener('DOMContentLoaded', () => clearTimeout(stall), {
    once: true,
  });
})();
