/**
 * @fileoverview The cue overview grid on AG Grid Community.
 *
 * Loads the project's rows, columns and views from the grid endpoint,
 * sends each cell edit as a PATCH and replaces the row with the server's
 * answer, and switches saved views with the tabs under the grid. Reels
 * show as a pinned column with a rule where each reel starts (row
 * grouping is an Enterprise feature). Runs under a strict CSP: AG Grid's
 * styles carry the request's nonce and nothing uses inline scripts.
 */

import {ChipsEditor, chipsRenderer} from './overview/chips-editor.js';
import {ask} from './confirm.js';
import {copyJson} from './copyjson.js';
import {keys} from './keys.js';
import {showMenu} from './overview/context-menu.js';
import {
  groupId,
  groupRenderer,
  keepGroupsAbove,
  withGroups,
} from './overview/groups.js';
import {
  invert,
  modifierTest,
  selectNodes,
  shownCueNodes,
  showSummary,
} from './overview/selection.js';
import {StatusEditor} from './overview/status-editor.js';
import {editView} from './overview/view-editor.js';
import {drawTimeline} from './overview/timeline.js';
import {setUpTools} from './overview/tools.js';
import {askBatch} from './overview/batch.js';

const {createGrid, themeQuartz} = window.agGrid;

const config = JSON.parse(document.getElementById('grid-config').textContent);
const gridElement = document.getElementById('cue-grid');
const statusElement = document.getElementById('grid-status');
const tabsElement = document.getElementById('view-tabs');
const inactiveToggle = document.getElementById('grid-show-inactive');
const undoButton = document.getElementById('grid-undo');
const importButton = document.getElementById('grid-import');
/**
 * Opens a cue tool by name (setUpTools replaces it in start()).
 * @type {function(string)}
 */
let openTool = () => {};
const exportButton = document.getElementById('grid-export');
const searchInput = document.getElementById('grid-search');
const findElement = document.getElementById('grid-find');
const filterButton = document.getElementById('grid-filter-button');
const filterPanel = document.getElementById('grid-filter-panel');
const selectionElement = document.getElementById('grid-selection');
// Nominal frames per second, for timeline positions (labels count at
// this rate).
const FPS = Math.round(Number(config.frameRate) || 24);
// Frames per second of duration, as the server counts it: the actual
// rate at drop-frame, else nominal, so totals match the cue sheet.
const DURATION_FPS = Number(config.durationFps) || FPS;
// Row fields the find field looks in: cue #, title, scene and notes.
const SEARCH_FIELDS = config.searchFields || ['number', 'title'];
const COLLAPSED_KEY = `reellife.collapsed.${config.projectId}`;
const TIMELINE_KEY = 'reellife.timelineCollapsed';
const timelineElement = document.getElementById('timeline');
const VIEW_KEY = `reellife.view.${config.projectId}`;
const ROW_SIZE_KEY = 'reellife.rowSize';
// The most cues Copy JSON sends (cues/api/views.py MAX_JSON_CUES).
const MAX_JSON_CUES = 500;
// Row sizes for the View menu; 'auto' fits each row's text.
const ROW_HEIGHTS = {tiny: 24, small: 28, medium: 34, large: 44, auto: 34};
const ROW_SIZES = Object.keys(ROW_HEIGHTS);
// Text columns wrap, but short codes stay on one line.
const NO_WRAP = new Set([
  'number',
  'key',
  'meter',
  'orchestration_version',
  'mix_version',
]);
// One line of wrapped text (line height 1.35 at 14px) plus cell borders.
const WRAPPED_LINE_PX = 21;
// The Filter menu's Show choices (Shift+1-6).
const SHOW = {
  '': {label: 'All Cues', test: () => true},
  compositions: {
    label: 'Compositions',
    test: (row) => ['unapproved', 'approved'].includes(statusOf(row)?.category),
  },
  songs: {
    label: 'Original Songs',
    test: (row) => statusOf(row)?.kind === 'song',
  },
  licensed: {
    label: 'Licensed',
    test: (row) => statusOf(row)?.kind === 'license',
  },
  active: {label: 'Active', test: (row) => row._active},
  inactive: {label: 'Inactive', test: (row) => !row._active},
};
const SHOW_KEYS = Object.keys(SHOW);
// Toolbar menus and the keys that open them.
const MENU_KEYS = {q: 'tools-menu', f: 'grid-filter', v: 'grid-view-menu'};
// Header tooltips for columns whose names are terse.
const HEADER_TIPS = {
  sequence_number: 'Order in the picture',
  number: 'Cue number, in its status colour',
  version: keys('Current version; + adds the next (Ctrl+Shift+V)'),
  seq_start: 'Seq. start: where IN falls in the sequence',
  tc_in: 'IN: where the cue starts in the current picture',
  tc_out: 'OUT: where the cue ends',
  duration: 'DUR: OUT minus IN',
  written_to: 'The picture the current version was written to',
  current_picture: 'The latest turnover of the picture the cue is in',
  start_offset: 'How far IN moved since the version was written',
  duration_difference: 'How much the length changed since then',
  version_in: 'IN when the current version was written',
  version_out: 'OUT when the current version was written',
  version_duration: 'Length when the current version was written',
  version_seq_start: 'Seq. start when the current version was written',
};
// The side panel's contents and the buttons that toggle them.
const PANEL_TOGGLES = {notes: 'notes-toggle', stats: 'stats-toggle'};
// Plain keys that act on the page, not a focused cell (besides MENU_KEYS).
const PAGE_KEYS = new Set(['h', 'l']);
const GROUP_ROW_HEIGHT = 31;
// Notes columns and the kind of project notes each goes with.
const NOTE_KINDS = {
  spotting_notes: 'spotting',
  notes: 'notes',
  revision_notes: 'revision',
  change_notes: 'conform',
  orchestration_notes: 'orchestration',
  recording_notes: 'recording',
  mix_notes: 'mix',
  cue_sheet_notes: 'cue_sheet',
};
// Short notes kept whole in the Spotting view, besides the long ones.
// Content-sizing bounds [min, max] in px (widthBounds).
const WIDTHS_BY_COLUMN = {
  sequence_number: [44, 64],
  status: [110, 190],
  number: [72, 110],
  title: [140, 320],
  version: [92, 130],
  written_to: [110, 190],
  current_picture: [110, 190],
  updated_at: [52, 60],
};
const WIDTHS_BY_EDITOR = {
  timecode: [96, 124],
  duration: [64, 100],
  select: [80, 200],
  checkbox: [70, 140],
  number: [60, 90],
  text: [80, 260],
  markdown: [140, 360],
  tags: [70, 260],
  none: [64, 200],
};
// Where keys are typing, not shortcuts (checkboxes and radios aren't).
const TYPING_FIELDS =
  'input:not([type=checkbox]):not([type=radio]), textarea, select';
// AG Grid's id for the checkbox column it adds.
const SELECTION_COLUMN = 'ag-Grid-SelectionColumn';
// Columns pinned left unless a view says otherwise.
const PINNED = new Set([
  'thumbnail',
  'sequence_number',
  'status',
  'number',
  'title',
]);
// Filter groups: which row value each one checks.
const FILTER_GROUPS = [
  {key: 'status', label: 'Status', value: (row) => row.status},
  {key: 'cue_type', label: 'Cue Type', value: (row) => row.cue_type},
  {key: 'picture', label: 'Reel', value: (row) => row._picture.id},
  {key: 'priority', label: 'Priority', value: (row) => row.priority},
  {key: 'difficulty', label: 'Difficulty', value: (row) => row.difficulty},
  {key: 'sub_status', label: 'Sub Status', value: (row) => row.sub_status},
  {key: 'themes', label: 'Theme', value: (row) => row.themes.map((c) => c.id)},
  {
    key: 'instrumentation',
    label: 'Instrumentation',
    value: (row) => row.instrumentation.map((c) => c.id),
  },
];

/** @type {?Object} */
let api = null;
/** @type {!Object<string, !Object>} */
let columnMeta = {};

/** App Settings' Enter in a notes cell: true when Enter saves the cell. */
let enterSaves = false;
/** @type {!Array<!Object>} */
let views = [];
/** @type {?Object} */
let currentView = null;
/** @type {!Object} */
let filters = emptyFilters();
/** @type {!Array<!Object>} The cues (group rows aren't in here). */
let allRows = [];

/** Each reel's Written To choices, by picture id (from the server). */
let writtenTo = {};
/** @type {!Array<!Object>} The reels, from the payload. */
let pictures = [];
/** @type {!Set<number>} Collapsed reels' picture ids. */
let collapsed = loadCollapsed();

/**
 * Shows a short status message for screen readers and sighted users.
 * @param {string} message
 * @param {boolean=} isError
 */
function announce(message, isError = false) {
  clearTimeout(announce.timer);
  statusElement.textContent = message;
  statusElement.classList.toggle('grid-status--error', isError);
  // Routine news fades; errors stay until the next message, and the
  // filter count comes back (Nick, Oct 31: no standing cue count).
  if (message && !isError) {
    announce.timer = setTimeout(() => announce(filterSummary()), 4000);
  }
}

/** Opens the find field (a magnifier until then) and selects its text. */
function openSearch() {
  findElement.classList.add('toolbar__find--open');
  searchInput.focus();
  searchInput.select();
}

/**
 * How many cues the filters show, or '' when they show them all.
 * @return {string}
 */
function filterSummary() {
  const shown = allRows.filter(passes).length;
  return shown === allRows.length
    ? ''
    : `${shown} of ${allRows.length} cues shown.`;
}

/**
 * Tells the page the data changed: the stats panel and the like reload,
 * and so does the project header unless a save brought it back.
 * @param {string=} header The header's new HTML, from a cell save.
 */
function notifyChanged(header = '') {
  const headerElement = document.getElementById('project-header');
  if (header && headerElement) {
    window.htmx.swap(headerElement, header, {swapStyle: 'innerHTML'});
  }
  document.body.dispatchEvent(
    new CustomEvent('cues:changed', {detail: {headerFresh: Boolean(header)}}),
  );
}

// The project header follows every change. A cell save brings it back
// with the row; anything else (dialogs, imports, undo) fetches it.
document.body.addEventListener('cues:changed', (event) => {
  if (!event.detail?.headerFresh) {
    document.body.dispatchEvent(new CustomEvent('cues:refresh-header'));
  }
});

/**
 * Reads a CSS custom property from the page, for the grid theme.
 * @param {string} name
 * @return {string}
 */
