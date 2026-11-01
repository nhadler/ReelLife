/**
 * @fileoverview Copy JSON: cue data for macros on the
 * clipboard, for Keyboard Maestro and SoundFlow macros (Ctrl+Shift+J).
 *
 * Browsers only allow clipboard writes during a key press or click, so
 * the clipboard gets a promise of the text straight away (Safari needs
 * this). If the browser still refuses, the JSON is shown selected in the
 * page's dialog to copy by hand.
 */

import {keys} from './keys.js';

/**
 * Shows the JSON in the dialog, selected, with a hint to copy it.
 * @param {string} text
 */
function showFallback(text) {
  const dialog = document.getElementById('app-dialog');
  const head = document.createElement('header');
  head.className = 'dialog__head';
  const title = document.createElement('h2');
  title.id = 'dialog-title';
  title.textContent = 'Copy JSON';
  const close = document.createElement('button');
  close.className = 'dialog__close';
  close.type = 'button';
  close.dataset.dialogClose = '';
  close.setAttribute('aria-label', 'Close');
  close.textContent = '×';
  head.append(title, close);
  const hint = document.createElement('p');
  hint.className = 'muted';
  hint.textContent = keys(
    'The browser blocked the clipboard. Press Ctrl+C to copy.',
  );
  const area = document.createElement('textarea');
  area.className = 'copy-json';
  area.readOnly = true;
  area.rows = 14;
  area.value = text;
  dialog.replaceChildren(head, hint, area);
  dialog.showModal();
  area.select();
}

/**
 * Copies the JSON the server returns for `url`.
 * @param {string} url The Copy JSON endpoint with ids (and version).
 * @return {!Promise<string>} A message for the page's status line.
 */
export async function copyJson(url) {
  const text = fetch(url, {credentials: 'same-origin'}).then(
    async (response) => {
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || `HTTP ${response.status}`);
      }
      return JSON.stringify(body, null, 2);
    },
  );
  try {
    await navigator.clipboard.write([
      new ClipboardItem({
        'text/plain': text.then((t) => new Blob([t], {type: 'text/plain'})),
      }),
    ]);
    return 'Copied JSON.';
  } catch {
    let value;
    try {
      value = await text;
    } catch (error) {
      return `Couldn't copy JSON: ${error.message}`;
    }
    try {
      await navigator.clipboard.writeText(value);
      return 'Copied JSON.';
    } catch {
      showFallback(value);
      return 'Copy the JSON from the window.';
    }
  }
}
