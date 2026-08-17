/**
 * Fitting the camera frame into the space available for it.
 *
 * This is pure arithmetic and lives on its own because the alternative — leaning
 * on CSS `aspect-ratio` — silently produced a zero-sized stage. The stage's only
 * children are absolutely positioned, so it shrink-to-fits to nothing, and an
 * aspect ratio applied to a zero width is still zero. Nothing errors; the canvas
 * simply never gets a size and the camera never gets its aspect.
 *
 * Sizing it in script instead makes the result explicit and testable.
 */

/**
 * Largest rectangle of ratio `aspect` that fits inside `availableWidth` x
 * `availableHeight` — the same rule as `object-fit: contain`.
 *
 * Returns zeros when there is no room to work with, which the caller treats as
 * "not laid out yet, try again next frame" rather than as a size to apply.
 */
export function fitRect(availableWidth, availableHeight, aspect) {
  if (!(availableWidth > 0) || !(availableHeight > 0) || !(aspect > 0)) {
    return { width: 0, height: 0 };
  }

  let width = availableWidth;
  let height = width / aspect;

  if (height > availableHeight) {
    height = availableHeight;
    width = height * aspect;
  }

  // Whole pixels: a fractional canvas size means the renderer's drawing buffer and
  // the element's box disagree, which shows up as a soft, slightly offset image.
  return { width: Math.floor(width), height: Math.floor(height) };
}
