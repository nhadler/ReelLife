/**
 * @fileoverview The review page's player and comment box.
 *
 * The video has its own controls so comments can sit on the scrubber as
 * marks: play, frame steps, the timecode of the frame on screen, mute
 * and full screen. Positions are whole frames at the project's rate; the
 * timecode is the video's first frame's (from the server) plus the frame.
 *
 * Typing a comment pauses the video. A comment is about the frame on
 * screen unless "At" is unticked; Enter sends it, Shift+Enter breaks the
 * line. Reply on a comment turns the box into a reply to it. Clicking a
 * comment's timecode or its mark goes to its frame.
 */

const config = JSON.parse(document.getElementById('review-config').textContent);
const FPS = config.fps[0] / config.fps[1];
const JUMP_SECONDS = 5;

const video = document.getElementById('review-video');
const player = document.getElementById('review-player');
const seek = document.getElementById('review-seek');
const marks = document.getElementById('review-marks');
const time = document.getElementById('review-time');
const list = document.getElementById('review-list');
const form = document.getElementById('review-form');

/**
 * A frame count as a timecode label, e.g. 1:02:14:08, with ';' before
 * the frames and skipped labels at drop-frame rates.
 * @param {number} frames Frames since 00:00:00:00.
 * @return {string}
 */
function label(frames) {
  const nominal = config.nominal;
  let count = frames;
  if (config.dropFrame) {
    const drop = Math.floor(nominal / 15);
    const perMinute = nominal * 60 - drop;
    const perTen = nominal * 600 - 9 * drop;
    const tens = Math.floor(count / perTen);
    const rest = count % perTen;
    count += 9 * drop * tens;
    if (rest > drop) {
      count += drop * Math.floor((rest - drop) / perMinute);
    }
  }
  const pad = (value) => String(value).padStart(2, '0');
  const f = count % nominal;
  const seconds = Math.floor(count / nominal);
  const s = seconds % 60;
  const m = Math.floor(seconds / 60) % 60;
  const h = Math.floor(seconds / 3600);
  return `${h}:${pad(m)}:${pad(s)}${config.dropFrame ? ';' : ':'}${pad(f)}`;
}

/**
 * The frame on screen at a time in the video.
 * @param {number} seconds
 * @return {number}
 */
function frameAt(seconds) {
  return Math.max(0, Math.floor(seconds * FPS + 0.01));
}

/** @return {number} The video's last frame (0 until its length is known). */
function lastFrame() {
  return Number.isFinite(video.duration)
    ? Math.max(0, frameAt(video.duration) - 1)
    : 0;
}

/**
 * Shows a frame: the middle of it, so rounding can't land on the one
 * before.
 * @param {number} frame
 */
function show(frame) {
  const target = Math.min(Math.max(0, frame), lastFrame());
  video.currentTime = (target + 0.5) / FPS;
}

/** Updates the timecode, the scrubber and the comment box's "At". */
function update() {
  const frame = frameAt(video.currentTime);
  const text = label(config.firstFrame + frame);
  if (time.value !== text) {
    time.value = text;
    seek.value = String(frame);
    seek.setAttribute('aria-valuetext', text);
    const at = document.getElementById('review-at');
    if (at) {
      at.textContent = text;
    }
    highlight(frame);
  }
}

/**
 * Marks the comment the video is at: the last one at or before the
 * frame, for the two seconds after it.
 * @param {number} frame
 */
function highlight(frame) {
  let current = null;
  for (const item of list.querySelectorAll('[data-frame]')) {
    const at = Number(item.dataset.frame);
    item.classList.remove('is-current');
    if (at <= frame && frame - at < 2 * FPS) {
      current = item;
    }
  }
  current?.classList.add('is-current');
}

/** Draws each timed comment as a mark on the scrubber. */
function drawMarks() {
  const last = lastFrame();
  marks.replaceChildren();
  if (!last) {
    return;
  }
  for (const item of list.querySelectorAll('[data-frame]')) {
    const frame = Math.min(Number(item.dataset.frame), last);
    const mark = document.createElement('button');
    mark.type = 'button';
    mark.className = 'review-scrub__mark';
    mark.classList.toggle(
      'is-resolved',
      item.classList.contains('is-resolved'),
    );
    mark.tabIndex = -1;
    mark.title = item.dataset.summary;
    mark.style.setProperty('--at', String(frame / last));
    mark.addEventListener('click', () => show(frame));
    marks.append(mark);
  }
}

/** Plays or pauses. */
function toggle() {
  if (video.paused) {
    video.play().catch(() => {});
  } else {
    video.pause();
  }
}

/** Mutes or unmutes. */
function mute() {
  video.muted = !video.muted;
}

