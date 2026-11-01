/**
 * @fileoverview Drop zones (cues/includes/dropzone.html): drag a file onto
 * the zone, or click it to choose one, and it shows what was chosen.
 *
 * A zone marked data-dropzone-submit sends its form as soon as a file is
 * chosen (the cue page's thumbnail). Listens on the document, so zones
 * in dialogs loaded later work too.
 */

/**
 * The drop zone an event happened in, if any.
 * @param {!Event} event
 * @return {?HTMLElement}
 */
function zoneOf(event) {
  return event.target instanceof Element
    ? event.target.closest('[data-dropzone]')
    : null;
}

/**
 * Shows the chosen file's name in its zone.
 * @param {!HTMLElement} zone
 */
function showChoice(zone) {
  const input = zone.querySelector('input[type=file]');
  const file = input.files && input.files[0];
  zone.querySelector('.dropzone__name').textContent = file
    ? `Chosen: ${file.name}`
    : '';
  zone.classList.toggle('dropzone--chosen', Boolean(file));
  if (file && zone.hasAttribute('data-dropzone-submit')) {
    zone.closest('form')?.requestSubmit();
  }
}

document.addEventListener('dragover', (event) => {
  const zone = zoneOf(event);
  if (zone) {
    event.preventDefault();
    zone.classList.add('dropzone--over');
  }
});

document.addEventListener('dragleave', (event) => {
  const zone = zoneOf(event);
  if (zone && !zone.contains(event.relatedTarget)) {
    zone.classList.remove('dropzone--over');
  }
});

document.addEventListener('drop', (event) => {
  const zone = zoneOf(event);
  if (!zone) {
    return;
  }
  event.preventDefault();
  zone.classList.remove('dropzone--over');
  if (event.dataTransfer.files.length) {
    zone.querySelector('input[type=file]').files = event.dataTransfer.files;
    showChoice(zone);
  }
});

document.addEventListener('change', (event) => {
  const zone = zoneOf(event);
  if (zone) {
    showChoice(zone);
  }
});
