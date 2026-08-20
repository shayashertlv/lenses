/**
 * An identity basis built from the template mesh and published anthropometry.
 *
 * Twenty modes, each one a trait an optician or a frame designer would
 * recognise by name, each scaled so that a coefficient of 1.0 is one population
 * standard deviation of that trait. Nothing is learned; every mode is a
 * constructed field over the mesh, and every scale is a number with a stated
 * source in `docs/CONSTANTS.md`.
 *
 * ## Honest provenance, up front
 *
 * The *shapes* of these modes are stated — they are smooth fields somebody
 * (me) designed to move the right part of the face in the right direction, and
 * there is no measurement that decides the exact falloff of "cheek prominence".
 * The *magnitudes* are transferred from published adult craniofacial
 * anthropometry as coefficients of variation, not as absolute millimetres,
 * because the landmark conventions differ between a caliper study and this mesh
 * and only the ratio survives the translation. That transfer rule is inherited
 * from v1's `LIMITS` block, where it was the right idea.
 *
 * What that buys and what it does not: a basis with the correct *dimensionality
 * and scale* of human facial variation, which is what a solver needs to be
 * well-conditioned and correctly regularised. It is not a claim that the modes
 * are the true principal components. When FLAME 2023 is available it should
 * replace this and the reported errors should improve; the harness in
 * `tests/shape-basis.test.ts` measures how much of a held-out face each basis
 * can explain, so the swap is a measurement rather than a belief.
 *
 * ## The modes that exist because this is eyewear
 *
 * `noseBridgeDepth`, `noseSidewallFlare`, `noseBridgeHeight` and
 * `noseDeviation` would be minor components in a general face basis and are
 * first-class here. Between them they decide where a frame comes to rest, how
 * far down the wedge it slides, and whether it sits crooked. `noseDeviation` in
 * particular — a nose bent off the midline — has no place in a symmetric-mean
 * shape model and is the single most common reason a real pair of glasses looks
 * skewed on a real face.
 */

import { LM, type FaceMesh, standardRegions, measure } from '../mesh.js';
import { type ShapeBasis, orthonormalizeModes, pruneBasis } from './basis.js';

/**
 * Coefficients of variation for adult craniofacial traits, as SD / mean.
 *
 * Transferred as ratios (rule 1 of v1's bound derivation): published absolute
 * ranges are never pasted in as millimetres, because a caliper's bizygomatic
 * width and this mesh's temple-landmark span are not the same measurement. Each
 * is applied to the corresponding span measured on the template itself.
 *
 * Sources and classes are in `docs/CONSTANTS.md`. Summary of the class mix:
 * eight are `published` (a literature CV), nine are `derived` (a published CV
 * for a related span, transferred), three are `stated`.
 */
const CV = {
  /** Bizygomatic breadth. Farkas, adult pooled. */
  faceWidth: 0.05,
  /** Morphological face height. */
  faceHeight: 0.055,
  /** Head depth, glabella to occiput; transferred to the mesh's shallow z span. */
  faceDepth: 0.06,
  /** Alar width. One of the most variable facial dimensions across populations. */
  noseWidth: 0.09,
  /** Nasal tip protrusion. */
  noseProtrusion: 0.10,
  /** Nasal root depth — the bridge's own forward stand. Large between-group
   *  variation; this is the number a borrowed average nose gets worst. */
  noseBridgeDepth: 0.13,
  /** Nasal bridge height above subnasale. */
  noseBridgeHeight: 0.09,
  /** Nasal sidewall flare — how fast the wedge widens on the way down. */
  noseSidewallFlare: 0.14,
  /** Nasal tip inclination. */
  noseTipDroop: 0.12,
  /** Interpupillary distance. */
  eyeSpacing: 0.055,
  /** Orbital depth (deep-set vs prominent). Stated: no clean published span. */
  eyeDepth: 0.10,
  /** Supraorbital ridge prominence. Stated. */
  browRidge: 0.15,
  /** Malar (cheekbone) prominence. */
  cheekProminence: 0.10,
  /** Bigonial (jaw) breadth. */
  jawWidth: 0.06,
  /** Chin projection (pogonion). */
  chinProjection: 0.12,
  /** Forehead inclination. Stated. */
  foreheadSlope: 0.12,
  /** Temple breadth, independent of bizygomatic. */
  templeWidth: 0.05,
  /** Face taper — wide-forehead-narrow-jaw vs the reverse. Derived from the
   *  difference of the two breadth CVs. */
  faceTaper: 0.05,
  /** Lateral nasal deviation. Not a CV: an absolute SD in mm (see below), because
   *  the mean is zero by construction and a ratio is undefined. */
  noseDeviationMm: 1.6,
  /** Global left-right facial asymmetry, absolute mm. */
  faceAsymmetryMm: 1.2,
} as const;

/**
 * A mode generator writes its displacement into `out[0..2]`.
 *
 * Out-parameter rather than a returned tuple: this runs 468 times per mode at
 * construction, which is not hot, but the same signature is reused by the
 * corrective fields in `displacement.ts` which are, and one signature for both
 * means a mode can move between the two files without being rewritten.
 */
