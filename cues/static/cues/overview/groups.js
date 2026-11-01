/**
 * @fileoverview Reel group rows for the cue grid.
 *
 * Cues are grouped by reel. AG Grid's row grouping is an Enterprise
 * feature, so on Community each reel is a full-width row of our own in the row data,
 * kept above its cues whatever the sort, with a caret that collapses it
 * and a checkbox for its cues.
 */

import {IS_MAC} from '../keys.js';

/**
 * The group row's id for a picture.
 * @param {number} pictureId
 * @return {string}
 */
export function groupId(pictureId) {
  return `reel-${pictureId}`;
}

/**
 * Row data: a group row per reel, then the cues (order is set by sort).
 * @param {!Array<!Object>} cues
 * @param {!Array<!Object>} pictures From the grid payload.
 * @return {!Array<!Object>}
 */
export function withGroups(cues, pictures) {
  const used = new Set(cues.map((row) => row._picture.id));
  const groups = pictures
    .filter((picture) => used.has(picture.id))
    .map((picture, index) => ({
      id: groupId(picture.id),
      _group: true,
      _order: index,
      _picture: picture,
    }));
  // A cue on a reel the payload doesn't list (archived) still gets a group.
  for (const row of cues) {
    if (!groups.some((group) => group._picture.id === row._picture.id)) {
      groups.push({
        id: groupId(row._picture.id),
        _group: true,
        _order: groups.length,
        _picture: {
          id: row._picture.id,
          name: row._picture.name,
          title: row._picture.name,
          label: '',
        },
      });
    }
  }
  return [...groups, ...cues];
}

/**
 * AG Grid postSortRows: each reel's group row, then its cues as sorted.
 * @param {!Object} params AG Grid params with a mutable `nodes` array.
 */
export function keepGroupsAbove(params) {
  const groups = params.nodes
    .filter((node) => node.data._group)
    .sort((a, b) => a.data._order - b.data._order);
  const byReel = new Map(groups.map((node) => [node.data._picture.id, []]));
  for (const node of params.nodes) {
    if (!node.data._group) {
      byReel.get(node.data._picture.id).push(node);
    }
  }
  params.nodes.length = 0;
  for (const group of groups) {
    params.nodes.push(group, ...byReel.get(group.data._picture.id));
  }
}

/**
 * Formats a frame count as m:ss or h:mm:ss.
 * @param {number} frames
 * @param {number} fps Frames per second to count in.
 * @return {string}
 */
export function clock(frames, fps) {
  const total = Math.round(frames / fps);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, '0');
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}`
    : `${minutes}:${seconds}`;
}

/**
 * Makes the full-width renderer for group rows.
 * @param {!Object} hooks
 * @param {function(number): boolean} hooks.isCollapsed
 * @param {function(number)} hooks.toggle Collapses or expands a reel.
 * @param {function(number, !MouseEvent)} hooks.select The checkbox.
 * @param {function(number): string} hooks.checkState 'all', 'some', 'none'.
 * @return {function(new:Object)} An AG Grid component class.
 */
export function groupRenderer(hooks) {
  return class GroupRow {
    /**
     * @param {!Object} params AG Grid full-width cell renderer params.
     */
    init(params) {
      const picture = params.data._picture;
      this.pictureId = picture.id;
      this.gui = document.createElement('div');
      this.gui.className = 'group-row';
      // AG Grid's full-width row is a row; its content is the one cell.
      this.gui.setAttribute('role', 'gridcell');

      const caret = document.createElement('button');
      caret.type = 'button';
      caret.className = 'group-row__caret';
      caret.setAttribute(
        'aria-expanded',
        String(!hooks.isCollapsed(picture.id)),
      );
      caret.setAttribute('aria-label', picture.title);
      caret.title = 'Collapse or expand (Enter)';
      caret.addEventListener('click', () => hooks.toggle(picture.id));

      // The grid's own checkbox markup, so it looks like the cue rows'.
      this.checkWrapper = document.createElement('span');
      this.checkWrapper.className =
        'ag-wrapper ag-input-wrapper ag-checkbox-input-wrapper group-row__check';
      this.checkbox = document.createElement('input');
      this.checkbox.type = 'checkbox';
      this.checkbox.className = 'ag-input-field-input ag-checkbox-input';
      this.checkWrapper.append(this.checkbox);
      this.checkbox.setAttribute('aria-label', `Select ${picture.title}`);
      this.checkbox.title = IS_MAC
        ? 'Select these cues. ⇧-click: compositions, ⌥-click: songs, ⌘-click: licensed'
        : 'Select these cues. Shift-click: compositions, Alt-click: songs, Ctrl-click: licensed';
      this.checkbox.addEventListener('click', (event) => {
        event.preventDefault(); // the state follows the grid's selection
        hooks.select(picture.id, event);
      });

      const title = document.createElement('span');
      title.className = 'group-row__title';
      title.textContent = picture.label
        ? `${picture.title} – ${picture.label}`
        : picture.title;
      const notes = document.createElement('span');
      notes.className = 'group-row__notes';
      notes.textContent = picture.notes || '';
      notes.title = picture.notes || '';

      this.gui.append(caret, this.checkWrapper, title, notes);
      this.update();
    }

    /** Shows whether all, some or none of the reel's cues are selected. */
    update() {
      const state = hooks.checkState(this.pictureId);
      this.checkbox.checked = state === 'all';
      this.checkbox.indeterminate = state === 'some';
      this.checkWrapper.classList.toggle('ag-checked', state === 'all');
      this.checkWrapper.classList.toggle('ag-indeterminate', state === 'some');
    }

    /** @return {!HTMLElement} */
    getGui() {
      return this.gui;
    }

    /** @return {boolean} False: redraw on refresh. */
    refresh() {
      return false;
    }
  };
}
