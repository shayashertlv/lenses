// Photo pixels and the existing coating swatch are sRGB bytes. Compare colour
// at one fixed lightness: exposure/see-through brightness must not drive the fit.
function chromaticity(rgb) {
  if (!rgb.every(Number.isFinite)) return null;
  const linear = rgb.map(byte => {
    const value = byte / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  const [r, g, b] = linear;
  const luminance = .2126729 * r + .7151522 * g + .0721750 * b;
  if (!(luminance > 0)) return null;
  const scale = .5 / luminance;
  const xyz = [(.4124564 * r + .3575761 * g + .1804375 * b) * scale / .95047,
    .5, (.0193339 * r + .1191920 * g + .9503041 * b) * scale / 1.08883];
  const lab = xyz.map(value => value > (6 / 29) ** 3 ? Math.cbrt(value) : value / (3 * (6 / 29) ** 2) + 4 / 29);
  return [500 * (lab[0] - lab[1]), 200 * (lab[1] - lab[2])];
}
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** Exhaustive, straight-on fit using the exact function that draws the bar.
 * Distinct choices are local error minima separated by a perceptible rise in
 * error, not adjacent 1 nm samples or quantization ripples from the same valley.
 * No material values are changed here; choosing a result is a separate edit.
 */
export function findCoatingThicknesses(rgb, values, colourAtAngle) {
  if (!(values.iridescence > 0)) return { choices: [], reason: 'Coating shimmer is off. Turn it on and set its strength before matching a colour.' };
  const target = chromaticity(rgb);
  if (!target || Math.hypot(...target) < 2) return { choices: [], reason: 'This point has no clear colour to match. Pick a coloured reflection on the lens.' };
  const samples = [];
  for (let thickness = 0; thickness <= 1200; thickness++) {
    const colour = chromaticity(colourAtAngle({ ...values, iridescence_thickness_min: thickness, iridescence_thickness_max: thickness }, 1));
    samples.push({ thickness, colour, distance: colour ? distance(colour, target) : Infinity });
  }
  const firstColour = samples.find(sample => sample.colour)?.colour;
  if (!firstColour || !samples.some(sample => sample.colour && distance(sample.colour, firstColour) >= 1)) {
    return { choices: [], reason: 'At these settings, changing thickness gives no distinct coating colours. Adjust the coating settings, then try again.' };
  }
  const minima = [];
  for (let start = 0; start < samples.length; start++) {
    let end = start;
    while (end + 1 < samples.length && samples[end + 1].distance === samples[start].distance) end++;
    if (Number.isFinite(samples[start].distance)
        && (start === 0 || samples[start].distance < samples[start - 1].distance)
        && (end === samples.length - 1 || samples[end].distance < samples[end + 1].distance)) minima.push(samples[start]);
    start = end;
  }
  minima.sort((a, b) => a.distance - b.distance || a.thickness - b.thickness);
  const choices = [];
  for (const candidate of minima) {
    const distinct = choices.every(chosen => {
      const low = Math.min(chosen.thickness, candidate.thickness), high = Math.max(chosen.thickness, candidate.thickness);
      // One Lab unit excludes byte-rounding ripples without imposing a fixed
      // thickness spacing, which would discard orders at a high film IOR.
      return samples.slice(low, high + 1).some(sample => sample.distance > Math.max(chosen.distance, candidate.distance) + 1);
    });
    if (distinct) choices.push({ thickness: candidate.thickness, distance: candidate.distance });
    if (choices.length === 3) break;
  }
  return { choices, reason: choices.length ? null : 'No distinct coating colour could be matched at these settings. Try another photo point.' };
}
