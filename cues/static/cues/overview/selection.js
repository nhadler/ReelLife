/**
 * @fileoverview Row selection helpers: modifier
 * clicks, invert, and the "2 cues selected (3:42 / 12%)" summary.
 */

import {keys} from '../keys.js';
import {clock} from './groups.js';

/**
 * What a modifier click selects on the select-all and
 * reel checkboxes: Shift compositions, Alt original songs, Ctrl or Cmd
 * licensed, combinable. Null without a modifier.
 * @param {!MouseEvent} event
 * @param {function(!Object): ?Object} statusOf
 * @return {?function(!Object): boolean}
 */
export function modifierTest(event, statusOf) {
  const wanted = [];
  if (event.shiftKey) {
    wanted.push('unapproved', 'approved');
  }
  if (event.altKey) {
    wanted.push('song');
  }
  if (event.ctrlKey || event.metaKey) {
    wanted.push('license');
  }
  if (!wanted.length) {
    return null;
  }
  return (row) => wanted.includes(statusOf(row)?.category ?? 'unapproved');
}

/**
 * The cue nodes that pass the filters (collapsed reels included).
 * @param {!Object} api
 * @param {function(!Object): boolean} passes The page's row filter.
 * @return {!Array<!Object>}
 */
export function shownCueNodes(api, passes) {
  const nodes = [];
  api.forEachNode((node) => {
    if (!node.data._group && passes(node.data)) {
      nodes.push(node);
    }
  });
  return nodes;
}

/**
 * Selects the given nodes, replacing the selection unless `add`.
 * @param {!Object} api
 * @param {!Array<!Object>} nodes
 * @param {boolean} add
 */
export function selectNodes(api, nodes, add) {
  if (!add) {
    api.deselectAll();
  }
  api.setNodesSelected({nodes, newValue: true});
}

/**
 * Selects the shown cues that aren't selected, and deselects the rest.
 * @param {!Object} api
 * @param {!Array<!Object>} shown
 */
export function invert(api, shown) {
  const selected = shown.filter((node) => node.isSelected());
  const unselected = shown.filter((node) => !node.isSelected());
  api.setNodesSelected({nodes: selected, newValue: false});
  api.setNodesSelected({nodes: unselected, newValue: true});
}

/**
 * Fills the toolbar's selection summary, or hides it.
 * @param {!HTMLElement} element
 * @param {!Array<!Object>} selected Selected rows' data.
 * @param {number} totalFrames All shown music, for the percentage.
 * @param {number} fps
 * @param {!Object<string, function()>} actions deselect and invert.
 */
export function showSummary(element, selected, totalFrames, fps, actions) {
  element.hidden = !selected.length;
  if (!selected.length) {
    element.replaceChildren();
    return;
  }
  const frames = selected.reduce(
    (sum, row) => sum + Math.max(row.duration?.frames ?? 0, 0),
    0,
  );
  const percent = totalFrames ? Math.round((100 * frames) / totalFrames) : 0;
  const count = `${selected.length} cue${selected.length === 1 ? '' : 's'}`;
  const link = (label, title, action) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'selection-summary__link';
    button.textContent = label;
    button.title = keys(title);
    button.addEventListener('click', action);
    return button;
  };
  element.replaceChildren(
    `${count} selected (${clock(frames, fps)} / ${percent}%) – `,
    link('deselect all', 'Ctrl+Shift+D', actions.deselect),
    ' – ',
    link('invert selection', 'Ctrl+Shift+I', actions.invert),
  );
}
