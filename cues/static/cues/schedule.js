/**
 * @fileoverview The Schedule's Projects list applies as soon as a box is
 * ticked; its Show button is for browsers without scripts. A pin set or
 * cleared from the page ("Schedule On…") redraws it. The month fills the
 * window (app.css); each day shows the cues that fit, then "N more".
 * Choosing a day swaps its list in without reloading the page.
 */

for (const form of document.querySelectorAll('form[data-autosubmit]')) {
  form.querySelector('[data-autosubmit-button]')?.setAttribute('hidden', '');
  form.addEventListener('change', () => form.requestSubmit());
}

document.body.addEventListener('schedule:changed', () => {
  window.location.reload();
});

/**
 * Hides a day's cues from the last until the rest fit, and says how many.
 * @param {!Element} link The day's link.
 */
function fitDay(link) {
  const cues = [...link.querySelectorAll('.cal-cue')];
  const more = link.querySelector('.cal-day__more');
  if (!more) {
    return;
  }
  for (const cue of cues) {
    cue.hidden = false;
  }
  more.hidden = true;
  let hidden = 0;
  while (link.scrollHeight > link.clientHeight && hidden < cues.length) {
    hidden += 1;
    cues[cues.length - hidden].hidden = true;
    more.textContent = `${hidden} more`;
    more.hidden = false;
  }
}

/** Says, in each day of the month, how many cues don't fit. */
function fitMonth() {
  document.querySelectorAll('.cal--month .cal-day__link').forEach(fitDay);
}

let pending = 0;
/** Refits once per frame while the window is resized. */
function refit() {
  window.cancelAnimationFrame(pending);
  pending = window.requestAnimationFrame(fitMonth);
}

fitMonth();
document.fonts?.ready.then(refit);
window.addEventListener('resize', refit);

// Choosing a day swaps only its list in (htmx); move the highlight.
document.body.addEventListener('htmx:beforeRequest', (event) => {
  const link = /** @type {!Element} */ (event.target);
  if (!link.matches?.('.cal-day__link')) {
    return;
  }
  for (const day of document.querySelectorAll('.cal-day--selected')) {
    day.classList.remove('cal-day--selected');
    day.querySelector('.cal-day__link')?.removeAttribute('aria-current');
  }
  link.closest('.cal-day')?.classList.add('cal-day--selected');
  link.setAttribute('aria-current', 'true');
});
