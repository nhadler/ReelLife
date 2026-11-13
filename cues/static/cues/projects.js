/**
 * @fileoverview The Projects page: search as you type, sort, Show
 * Archived, and bulk actions.
 *
 * Typing filters the listed projects at once (every word must be in the
 * title, production number or series) and choosing a sort reorders them;
 * the arrow beside Sort reverses it. All of it is kept in the address,
 * and the sort and direction are remembered in this browser. Show
 * Archived reloads the list with archived projects as soon as it's
 * ticked; they sort among the rest. A project filtered out is unticked,
 * so a bulk action never touches one you can't see; the bulk bar shows
 * only while something is ticked.
 */

const SORT_KEY = 'reeltime.projectSort';
const DIRECTION_KEY = 'reeltime.projectSortDirection';

/**
 * @param {string} key
 * @return {?string} The stored value, or null if storage is unavailable.
 */
function stored(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/**
 * @param {string} key
 * @param {string} value
 */
function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private windows may refuse; the sort just isn't remembered.
  }
}

/**
 * Orders two projects for a sort. Blank values (no number, no deadline)
 * go after the rest, whichever the direction.
 * @param {string} by changed, title, number or deadline.
 * @param {string} direction asc or desc.
 * @return {function(!Element, !Element): number}
 */
function comparer(by, direction) {
  const field = ['title', 'number', 'deadline'].includes(by) ? by : 'changed';
  const sign = direction === 'desc' ? -1 : 1;
  return (a, b) => {
    const x = a.dataset[field];
    const y = b.dataset[field];
    return !x - !y || sign * x.localeCompare(y);
  };
}

const searchForm = document.querySelector('[data-project-search]');
const archivedBox = document.getElementById('show-archived');
const list = document.querySelector('.projects');

// Show Archived reloads the list (archived projects aren't on the page).
archivedBox?.addEventListener('change', () => searchForm.submit());

if (searchForm && list) {
  const field = searchForm.querySelector('[name="q"]');
  const sort = searchForm.querySelector('[name="sort"]');
  const direction = searchForm.querySelector('input[name="dir"]');
  const flip = searchForm.querySelector('button[data-direction]');
  const naturally = () => sort.selectedOptions[0].dataset.direction;
  const noMatch = document.querySelector('[data-no-match]');
  const projects = [...list.children];

  const remember = () => {
    const params = new URLSearchParams(window.location.search);
    const query = field.value.trim();
    if (query) {
      params.set('q', query);
    } else {
      params.delete('q');
    }
    if (sort.value === 'changed') {
      params.delete('sort');
    } else {
      params.set('sort', sort.value);
    }
    if (direction.value === naturally()) {
      params.delete('dir');
    } else {
      params.set('dir', direction.value);
    }
    const search = params.toString();
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${search ? `?${search}` : ''}`,
    );
  };

  const filter = () => {
    const words = field.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const project of projects) {
      const match = words.every((word) =>
        project.dataset.search.includes(word),
      );
      project.hidden = !match;
      shown += match ? 1 : 0;
      const box = project.querySelector('[data-bulk-item]');
      if (!match && box?.checked) {
        box.checked = false;
        box.dispatchEvent(new Event('change'));
      }
    }
    noMatch.hidden = shown > 0;
    remember();
  };

  const order = () => {
    const ascending = direction.value === 'asc';
    flip.textContent = ascending ? '↑' : '↓';
    flip.setAttribute(
      'aria-label',
      ascending ? 'Ascending; reverse' : 'Descending; reverse',
    );
    list.append(...[...projects].sort(comparer(sort.value, direction.value)));
    remember();
  };

  // The sort chosen last time, unless the address names one.
  const saved = stored(SORT_KEY);
  if (
    !new URLSearchParams(window.location.search).has('sort') &&
    saved &&
    [...sort.options].some((option) => option.value === saved)
  ) {
    sort.value = saved;
    direction.value = stored(DIRECTION_KEY) || naturally();
  }
  field.addEventListener('input', filter);
  sort.addEventListener('change', () => {
    direction.value = naturally();
    store(SORT_KEY, sort.value);
    store(DIRECTION_KEY, direction.value);
    order();
  });
  flip.addEventListener('click', (event) => {
    event.preventDefault();
    direction.value = direction.value === 'asc' ? 'desc' : 'asc';
    store(DIRECTION_KEY, direction.value);
    order();
  });
  // Enter just filters; the list is already here.
  searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    filter();
  });
  order();
  filter();
}

const form = document.querySelector('[data-bulk]');

// The bulk bar shows only once a project is ticked; the checkboxes sit
// over the thumbnails.
if (form) {
  const all = form.querySelector('[data-bulk-all]');
  const clear = form.querySelector('[data-bulk-clear]');
  const count = form.querySelector('[data-bulk-count]');
  const items = [...document.querySelectorAll('[data-bulk-item]')];
  const visible = () =>
    items.filter((item) => !item.closest('.project').hidden);

  const update = () => {
    const ticked = items.filter((item) => item.checked).length;
    form.hidden = !ticked;
    count.textContent = `${ticked} selected`;
    all.hidden = ticked === visible().length;
  };

  all.addEventListener('click', () => {
    for (const item of visible()) {
      item.checked = true;
    }
    update();
  });
  clear.addEventListener('click', () => {
    for (const item of items) {
      item.checked = false;
    }
    update();
  });
  for (const item of items) {
    item.addEventListener('change', update);
  }
  searchForm?.querySelector('[name="q"]')?.addEventListener('input', update);
  update();
}
