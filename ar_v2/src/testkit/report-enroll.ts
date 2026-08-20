/**
 * The enrollment accuracy report.
 *
 * Run with `npm run report:enroll`. This is the number that says whether the
 * rewrite's central claim is true: that a guided multi-view scan recovers a
 * wearer's nose to a millimetre where v1's average head is out by many.
 *
 * It prints distributions across the whole synthetic population and the whole
 * camera ladder, never a single figure, and it prints the ablations alongside —
 * what the scan is worth without the profile beat, without the free-form field,
 * without the lean. An accuracy number with no ablation beside it is a number
 * nobody can act on.
 */

import { loadBasis, loadRegions, loadTemplateMesh } from './fixtures.js';
import {
  CAMERA_LADDER, generatePopulation, subjectResidualAgainstBasis, synthesizeCapture,
  type CaptureOptions, type SyntheticSubject,
} from './synthetic.js';
import { enroll } from '../enroll/enroll.js';
import { compareToTruth, distribution, fmt, table } from './metrics.js';
import type { BundleOptions } from '../enroll/bundle.js';

export interface Variant {
  name: string;
  capture?: Partial<CaptureOptions>;
  bundle?: Partial<BundleOptions>;
  /** Use v1's fixed iris constant rather than this subject's true one. Always
   *  true for realism; the flag exists so the harness can isolate the bias. */
  useTrueIris?: boolean;
}

export const VARIANTS: Variant[] = [
  { name: 'full' },
  { name: 'no-profile', capture: { includeProfile: false } },
  { name: 'no-field', bundle: { solveField: false } },
  { name: 'no-silhouette', bundle: { useSilhouette: false } },
  { name: 'no-lean', capture: { includeLean: false } },
  { name: 'true-iris', useTrueIris: true },
];

export interface RunOptions {
  subjects: number;
  variants: string[];
  geometries: string[];
  verbose: boolean;
}

export function runEnrollReport(options: Partial<RunOptions> = {}): string {
  const opt: RunOptions = {
    subjects: 8,
    variants: VARIANTS.map((v) => v.name),
    geometries: CAMERA_LADDER.map((g) => g.name),
    verbose: false,
    ...options,
  };

  const mesh = loadTemplateMesh();
  const basis = loadBasis();
  const regions = loadRegions();
  const population = generatePopulation(mesh, basis, { count: opt.subjects });

  const out: string[] = [];
  out.push('ENROLLMENT ACCURACY');
  out.push('===================');
  out.push(`${population.length} subjects x ${opt.geometries.length} camera geometries`);
  out.push(`basis: ${basis.name}, ${basis.dim} modes`);
  out.push('');

  // The "can this test fail" check, printed rather than assumed.
  const residuals = population.map((s) => subjectResidualAgainstBasis(basis, s).residualRmsMm);
  const rd = distribution(residuals);
  out.push(`Basis cannot explain (median/p90/worst RMS mm): ${fmt(rd, 3)}`);
  out.push('  If this were near zero the reconstruction tests would be measuring');
  out.push('  the basis rather than the scan. It is the harness\'s own falsifiability.');
  out.push('');

  const rows: (string | number)[][] = [];
  const baseline: Record<string, number> = {};

  for (const variantName of opt.variants) {
    const variant = VARIANTS.find((v) => v.name === variantName);
    if (!variant) continue;

    const noseRms: number[] = [];
    const padRms: number[] = [];
    const bridgeRms: number[] = [];
    const wholeRms: number[] = [];
    const protrusion: number[] = [];
    const standoff: number[] = [];
    const scaleErr: number[] = [];
    const pdErr: number[] = [];
    const solveMs: number[] = [];
    const reproj: number[] = [];
    let degradedCount = 0;

    for (const subject of population) {
      for (const geometry of CAMERA_LADDER) {
        if (!opt.geometries.includes(geometry.name)) continue;
        const result = runOne(subject, geometry, variant, mesh, basis, regions, opt.verbose);
        if (!result) continue;
        const { comparison, model } = result;
        noseRms.push(comparison.perRegion.nose.rmsMm);
        padRms.push(comparison.perRegion.padStrip.rmsMm);
        bridgeRms.push(comparison.perRegion.bridge.rmsMm);
        wholeRms.push(comparison.wholeMeshRmsMm);
        protrusion.push(Math.abs(comparison.measurementError.noseProtrusion));
        standoff.push(Math.abs(comparison.measurementError.bridgeStandoff));
        scaleErr.push(Math.abs(comparison.scaleErrorPct));
        if (comparison.pdErrorMm !== null) pdErr.push(Math.abs(comparison.pdErrorMm));
        solveMs.push(model.solveMs);
        reproj.push(model.reprojectionRmsPx);
        if (model.degraded) degradedCount++;
      }
    }

    if (variantName === 'full') baseline.nose = distribution(noseRms).median;

    rows.push([
      variantName,
      fmt(distribution(noseRms)),
      fmt(distribution(padRms)),
      fmt(distribution(bridgeRms)),
      fmt(distribution(protrusion)),
      fmt(distribution(standoff)),
      distribution(scaleErr).median.toFixed(2),
      pdErr.length ? distribution(pdErr).median.toFixed(2) : '-',
      distribution(reproj).median.toFixed(2),
      Math.round(distribution(solveMs).median),
      degradedCount,
    ]);
  }

  out.push('Error against ground truth, mm — median / p90 / worst across the population.');
  out.push('');
  out.push(table(
    ['variant', 'nose', 'pad strip', 'bridge', '|protrusion|', '|standoff|',
      'scale%', 'PD mm', 'reproj px', 'ms', 'degraded'],
    rows,
  ));
  out.push('');
  out.push('Reading this table:');
  out.push('  nose/pad strip/bridge  surface error over that region, after rigid alignment');
  out.push('  |protrusion|           error in nose-tip-forward-of-bridge, the number a');
  out.push('                         borrowed average nose gets most wrong');
  out.push('  |standoff|             error in bridge-forward-of-eye-plane, which decides');
  out.push('                         whether a frame clears the face at all');
  out.push('  scale%                 absolute size error — this is the iris ruler\'s');
  out.push('                         accuracy, and the "true-iris" row is the same');
  out.push('                         pipeline with the population bias removed');
  return out.join('\n');
}

function runOne(
  subject: SyntheticSubject, geometry: typeof CAMERA_LADDER[number], variant: Variant,
  mesh: ReturnType<typeof loadTemplateMesh>, basis: ReturnType<typeof loadBasis>,
  regions: ReturnType<typeof loadRegions>, verbose: boolean,
) {
  const capture = synthesizeCapture(mesh, subject, geometry, {
    framesPerBeat: 14, ...variant.capture,
  });
  const result = enroll({
    mesh,
    basis,
    frames: capture.frames.map((f) => ({
      landmarks: f.landmarks,
      sigmaPx: f.sigmaPx,
      visibility: f.visibility,
      silhouette: f.silhouette,
      beat: f.beat,
    })),
    imageWidth: geometry.width,
    imageHeight: geometry.height,
    irisMm: variant.useTrueIris ? subject.irisDiameterMm : undefined,
    bundle: variant.bundle,
    trace: verbose ? (m) => console.log(`    [${subject.id}/${geometry.name}] ${m}`) : undefined,
  });
  const comparison = compareToTruth(result.model, subject, regions, mesh);
  return { comparison, model: result.model, result };
}