function token(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

/**
 * The row size the viewer picked last (a per-browser preference).
 * @return {string}
 */
function rowSize() {
  try {
    const saved = localStorage.getItem(ROW_SIZE_KEY);
    return ROW_HEIGHTS[saved] ? saved : 'medium';
  } catch {
    return 'medium';
  }
}

/**
 * Builds the AG Grid theme from the page's colour tokens.
 * @return {!Object}
 */
function gridTheme() {
  return themeQuartz.withParams({
    backgroundColor: token('--bay'),
    foregroundColor: token('--ink'),
    chromeBackgroundColor: token('--panel'),
    headerBackgroundColor: token('--panel'),
    headerTextColor: token('--slate'),
    borderColor: token('--rule'),
    accentColor: token('--accent'),
    oddRowBackgroundColor: token('--bay'),
    selectedRowBackgroundColor: `color-mix(in srgb, ${token('--accent')} 18%, transparent)`,
    fontFamily: token('--sans'),
    fontSize: 14,
    rowHeight: ROW_HEIGHTS.medium,
    headerHeight: 36,
    // Tight cells keep content-sized columns narrow.
    cellHorizontalPadding: 8,
    wrapperBorderRadius: 6,
    // Matches tooltip.js's box for every other control.
    tooltipBackgroundColor: '#08090c',
    tooltipTextColor: '#f2f2f0',
    tooltipBorder: false,
  });
}

/**
 * Builds a request with the CSRF token.
 * @param {string} method
 * @param {?Object=} body
 * @return {!Object} Options for fetch().
 */
function request(method, body = null) {
  return {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': config.csrfToken,
    },
    body: body === null ? null : JSON.stringify(body),
    credentials: 'same-origin',
  };
}

/**
 * A row's status option (label, colour, kind, category), if it has one.
 * @param {!Object} row
 * @return {?Object}
 */
function statusOf(row) {
  return (
    (columnMeta.status.options || []).find((o) => o.value === row.status) ||
    null
  );
}

/**
 * Finds an option's label for a select column.
 * @param {!Array<!Object>} options
 * @param {?(string|number)} value
 * @return {string}
 */
function optionLabel(options, value) {
  const found = options.find((option) => option.value === value);
  return found ? found.label : '';
}

/**
 * Renders a status as a coloured pill.
 * @param {!Object} params AG Grid cell renderer params.
 * @return {!HTMLElement}
 */
function statusRenderer(params) {
  // A soft chip tinted with the status colour, its edge in full.
  const option = (columnMeta.status.options || []).find(
    (o) => o.value === params.value,
  );
  const pill = document.createElement('span');
  pill.className = 'status-pill';
  if (option) {
    pill.textContent = option.label;
    pill.style.setProperty('--chip', option.color);
  } else {
    pill.classList.add('status-pill--none');
    pill.textContent = '–';
  }
  return pill;
}

/**
 * A renderer drawing a select's chosen option with its colour.
 * @param {!Object} meta Column metadata whose options have colours.
 * @return {function(!Object): !HTMLElement}
 */
function optionPill(meta) {
  // A soft chip in the option's colour, like Status.
  return (params) => {
    const option = meta.options.find((o) => o.value === params.value);
    const pill = document.createElement('span');
    pill.className = 'status-pill';
    if (option && option.color && option.value !== '') {
      pill.textContent = option.label;
      pill.style.setProperty('--chip', option.color);
    } else {
      pill.classList.add('status-pill--none');
      pill.textContent = '–';
    }
    return pill;
  };
}

/**
 * Priority and difficulty as three bars, filled up to
 * the level (1 pale, 2 orange, 3 red), then the level's name.
 * @param {!Object} meta Column metadata (its options, in level order).
 * @return {function(!Object): !HTMLElement}
 */
function gradingRenderer(meta) {
  return (params) => {
    const wrap = document.createElement('span');
    wrap.className = 'grading';
    const levels = meta.options.filter((o) => o.value !== '');
    const level = levels.findIndex((o) => o.value === params.value) + 1;
    if (!level) {
      wrap.classList.add('grading--none');
      wrap.textContent = '–';
      return wrap;
    }
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('class', `grading__bars grading__bars--${level}`);
    svg.setAttribute('aria-hidden', 'true');
    [8, 5, 2].forEach((y, index) => {
      const bar = document.createElementNS(ns, 'rect');
      bar.setAttribute('x', String(1 + index * 5));
      bar.setAttribute('y', String(y));
      bar.setAttribute('width', '3');
      bar.setAttribute('height', String(14 - y));
      bar.setAttribute('rx', '1');
      if (index >= level) {
        bar.setAttribute('class', 'grading__off');
      }
      svg.append(bar);
    });
    wrap.append(svg, levels[level - 1].label);
    return wrap;
  };
}

/**
 * The cue's thumbnail, the project's (faded) standing in, or an empty
 * frame.
 * @param {!Object} params AG Grid cell renderer params.
 * @return {!HTMLElement}
 */
function thumbnailRenderer(params) {
  if (!params.value) {
    // The project's initials, as its own thumbnail shows them.
    const frame = document.createElement('span');
    frame.className = 'cue-thumb cue-thumb--empty';
    frame.textContent = gridElement?.dataset.initials ?? '';
    frame.setAttribute('aria-hidden', 'true');
    return frame;
  }
  const image = document.createElement('img');
  // The project's thumbnail, standing in: drawn faded.
  image.className = params.data._thumbnail_inherited
    ? 'cue-thumb cue-thumb--inherited'
    : 'cue-thumb';
  image.title = params.data._thumbnail_inherited
    ? "The project's thumbnail"
    : '';
  image.src = params.value;
  image.alt = '';
  image.loading = 'lazy';
  return image;
}

/**
 * Who changed the cue last: a small avatar, with the
 * time and their name on hover.
 * @param {!Object} params AG Grid cell renderer params.
 * @return {!HTMLElement}
 */
function updatedRenderer(params) {
  const who = params.data._updated_by;
  const avatar = document.createElement('span');
  avatar.className = who ? 'updated' : 'updated updated--nobody';
  avatar.textContent = who
    ? who
        .split(/\s+/)
        .map((part) => part[0])
        .slice(0, 2)
        .join('')
        .toUpperCase()
    : '';
  avatar.title = who
    ? `Updated ${params.value} by ${who}`
    : `Updated ${params.value}`;
  avatar.setAttribute('aria-label', avatar.title);
  return avatar;
}

/**
 * Dark or light text, whichever reads better on a status colour.
 * @param {string} hex '#rrggbb'.
 * @return {string}
 */
function inkFor(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55
    ? '#14171c'
    : '#ffffff';
}

/**
 * Renders the cue number as a pill in its status colour.
 * @param {!Object} params AG Grid cell renderer params.
 * @return {!HTMLElement}
 */
function numberRenderer(params) {
  const pill = document.createElement('span');
  pill.className = 'cue-number';
  pill.textContent = params.value || '';
  const option = (columnMeta.status.options || []).find(
    (o) => o.value === params.data.status,
  );
  if (option) {
    // Plain text with a thin tick of its status colour, so views
    // without a Status column still show it, quietly.
    pill.style.setProperty('--chip', option.color);
  }
  return pill;
}

/**
 * Opens the new version dialog for a cue (htmx loads the form).
 * @param {number} cueId
 */
function openNewVersion(cueId) {
  window.htmx.ajax('GET', config.newVersionUrl.replace('/0/', `/${cueId}/`), {
    target: '#app-dialog',
    swap: 'innerHTML',
  });
}

/**
 * The row of the focused (or last focused) cell, if any.
 * @return {?Object} The row's data.
 */
function focusedRow() {
  const cell = api.getFocusedCell();
  const node = cell ? api.getDisplayedRowAtIndex(cell.rowIndex) : null;
  return node && !node.data._group ? node.data : null;
}

/**
 * A row's Written To choices: its reel's picture versions.
 * @param {!Object} row
 * @return {!Array<!Object>}
 */
function writtenToFor(row) {
  return writtenTo[row._picture?.id] ?? [];
}

/**
 * Opens the cue details page for the focused cue.
 */
function openDetails() {
  const row = focusedRow();
  if (!row) {
    announce('Pick a cue in the grid first.', true);
    return;
  }
  api.stopEditing();
  openCue(row.id);
}

/**
 * Goes to a cue's details page once any edits still saving have landed,
 * so the page shows them and none is cut off by leaving.
 * @param {number} id
 */
async function openCue(id) {
  await Promise.allSettled(saving.values());
  window.location.assign(config.detailUrl.replace('/0/', `/${id}/`));
}

/**
 * Opens the export dialog on the current view, for the selected cues if
 * any, else the shown ones, in their on-screen order.
 */
function openExport() {
  const params = new URLSearchParams({view: String(currentView.id)});
  const selected = new Set(api.getSelectedRows().map((row) => row.id));
  const shown = [];
  api.forEachNodeAfterFilterAndSort((node) => {
    if (!node.data._group && (!selected.size || selected.has(node.data.id))) {
      shown.push(node.data.id);
    }
  });
  if (selected.size || shown.length < allRows.length) {
    params.set('ids', shown.join(','));
  }
  api.stopEditing();
  window.htmx.ajax('GET', `${config.exportUrl}?${params}`, {
    target: '#app-dialog',
    swap: 'innerHTML',
  });
}

/**
 * Renders the version label with a button to add the next version.
 * @param {!Object} params AG Grid cell renderer params.
 * @return {!HTMLElement}
 */
function versionRenderer(params) {
  const wrap = document.createElement('span');
  wrap.className = 'version-cell';
  wrap.append(params.value || '');
  if (config.canEdit) {
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'version-cell__add';
    add.textContent = '+';
    add.title = keys('New version (Ctrl+Shift+V)');
    add.setAttribute('aria-label', `New version of ${params.data.number}`);
    add.addEventListener('click', (event) => {
      event.stopPropagation();
      openNewVersion(params.data.id);
    });
    wrap.append(add);
  }
  return wrap;
}

/**
 * Column definition for a timecode, duration or offset value object.
 * @param {!Object} meta Column metadata from the server.
 * @return {!Object}
 */
function frameColumn(meta) {
  return {
    valueGetter: (params) => params.data[meta.id]?.display ?? '',
    // Show the typed text until the server answers with parsed frames.
    valueSetter: (params) => {
      if (params.newValue === params.oldValue) {
        return false;
      }
      params.data[meta.id] = {frames: null, display: params.newValue};
      return true;
    },
    comparator: (a, b, nodeA, nodeB) =>
      (nodeA.data[meta.id]?.frames ?? -1) - (nodeB.data[meta.id]?.frames ?? -1),
    cellClass: 'tc',
  };
}

/**
 * Column definition for a select with options.
 * @param {!Object} meta Column metadata from the server.
 * @return {!Object}
 */
