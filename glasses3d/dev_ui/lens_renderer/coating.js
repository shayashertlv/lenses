/** Reflected-film swatch, translated from the pinned Three r185 shader:
 * three/src/renderers/shaders/ShaderChunk/iridescence_fragment.glsl.js (MIT).
 * This is the coating's Fresnel colour at one angle, not a room-lit lens render.
 * The preview itself always uses Three's original GLSL through TryOnRenderer.
 */
/* Copyright (c) 2010-2026 three.js authors. The MIT License.
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
const clamp = (x, min = 0, max = 1) => Math.max(min, Math.min(max, x));
const mix = (a, b, t) => a * (1 - t) + b * t;
const schlick = (f0, cosine) => mix(f0, 1, 2 ** ((-5.55473 * cosine - 6.98316) * cosine));
const fresnel0 = (transmitted, incident) => ((transmitted - incident) / (transmitted + incident)) ** 2;

function sensitivity(opd, shift) {
  const phase = 2 * Math.PI * opd * 1e-9;
  const amplitude = [5.4856e-13, 4.4201e-13, 5.2481e-13];
  const position = [1.6810e6, 1.7953e6, 2.2084e6];
  const variance = [4.3278e9, 9.3046e9, 6.6121e9];
  const xyz = amplitude.map((value, i) => value * Math.sqrt(2 * Math.PI * variance[i])
    * Math.cos(position[i] * phase + shift[i]) * Math.exp(-(phase ** 2) * variance[i]));
  xyz[0] += 9.7470e-14 * Math.sqrt(2 * Math.PI * 4.5282e9)
    * Math.cos(2.2399e6 * phase + shift[0]) * Math.exp(-4.5282e9 * phase ** 2);
  for (let i = 0; i < 3; i++) xyz[i] /= 1.0685e-7;
  return [
    3.2404542 * xyz[0] - 1.5371385 * xyz[1] - .4985314 * xyz[2],
    -.9692660 * xyz[0] + 1.8760108 * xyz[1] + .0415560 * xyz[2],
    .0556434 * xyz[0] - .2040259 * xyz[1] + 1.0572252 * xyz[2],
  ];
}

export function evalCoatingReflectance(outsideIOR, filmIOR, cosine, thickness, baseF0) {
  const t = clamp(thickness / .03), eta = mix(outsideIOR, filmIOR, t * t * (3 - 2 * t));
  const cosineSquared = 1 - (outsideIOR / eta) ** 2 * (1 - cosine ** 2);
  if (cosineSquared < 0) return [1, 1, 1];
  const cosFilm = Math.sqrt(cosineSquared), r12 = schlick(fresnel0(eta, outsideIOR), cosine), transmission = 1 - r12;
  const phi21 = Math.PI - (eta < outsideIOR ? Math.PI : 0);
  const baseIOR = baseF0.map(value => {const root = Math.sqrt(clamp(value, 0, .9999)); return (1 + root) / (1 - root);});
  const r23 = baseIOR.map(value => schlick(fresnel0(value, eta), cosFilm));
  const phi = baseIOR.map(value => phi21 + (value < eta ? Math.PI : 0));
  const opd = 2 * eta * thickness * cosFilm;
  const r123 = r23.map(value => clamp(r12 * value, 1e-5, .9999));
  const rs = r23.map((value, i) => transmission ** 2 * value / (1 - r123[i]));
  const intensity = rs.map(value => r12 + value), cm = rs.map(value => value - transmission);
  for (let m = 1; m <= 2; m++) {
    const sm = sensitivity(m * opd, phi.map(value => m * value));
    for (let i = 0; i < 3; i++) {cm[i] *= Math.sqrt(r123[i]); intensity[i] += cm[i] * 2 * sm[i];}
  }
  return intensity.map(value => Math.max(0, value));
}

/** sRGB integers for the angular coating-reflection swatch. No invented hue map. */
export function coatingSwatchRGB(values, cosTheta = .7) {
  const cosine = clamp(cosTheta), dielectric = fresnel0(values.ior, 1);
  const base = values.tint.map(value => mix(dielectric, value, values.metallic));
  const film = evalCoatingReflectance(1, values.iridescence_ior, cosine, values.iridescence_thickness_max, base);
  return film.map((value, index) => {
    const linear = clamp(mix(schlick(base[index], cosine), value, values.iridescence));
    const srgb = linear <= .0031308 ? 12.92 * linear : 1.055 * linear ** (1 / 2.4) - .055;
    return Math.round(255 * srgb);
  });
}
