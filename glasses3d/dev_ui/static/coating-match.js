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

export const STRAIGHT_ON = 1, SIDES = .4;  // the bar's two ends: lens centre and lens sides
export const FILM_IORS = Array.from({length: 25}, (_, i) => Math.round((1.2 + .05 * i) * 100) / 100);

/** Two-colour coating fit: the owner names the colour seen straight on and the colour
 * seen at the lens sides; every thickness (0-1200 nm) and film IOR (1.2-2.4) is tried
 * with the exact bar function at both angles, comparing colour at fixed lightness.
 * Lens IOR and coating strength stay as set (Brightness and Strength controls).
 * For one coating colour pass the same target twice. Choosing a result is a separate edit. */
export function findCoatingForColours(targets, values, colourAtAngle) {
  const centre = chromaticity(targets.straight_on), side = chromaticity(targets.sides);
  if (!centre || !side || Math.hypot(...centre) < 2 || Math.hypot(...side) < 2) {
    return {choices: [], reason: 'Choose clear colours (not white, grey or black) for the coating.'};
  }
  const strength = values.iridescence > 0 ? values.iridescence : 1;
  const best = [];
  for (let thickness = 0; thickness <= 1200; thickness++) {
    let row = {thickness, distance: Infinity};
    for (const film of FILM_IORS) {
      const trial = {...values, iridescence: strength, iridescence_ior: film,
        iridescence_thickness_min: thickness, iridescence_thickness_max: thickness};
      const a = chromaticity(colourAtAngle(trial, STRAIGHT_ON)), b = chromaticity(colourAtAngle(trial, SIDES));
      if (!a || !b) continue;
      const straight = distance(a, centre), sides = distance(b, side), total = straight + sides;
      if (total < row.distance) row = {thickness, film_ior: film, distance: total, straight, sides};
    }
    best.push(row);
  }
  const minima = [];
  for (let i = 0; i < best.length; i++) {
    const here = best[i].distance;
    if (Number.isFinite(here) && (i === 0 || here < best[i - 1].distance) && (i === best.length - 1 || here <= best[i + 1].distance)) minima.push(best[i]);
  }
  minima.sort((a, b) => a.distance - b.distance || a.thickness - b.thickness);
  const choices = [];
  for (const candidate of minima) {
    const distinct = choices.every(chosen => {
      const low = Math.min(chosen.thickness, candidate.thickness), high = Math.max(chosen.thickness, candidate.thickness);
      // A real alternative needs both a perceptible error ridge and a different coating
      // order, not a 15 nm neighbour inside the same valley.
      return high - low >= 40 && best.slice(low, high + 1).some(row => row.distance > Math.max(chosen.distance, candidate.distance) + 3);
    });
    if (distinct) choices.push({...candidate, quality: candidate.straight < 8 && candidate.sides < 8 ? 'Close match'
      : candidate.straight < 18 && candidate.sides < 18 ? 'Approximate match' : 'Closest a real coating can get'});
    if (choices.length === 3) break;
  }
  return {choices, reason: choices.length ? null : 'No coating could be matched. Try other colours.'};
}

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