function selectColumn(meta) {
  if (meta.id === 'written_to') {
    return {
      cellEditor: 'agSelectCellEditor',
      cellEditorParams: (params) => ({
        values: [null, ...writtenToFor(params.data).map((o) => o.value)],
        formatValue: (value) =>
          optionLabel(writtenToFor(params.data), value) || 'none',
      }),
      valueFormatter: (params) =>
        optionLabel(writtenToFor(params.data), params.value),
    };
  }
  const options = meta.options || [];
  return {
    cellEditor: 'agSelectCellEditor',
    cellEditorParams: {
      values: options.map((o) => o.value),
      formatValue: (value) => optionLabel(options, value) || 'none',
    },
    valueFormatter: (params) => optionLabel(options, params.value),
  };
}

/**
 * Column definition for plain values and read-only {frames, display}
 * values (offsets, version duration), which show their display text.
 * @param {!Object} meta Column metadata from the server.
 * @param {!Object} def The base definition.
 * @return {!Object}
 */
function readOnlyColumn(meta, def) {
  const offset =
    meta.id === 'start_offset' || meta.id === 'duration_difference';
  /**
   * A sortable value: frames for timecodes, else the value itself.
   * @param {!Object} data A grid row.
   * @return {number|string}
   */
  const sortKey = (data) => {
    const value = data[meta.id];
    if (value === null || value === undefined) {
      return '';
    }
    return typeof value === 'object' ? (value.frames ?? '') : value;
  };
  return {
    ...def,
    cellDataType: 'text',
    valueGetter: (params) => {
      const value = params.data[meta.id];
      return value && typeof value === 'object' ? value.display : value;
    },
    // Timecodes sort by frames, not by their display text.
    comparator: (a, b, nodeA, nodeB) => {
      const x = sortKey(nodeA.data);
      const y = sortKey(nodeB.data);
      if (x === y) {
        return 0;
      }
      if (x === '') {
        return -1;
      }
      if (y === '') {
        return 1;
      }
      return x < y ? -1 : 1;
    },
    cellClass: [
      def.cellClass,
      offset ? 'tc tc--offset' : '',
      meta.id === 'version_duration' ? 'tc' : '',
    ]
      .filter(Boolean)
      .join(' '),
  };
}

/**
 * How narrow and wide a column may be sized to its content, in pixels,
 * by column, then by editor.
 * @param {!Object} meta Column metadata from the server.
 * @return {{minWidth: number, maxWidth: number}}
 */
function widthBounds(meta) {
  const [minWidth, maxWidth] = WIDTHS_BY_COLUMN[meta.id] ||
    WIDTHS_BY_EDITOR[meta.editor] || [70, 240];
  return {minWidth, maxWidth};
}

/**
 * Text columns show all of their text, wrapping, with rows growing to
 * fit (Nick, Oct 31: "all text should wrap"). The row size is each
 * row's least height (rowMinimum); Auto has none. Short codes don't wrap.
 * @param {!Object} meta Column metadata from the server.
 * @return {{wrapText: boolean, autoHeight: boolean}}
 */
function fullNotes(meta) {
  const wrap = !NO_WRAP.has(meta.id);
  return {wrapText: wrap, autoHeight: wrap};
}

/**
 * Sets the least height of rows, which wrapped text can only add to.
 * @param {string} size A ROW_HEIGHTS key.
 */
function rowMinimum(size) {
  const height = size === 'auto' ? 0 : ROW_HEIGHTS[size];
  gridElement.style.setProperty('--row-minimum', `${height}px`);
  // One line of wrapped text sits centred, like the cells beside it.
  const padding = height ? Math.max(2, (height - WRAPPED_LINE_PX) / 2) : 5;
  gridElement.style.setProperty('--row-padding', `${padding}px`);
}

/**
 * Sizes the view's columns that have no saved width to their content
 * (header included), within each column's bounds.
 */
function autoSizeColumns() {
  const saved = new Set(
    currentView.columns.filter((c) => c.width).map((c) => c.id),
  );
  const ids = api
    .getColumns()
    .map((column) => column.getColId())
    .filter(
      (id) => !saved.has(id) && id !== SELECTION_COLUMN && id !== '_active',
    );
  api.autoSizeColumns(ids, false);
}

/**
 * What a terse column header means, or where a column comes from.
 * @param {!Object} meta Column metadata from the server.
 * @return {string|undefined}
 */
function headerTip(meta) {
  const tip = HEADER_TIPS[meta.id];
  if (meta.source === 'version') {
    return `${tip || meta.label}: from the current version`;
  }
  return tip;
}

/**
 * The AG Grid column definition for one column in a view.
 * @param {!Object} meta Column metadata from the server.
 * @param {!Object} layout The view's entry: id, width, pinned.
 * @return {!Object}
 */
function columnDef(meta, layout) {
  const def = {
    colId: meta.id,
    field: meta.id,
    headerName: meta.label,
    editable: meta.editable,
    sortable: true,
    resizable: true,
    width: layout.width,
    ...widthBounds(meta),
    pinned: layout.pinned,
    // Version columns look like the rest (Nick, Oct 31).
    headerTooltip: headerTip(meta),
    tooltipValueGetter: (params) =>
      meta.editor === 'markdown' ? params.value : undefined,
  };
  if (meta.editor === 'text') {
    Object.assign(def, fullNotes(meta));
  }
  if (meta.id === 'number') {
    def.cellRenderer = numberRenderer;
    def.cellClass = 'cell--pill';
  }
  if (meta.id === 'priority' || meta.id === 'difficulty') {
    return {...def, ...selectColumn(meta), cellRenderer: gradingRenderer(meta)};
  }
  if (meta.id === 'thumbnail') {
    return {
      ...def,
      sortable: false,
      width: layout.width || 64,
      cellClass: 'cell--thumb',
      cellRenderer: thumbnailRenderer,
      // A click opens the cue (Enter does too).
      onCellClicked: (params) => openCue(params.data.id),
    };
  }
  if (meta.id === 'updated_at') {
    return {...readOnlyColumn(meta, def), cellRenderer: updatedRenderer};
  }
  if (meta.id === 'lane') {
    // 0 is automatic placement on the timeline; 1 and up a chosen lane.
    return {
      ...def,
      cellDataType: 'number',
      cellEditor: 'agNumberCellEditor',
      cellEditorParams: {min: 0, max: config.lanes, precision: 0},
      valueFormatter: (params) =>
        params.value ? String(params.value) : 'Auto',
    };
  }
  switch (meta.editor) {
    case 'timecode':
    case 'duration':
      return {...def, ...frameColumn(meta)};
    case 'status':
      return {
        ...def,
        cellRenderer: statusRenderer,
        cellEditor: StatusEditor,
        cellEditorPopup: true,
        cellEditorParams: {options: meta.options || []},
        cellClass: 'cell--pill',
      };
    case 'select':
      // Department statuses carry their colours: draw them like Status.
      // Coloured choices (Sub Status, department and review statuses):
      // pills and the popup list, like Status.
      return meta.creatable || (meta.options || []).some((o) => o.color)
        ? {
            ...def,
            ...selectColumn(meta),
            cellRenderer: optionPill(meta),
            cellEditor: StatusEditor,
            cellEditorPopup: true,
            cellEditorParams: () => ({
              options: meta.options || [],
              creatable: Boolean(meta.creatable),
              label: meta.label,
            }),
            cellClass: 'cell--pill',
          }
        : {...def, ...selectColumn(meta)};
    case 'none':
      if (meta.id === 'version') {
        return {...def, cellRenderer: versionRenderer};
      }
      return readOnlyColumn(meta, def);
    case 'tags':
      // Theme and Instrumentation: chips, edited in a popup list.
      return {
        ...def,
        cellRenderer: chipsRenderer(inkFor),
        cellEditor: ChipsEditor,
        cellEditorPopup: true,
        cellEditorParams: () => ({
          options: meta.options || [],
          label: meta.label,
        }),
        cellDataType: false,
        sortable: false,
        equals: (a, b) => a === b,
      };
    case 'checkbox':
      return {...def, cellDataType: 'boolean'};
    case 'markdown':
      return {
        ...def,
        cellEditor: 'agLargeTextCellEditor',
        cellEditorPopup: true,
        cellEditorParams: {maxLength: 10000, rows: 8, cols: 50},
        suppressKeyboardEvent: notesKeepEnter,
        ...fullNotes(meta),
      };
    default:
      return readOnlyColumn(meta, def);
  }
}

/**
 * Whether the grid leaves a key to a notes editor's text box: Enter adds
 * a line unless App Settings say Enter saves the cell. Ctrl+Enter (⌘ on
 * a Mac) always saves and Shift+Enter always adds a line.
 * @param {!Object} params AG Grid suppressKeyboardEvent params.
 * @return {boolean}
 */
function notesKeepEnter(params) {
  const event = params.event;
  return (
    params.editing &&
    event.key === 'Enter' &&
    !enterSaves &&
    !event.ctrlKey &&
    !event.metaKey
  );
}

/**
 * Column definitions for a view, after the pinned reel column.
 * @param {!Object} view
 * @return {!Array<!Object>}
 */
function columnsFor(view) {
  // On a phone only the Cue # stays pinned beside the checkboxes, and the
  // thumbnails are left out: pinning more than fits made the grid unpin
  // them in a jumbled order.
  const phone = window.matchMedia('(max-width: 40rem)').matches;
  const defs = view.columns
    .filter((layout) => columnMeta[layout.id])
    .filter((layout) => !(phone && layout.id === 'thumbnail'))
    .map((layout) => {
      let pinned = layout.pinned;
      if (phone) {
        pinned = layout.id === 'number' ? 'left' : undefined;
      } else if (pinned === undefined && PINNED.has(layout.id)) {
        pinned = 'left'; // #, Status, Cue # and Title stay pinned left
      }
      return columnDef(columnMeta[layout.id], {...layout, pinned});
    });
  return [...defs, activeColumn()];
}

/**
 * The eye at the end of every row: shows and toggles whether the cue is
 * active (inactive cues are dimmed and left out of totals).
 * @return {!Object}
 */
function activeColumn() {
  return {
    colId: '_active',
    headerName: '',
    headerTooltip: 'Active',
    pinned: 'right',
    width: 44,
    resizable: false,
    sortable: false,
    suppressMovable: true,
    cellClass: 'cell--active',
    cellRenderer: (params) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'active-toggle';
      button.setAttribute('aria-pressed', String(params.data._active));
      button.setAttribute(
        'aria-label',
        `${params.data.number} is ${params.data._active ? 'active' : 'inactive'}`,
      );
      button.title = params.data._active
        ? 'Active: click to make inactive'
        : 'Inactive: click to make active';
      button.disabled = !config.canEdit;
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        sendEdit(params.data, 'active', !params.data._active);
      });
      return button;
    },
  };
}