type Field = (out: Float64Array, x: number, y: number, z: number, i: number) => void;

export function buildAnthropometricBasis(mesh: FaceMesh): ShapeBasis {
  const V = mesh.vertexCount;
  const mean = new Float64Array(mesh.positions);
  const regions = standardRegions(mesh);
  const m = measure(mean);

  const p = (i: number, c: number) => mean[i * 3 + c];

  // Reference extents of the template, for normalising the fields.
  const halfWidth = m.templeWidth / 2;
  const eyeY = (p(LM.EYE_OUTER_R, 1) + p(LM.EYE_OUTER_L, 1)) / 2;
  const chinY = p(LM.CHIN, 1);
  const foreheadY = p(LM.FOREHEAD, 1);
  const height = Math.max(foreheadY - chinY, 1e-6);
  const bridgeY = p(LM.NOSE_BRIDGE, 1);
  const subnasaleY = p(LM.SUBNASALE, 1);
  const noseSpanY = Math.max(bridgeY - subnasaleY, 1e-6);
  const templeZ = (p(LM.TEMPLE_R, 2) + p(LM.TEMPLE_L, 2)) / 2;
  const depth = Math.max(p(LM.NOSE_TIP, 2) - templeZ, 1e-6);

  const nose = regions.nose.weight;
  const bridge = regions.bridge.weight;
  const eyes = regions.eyes.weight;
  const temples = regions.temples.weight;
  const cheeks = regions.cheeks.weight;

  /** Smooth 0..1 ramp on y, 1 at `hi` and 0 at `lo`. */
  const ramp = (y: number, lo: number, hi: number) => {
    const t = Math.min(Math.max((y - lo) / (hi - lo), 0), 1);
    return t * t * (3 - 2 * t);
  };

  // Each generator returns a displacement whose magnitude, at the landmark the
  // trait is measured on, equals one standard deviation of that trait.
  const set = (out: Float64Array, dx: number, dy: number, dz: number) => {
    out[0] = dx; out[1] = dy; out[2] = dz;
  };
  /** 1 at the nose tip, 0 at the bridge — "how far down the nose is this". */
  const tipness = (y: number) => 1 - ramp(y, subnasaleY, bridgeY);
  const halfNose = Math.max(m.noseWidth / 2, 1e-6);
  /**
   * The reference span for every "how far does this feature stand forward"
   * mode, mm.
   *
   * Nasal root depth (14.8 mm on the template) rather than any whole-head
   * depth. Prominence traits — brow, cheek, chin, forehead, orbit, nasal root —
   * are all local stand-off of one feature against its neighbours, and they all
   * have population standard deviations of one to three millimetres. Scaling
   * them off a head-scale span produces 10 mm standard deviations, which is a
   * basis that can build faces nobody has and a regulariser that never objects.
   */
  const projRef = Math.max(Math.abs(m.nasalRootDepth), 1e-6);

  const generators: [string, number, Field][] = [
    ['faceWidth', CV.faceWidth * m.templeWidth,
      (o, x) => set(o, x / halfWidth, 0, 0)],

    ['faceHeight', CV.faceHeight * height,
      (o, _x, y) => set(o, 0, (y - eyeY) / height, 0)],

    ['faceDepth', CV.faceDepth * depth,
      (o, _x, _y, z) => set(o, 0, 0, (z - templeZ) / depth)],

    ['templeWidth', CV.templeWidth * m.templeWidth,
      (o, x, _y, _z, i) => set(o, (temples[i] * x) / halfWidth, 0, 0)],

    // Widen above the eye line and narrow below it, or the reverse.
    ['faceTaper', CV.faceTaper * m.templeWidth,
      (o, x, y) => set(o, (x / halfWidth) * ((y - eyeY) / height) * 2, 0, 0)],

    ['noseWidth', CV.noseWidth * m.noseWidth,
      (o, x, _y, _z, i) => set(o, (nose[i] * x) / halfNose, 0, 0)],

    // Grows toward the tip: zero at the bridge, full at the tip.
    ['noseProtrusion', CV.noseProtrusion * Math.abs(m.noseProtrusion),
      (o, _x, y, _z, i) => set(o, 0, 0, nose[i] * tipness(y))],

    // The bridge and nasion move forward together; the tip barely moves. This
    // is the mode a borrowed average nose gets most wrong.
    ['noseBridgeDepth', CV.noseBridgeDepth * projRef,
      (o, _x, _y, _z, i) => set(o, 0, 0, bridge[i])],

    ['noseBridgeHeight', CV.noseBridgeHeight * noseSpanY,
      (o, _x, _y, _z, i) => set(o, 0, bridge[i], 0)],

    // The wedge: widen the LOWER nose much more than the upper. This is the
    // mode that decides how far a frame slides before its pads bear.
    ['noseSidewallFlare', CV.noseSidewallFlare * m.noseWidth,
      (o, x, y, _z, i) => set(o, (nose[i] * tipness(y) * x) / halfNose, 0, 0)],

    ['noseTipDroop', CV.noseTipDroop * noseSpanY,
      (o, _x, y, _z, i) => set(o, 0, -nose[i] * tipness(y), 0)],

    ['eyeSpacing', CV.eyeSpacing * m.outerEyeSpan,
      (o, x, _y, _z, i) => set(o, eyes[i] * Math.sign(x), 0, 0)],

    ['eyeDepth', CV.eyeDepth * projRef,
      (o, _x, _y, _z, i) => set(o, 0, 0, -eyes[i])],

    ['browRidge', CV.browRidge * projRef,
      (o, _x, y) => set(o, 0, 0,
        ramp(y, eyeY, foreheadY) * (1 - ramp(y, eyeY + height * 0.35, foreheadY)))],

    ['cheekProminence', CV.cheekProminence * projRef,
      (o, _x, _y, _z, i) => set(o, 0, 0, cheeks[i])],

    ['jawWidth', CV.jawWidth * m.templeWidth,
      (o, x, y) => set(o, (x / halfWidth) * (1 - ramp(y, chinY, eyeY)), 0, 0)],

    ['chinProjection', CV.chinProjection * projRef,
      (o, _x, y) => set(o, 0, 0, Math.pow(1 - ramp(y, chinY, eyeY), 2))],

    ['foreheadSlope', CV.foreheadSlope * projRef,
      (o, _x, y) => set(o, 0, 0, -Math.pow(ramp(y, eyeY, foreheadY), 2))],

    // A nose bent off the midline. Antisymmetric by construction — no symmetric
    // mean shape can express it, and it is the most common reason a real pair of
    // glasses looks crooked on a real face.
    ['noseDeviation', CV.noseDeviationMm,
      (o, _x, y, _z, i) => set(o, nose[i] * tipness(y), 0, 0)],

    // Whole-face lateral shear: one side sits higher than the other.
    ['faceAsymmetry', CV.faceAsymmetryMm,
      (o, x, y) => set(o, 0, (x / halfWidth) * ramp(y, chinY, foreheadY), 0)],
  ];

  const raw: Float64Array[] = [];
  const labels: string[] = [];
  const scratch = new Float64Array(3);

  for (const [label, magnitude, field] of generators) {
    const g = new Float64Array(V * 3);
    let peak = 0;
    for (let i = 0; i < V; i++) {
      const x = mean[i * 3], y = mean[i * 3 + 1], z = mean[i * 3 + 2];
      scratch.fill(0);
      field(scratch, x, y, z, i);
      g[i * 3] = scratch[0]; g[i * 3 + 1] = scratch[1]; g[i * 3 + 2] = scratch[2];
      peak = Math.max(peak, Math.hypot(scratch[0], scratch[1], scratch[2]));
    }
    // Normalise the field to unit peak, then scale to one SD of the trait. This
    // is what makes `sigma = 1` correct for every coefficient and lets one
    // regulariser weight serve all twenty.
    const s = peak > 1e-9 ? magnitude / peak : 0;
    for (let i = 0; i < g.length; i++) g[i] *= s;
    raw.push(g);
    labels.push(label);
  }

  const { modes } = orthonormalizeModes(raw);

  return pruneBasis({
    name: 'anthropometric-20',
    dim: modes.length,
    vertexCount: V,
    mean,
    modes,
    sigma: new Float64Array(modes.length).fill(1),
    labels,
  });
}

