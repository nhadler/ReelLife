/**
 * @fileoverview The Tools menu's cue tools. Each opens a
 * server-rendered dialog for the selected cues, sent in the grid's order;
 * items that need a selection are greyed until there is one.
 */

/**
 * How many selected cues each kind of tool needs.
 * @constant {!Object<string, function(number): boolean>}
 */
const NEEDS = {
  one: (count) => count === 1,
  two: (count) => count >= 2,
  any: (count) => count >= 1,
};

/**
 * Wires the Tools menu.
 * @param {!Object} input
 * @param {!Object} input.api The grid's API.
 * @param {string} input.url The tool URL, with 'tool' for the name.
 * @param {!HTMLDetailsElement} input.menu The Tools menu.
 * @return {function(string)} Opens a tool by name (for shortcuts).
 */
export function setUpTools({api, url, menu}) {
  /**
   * The shown cues' ids in the grid's order.
   * @return {!Array<number>}
   */
  function shownIds() {
    const ids = [];
    api.forEachNodeAfterFilterAndSort((node) => {
      if (!node.data._group) {
        ids.push(node.data.id);
      }
    });
    return ids;
  }

  /**
   * The cues a tool works on, by each tool's rules.
   * @param {string} name
   * @return {!Array<number>}
   */
  function cuesFor(name) {
    if (name === 'new') {
      return [];
    }
    const shown = shownIds();
    const selected = new Set(api.getSelectedRows().map((row) => row.id));
    const ids = shown.filter((id) => selected.has(id));
    if (name === 'versions' && !ids.length) {
      return shown; // none selected: every cue shown
    }
    if (name === 'renumber' && ids.length === 1) {
      return shown.slice(shown.indexOf(ids[0])); // it and every one after
    }
    return ids;
  }

  /** Greys the tools the selection doesn't suit. */
  function refresh() {
    const count = api.getSelectedRows().length;
    for (const item of menu.querySelectorAll('[data-needs]')) {
      item.disabled = !NEEDS[item.dataset.needs](count);
    }
  }

  /**
   * Opens a tool's dialog.
   * @param {string} name
   */
  function open(name) {
    const item = menu.querySelector(`[data-tool="${name}"]`);
    if (!item) {
      return; // viewers have no tools
    }
    refresh();
    if (item.disabled) {
      return;
    }
    menu.open = false;
    api.stopEditing();
    const params = new URLSearchParams({cues: cuesFor(name).join(',')});
    window.htmx.ajax('GET', `${url.replace('/tool/', `/${name}/`)}?${params}`, {
      target: '#app-dialog',
      swap: 'innerHTML',
    });
  }

  menu.addEventListener('toggle', () => {
    if (menu.open) {
      refresh();
    }
  });
  menu.addEventListener('click', (event) => {
    const item = event.target.closest('[data-tool]');
    if (item) {
      open(item.dataset.tool);
    }
  });
  return open;
}