/**
 * Applies a view: its columns and its sort (reel first, then the view's).
 * @param {!Object} view
 */
/**
 * No filters: every group empty.
 * @return {!Object}
 */
function emptyFilters() {
  return {
    show: '',
    status: [],
    cue_type: [],
    picture: [],
    priority: [],
    difficulty: [],
    sub_status: [],
    themes: [],
    instrumentation: [],
    conform: false,
  };
}

/**
 * Where the current view's filters are kept.
 * @return {string}
 */
function filterKey() {
  return `reellife.filter.${config.projectId}.${currentView.id}`;
}

/**
 * Loads the current view's filters (each view remembers its own).
 */
function loadFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(filterKey()) || 'null');
    filters = {...emptyFilters(), ...(saved || {})};
  } catch {
    filters = emptyFilters();
  }
}

/**
 * Remembers the current view's filters in this browser.
 */
function saveFilters() {
  try {
    localStorage.setItem(filterKey(), JSON.stringify(filters));
  } catch {
    // Storage can be unavailable; the filter still applies until reload.
  }
}

/**
 * How many filter groups are narrowing the rows.
 * @return {number}
 */
function filterCount() {
  return Object.values(filters).filter((value) =>
    Array.isArray(value) ? value.length : value,
  ).length;
}

/**
 * Whether a row passes the show-inactive toggle, filters and search.
 * @param {!Object} row
 * @return {boolean}
 */
function passes(row) {
  if (row._group) {
    return allRows.some(
      (cue) => cue._picture.id === row._picture.id && passes(cue),
    );
  }
  if (!inactiveToggle.checked && !row._active) {
    return false;
  }
  if (filters.show && !SHOW[filters.show].test(row)) {
    return false;
  }
  if (filters.conform && !row._needs_conform) {
    return false;
  }
  for (const group of FILTER_GROUPS) {
    const chosen = filters[group.key] || [];
    const values = [group.value(row)].flat().map((v) => String(v ?? ''));
    if (chosen.length && !values.some((v) => chosen.includes(v))) {
      return false;
    }
  }
  return searchWords().every((word) =>
    SEARCH_FIELDS.some((field) =>
      String(row[field] ?? '')
        .toLowerCase()
        .includes(word),
    ),
  );
}

/**
 * The find field's words, lowercased; a row must have every one.
 * @return {!Array<string>}
 */
function searchWords() {
  return searchInput.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/**
 * The choices for each filter group, from the column options and rows.
 * @return {!Array<!Object>} Groups of {key, label, options}.
 */
function filterChoices() {
  const reels = new Map();
  const titles = new Map(pictures.map((p) => [p.id, p.title]));
  for (const row of allRows) {
    reels.set(String(row._picture.id), {
      value: String(row._picture.id),
      label: titles.get(row._picture.id) ?? row._picture.name,
      order: row._picture_order,
    });
  }
  const fromColumn = (id) =>
    (columnMeta[id].options || [])
      .filter((o) => o.value !== '' && o.value !== null)
      .map((o) => ({value: String(o.value), label: o.label, color: o.color}));
  return [
    {key: 'status', label: 'Status', options: fromColumn('status')},
    {key: 'cue_type', label: 'Cue Type', options: fromColumn('cue_type')},
    {
      key: 'picture',
      label: config.reelWord,
      options: [...reels.values()].sort((a, b) => a.order - b.order),
    },
    {key: 'priority', label: 'Priority', options: fromColumn('priority')},
    {key: 'difficulty', label: 'Difficulty', options: fromColumn('difficulty')},
    {key: 'sub_status', label: 'Sub Status', options: fromColumn('sub_status')},
    {key: 'themes', label: 'Theme', options: fromColumn('themes')},
    {
      key: 'instrumentation',
      label: 'Instrumentation',
      options: fromColumn('instrumentation'),
    },
  ];
}

/**
 * Builds the Filter panel's checkboxes for the current view's filters.
 */
function buildFilterPanel() {
  filterPanel.replaceChildren();
  const show = document.createElement('fieldset');
  show.className = 'menu__group';
  const showLegend = document.createElement('legend');
  showLegend.textContent = 'Show';
  showLegend.title = keys('Shift+1 to Shift+6');
  show.append(showLegend);
  for (const key of SHOW_KEYS) {
    const label = document.createElement('label');
    label.className = 'menu__choice';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'filter-show';
    radio.checked = filters.show === key;
    radio.addEventListener('change', () => {
      filters.show = key;
      applyFilters();
    });
    label.append(radio, SHOW[key].label);
    show.append(label);
  }
  filterPanel.append(show);
  for (const group of filterChoices()) {
    if (!group.options.length) {
      continue;
    }
    const fieldset = document.createElement('fieldset');
    fieldset.className = 'menu__group';
    const legend = document.createElement('legend');
    legend.textContent = group.label;
    fieldset.append(legend);
    for (const option of group.options) {
      const label = document.createElement('label');
      label.className = 'menu__choice';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = filters[group.key].includes(option.value);
      box.addEventListener('change', () => {
        const chosen = filters[group.key].filter((v) => v !== option.value);
        filters[group.key] = box.checked ? [...chosen, option.value] : chosen;
        applyFilters();
      });
      label.append(box);
      if (option.color) {
        const dot = document.createElement('span');
        dot.className = 'pill__dot';
        dot.style.backgroundColor = option.color;
        label.append(dot);
      }
      label.append(option.label);
      fieldset.append(label);
    }
    filterPanel.append(fieldset);
  }
  const conform = document.createElement('label');
  conform.className = 'menu__choice menu__choice--wide';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = filters.conform;
  box.addEventListener('change', () => {
    filters.conform = box.checked;
    applyFilters();
  });
  conform.append(box, 'Only Cues That Need Conform');
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'button button--quiet button--small';
  clear.dataset.keepOpen = '';
  clear.textContent = 'Clear Filters';
  clear.addEventListener('click', clearFilters);
  const footer = document.createElement('div');
  footer.className = 'filter-panel__footer';
  footer.append(conform, clear);
  filterPanel.append(footer);
}

/**
 * Saves and applies the filters, and shows how many are on.
 */
function applyFilters() {
  saveFilters();
  const count = filterCount();
  let badge = filterButton.querySelector('.toolbar__count');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'toolbar__count';
    filterButton.append(badge);
  }
  badge.textContent = count ? String(count) : '';
  badge.hidden = !count;
  filterButton.classList.toggle('toolbar__button--on', count > 0);
  api.onFilterChanged();
}

/** Turns every filter off (the Filter menu's button, Ctrl+Shift+X). */
function clearFilters() {
  filters = emptyFilters();
  applyFilters();
  buildFilterPanel();
}

/**
 * Whether the timeline draws every cue on one lane (View menu, L).
 * @return {boolean}
 */
function timelineCollapsed() {
  try {
    return localStorage.getItem(TIMELINE_KEY) === '1';
  } catch {
    return false;
  }
}

/**
 * Sets and remembers the timeline's lanes mode.
 * @param {boolean} collapse
 */
function setTimelineCollapsed(collapse) {
  try {
    localStorage.setItem(TIMELINE_KEY, collapse ? '1' : '0');
  } catch {
    // Unavailable storage: the choice applies until reload.
  }
  for (const radio of document.querySelectorAll(
    'input[name="timeline-lanes"]',
  )) {
    radio.checked = radio.value === (collapse ? 'collapsed' : 'all');
  }
  redrawTimeline();
}

/**
 * Finds a cue in the grid: expands its reel, scrolls to it, focuses it.
 * @param {!Object} row
 */
function findInGrid(row) {
  if (collapsed.has(row._picture.id)) {
    toggleReel(row._picture.id);
  }
  const node = api.getRowNode(String(row.id));
  if (!node || node.rowIndex === null) {
    announce(`${row.number} is hidden by the filters.`, true);
    return;
  }
  api.ensureNodeVisible(node, 'middle');
  api.setFocusedCell(node.rowIndex, 'number');
  api.flashCells({rowNodes: [node]});
}

/**
 * Draws the timeline from the shown cues (it follows the filters).
 */
function redrawTimeline() {
  drawTimeline(timelineElement, {
    pictures,
    rows: allRows.filter(passes),
    lanes: config.lanes,
    collapsed: timelineCollapsed(),
    fps: FPS,
    statusOf,
    ink: inkFor,
    onPick: findInGrid,
    onOpen: (row) => openCue(row.id),
    onLane: config.canEdit ? (row, lane) => sendEdit(row, 'lane', lane) : null,
  });
}

/**
 * The reels collapsed in this browser.
 * @return {!Set<number>}
 */
function loadCollapsed() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]'));
  } catch {
    return new Set();
  }
}

/**
 * Collapses or expands a reel's cues.
 * @param {number} pictureId
 */
function toggleReel(pictureId) {
  if (collapsed.has(pictureId)) {
    collapsed.delete(pictureId);
  } else {
    collapsed.add(pictureId);
  }
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
  } catch {
    // Unavailable storage: the reel stays collapsed until reload.
  }
  api.onFilterChanged();
}

/**
 * Collapses or expands every reel.
 * @param {boolean} collapse
 */
function setAllReels(collapse) {
  collapsed = collapse ? new Set(pictures.map((p) => p.id)) : new Set();
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
  } catch {
    // As above.
  }
  api.onFilterChanged();
}

/**
 * A reel's cues that pass the filters (shown, or hidden by collapsing).
 * @param {number} pictureId
 * @return {!Array<!Object>}
 */
function shownCues(pictureId) {
  return allRows.filter((row) => row._picture.id === pictureId && passes(row));
}

/**
 * Whether all, some or none of a reel's shown cues are selected.
 * @param {number} pictureId
 * @return {string}
 */
function reelCheckState(pictureId) {
  const ids = new Set(shownCues(pictureId).map((row) => row.id));
  const selected = api
    ? api.getSelectedRows().filter((row) => ids.has(row.id)).length
    : 0;
  if (!selected) {
    return 'none';
  }
  return selected === ids.size ? 'all' : 'some';
}

/**
 * The reel checkbox: its cues, or by category with a modifier.
 * @param {number} pictureId
 * @param {!MouseEvent} event
 */
function selectReel(pictureId, event) {
  const nodes = shownCueNodes(api, passes).filter(
    (node) => node.data._picture.id === pictureId,
  );
  const test = modifierTest(event, statusOf);
  if (test) {
    selectNodes(
      api,
      nodes.filter((node) => test(node.data)),
      false,
    );
    return;
  }
  const all = reelCheckState(pictureId) === 'all';
  api.setNodesSelected({nodes, newValue: !all});
}