/**
 * How much of a target shape this basis can explain, in mm RMS.
 *
 * The instrument that makes swapping in FLAME a measurement rather than a
 * belief. Least squares onto the (orthogonal) modes, then the residual.
 */
export function basisExplains(
  basis: ShapeBasis, target: Float64Array,
): { coeffs: Float64Array; residualRmsMm: number; residualMaxMm: number } {
  const d = new Float64Array(target.length);
  for (let i = 0; i < d.length; i++) d[i] = target[i] - basis.mean[i];

  const coeffs = new Float64Array(basis.dim);
  for (let k = 0; k < basis.dim; k++) {
    const mk = basis.modes[k];
    let num = 0, den = 0;
    for (let i = 0; i < d.length; i++) { num += mk[i] * d[i]; den += mk[i] * mk[i]; }
    coeffs[k] = den > 1e-12 ? num / den : 0;
  }

  for (let k = 0; k < basis.dim; k++) {
    const mk = basis.modes[k];
    const c = coeffs[k];
    for (let i = 0; i < d.length; i++) d[i] -= c * mk[i];
  }

  let sum = 0, max = 0;
  const n = d.length / 3;
  for (let i = 0; i < n; i++) {
    const e = Math.hypot(d[i * 3], d[i * 3 + 1], d[i * 3 + 2]);
    sum += e * e;
    if (e > max) max = e;
  }
  return { coeffs, residualRmsMm: Math.sqrt(sum / n), residualMaxMm: max };
}
