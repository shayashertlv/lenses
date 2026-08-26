#!/usr/bin/env node
/**
 * The only independent check on pad derivation that exists anywhere, extracted
 * from the two assets whose authors named their nose pads.
 *
 * `assets/glasses/navigator.glb` ships `NosePad_L` / `NosePad_R`, and
 * `sunglasses-khronos.glb` ships a single `Nosepads` part carrying both. Nine
 * of the eleven assets have nothing of the kind — they are photogrammetry or
 * image-to-3D output with either generic part names or one fused mesh — so
 * this grades 2 of 11, both of the favourable authored kind, and that limit is
 * the headline fact about it rather than a footnote.
 *
 * Until now this lived inside two binary files. A derivation cannot be graded
 * against geometry nobody has written down, and `ar/` is scheduled for
 * deletion, so it is committed as `assets/glasses/ground-truth.json`.
 *
 * ## The definition, and why this one
 *
 * `padSeparationMm` is the distance between the two pads' **contact-sample
 * centroids** — not their whole-mesh centroids. Settled from how the field is
 * built and used, not by preference:
 *
 *  - `parametricFrame` PLACES its pad samples at `x = ±padSeparationMm / 2`,
 *    so for every frame the tree already has, the field IS the separation of
 *    the contact samples.
 *  - Nothing in the physics reads it. `contact.ts` uses `padSamples` and
 *    `padNormals` directly; `padSeparationMm` is consumed only by the
 *    renderer's rim sizing, the occlusion instrument and the seat report.
 *
 * Measured both ways on navigator, because the gap matters: whole-mesh
 * centroids give **18.48 mm**, the inward contact faces give **12.43 mm**, and
 * a plane-fit of the same faces gives **13.79 mm**. The ~1.4 mm spread between
 * the two contact-face methods is the honest uncertainty on this number, and
 * it is recorded in the output rather than averaged away.
 *
 * ## Three angles, because `padAngleRad` was two
 *
 * Until 2026-08-26 this emitted one `padAngleRad` and measured it as a CONE
 * angle from the x axis, while `parametricFrame` consumed it as a YAW about the
 * vertical. On a parametric pad those are the same number — `ny` is identically
 * zero — so nothing caught it. On a real pad, whose normal leans down as well
 * as in, they differ by 3.8 degrees on navigator and 8.8 on khronos.
 *
 * The yaw keeps the name, because that is what the consumer inverts and what
 * `SKIN`'s `atan(0.60 / 0.76)` was derived as. The cone angle stays in the file
 * under its own name, because `PAD_INWARD_COS` gates on `n . x` and cites this
 * file for its band; the vertical lean is emitted too, because it is a real
 * property of a pad and the one this derivation recovers best.
 *
 *   node scripts/extract-pad-truth.mjs          write assets/glasses/ground-truth.json
 *   node scripts/extract-pad-truth.mjs --check  fail if the file is stale
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { readGlb } from '../dist/src/fit/mesh-io.js';

const ROOT = resolve(import.meta.dirname, '..');
const OUT = join(ROOT, 'assets', 'glasses', 'ground-truth.json');

/** How far a face must look toward the midline to count as contact surface. */
const INWARD_COS = 0.35;

const SOURCES = [
  { asset: 'navigator.glb', match: /nosepad/i, note: 'Blender-authored, NosePad_L / NosePad_R' },
  { asset: 'sunglasses-khronos.glb', match: /nose_?pad/i, note: 'RapidCompact, one Nosepads part carrying both' },
];

/** Per-triangle centroid and unit normal. */
function* faces(part) {
  const { positions: p, indices: ix } = part;
  for (let t = 0; t < ix.length; t += 3) {
    const a = ix[t] * 3, b = ix[t + 1] * 3, c = ix[t + 2] * 3;
    const ux = p[b] - p[a], uy = p[b + 1] - p[a + 1], uz = p[b + 2] - p[a + 2];
    const vx = p[c] - p[a], vy = p[c + 1] - p[a + 1], vz = p[c + 2] - p[a + 2];
    const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz) || 1;
    yield {
      cx: (p[a] + p[b] + p[c]) / 3,
      cy: (p[a + 1] + p[b + 1] + p[c + 1]) / 3,
      cz: (p[a + 2] + p[b + 2] + p[c + 2]) / 3,
      nx: nx / len, ny: ny / len, nz: nz / len,
      area: len / 2,
    };
  }
}