/**
 * Selects every shown cue, or by category with a modifier (the header
 * checkbox).
 * @param {?MouseEvent=} event
 */
function selectAllShown(event = null) {
  const nodes = shownCueNodes(api, passes);
  const test = event ? modifierTest(event, statusOf) : null;
  selectNodes(
    api,
    test ? nodes.filter((node) => test(node.data)) : nodes,
    false,
  );
}

/**
 * Updates the selection summary and the reel checkboxes.
 */
function onSelectionChanged() {
  let totalFrames = 0;
  for (const row of allRows) {
    if (passes(row)) {
      totalFrames += Math.max(row.duration?.frames ?? 0, 0);
    }
  }
  showSummary(
    selectionElement,
    api.getSelectedRows(),
    totalFrames,
    DURATION_FPS,
    {
      deselect: () => api.deselectAll(),
      invert: () => invert(api, shownCueNodes(api, passes)),
    },
  );
  const groups = [];
  api.forEachNode((node) => node.data._group && groups.push(node));
  api.redrawRows({rowNodes: groups});
}

/** Columns the user resized in this view (their widths are kept). */
const resized = new Set();

function showView(view) {
  resized.clear();
  currentView = view;
  loadFilters();
  buildFilterPanel();
  const defs = columnsFor(view);
  api.setGridOption('columnDefs', defs);
  // AG Grid keeps existing columns where they were when definitions change,
  // so apply the view's order explicitly, with its sort (reels stay in
  // order: group rows keep each reel's cues together).
  const sortBy = new Map();
  view.sort.forEach((entry, index) =>
    sortBy.set(entry.id, {sort: entry.dir, sortIndex: index}),
  );
  api.applyColumnState({
    state: defs.map((def) => ({
      colId: def.colId,
      ...(sortBy.get(def.colId) || {sort: null}),
    })),
    applyOrder: true,
  });
  applyFilters();
  // After this frame, so the view's rows are drawn to be measured.
  requestAnimationFrame(autoSizeColumns);
  for (const button of tabsElement.querySelectorAll('[data-view-id]')) {
    button.setAttribute(
      'aria-pressed',
      String(button.dataset.viewId === String(view.id)),
    );
  }
  try {
    localStorage.setItem(VIEW_KEY, String(view.id));
  } catch {
    // Storage can be unavailable (private windows); the view still shows.
  }
}

/**
 * Builds the view tabs under the grid.
 */
function buildTabs() {
  tabsElement.replaceChildren();
  if (config.canEdit) {
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'view-tab view-tab--add';
    add.textContent = '+';
    add.title = keys('New view (Control+Shift+=)');
    add.setAttribute('aria-label', 'New View');
    add.addEventListener('click', newView);
    tabsElement.append(add);
  }
  views.forEach((view, index) => {
    const wrap = document.createElement('span');
    wrap.className = 'view-tab-wrap';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'view-tab';
    button.dataset.viewId = String(view.id);
    button.textContent = view.name;
    if (index < 9) {
      // Chrome on a Mac keeps ⌘1–9 for its tabs, so these use Control.
      button.title = keys(`${view.name} (Control+${index + 1})`);
    }
    button.setAttribute(
      'aria-pressed',
      String(currentView !== null && currentView.id === view.id),
    );
    button.addEventListener('click', () => showView(view));
    wrap.append(button);
    if (config.canEdit) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'view-tab__menu';
      more.textContent = '▾';
      more.setAttribute('aria-label', `${view.name} view options`);
      more.title = 'Edit, duplicate or delete this view';
      more.addEventListener('click', () => viewMenu(view, more));
      wrap.append(more);
    }
    tabsElement.append(wrap);
  });
}

/**
 * Sends a change to the views API.
 * @param {string} method
 * @param {string} url
 * @param {?Object=} body
 * @return {!Promise<?Object>} The answer, or null after showing an error.
 */
async function viewRequest(method, url, body = null) {
  const response = await fetch(url, request(method, body));
  const answer = await response
    .json()
    .catch(() => ({error: 'The server sent an unexpected reply.'}));
  if (!response.ok) {
    announce(
      answer.error || `Couldn't save the view (${response.status}).`,
      true,
    );
    return null;
  }
  return answer;
}

/**
 * The URL of one view (or, without an id, of the project's views).
 * @param {(number|string)=} id
 * @return {string}
 */
function viewUrl(id = undefined) {
  return id === undefined
    ? config.viewsUrl
    : config.viewUrl.replace('/0/', `/${id}/`);
}

/**
 * Takes the project's views from an answer, showing `show` (by id) or
 * the current one again.
 * @param {!Array<!Object>} fresh
 * @param {?number=} show
 */
function useViews(fresh, show = null) {
  views = fresh;
  const target =
    views.find((v) => v.id === show) ||
    views.find((v) => v.name === currentView?.name) ||
    views[0];
  currentView = target;
  buildTabs();
  showView(target);
}

/**
 * The columns every view can show, for the editor.
 * @return {!Array<!Object>}
 */
function editorFields() {
  return Object.values(columnMeta).map((c) => ({id: c.id, label: c.label}));
}

/** Opens the editor for a new view, starting from the current one. */
async function newView() {
  const result = await editView({
    title: 'New View',
    name: '',
    fixedName: false,
    columns: currentView.columns,
    fields: editorFields(),
    presets: views.filter((v) => v.builtIn),
  });
  if (result) {
    const answer = await viewRequest('POST', viewUrl(), {
      ...result,
      sort: currentView.sort,
    });
    if (answer) {
      useViews([...views, answer.view], answer.view.id);
      announce(`Added the ${answer.view.name} view.`);
    }
  }
}

/**
 * Edits a view's name and columns.
 * @param {!Object} view
 */
async function editViewDialog(view) {
  const result = await editView({
    title: `Edit View: ${view.name}`,
    name: view.name,
    fixedName: view.builtIn,
    columns: view.columns,
    fields: editorFields(),
    presets: views.filter((v) => v.builtIn && v.id !== view.id),
  });
  if (result) {
    const answer = await viewRequest('PATCH', viewUrl(view.id), result);
    if (answer) {
      useViews(answer.views, answer.view.id);
      announce(`Saved the ${answer.view.name} view.`);
    }
  }
}

/**
 * A copy of a view under a new name.
 * @param {!Object} view
 */
async function duplicateView(view) {
  const taken = new Set(views.map((v) => v.name.toLowerCase()));
  let n = 2;
  while (taken.has(`${view.name} ${n}`.toLowerCase())) {
    n += 1;
  }
  const answer = await viewRequest('POST', viewUrl(), {
    name: `${view.name} ${n}`,
    columns: view.columns,
    sort: view.sort,
  });
  if (answer) {
    useViews([...views, answer.view], answer.view.id);
    announce(`Duplicated as ${answer.view.name}.`);
  }
}

/**
 * Deletes a project's view, or resets a changed standard one.
 * @param {!Object} view
 */
async function deleteView(view) {
  const reset = view.builtIn;
  const question = reset
    ? `Reset ${view.name} to its standard columns?`
    : `Delete the ${view.name} view?`;
  if (!(await ask(question, reset ? 'Reset' : 'Delete View'))) {
    return;
  }
  const answer = await viewRequest('DELETE', viewUrl(view.id));
  if (answer) {
    useViews(answer.views);
    announce(reset ? `Reset ${view.name}.` : `Deleted ${view.name}.`);
  }
}

/**
 * The ▾ menu on a view tab.
 * @param {!Object} view
 * @param {!HTMLElement} anchor
 */
function viewMenu(view, anchor) {
  const box = anchor.getBoundingClientRect();
  showMenu(box.left, box.top - 8, [
    {label: 'Edit…', action: () => editViewDialog(view)},
    {label: 'Duplicate', action: () => duplicateView(view)},
    null,
    view.builtIn
      ? {
          label: 'Reset to Standard',
          action: () => deleteView(view),
          disabled: !view.changed,
        }
      : {label: 'Delete', action: () => deleteView(view)},
  ]);
}

/**
 * Saves the grid's column order, widths, pinning and sort to the view,
 * a moment after the user changes them.
 */
function saveLayoutSoon() {
  if (!config.canEdit || typeof currentView.id !== 'number') {
    return;
  }
  clearTimeout(saveLayoutSoon.timer);
  saveLayoutSoon.timer = setTimeout(saveLayout, 600);
}

/** Saves the grid's layout to the current view now. */
async function saveLayout() {
  const view = currentView;
  const kept = new Map(view.columns.map((c) => [c.id, c]));
  const columns = api
    .getColumnState()
    .filter(
      (c) => !c.hide && c.colId !== SELECTION_COLUMN && c.colId !== '_active',
    )
    .map((c) => {
      const entry = {id: c.colId};
      // Widths only where the user set one; the rest keep sizing to fit.
      if (kept.get(c.colId)?.width || resized.has(c.colId)) {
        entry.width = Math.round(c.width);
      }
      if (c.pinned) {
        entry.pinned = c.pinned;
      }
      return entry;
    });
  const sort = api
    .getColumnState()
    .filter((c) => c.sort)
    .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
    .map((c) => ({id: c.colId, dir: c.sort}));
  const answer = await viewRequest('PATCH', viewUrl(view.id), {columns, sort});
  if (answer) {
    // A standard view was copied into the project: its id changed.
    const saved = answer.view;
    views = answer.views;
    if (currentView.name === saved.name) {
      currentView = views.find((v) => v.id === saved.id);
      buildTabs();
    }
  }
}

/**
 * The view to open: the last one used here, else the first.
 * @return {!Object}
 */
function initialView() {
  let saved = null;
  try {
    saved = localStorage.getItem(VIEW_KEY);
  } catch {
    saved = null;
  }
  return views.find((view) => String(view.id) === saved) || views[0];
}

/**
 * Sends one cell edit and puts the server's row in place.
 * @param {!Object} event AG Grid cellValueChanged event.
 */
async function saveEdit(event) {
  const field = event.colDef.colId;
  const selected = api.getSelectedRows();
  if (
    config.canEdit &&
    selected.length >= 2 &&
    selected.some((row) => row.id === event.data.id) &&
    !config.notBatch.includes(field)
  ) {
    const choice = await askBatch({
      field: event.colDef.headerName,
      value: shownValue(field, event.newValue),
      count: selected.length,
    });
    if (choice.apply) {
      await sendBatch(
        selected.map((row) => row.id),
        field,
        event.newValue,
        choice.keep,
      );
      return;
    }
  }
  await sendEdit(event.data, field, event.newValue);
}

/**
 * A value as the grid shows it, for the batch question.
 * @param {string} field
 * @param {(string|number|boolean|null|!Array<?>)} value
 * @return {string}
 */
