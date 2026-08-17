/**
 * Matching the virtual light to the light the camera actually sees.
 *
 * The frame used to be lit by a fixed studio key whatever was on screen: at full
 * brightness in a dark room, the glasses glowed; by a bright window, they read as
 * dull grey. Nothing betrays a composite faster than an object lit differently
 * from the scene it claims to be in.
 *
 * A single camera cannot recover real illumination, but it does not need to. The
 * image already integrates the answer: how bright the scene reads, and what colour
 * it is cast in. Sampling the face region — the thing the glasses actually sit on —
 * gives both, and driving the key and ambient from them keeps the frame plausible
 * across rooms, times of day and white balances.
 *
 * Everything here is pure measurement and mapping; applying the result to the
 * scene, and smoothing it over time, is the caller's job.
 */

/** Rec. 709 luma — how bright a pixel reads, not how large its values are. */
const luma = (r, g, b) => 0.2126 * r + 0.7152 * g + 0.0722 * b;

/**
 * Builds a reusable probe that measures brightness and colour cast of a source.
 *
 * The source is downsampled hard (the default is a 32×24 readback) because the
 * question is "how bright is this scene", which survives any amount of blurring —
 * and a full-resolution readback every sampling interval would cost more than the
 * render it feeds.
 */
export function createLightProbe({ width = 32, height = 24 } = {}) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  return {
    /**
     * Measures `element` (anything drawImage accepts). `region`, if given, is a
     * normalised {minX, minY, maxX, maxY} — pass the face bounding box so a bright
     * window behind the wearer does not read as a bright face.
     *
     * Returns { brightness, tint } — brightness 0..1, tint an [r, g, b] cast with
     * its largest channel at 1.
     */
    sample(element, region = null) {
      ctx.drawImage(element, 0, 0, width, height);

      let x0 = 0;
      let y0 = 0;
      let x1 = width;
      let y1 = height;
      if (region) {
        x0 = Math.max(0, Math.floor(region.minX * width));
        y0 = Math.max(0, Math.floor(region.minY * height));
        x1 = Math.min(width, Math.ceil(region.maxX * width));
        y1 = Math.min(height, Math.ceil(region.maxY * height));
        // A degenerate region (face off-frame, bad landmarks) falls back to the
        // whole image rather than measuring nothing.
        if (x1 - x0 < 2 || y1 - y0 < 2) { x0 = 0; y0 = 0; x1 = width; y1 = height; }
      }

      const data = ctx.getImageData(x0, y0, x1 - x0, y1 - y0).data;
      let r = 0;
      let g = 0;
      let b = 0;
      const n = data.length / 4;
      for (let i = 0; i < data.length; i += 4) {
        r += data[i];
        g += data[i + 1];
        b += data[i + 2];
      }
      r /= n * 255;
      g /= n * 255;
      b /= n * 255;

      const peak = Math.max(r, g, b, 1e-6);
      return {
        brightness: luma(r, g, b),
        // Normalised so the dominant channel is 1: a cast, not a brightness.
        tint: [r / peak, g / peak, b / peak],
      };
    },
  };
}

/**
 * Maps a measured scene brightness to light intensities.
 *
 * Two deliberate properties. The curve is compressive (^0.8), because perceived
 * brightness is; and every output has a floor, because a frame lit to zero in a
 * dark room disappears entirely — worse than being slightly too bright, since the
 * user's own face is still visible via the camera's exposure.
 *
 * `environment` scales the image-based lighting and is the one that actually
 * dims a metal frame: at metalness 0.95 the key and ambient contribute almost
 * nothing to how bright the frame reads — its appearance *is* its reflections.
 */
export function lightingFor(brightness) {
  const b = Math.pow(Math.min(Math.max(brightness, 0), 1), 0.8);
  return {
    key: 0.5 + 1.3 * b,
    ambient: 0.12 + 0.5 * b,
    environment: 0.15 + 0.95 * b,
    /**
     * The screen, which is the only thing a lens has to reflect (see `scene.js`).
     *
     * Its floor is the highest here in proportion, and on purpose: a screen is the one
     * light in the picture that does *not* go away when the room does. Somebody trying
     * frames on in the dark is lit by their monitor almost exclusively, and that is
     * precisely when the reflection on the lens is strongest — so this dims with the
     * room far less than the room's own lights do.
     *
     * The absolute level is low, and the first attempt at it was four times this. A
     * reflection bright enough to be *obvious* is brighter than a coated lens can
     * physically produce, and on a material that opts out of tone mapping — which a
     * transmissive one must, to match the un-graded camera pixels behind it — there is
     * no filmic roll-off left to catch it, so it clips to flat white with a hard edge.
     * That is a camera flash, not a lens. What a lens does is glint.
     */
    screen: 0.35 + 0.35 * b,
  };
}

/**
 * Softens a measured colour cast before it drives the lights.
 *
 * The raw cast includes the face's own colour, not just the illuminant's, so
 * applying it at full strength would tint every frame skin-toned. Blending toward
 * white keeps the direction of the cast — warm room, cool daylight — at believable
 * strength. `strength` 0 is no cast at all.
 */
export function softenTint(tint, strength = 0.5) {
  return tint.map((c) => 1 + (c - 1) * strength);
}