function measure(assetFile, match) {
  const mesh = readGlb(new Uint8Array(readFileSync(join(ROOT, 'assets', 'glasses', assetFile))));
  const pads = mesh.parts.filter((p) => match.test(p.name) || match.test(p.materialName));
  if (!pads.length) throw new Error(`${assetFile}: no part matches ${match}`);

  // The asset's own origin is wherever its author put it. Everything below is
  // measured relative to the MIDLINE (x = 0), which every asset in this
  // catalogue does respect, and reported as separations and angles — both of
  // which are origin-independent. That is why this file does not need each
  // asset's centring correction to be right.
  const all = [];
  for (const part of pads) for (const f of faces(part)) all.push(f);

  const side = (sign) => {
    // Split by which side of the midline the face sits on. `Nosepads` on the
    // khronos asset is ONE part carrying both, so splitting by part is wrong
    // and splitting by x is right for both files.
    const inward = -sign; // a pad left of the midline (x<0) contacts toward +x
    const chosen = all.filter((f) => Math.sign(f.cx) === sign && f.nx * inward > INWARD_COS);
    if (chosen.length < 20) return null;
    let sx = 0, sy = 0, sz = 0, nx = 0, ny = 0, nz = 0, area = 0;
    for (const f of chosen) {
      sx += f.cx * f.area; sy += f.cy * f.area; sz += f.cz * f.area;
      nx += f.nx * f.area; ny += f.ny * f.area; nz += f.nz * f.area;
      area += f.area;
    }
    const nlen = Math.hypot(nx, ny, nz) || 1;
    return {
      centroid: [sx / area, sy / area, sz / area],
      normal: [nx / nlen, ny / nlen, nz / nlen],
      faces: chosen.length,
      areaMm2: area,
    };
  };

  const right = side(-1);
  const left = side(1);
  if (!right || !left) throw new Error(`${assetFile}: could not find two contact faces`);

  // **A YAW about the vertical axis**: the quantity `parametricFrame` inverts
  // at `frame-asset.ts:293` to build a pad plane, and the one `SKIN`'s
  // `atan(0.60 / 0.76) = 0.67 rad` was derived as. This measured the CONE angle
  // from the x axis until 2026-08-26, which on a parametric frame is the same
  // number (`ny` is identically 0) and on a real pad is 6.7 to 8.8 degrees
  // larger.
  const yawOf = (n) => Math.atan2(Math.abs(n[2]), Math.abs(n[0]));
  // The downward lean the yaw drops — an optician's frontal angle.
  const dropOf = (n) => Math.asin(Math.max(-1, Math.min(1, -n[1])));
  // The cone angle is still emitted, because `PAD_INWARD_COS` gates on `n . x`
  // and its justification cites this file. Swapping one angle for another would
  // have left that citation pointing at a number for a different quantity.
  const coneOf = (n) => Math.atan2(Math.hypot(n[1], n[2]), Math.abs(n[0]));

  return {
    padSeparationMm: Math.abs(left.centroid[0] - right.centroid[0]),
    padAngleRad: (yawOf(left.normal) + yawOf(right.normal)) / 2,
    padVerticalLeanRad: (dropOf(left.normal) + dropOf(right.normal)) / 2,
    padConeAngleRad: (coneOf(left.normal) + coneOf(right.normal)) / 2,
    right,
    left,
  };
}

const measured = {};
for (const { asset, match, note } of SOURCES) {
  const m = measure(asset, match);
  measured[asset] = {
    note,
    padSeparationMm: +m.padSeparationMm.toFixed(3),
    padAngleRad: +m.padAngleRad.toFixed(4),
    padAngleDeg: +((m.padAngleRad * 180) / Math.PI).toFixed(2),
    padVerticalLeanRad: +m.padVerticalLeanRad.toFixed(4),
    padVerticalLeanDeg: +((m.padVerticalLeanRad * 180) / Math.PI).toFixed(2),
    padConeAngleRad: +m.padConeAngleRad.toFixed(4),
    padConeAngleDeg: +((m.padConeAngleRad * 180) / Math.PI).toFixed(2),
    contactAreaMm2: +(m.right.areaMm2 + m.left.areaMm2).toFixed(1),
    faces: m.right.faces + m.left.faces,
  };
}