function shownValue(field, value) {
  const options = columnMeta[field]?.options || [];
  const label = (item) =>
    options.find((o) => o.value === (item?.id ?? item))?.label ??
    item?.name ??
    item?.label ??
    String(item ?? '');
  if (Array.isArray(value)) {
    return value.map(label).join(', ');
  }
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No';
  }
  return label(value);
}

/** Each cue's saves still in flight, chained so they go one at a time. */
const saving = new Map();

/** Edits typed but not yet saved, per cue id, oldest first. */
const unsaved = new Map();

window.addEventListener('beforeunload', (event) => {
  if (saving.size) {
    event.preventDefault(); // the browser asks before leaving
  }
});

/**
 * Sends a request, turning a network failure into a reply like the
 * server's errors so every caller handles both the same way.
 * @param {string} url
 * @param {!Object} init
 * @return {!Promise<{ok: boolean, status: number, body: !Object}>}
 */
async function send(url, init) {
  let response;
  try {
    response = await fetch(url, init);
  } catch {
    return {
      ok: false,
      status: 0,
      body: {error: "Couldn't reach ReelLife, so this wasn't saved."},
    };
  }
  const body = await response
    .json()
    .catch(() => ({error: 'The server sent an unexpected reply.'}));
  return {ok: response.ok, status: response.status, body};
}

/**
 * Applies one edit to several cues (one change set) and shows them.
 * @param {!Array<number>} ids
 * @param {string} field
 * @param {(string|number|boolean|null|!Array<?>)} value
 * @param {boolean} keep Keep the selection afterwards.
 */
async function sendBatch(ids, field, value, keep) {
  const reply = await send(
    config.batchUrl,
    request('POST', {ids, field, value}),
  );
  if (!reply.ok) {
    if (reply.status) {
      await reload(); // nothing changed: show the cues as they are
    }
    announce(reply.body.error || `Couldn't save (${reply.status}).`, true);
    return;
  }
  if (columnMeta[field]?.creatable) {
    await refreshOptions(field);
  }
  updateRows(reply.body.rows);
  api.redrawRows();
  if (!keep) {
    api.deselectAll();
  }
  announce(`Saved ${reply.body.rows.length} cues.`);
  notifyChanged();
}

/**
 * Runs one of the Settings menu's grid actions (project_menu.html).
 * @param {?string} action 'export', 'stats' or 'copy-json'.
 */
function gridAction(action) {
  if (action === 'export') {
    openExport();
  } else if (action === 'stats') {
    toggleSidePanel('stats', document.getElementById('stats-toggle'));
  } else if (action === 'copy-json') {
    copyCuesJson();
  }
}

/**
 * Copy JSON for the selected cues, else the focused one, else every cue
 * shown (up to the server's limit), in grid order.
 */
function copyCuesJson() {
  let ids = api.getSelectedRows().map((row) => row.id);
  if (!ids.length && focusedRow()) {
    ids = [focusedRow().id];
  }
  if (!ids.length) {
    api.forEachNodeAfterFilterAndSort((node) => {
      if (!node.data._group) {
        ids.push(node.data.id);
      }
    });
  }
  if (!ids.length) {
    announce('No cues to copy.', true);
    return;
  }
  if (ids.length > MAX_JSON_CUES) {
    announce(
      `Copy JSON takes up to ${MAX_JSON_CUES} cues; select fewer.`,
      true,
    );
    return;
  }
  copyJson(`${config.jsonUrl}?ids=${ids.join(',')}`).then((message) =>
    announce(message),
  );
}

/**
 * Puts the server's copies of rows in the grid (and in allRows), with
 * any edits still waiting to be saved laid over them, and redraws the
 * timeline once.
 * @param {!Array<!Object>} rows
 */
function updateRows(rows) {
  const fresh = new Map(
    rows.map((row) => {
      const shown = {...row};
      for (const edit of unsaved.get(row.id) ?? []) {
        shown[edit.field] = edit.value;
      }
      return [row.id, shown];
    }),
  );
  allRows = allRows.map((old) => fresh.get(old.id) ?? old);
  api.applyTransaction({update: [...fresh.values()]});
  redrawTimeline();
}

/**
 * Saves one field edit after the cue's earlier edits have saved, so
 * each sends the revision the one before it got back.
 * @param {!Object} row The row as the grid has it.
 * @param {string} field
 * @param {(string|number|boolean|null)} value
 * @return {!Promise<void>}
 */
function sendEdit(row, field, value) {
  const edit = {field, value};
  unsaved.set(row.id, [...(unsaved.get(row.id) ?? []), edit]);
  const run = (saving.get(row.id) ?? Promise.resolve()).then(() =>
    saveOne(row.id, edit),
  );
  saving.set(row.id, run);
  return run.finally(() => {
    if (saving.get(row.id) === run) {
      saving.delete(row.id);
    }
  });
}

/**
 * Sends one queued edit and shows the server's row.
 * @param {number} id
 * @param {{field: string, value: ?}} edit
 */
async function saveOne(id, edit) {
  const {field, value} = edit;
  const current = allRows.find((row) => row.id === id);
  const reply = await send(
    config.editUrl.replace('/0/', `/${id}/`),
    request('PATCH', {field, value, updated_at: current._updated_at}),
  );
  const left = (unsaved.get(id) ?? []).filter((other) => other !== edit);
  if (left.length) {
    unsaved.set(id, left);
  } else {
    unsaved.delete(id);
  }
  const {body} = reply;
  if (reply.ok) {
    const known = (option) =>
      columnMeta[field].options.some((o) => o.value === option);
    const saved = [body.row[field]].flat();
    if (
      columnMeta[field]?.creatable &&
      !saved.every((option) => option === null || known(option?.id ?? option))
    ) {
      await refreshOptions(field); // the edit added a new choice
    }
    updateRows([body.row]);
    // Pills, the eye and the row's dimming follow the status and active
    // flag, and reel totals follow durations: redraw the row and its reel.
    api.redrawRows({
      rowNodes: [
        api.getRowNode(String(body.row.id)),
        api.getRowNode(groupId(body.row._picture.id)),
      ].filter(Boolean),
    });
    announce(`Saved ${body.row.number}.`);
    notifyChanged(body.header);
    return;
  }
  // Errors carry the row as the server has it, so the cell shows the
  // truth again. Without one the server wasn't reached: say what didn't
  // save, so it can be typed again, and show the last saved values.
  if (body.row) {
    updateRows([body.row]);
    announce(body.error || `Couldn't save (${reply.status}).`, true);
    return;
  }
  const what =
    `${current.number} ${columnMeta[field]?.label ?? field} ` +
    `(you typed “${shownValue(field, value)}”)`;
  try {
    await reload();
    announce(`${body.error} ${what} is back to its saved value.`, true);
  } catch {
    announce(`${body.error} ${what} is not saved yet.`, true);
  }
}

/**
 * Fetches a column's choices again (after the server added one).
 * @param {string} field
 */
async function refreshOptions(field) {
  const response = await fetch(config.dataUrl, {credentials: 'same-origin'});
  const data = await response.json();
  const fresh = data.columns.find((c) => c.id === field);
  if (fresh) {
    columnMeta[field].options = fresh.options;
    buildFilterPanel();
  }
}

/**
 * What the grid shows with no rows: how to add cues, or that the
 * filters hide them all.
 * @return {string} The overlay's HTML (fixed text, nothing from data).
 */
function emptyOverlay() {
  if (allRows.length) {
    return (
      '<div class="grid-empty"><p>No cues match the filters.</p>' +
      '<div class="grid-empty__actions"><button class="button button--quiet"' +
      ' type="button" data-empty="clear">Clear Filters</button></div></div>'
    );
  }
  if (!config.canEdit) {
    return '<div class="grid-empty"><p>No cues yet.</p></div>';
  }
  return (
    '<div class="grid-empty"><p>No cues yet. Import a Pro Tools session,' +
    ' a Cubase marker track, a MIDI file or a spreadsheet, or add cues' +
    ' one by one.</p><div class="grid-empty__actions">' +
    '<button class="button" type="button" data-empty="import">' +
    'Import Cues</button><button class="button button--quiet"' +
    ' type="button" data-empty="new">New Cue</button></div></div>'
  );
}

/**
 * Reloads every row (after undo).
 */
async function reload() {
  const response = await fetch(config.dataUrl, {credentials: 'same-origin'});
  const data = await response.json();
  allRows = data.rows;
  pictures = data.pictures;
  writtenTo = data.written_to;
  api.setGridOption('overlayNoRowsTemplate', emptyOverlay());
  api.setGridOption('rowData', withGroups(data.rows, data.pictures));
  buildFilterPanel();
  api.redrawRows();
  redrawTimeline();
}

/**
 * Undoes the user's last change in this project.
 */
async function undo() {
  const response = await fetch(config.undoUrl, request('POST'));
  const body = await response.json();
  if (response.ok) {
    await reload();
    announce(`Undid: ${body.undone}.`);
    notifyChanged();
  } else {
    announce(body.error, true);
  }
}

/**
 * Row classes: inactive and needs conform.
 * @param {!Object} params
 * @return {!Array<string>}
 */
function rowClass(params) {
  const classes = [];
  if (params.data._group) {
    return ['group-row-node'];
  }
  if (!params.data._active) {
    classes.push('cue-row--inactive');
  }
  if (params.data._needs_conform) {
    classes.push('cue-row--conform');
  }
  return classes;
}

/**
 * Whether the event is a letter key: by physical key (any layout), or by
 * character for synthesized events that carry no code.
 * @param {!KeyboardEvent} event
 * @param {string} letter Lowercase letter.
 * @return {boolean}
 */
function isLetter(event, letter) {
  return (
    event.code === `Key${letter.toUpperCase()}` ||
    event.key.toLowerCase() === letter
  );
}

/**
 * Keyboard shortcuts that aren't AG Grid's own.
 * @param {!KeyboardEvent} event
 */