/** Enters or leaves full screen: the player with its controls. */
function fullScreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen();
  } else if (player.requestFullscreen) {
    player.requestFullscreen().catch(() => {});
  } else {
    // iPhone: only the video itself can go full screen.
    video.webkitEnterFullscreen?.();
  }
}

/**
 * Steps frames from the one on screen, pausing first.
 * @param {number} frames
 */
function step(frames) {
  video.pause();
  show(frameAt(video.currentTime) + frames);
}

video.addEventListener('loadedmetadata', () => {
  seek.max = String(lastFrame());
  drawMarks();
  update();
});
video.addEventListener('timeupdate', update);
video.addEventListener('seeked', update);
video.addEventListener('click', toggle);
video.addEventListener('error', () => {
  document.getElementById('review-video-error').hidden = false;
  player.classList.add('is-broken');
  // Nothing on screen to comment at: comments are on the whole cue.
  const timed = document.getElementById('review-timed');
  if (timed) {
    timed.checked = false;
    timed.disabled = true;
    document.getElementById('review-timed-field').hidden = true;
  }
});
for (const name of ['play', 'pause', 'volumechange']) {
  video.addEventListener(name, () => {
    player.classList.toggle('is-playing', !video.paused);
    player.classList.toggle('is-muted', video.muted);
    const play = document.getElementById('review-play');
    play.setAttribute('aria-label', video.paused ? 'Play' : 'Pause');
    document
      .getElementById('review-mute')
      .setAttribute('aria-pressed', String(video.muted));
  });
}
// Between timeupdate events (four a second) while playing, follow each
// frame the browser shows, where it can say.
if ('requestVideoFrameCallback' in video) {
  const follow = () => {
    update();
    video.requestVideoFrameCallback(follow);
  };
  video.requestVideoFrameCallback(follow);
}

seek.addEventListener('input', () => show(Number(seek.value)));
document.getElementById('review-play').addEventListener('click', toggle);
document.getElementById('review-back').addEventListener('click', () => {
  step(-1);
});
document.getElementById('review-forward').addEventListener('click', () => {
  step(1);
});
document.getElementById('review-mute').addEventListener('click', mute);
document.getElementById('review-full').addEventListener('click', fullScreen);

const KEYS = {
  ' ': toggle,
  k: toggle,
  ArrowLeft: () => step(-1),
  ArrowRight: () => step(1),
  j: () => (video.currentTime -= JUMP_SECONDS),
  l: () => (video.currentTime += JUMP_SECONDS),
  m: mute,
  f: fullScreen,
};
document.addEventListener('keydown', (event) => {
  const action = KEYS[event.key];
  if (
    !action ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    event.target.closest('input, textarea, select, button, a')
  ) {
    return;
  }
  event.preventDefault();
  action();
});

// Comments: go to one's frame; redraw the marks when the list changes.
list.addEventListener('click', (event) => {
  const button = event.target.closest('[data-seek]');
  if (button) {
    video.pause();
    show(Number(button.dataset.seek));
  }
});
list.addEventListener('htmx:afterSwap', () => {
  drawMarks();
  highlight(frameAt(video.currentTime));
});

if (form) {
  const body = document.getElementById('review-body');
  const timed = document.getElementById('review-timed');
  const frame = document.getElementById('review-frame');
  body.addEventListener('input', () => video.pause());
  body.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  const parent = document.getElementById('review-parent');
  const replying = document.getElementById('review-replying');
  /**
   * Starts a reply to a note, or with no id goes back to a new comment.
   * @param {string=} id The note's id.
   * @param {string=} name Who wrote it.
   */
  const replyTo = (id = '', name = '') => {
    parent.value = id;
    replying.hidden = !id;
    document.getElementById('review-replying-name').textContent = name;
    // A reply is about its note, not a frame.
    document.getElementById('review-timed-field').hidden =
      Boolean(id) || timed.disabled;
  };
  list.addEventListener('click', (event) => {
    const button = event.target.closest('[data-reply]');
    if (button) {
      replyTo(button.dataset.reply, button.dataset.replyName);
      body.focus();
    }
  });
  // The frame on screen as the comment is sent, unless "At" is unticked
  // or it's a reply.
  form.addEventListener('htmx:configRequest', (event) => {
    const value =
      timed.checked && !parent.value ? String(frameAt(video.currentTime)) : '';
    frame.value = value;
    event.detail.parameters.frame = value;
  });
  document.body.addEventListener('review:posted', () => {
    body.value = '';
    timed.checked = !timed.disabled;
    replyTo();
  });
  form.addEventListener('click', (event) => {
    if (event.target.closest('[data-reply-cancel]')) {
      replyTo();
    }
    if (event.target.closest('[data-review-rename]')) {
      const field = form.querySelector('.review-name .field');
      field.hidden = false;
      field.querySelector('input').focus();
    }
  });
}

update();