const doc = {
  what: 'Author-declared nose pad geometry, the only independent check on pad derivation in this tree.',
  definition: 'padSeparationMm is the distance between the two pads\' CONTACT-SAMPLE centroids, '
    + 'area-weighted over faces whose normal leans toward the midline by more than '
    + `${INWARD_COS}. padAngleRad is the mean YAW of those contact normals about the `
    + 'VERTICAL axis - atan2(|nz|, |nx|) - which is the angle parametricFrame inverts to '
    + 'build a pad plane and the one SKIN\'s atan(0.60/0.76) = 0.67 rad was derived as. '
    + 'padVerticalLeanRad is the downward component the yaw drops, asin(-ny), an optician\'s '
    + 'frontal angle. padConeAngleRad is the full lean out of the x axis, atan2(hypot(ny, nz), '
    + '|nx|) - the quantity PAD_INWARD_COS gates on, and the one padAngleRad USED to hold. '
    + 'They were one number until 2026-08-26 and it was neither.',
  coverage: '2 of 11 assets. The other nine are photogrammetry or image-to-3D output with '
    + 'generic part names or a single fused mesh, and have no author-declared pads to extract.',
  uncertainty: 'Method-dependent, and the size of that depends on WHICH methods. On navigator: '
    + 'whole-mesh part centroids give 18.48 mm, these inward contact faces give 13.843 mm (the '
    + 'figure emitted below), and a plane-fit of the same faces gives 13.79 mm. So the two '
    + 'CONTACT-FACE methods agree to 0.05 mm and the whole-mesh centroid is 4.6 mm away from '
    + 'both -- it is measuring the pad ARMS as well as the pads. This field used to claim '
    + '"+/-1.4 mm" from a contact-face reading of 12.43 mm, which is not what this script '
    + 'produces and disagreed with the corroboration note directly below it. The contact-face '
    + 'reading is the one that matches how parametricFrame places its own samples; the spread '
    + 'is recorded rather than averaged away.',
  corroboration: 'SEPARATION replicates across methods and ANGLE does not, which is the useful '
    + 'part. Two independent extractions agree on navigator to 0.05 mm (13.843 here against '
    + '13.79 from a plane fit) and on khronos to 0.10 mm. The ANGLE does not replicate even '
    + 'across DEFINITIONS of itself: on navigator the same contact faces read 30.80 deg as a '
    + 'yaw and 34.56 deg as a cone, and on khronos 7.95 against 16.77 - a gap of 3.8 and 8.8 '
    + 'degrees with no measurement error in it at all, only two readings of one word. '
    + 'So the claim that the authored pad angle corroborates this tree\'s anthropometric '
    + 'padAngleRad of 0.67 rad (38.39 deg) to within half a percent is FALSE: against the '
    + 'authored YAW, which is what 0.67 is, navigator is 7.59 deg out. The old figure of 3.8 '
    + 'deg was itself an artefact of the naming collision - it compared a CONE measurement '
    + 'against a constant that only ever produces a yaw, and the two errors partly cancelled. '
    + 'Grade a derivation against separation with confidence, and against angle with a '
    + 'tolerance no tighter than the definitions disagree with each other.',
  inwardCos: INWARD_COS,
  measured,
};

const text = JSON.stringify(doc, null, 2) + '\n';

if (process.argv.includes('--check')) {
  let current = null;
  try { current = readFileSync(OUT, 'utf8'); } catch { /* absent */ }
  if (current !== text) {
    console.error('assets/glasses/ground-truth.json is stale or missing.');
    console.error('  run: node scripts/extract-pad-truth.mjs');
    process.exit(1);
  }
  console.log('pad ground truth matches the assets it was extracted from.');
} else {
  writeFileSync(OUT, text);
  console.log(`wrote ${OUT}`);
  for (const [k, v] of Object.entries(measured)) {
    console.log(`  ${k.padEnd(24)} sep ${v.padSeparationMm} mm   angle ${v.padAngleRad} rad (${v.padAngleDeg} deg)   ${v.faces} faces`);
  }
}