function onKey(event) {
  if (document.querySelector('dialog[open]')) {
    return;
  }
  const modifier = event.ctrlKey || event.metaKey;
  // The dialog shortcuts also work mid-edit (single-click editing keeps
  // a cell open most of the time); the edit is saved first.
  if (modifier && !event.shiftKey && isLetter(event, 'n')) {
    // Ctrl+N; Cmd+N stays the browser's (it can't be intercepted).
    // Looked up each time: the header is re-fetched after edits.
    const newCueButton = document.getElementById('grid-new-cue');
    if (newCueButton) {
      event.preventDefault();
      api.stopEditing();
      newCueButton.click();
    }
    return;
  }
  if (modifier && event.shiftKey && isLetter(event, 'v')) {
    const row = focusedRow();
    if (config.canEdit && row) {
      event.preventDefault();
      api.stopEditing();
      openNewVersion(row.id);
    }
    return;
  }
  if (modifier && !event.shiftKey && isLetter(event, 'i') && importButton) {
    event.preventDefault();
    api.stopEditing();
    importButton.click();
    return;
  }
  if (modifier && event.shiftKey && isLetter(event, 'j')) {
    event.preventDefault();
    const row = focusedRow();
    if (row) {
      copyJson(`${config.jsonUrl}?ids=${row.id}`).then((message) =>
        announce(message),
      );
    } else {
      announce('Pick a cue in the grid first.', true);
    }
    return;
  }
  if (modifier && !event.shiftKey && isLetter(event, 'e')) {
    event.preventDefault();
    openExport();
    return;
  }
  if (modifier && event.shiftKey && isLetter(event, 'x')) {
    event.preventDefault();
    clearFilters();
    return;
  }
  if (modifier && event.shiftKey && isLetter(event, 's')) {
    event.preventDefault();
    toggleSidePanel('stats', document.getElementById('stats-toggle'));
    return;
  }
  if (modifier && event.shiftKey && event.key === 'Backspace') {
    event.preventDefault();
    openTool('delete');
    return;
  }
  if (modifier && event.key === 'Enter' && focusedRow()) {
    event.preventDefault();
    openDetails();
    return;
  }
  if (api.getEditingCells().length || event.target.closest(TYPING_FIELDS)) {
    return;
  }
  const plain = !modifier && !event.altKey && !event.shiftKey;
  const menuId = plain ? MENU_KEYS[event.key.toLowerCase()] : undefined;
  if (modifier && event.shiftKey && !event.altKey) {
    // Ctrl+Shift+A / D / I: select all shown, deselect, invert.
    const action = isLetter(event, 'a')
      ? () => selectAllShown()
      : isLetter(event, 'd')
        ? () => api.deselectAll()
        : isLetter(event, 'i')
          ? () => invert(api, shownCueNodes(api, passes))
          : null;
    if (action) {
      event.preventDefault();
      action();
      return;
    }
  }
  if (
    modifier &&
    !event.shiftKey &&
    isLetter(event, 'c') &&
    event.target.closest('.ag-root') &&
    !window.getSelection()?.toString()
  ) {
    event.preventDefault();
    copyCells(false);
    return;
  }
  if (plain && event.key === 'Enter') {
    const cell = api.getFocusedCell();
    const node = cell && api.getDisplayedRowAtIndex(cell.rowIndex);
    if (node?.data._group) {
      event.preventDefault();
      toggleReel(node.data._picture.id);
      return;
    }
  }
  if (
    modifier &&
    event.shiftKey &&
    (event.code === 'Equal' || event.key === '+') &&
    config.canEdit
  ) {
    event.preventDefault();
    newView();
    return;
  }
  if (modifier && event.key === 'z' && !event.shiftKey) {
    event.preventDefault();
    undo();
  } else if (modifier && !event.shiftKey && isLetter(event, 'f')) {
    event.preventDefault();
    openSearch();
  } else if (modifier && /^[1-9]$/.test(event.key)) {
    const view = views[Number(event.key) - 1];
    if (view) {
      event.preventDefault();
      showView(view);
    }
  } else if (menuId) {
    event.preventDefault();
    toggleMenu(document.getElementById(menuId));
  } else if (
    event.shiftKey &&
    !modifier &&
    !event.altKey &&
    /^Digit[1-6]$/.test(event.code)
  ) {
    // Shift+1-6: the Show filter (by key position; Shift changes the
    // character).
    filters.show = SHOW_KEYS[Number(event.code.slice(5)) - 1];
    applyFilters();
    buildFilterPanel();
  } else if (plain && /^[1-5]$/.test(event.key)) {
    setRowSize(ROW_SIZES[Number(event.key) - 1]);
  } else if (plain && event.key === 'l') {
    setTimelineCollapsed(!timelineCollapsed());
  } else if (plain && event.key === 'h') {
    inactiveToggle.checked = !inactiveToggle.checked;
    api.onFilterChanged();
  }
}

/**
 * Whether a key on a focused (not editing) cell is a page shortcut, so
 * the grid leaves it alone instead of starting an edit (cells are
 * in select mode until Enter).
 * @param {!Object} params AG Grid suppressKeyboardEvent params.
 * @return {boolean}
 */
function isPageShortcut(params) {
  const event = params.event;
  if (params.editing || event.ctrlKey || event.metaKey || event.altKey) {
    return false;
  }
  if (event.shiftKey) {
    return /^Digit[1-6]$/.test(event.code); // Show filter
  }
  const key = event.key.toLowerCase();
  return key in MENU_KEYS || PAGE_KEYS.has(key) || /^[1-5]$/.test(key);
}

/**
 * Opens a toolbar menu (closing the others), or closes it if open.
 * @param {!HTMLDetailsElement} menu
 */
function toggleMenu(menu) {
  const opening = !menu.open;
  for (const other of document.querySelectorAll('details.menu[open]')) {
    other.open = false;
  }
  menu.open = opening;
  if (opening) {
    const first = menu.querySelector(
      '.menu__panel button:not([disabled]), .menu__panel input',
    );
    (first || menu.querySelector('summary')).focus();
  }
}

/**
 * Applies and remembers a row size (a per-browser preference).
 * @param {string} size One of ROW_SIZES.
 */
function setRowSize(size) {
  try {
    localStorage.setItem(ROW_SIZE_KEY, size);
  } catch {
    // Unavailable storage: the size applies until reload.
  }
  for (const radio of document.querySelectorAll('input[name="row-size"]')) {
    radio.checked = radio.value === size;
  }
  rowMinimum(size);
  api.setGridOption('rowHeight', ROW_HEIGHTS[size]);
  api.resetRowHeights();
}

/**
 * Opens a panel beside the grid (or closes it if it's already open).
 * @param {string} name What it shows, e.g. 'notes'.
 * @param {!HTMLElement} button The button that toggles it.
 */
async function toggleSidePanel(name, button) {
  const panel = document.getElementById('side-panel');
  if (!panel.hidden && panel.dataset.showing === name) {
    closeSidePanel();
    return;
  }
  await window.htmx.ajax('GET', button.dataset.panelUrl, {
    target: '#side-panel',
    swap: 'innerHTML',
  });
  panel.hidden = false;
  panel.dataset.showing = name;
  for (const [key, id] of Object.entries(PANEL_TOGGLES)) {
    document
      .getElementById(id)
      ?.setAttribute('aria-expanded', String(name === key));
  }
}

/** Closes the panel beside the grid. */
function closeSidePanel() {
  const panel = document.getElementById('side-panel');
  panel.hidden = true;
  panel.dataset.showing = '';
  panel.replaceChildren();
  for (const id of Object.values(PANEL_TOGGLES)) {
    document.getElementById(id)?.setAttribute('aria-expanded', 'false');
  }
}

/**
 * The grid filter values a stats row stands for.
 * @param {!HTMLElement} button The row's swatch or name.
 * @return {{group: !Object, values: !Array<string>}}
 */
function statValues(button) {
  return {
    group: FILTER_GROUPS.find((g) => g.key === button.dataset.group),
    values: button.dataset.values.split(','),
  };
}

/**
 * A stats row's swatch: selects its shown cues (Shift adds).
 * @param {!HTMLElement} button
 * @param {boolean} add
 */
function selectStat(button, add) {
  const {group, values} = statValues(button);
  const nodes = shownCueNodes(api, passes).filter((node) =>
    [group.value(node.data)]
      .flat()
      .some((v) => values.includes(String(v ?? ''))),
  );
  selectNodes(api, nodes, add);
}

/**
 * A stats row's name: filters to its cues (Shift adds to that filter).
 * @param {!HTMLElement} button
 * @param {boolean} add
 */
function filterStat(button, add) {
  const {group, values} = statValues(button);
  const before = add ? filters[group.key] || [] : [];
  filters[group.key] = [...new Set([...before, ...values])];
  buildFilterPanel();
  applyFilters();
}

/**
 * Opens one kind of project notes in the page's dialog.
 * @param {string} kind
 */
function openProjectNotes(kind) {
  window.htmx.ajax('GET', `${config.notesPanelUrl}?kind=${kind}`, {
    target: '#app-dialog',
    swap: 'innerHTML',
  });
}

/**
 * The columns shown, in order, without the checkbox and eye columns.
 * @return {!Array<!Object>}
 */
function dataColumns() {
  return api
    .getAllDisplayedColumns()
    .filter((c) => ![SELECTION_COLUMN, '_active'].includes(c.getColId()));
}

/**
 * Copies the selected cues as tab-separated rows (or the focused cell).
 * @param {boolean} withHeaders
 */
async function copyCells(withHeaders) {
  const columns = dataColumns();
  const value = (node, column) =>
    String(
      api.getCellValue({rowNode: node, colKey: column, useFormatter: true}) ??
        '',
    ).replace(/[\t\n]+/g, ' ');
  let lines;
  const selected = api.getSelectedNodes();
  if (selected.length) {
    const order = [];
    api.forEachNodeAfterFilterAndSort((node) => order.push(node));
    lines = order
      .filter((node) => node.isSelected())
      .map((node) => columns.map((c) => value(node, c)).join('\t'));
    if (withHeaders) {
      lines.unshift(columns.map((c) => c.getColDef().headerName).join('\t'));
    }
  } else {
    const cell = api.getFocusedCell();
    const node = cell && api.getDisplayedRowAtIndex(cell.rowIndex);
    if (!node || node.data._group) {
      announce('Pick a cue in the grid first.', true);
      return;
    }
    lines = [value(node, cell.column)];
    if (withHeaders) {
      lines.unshift(cell.column.getColDef().headerName);
    }
  }
  try {
    await navigator.clipboard.writeText(lines.join('\n'));
    announce(
      `Copied ${selected.length || 1} ${selected.length > 1 ? 'rows' : 'cell'}.`,
    );
  } catch {
    announce("The browser didn't allow copying.", true);
  }
}

/**
 * Opens a cue's dialog from the grid's config (htmx loads the form).
 * @param {string} url The dialog's address for cue 0.
 * @param {number} cueId
 */
function openCueDialog(url, cueId) {
  window.htmx.ajax('GET', url.replace('/0/', `/${cueId}/`), {
    target: '#app-dialog',
    swap: 'innerHTML',
  });
}

/**
 * The right-click menu on a cue's cell.
 * @param {!Object} params AG Grid cell context menu event.
 */
