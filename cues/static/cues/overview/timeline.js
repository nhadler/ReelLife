/**
 * @fileoverview The timeline strip above the grid, as
 * drawn: a segment per reel, as wide as its picture is long,
 * with a minute ruler and the cues as blocks in lanes, coloured by status.
 *
 * A cue's lane is 0 ("automatic": the first lane where it doesn't overlap
 * the cue before it) or a lane picked by dragging its block. Click a
 * block to find the cue in the grid, double-click to open it. Positions
 * are set through the CSSOM (element.style), which the CSP allows.
 */

// A lane: an 18px block and a 4px gap.
const LANE_PX = 22;

/**
 * Places a reel's cues in lanes (0-based).
 * @param {!Array<!Object>} cues The reel's cues with timecodes, by IN.
 * @param {number} lanes How many lanes (at least 1).
 * @return {!Map<number, number>} Cue id to lane index.
 */
export function placeInLanes(cues, lanes) {
  const ends = new Array(lanes).fill(-Infinity);
  const placed = new Map();
  for (const cue of cues) {
    const start = cue.tc_in.frames;
    let lane = cue._lane > 0 ? Math.min(cue._lane, lanes) - 1 : -1;
    if (lane < 0) {
      lane = ends.findIndex((end) => end <= start);
      if (lane < 0) {
        lane = ends.indexOf(Math.min(...ends)); // all busy: least so
      }
    }
    ends[lane] = Math.max(ends[lane], cue.tc_out.frames);
    placed.set(cue.id, lane);
  }
  return placed;
}

/**
 * The cue's place in its reel, as percentages of the reel's length.
 * @param {!Object} cue
 * @param {!Object} reel With tc_in and tc_out in frames.
 * @return {{left: number, width: number}}
 */
export function span(cue, reel) {
  const length = reel.tc_out - reel.tc_in;
  const left = ((cue.tc_in.frames - reel.tc_in) / length) * 100;
  const width = ((cue.tc_out.frames - cue.tc_in.frames) / length) * 100;
  return {left, width: Math.max(width, 0.3)};
}

/**
 * The minute ruler: a dot per minute, a number every five.
 * @param {!Object} reel
 * @param {number} fps
 * @return {!HTMLElement}
 */
function ruler(reel, fps) {
  const element = document.createElement('div');
  element.className = 'timeline__ruler';
  element.setAttribute('aria-hidden', 'true');
  const minutes = (reel.tc_out - reel.tc_in) / (fps * 60);
  for (let minute = 0; minute < minutes; minute += 1) {
    const mark = document.createElement('span');
    mark.className = minute % 5 === 0 ? 'timeline__minute' : 'timeline__dot';
    mark.textContent = minute % 5 === 0 ? String(minute) : '·';
    mark.style.left = `${(minute / minutes) * 100}%`;
    element.append(mark);
  }
  return element;
}

/**
 * Draws the timeline.
 * @param {!HTMLElement} container
 * @param {!Object} input
 * @param {!Array<!Object>} input.pictures Reels with tc_in and tc_out.
 * @param {!Array<!Object>} input.rows The cues to show.
 * @param {number} input.lanes The project's lanes.
 * @param {boolean} input.collapsed All cues on one lane.
 * @param {number} input.fps
 * @param {function(!Object): ?Object} input.statusOf
 * @param {function(string): string} input.ink Text colour for a fill.
 * @param {function(!Object)} input.onPick Click: find in the grid.
 * @param {function(!Object)} input.onOpen Double-click: open the cue.
 * @param {?function(!Object, number)} input.onLane Dropped on a lane
 *     (1-based); null when the user can't edit.
 */
export function drawTimeline(container, input) {
  const reels = input.pictures.filter(
    (p) => p.tc_in !== null && p.tc_out !== null && p.tc_out > p.tc_in,
  );
  container.replaceChildren();
  if (!input.lanes) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  if (!reels.length) {
    const empty = document.createElement('p');
    empty.className = 'timeline__empty';
    empty.textContent = 'Log a turnover with IN and OUT to see the timeline.';
    container.append(empty);
    return;
  }
  const lanes = input.collapsed ? 1 : input.lanes;
  const total = reels.reduce((sum, r) => sum + r.tc_out - r.tc_in, 0);
  for (const reel of reels) {
    const segment = document.createElement('section');
    segment.className = 'timeline__reel';
    segment.style.flexGrow = String((reel.tc_out - reel.tc_in) / total);
    segment.setAttribute('aria-label', reel.title);
    const label = document.createElement('span');
    label.className = 'timeline__label';
    label.textContent = reel.title;
    const track = document.createElement('div');
    track.className = 'timeline__lanes';
    track.style.height = `${lanes * LANE_PX}px`;
    const cues = input.rows
      .filter(
        (row) =>
          row._picture.id === reel.id &&
          row.tc_in?.frames !== null &&
          row.tc_in?.frames !== undefined &&
          row.tc_out?.frames !== null &&
          row.tc_out?.frames !== undefined,
      )
      .sort((a, b) => a.tc_in.frames - b.tc_in.frames);
    const placed = placeInLanes(
      input.collapsed ? cues.map((c) => ({...c, _lane: 0})) : cues,
      lanes,
    );
    for (const cue of cues) {
      track.append(block(cue, reel, placed.get(cue.id), input));
    }
    segment.append(label, ruler(reel, input.fps), track);
    container.append(segment);
  }
}

/**
 * One cue's block.
 * @param {!Object} cue
 * @param {!Object} reel
 * @param {number} lane 0-based.
 * @param {!Object} input See drawTimeline.
 * @return {!HTMLElement}
 */
function block(cue, reel, lane, input) {
  const {left, width} = span(cue, reel);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'timeline__cue';
  button.textContent = `${cue.number} ${cue.title || ''}`.trim();
  button.title = `${cue.number} ${cue.title || ''}\n${cue.tc_in.display} – ${cue.tc_out.display} (${cue.duration?.display ?? ''})`;
  button.style.left = `${left}%`;
  button.style.width = `${width}%`;
  button.style.top = `${lane * LANE_PX}px`;
  const color = input.statusOf(cue)?.color;
  if (color) {
    button.style.backgroundColor = color;
    button.style.color = input.ink(color);
  }
  if (!cue._active) {
    button.classList.add('timeline__cue--inactive');
  }
  button.addEventListener('click', () => input.onPick(cue));
  button.addEventListener('dblclick', () => input.onOpen(cue));
  if (input.onLane && !input.collapsed) {
    dragBetweenLanes(button, cue, lane, input);
  }
  return button;
}

/**
 * Lets a block be dragged up or down to another lane.
 * @param {!HTMLElement} button
 * @param {!Object} cue
 * @param {number} lane Its lane now, 0-based.
 * @param {!Object} input See drawTimeline.
 */
function dragBetweenLanes(button, cue, lane, input) {
  button.addEventListener('pointerdown', (down) => {
    if (down.button !== 0) {
      return;
    }
    const startY = down.clientY;
    let target = lane;
    const move = (event) => {
      const offset = Math.round((event.clientY - startY) / LANE_PX);
      target = Math.min(Math.max(lane + offset, 0), input.lanes - 1);
      button.style.top = `${target * LANE_PX}px`;
      button.classList.toggle('timeline__cue--dragging', target !== lane);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if (target !== lane) {
        input.onLane(cue, target + 1);
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });
}
