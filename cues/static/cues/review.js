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
 *
 * A version can also have its audio alone, in a small player under the
 * video; playing one pauses the other, and a comment made while
 * listening to the audio is about the whole cue.
 *
 * This script runs after the video has started loading, so whatever it
 * listens for (the length arriving, an error) may already have happened:
 * each is also checked once at the end.
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
// A frame asked for before the video's length was known.
let pending = null;
// Whether the audio-only player was the last thing played.
let listening = false;

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

/** @return {boolean} Whether the video's length is known yet. */
function known() {
  return Number.isFinite(video.duration) && video.duration > 0;
}

/** @return {number} The video's last frame (0 until its length is known). */
function lastFrame() {
  return known() ? Math.max(0, frameAt(video.duration) - 1) : 0;
}

/**
 * The frame on screen: never past the last one, where the video rests
 * when it ends.
 * @return {number}
 */
function position() {
  const frame = frameAt(video.currentTime);
  return known() ? Math.min(frame, lastFrame()) : frame;
}

/**
 * Shows a frame: the middle of it, so rounding can't land on the one
 * before. Before the video's length is known the frame is kept and
 * shown once it is.
 * @param {number} frame
 */
function show(frame) {
  listening = false;
  showAt();
  if (!known()) {
    pending = Math.max(0, frame);
    return;
  }
  const target = Math.min(Math.max(0, frame), lastFrame());
  video.currentTime = (target + 0.5) / FPS;
}

/** Updates the timecode, the scrubber and the comment box's "At". */
function update() {
  const frame = pending ?? position();
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
  show((pending ?? position()) + frames);
  update();
}

/** The video's length is known (or has changed): size what depends on it. */
function sized() {
  if (!known()) {
    return;
  }
  seek.max = String(lastFrame());
  drawMarks();
  if (pending !== null) {
    const frame = pending;
    pending = null;
    show(frame);
  }
  update();
}

/** Shows or hides the comment box's "At": only the video has frames. */
function showAt() {
  const field = document.getElementById('review-timed-field');
  if (field) {
    field.hidden =
      listening ||
      player.classList.contains('is-broken') ||
      Boolean(document.getElementById('review-parent').value);
  }
}

/** The video can't play: say so, with nothing to comment "at". */
function broken() {
  document.getElementById('review-video-error').hidden = false;
  player.classList.add('is-broken');
  showAt();
}