function cellMenu(params) {
  const cueId = params.node?.data?.id;
  showMenu(params.event.clientX, params.event.clientY, [
    {label: 'Copy', shortcut: 'Ctrl+C', action: () => copyCells(false)},
    {label: 'Copy with Headers', action: () => copyCells(true)},
    null,
    {
      label: 'Schedule On…',
      action: () => openCueDialog(config.scheduleOnUrl, cueId),
      disabled: !config.canEdit || !cueId,
    },
    {
      label: 'Log Written…',
      action: () => openCueDialog(config.logWrittenUrl, cueId),
      disabled: !config.canEdit || !cueId,
    },
    null,
    {label: 'Autosize All Columns', action: () => api.autoSizeAllColumns()},
    null,
    {
      label: api.getSelectedRows().length
        ? 'Export Selected Cues…'
        : 'Export Cues…',
      shortcut: 'Ctrl+E',
      action: openExport,
    },
  ]);
}

/**
 * The right-click menu on a column header.
 * @param {!MouseEvent} event
 */
function headerMenu(event) {
  const header = event.target.closest('.ag-header-cell[col-id]');
  if (!header) {
    return;
  }
  const colId = header.getAttribute('col-id');
  if (colId === SELECTION_COLUMN || colId === '_active') {
    return;
  }
  event.preventDefault();
  const pinned = api.getColumn(colId)?.getPinned() ?? null;
  const pin = (side) => () => {
    api.setColumnsPinned([colId], side);
    saveLayoutSoon();
  };
  showMenu(event.clientX, event.clientY, [
    {label: 'Pin Left', action: pin('left'), disabled: pinned === 'left'},
    {label: 'Pin Right', action: pin('right'), disabled: pinned === 'right'},
    {label: 'No Pin', action: pin(null), disabled: !pinned},
    null,
    {label: 'Autosize This Column', action: () => api.autoSizeColumns([colId])},
    {label: 'Autosize All Columns', action: () => api.autoSizeAllColumns()},
    {
      label: 'Hide Column',
      action: () => {
        api.setColumnsVisible([colId], false);
        saveLayoutSoon();
      },
    },
    {
      label: currentView.changed ? 'Reset View to Standard' : 'Reset Columns',
      action: () =>
        currentView.changed ? deleteView(currentView) : showView(currentView),
    },
    null,
    ...(NOTE_KINDS[colId]
      ? [
          {
            label: 'Project Notes',
            action: () => openProjectNotes(NOTE_KINDS[colId]),
          },
          null,
        ]
      : []),
    {
      label: `Expand All ${config.reelsWord}`,
      action: () => setAllReels(false),
    },
    {
      label: `Collapse All ${config.reelsWord}`,
      action: () => setAllReels(true),
    },
  ]);
}

/**
 * Loads the data and creates the grid.
 */
async function start() {
  const response = await fetch(config.dataUrl, {credentials: 'same-origin'});
  const data = await response.json();
  columnMeta = Object.fromEntries(data.columns.map((c) => [c.id, c]));
  enterSaves = data.settings.enter_behaviour === 'save';
  views = data.views.length
    ? data.views
    : // Seeded views gone (e.g. deleted in the admin): show every column.
      [
        {
          id: 'all',
          name: 'All Columns',
          columns: data.columns.map((c) => ({id: c.id})),
          sort: [],
        },
      ];
  allRows = data.rows;
  pictures = data.pictures;
  writtenTo = data.written_to;
  rowMinimum(rowSize());
  api = createGrid(gridElement, {
    theme: gridTheme(),
    styleNonce: config.styleNonce,
    rowData: withGroups(data.rows, data.pictures),
    getRowId: (params) => String(params.data.id),
    getRowClass: rowClass,
    rowHeight: ROW_HEIGHTS[rowSize()],
    getRowHeight: (params) =>
      params.data._group ? GROUP_ROW_HEIGHT : undefined,
    defaultColDef: {suppressKeyboardEvent: isPageShortcut},
    // Reels as group rows of our own (row grouping is Enterprise).
    isFullWidthRow: (params) => Boolean(params.rowNode.data?._group),
    fullWidthCellRenderer: groupRenderer({
      isCollapsed: (id) => collapsed.has(id),
      toggle: toggleReel,
      select: selectReel,
      checkState: reelCheckState,
    }),
    postSortRows: keepGroupsAbove,
    // Views have at most ~40 columns: draw them all, so every column can
    // be measured when sizing to content.
    suppressColumnVirtualisation: true,
    rowSelection: {
      mode: 'multiRow',
      checkboxes: true,
      headerCheckbox: true,
      enableClickSelection: false,
      isRowSelectable: (node) => !node.data?._group,
    },
    selectionColumnDef: {
      pinned: 'left',
      width: 44,
      suppressHeaderMenuButton: true,
    },
    onSelectionChanged,
    preventDefaultOnContextMenu: true,
    onCellContextMenu: (params) =>
      params.event && !params.data?._group && cellMenu(params),
    onCellClicked: (params) => {
      // Alt-click a cue's checkbox: every shown cue with its status
      // (Alt+Shift adds them).
      if (
        params.column.getColId() === SELECTION_COLUMN &&
        params.event.altKey
      ) {
        const status = params.data.status;
        setTimeout(() =>
          selectNodes(
            api,
            shownCueNodes(api, passes).filter((n) => n.data.status === status),
            params.event.shiftKey,
          ),
        );
      }
    },
    singleClickEdit: data.settings.single_click_edit,
    stopEditingWhenCellsLoseFocus: true,
    undoRedoCellEditing: false,
    tooltipShowDelay: 400,
    isExternalFilterPresent: () => true,
    doesExternalFilterPass: (node) =>
      node.data._group
        ? passes(node.data)
        : !collapsed.has(node.data._picture.id) && passes(node.data),
    onCellValueChanged: saveEdit,
    // Cells that can't be edited open the cue instead.
    onCellDoubleClicked: (params) => {
      if (!params.colDef.editable) {
        openDetails();
      }
    },
    onSortChanged: (event) => {
      api.redrawRows();
      if (event.source !== 'api') {
        saveLayoutSoon();
      }
    },
    // Moving, resizing, pinning and hiding columns save to the view.
    onColumnMoved: (event) => event.finished && saveLayoutSoon(),
    onColumnResized: (event) => {
      if (
        event.finished &&
        event.source !== 'api' &&
        event.source !== 'autosizeColumns'
      ) {
        event.columns?.forEach((c) => resized.add(c.getColId()));
        saveLayoutSoon();
      }
    },
    onColumnPinned: (event) => event.source !== 'api' && saveLayoutSoon(),
    onFilterChanged: () => {
      api.setGridOption('overlayNoRowsTemplate', emptyOverlay());
      api.redrawRows();
      redrawTimeline();
      clearTimeout(announce.timer);
      statusElement.textContent = filterSummary();
      statusElement.classList.remove('grid-status--error');
      onSelectionChanged();
    },
    overlayNoRowsTemplate: emptyOverlay(),
  });
  // The empty overlay's buttons do what the toolbar's do.
  gridElement.addEventListener('click', (event) => {
    const action = event.target.closest('[data-empty]')?.dataset.empty;
    if (action === 'import') {
      importButton?.click();
    } else if (action === 'new') {
      document.getElementById('grid-new-cue')?.click();
    } else if (action === 'clear') {
      clearFilters();
    }
  });
  buildTabs();
  showView(initialView());
  gridElement.dataset.ready = 'true'; // the grid has its data
  // New cue from another project page arrives as ?new=1, and the
  // Settings menu's grid actions as ?do=.
  const query = new URLSearchParams(window.location.search);
  if (query.has('new')) {
    history.replaceState(null, '', window.location.pathname);
    document.getElementById('grid-new-cue')?.click();
  } else if (query.has('do')) {
    history.replaceState(null, '', window.location.pathname);
    gridAction(query.get('do'));
  }
  inactiveToggle.addEventListener('change', () => api.onFilterChanged());
  searchInput.addEventListener('input', () => api.onFilterChanged());
  document
    .getElementById('grid-search-open')
    .addEventListener('click', openSearch);
  searchInput.addEventListener('blur', () => {
    if (!searchInput.value) {
      findElement.classList.remove('toolbar__find--open');
    }
  });
  searchInput.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !searchInput.value) {
      searchInput.blur();
    }
  });
  for (const radio of document.querySelectorAll('input[name="row-size"]')) {
    radio.checked = radio.value === rowSize();
    radio.addEventListener('change', () => setRowSize(radio.value));
  }
  for (const radio of document.querySelectorAll(
    'input[name="timeline-lanes"]',
  )) {
    radio.checked = radio.value === (timelineCollapsed() ? 'collapsed' : 'all');
    radio.addEventListener('change', () =>
      setTimelineCollapsed(radio.value === 'collapsed'),
    );
  }
  if (undoButton) {
    undoButton.addEventListener('click', undo);
  }
  // Delegated: the header holding these is re-fetched after edits.
  document.addEventListener('click', (event) => {
    if (event.target.closest('#grid-details')) {
      openDetails();
    } else if (event.target.closest('#notes-toggle')) {
      toggleSidePanel('notes', event.target.closest('#notes-toggle'));
    } else if (event.target.closest('#stats-toggle')) {
      toggleSidePanel('stats', event.target.closest('#stats-toggle'));
    } else if (event.target.closest('[data-stat-select]')) {
      selectStat(event.target.closest('[data-stat-select]'), event.shiftKey);
    } else if (event.target.closest('[data-stat-filter]')) {
      filterStat(event.target.closest('[data-stat-filter]'), event.shiftKey);
    } else if (event.target.closest('[data-side-panel-close]')) {
      closeSidePanel();
    } else if (event.target.closest('[data-grid-action]')) {
      gridAction(event.target.closest('[data-grid-action]').dataset.gridAction);
    }
  });
  exportButton.addEventListener('click', openExport);
  openTool = setUpTools({
    api,
    url: config.toolUrl,
    menu: document.getElementById('tools-menu'),
  });
  document.addEventListener('keydown', onKey);
  gridElement.addEventListener('contextmenu', headerMenu);
  // Modifier clicks on the header checkbox select by category.
  gridElement.addEventListener(
    'click',
    (event) => {
      const all = event.target.closest('.ag-header-select-all');
      if (all && modifierTest(event, statusOf)) {
        event.preventDefault();
        event.stopPropagation();
        selectAllShown(event);
      }
    },
    true,
  );
  // A dialog saved a cue or version: fetch the rows again.
  document.body.addEventListener('cues:reload', () => {
    reload();
    announce('Saved.');
  });
  // An import says what it did (it arrives after cues:reload).
  document.body.addEventListener('cues:imported', (event) => {
    announce(event.detail.message);
  });
}

start().catch((error) => {
  console.error(error); // for whoever opens the developer tools
  announce('The cue grid failed to load. Reload the page.', true);
});