video.addEventListener('loadedmetadata', sized);
video.addEventListener('durationchange', sized);
video.addEventListener('timeupdate', update);
video.addEventListener('seeked', update);
video.addEventListener('click', toggle);
video.addEventListener('error', broken);
video.addEventListener('play', () => {
  listening = false;
  showAt();
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
const BUTTONS = {
  'review-play': toggle,
  'review-back': () => step(-1),
  'review-forward': () => step(1),
  'review-mute': mute,
  'review-full': fullScreen,
};
for (const [id, action] of Object.entries(BUTTONS)) {
  document.getElementById(id).addEventListener('click', (event) => {
    action();
    // After a mouse click the button would keep the keyboard, and Space
    // would press it again instead of playing.
    if (event.detail > 0) {
      event.currentTarget.blur();
    }
  });
}

// Keys held down repeat; these act once per press.
const ONCE = new Set([' ', 'k', 'm', 'f']);
const KEYS = {
  ' ': toggle,
  k: toggle,
  arrowleft: () => step(-1),
  arrowright: () => step(1),
  j: () => (video.currentTime -= JUMP_SECONDS),
  l: () => (video.currentTime += JUMP_SECONDS),
  m: mute,
  f: fullScreen,
};

/**
 * Whether a key belongs to what has focus rather than to the player:
 * anything typed into, a slider's own arrows, a button's Space or Enter.
 * @param {!KeyboardEvent} event
 * @return {boolean}
 */
function ownKey(event) {
  const target = event.target;
  if (!(target instanceof Element)) {
    return false;
  }
  const kind = target.matches('input') ? target.type : '';
  if (
    target.closest('textarea, select, [contenteditable]') ||
    (kind && !['range', 'checkbox', 'button'].includes(kind))
  ) {
    return true;
  }
  if (kind === 'range') {
    return event.key.startsWith('Arrow');
  }
  return (
    Boolean(target.closest('button, a, summary, input')) &&
    (event.key === ' ' || event.key === 'Enter')
  );
}

document.addEventListener('keydown', (event) => {
  // Letters whatever Caps Lock says.
  const key = event.key.toLowerCase();
  const action = KEYS[key];
  if (
    !action ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    ownKey(event) ||
    document.querySelector('dialog[open]')
  ) {
    return;
  }
  event.preventDefault();
  if (!(event.repeat && ONCE.has(key))) {
    action();
  }
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
  highlight(position());
});
// A refresh says which list it has, and gets nothing back while that's
// still the one. None is sent while a question is open over the page:
// the button that asked it would be replaced before the answer.
list.addEventListener('htmx:configRequest', (event) => {
  if (event.detail.verb !== 'get' || event.detail.elt !== list) {
    return;
  }
  if (document.querySelector('dialog[open]')) {
    event.preventDefault();
    return;
  }
  const stamp = list.querySelector('[data-stamp]')?.dataset.stamp;
  if (stamp) {
    event.detail.parameters.stamp = stamp;
  }
});

/**
 * Says under the comment box why something sent from the page failed.
 * @param {!CustomEvent} event htmx:responseError or htmx:sendError.
 */
function failed(event) {
  const told = document.getElementById('review-form-error');
  if (!told || event.detail.requestConfig?.verb === 'get') {
    return; // refreshes fail quietly and try again
  }
  const status = event.detail.xhr?.status;
  told.textContent =
    {
      403: 'This page is out of date. Reload it and try again.',
      404: 'This link no longer works. Reload the page.',
      429: 'Too many at once. Wait a minute and try again.',
    }[status] ?? "That couldn't be sent. Check your connection and try again.";
}
document.body.addEventListener('htmx:responseError', failed);
document.body.addEventListener('htmx:sendError', failed);

if (form) {
  const body = document.getElementById('review-body');
  const timed = document.getElementById('review-timed');
  const frame = document.getElementById('review-frame');
  // The comment as it was last sent, so only that text is cleared.
  let sent = null;
  body.addEventListener('input', () => {
    video.pause();
    document.getElementById('review-audio-source')?.pause();
  });
  body.addEventListener('keydown', (event) => {
    // 229: Safari's Enter that finishes typing in an input method.
    const composing = event.isComposing || event.keyCode === 229;
    if (event.key === 'Enter' && !event.shiftKey && !composing) {
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
    showAt();
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
    const at = document.getElementById('review-timed-field');
    const value = timed.checked && !at.hidden ? String(position()) : '';
    frame.value = value;
    event.detail.parameters.frame = value;
    sent = body.value;
  });
  document.body.addEventListener('review:posted', () => {
    // Anything typed since it was sent is the next comment: keep it.
    if (body.value === sent) {
      body.value = '';
    }
    replyTo();
  });
  // The note being replied to was deleted meanwhile.
  document.body.addEventListener('review:reply-gone', () => replyTo());
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

// The music alone, in its own player under the video.
const audio = document.getElementById('review-audio-source');
if (audio) {
  const box = document.getElementById('review-audio');
  const audioSeek = document.getElementById('review-audio-seek');
  const audioTime = document.getElementById('review-audio-time');
  /**
   * Seconds as m:ss.
   * @param {number} seconds
   * @return {string}
   */
  const clock = (seconds) => {
    const whole = Number.isFinite(seconds) ? Math.floor(seconds) : 0;
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
  };
  const showAudio = () => {
    if (Number.isFinite(audio.duration)) {
      audioSeek.max = String(audio.duration);
    }
    audioSeek.value = String(audio.currentTime);
    const text = `${clock(audio.currentTime)} / ${clock(audio.duration)}`;
    audioTime.value = text;
    audioSeek.setAttribute('aria-valuetext', text);
  };
  const audioBroken = () => {
    document.getElementById('review-audio-error').hidden = false;
    box.classList.add('is-broken');
  };
  audio.addEventListener('loadedmetadata', showAudio);
  audio.addEventListener('durationchange', showAudio);
  audio.addEventListener('timeupdate', showAudio);
  audio.addEventListener('error', audioBroken);
  for (const name of ['play', 'pause', 'ended']) {
    audio.addEventListener(name, () => {
      box.classList.toggle('is-playing', !audio.paused);
      document
        .getElementById('review-audio-play')
        .setAttribute(
          'aria-label',
          audio.paused ? 'Play the audio' : 'Pause the audio',
        );
    });
  }
  // One at a time: the video and the audio are the same music. While
  // the audio is what's being heard, a comment is on the whole cue.
  audio.addEventListener('play', () => {
    video.pause();
    listening = true;
    showAt();
  });
  video.addEventListener('play', () => audio.pause());
  audioSeek.addEventListener('input', () => {
    audio.currentTime = Number(audioSeek.value);
  });
  document.getElementById('review-audio-play').addEventListener('click', () => {
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  });
  if (audio.error) {
    audioBroken();
  } else {
    showAudio();
  }
}

// The visit: tell the server now and then how long has played and how
// far the video got, so whoever sent the link can see it was watched.
if (config.visit) {
  const REPORT_MS = 15000;
  const token = JSON.parse(document.body.getAttribute('hx-headers') || '{}')[
    'X-CSRFToken'
  ];
  let played = 0;
  let furthest = null;
  let last = performance.now();
  /** Adds the time since the last look, if anything was playing. */
  const count = () => {
    const now = performance.now();
    const sound = document.getElementById('review-audio-source');
    if (!video.paused || (sound && !sound.paused)) {
      played += (now - last) / 1000;
    }
    last = now;
  };
  const report = () => {
    count();
    const sent = new URLSearchParams({
      visit: config.visit,
      played: String(Math.round(played)),
    });
    if (furthest !== null) {
      sent.set('furthest', String(furthest));
    }
    // keepalive: it still goes when the page is being closed.
    fetch(config.watchingUrl, {
      method: 'POST',
      headers: {'X-CSRFToken': token},
      body: sent,
      keepalive: true,
    }).catch(() => {});
  };
  for (const name of ['play', 'pause', 'ended']) {
    video.addEventListener(name, count);
    document
      .getElementById('review-audio-source')
      ?.addEventListener(name, count);
  }
  video.addEventListener('timeupdate', () => {
    if (video.currentTime > 0) {
      furthest = Math.max(furthest ?? 0, position());
    }
  });
  setInterval(() => {
    if (document.visibilityState === 'visible') {
      report();
    }
  }, REPORT_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      report();
    }
  });
  window.addEventListener('pagehide', report);
}

// What may have happened before this script ran.
if (video.error) {
  broken();
} else {
  sized();
}
update();
