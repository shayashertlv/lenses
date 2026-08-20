/**
 * Every exported constant must answer for itself.
 *
 * v1's discipline, made mechanical. It scans `src/` for exported constants and
 * fails if one has no entry in `docs/CONSTANTS.md`.
 *
 * What it deliberately does NOT do is fail on the number of `stated` entries.
 * v1 got this exactly right and the reasoning is worth repeating: driving that
 * count to zero by writing better sentences is precisely the dishonesty the
 * ledger exists to prevent. The count is reported; a reviewer decides.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const LEDGER = readFileSync('docs/CONSTANTS.md', 'utf8');

/**
 * Names that are structural rather than tuning: types, factories, pure
 * conversions, and the enumerations that only give a thing a name.
 */
const EXEMPT = new Set([
  'CV_TO_GL', 'MM_TO_SCENE', 'GRAVITY_N_PER_G', 'FACE_MODEL_VERSION',
  'MESH_VERTEX_COUNT', 'MESH_LANDMARK_COUNT', 'FIRST_IRIS_INDEX',
  'DETECTOR_LANDMARK_COUNT', 'INTRINSICS_FREE_F', 'INTRINSICS_FIXED',
  'TRIVIAL', 'LM_DEFAULTS', 'POPULATION_DEFAULTS', 'CAPTURE_DEFAULTS',
  'BEATS', 'TEST_FRAMES', 'VARIANTS', 'SOFT_VERDICT', 'ROLL_PREROTATION',
  'UNCERTAINTY_DEFAULTS', 'SEAT_DEFAULTS', 'BUNDLE_DEFAULTS',
  'KEYFRAME_DEFAULTS', 'TRACKER_DEFAULTS', 'PNP_DEFAULTS',
  'DISPLACEMENT_PRIORS', 'SKIN', 'IRIS', 'POPULATION_HVID', 'ID1_CARD',
  'PLAUSIBLE', 'CAMERA_LADDER', 'COVERAGE_THRESHOLDS', 'PD_PLAUSIBLE_MM',
  'TRANSLATION_SMOOTHING', 'ROTATION_SMOOTHING', 'LM', 'EPS',
]);

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  return statSync(full).isDirectory() ? walk(full) : [full];
});

const found = [];
for (const file of walk('src')) {
  if (!file.endsWith('.ts')) continue;
  const rel = relative('.', file).split(sep).join('/');
  const text = readFileSync(file, 'utf8');
  // `export const NAME` where NAME is SCREAMING_SNAKE — the shape a tuning
  // constant takes in this tree. Functions and types are not constants.
  const pattern = /^export const ([A-Z][A-Z0-9_]*)\s*[:=]/gm;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    found.push({ name: match[1], file: rel });
  }
}

const missing = found.filter(
  (c) => !EXEMPT.has(c.name) && !LEDGER.includes(c.name),
);

// The class mix, reported not gated.
const counts = {};
for (const line of LEDGER.split('\n')) {
  const m = line.match(/\|\s*`(physics|derived|published|measured|stated)`\s*\|/);
  if (m) counts[m[1]] = (counts[m[1]] ?? 0) + 1;
}

console.log(`constants ledger: ${found.length} exported constants scanned`);
console.log('  ' + Object.entries(counts)
  .sort((a, b) => b[1] - a[1])
  .map(([k, v]) => `${v} ${k}`)
  .join(' · '));

if (counts.stated) {
  console.log(
    `  ${counts.stated} are 'stated' — somebody chose them. That number is reported,`,
  );
  console.log("  not gated: driving it to zero by writing better sentences would be worse.");
}

if (missing.length) {
  console.error('\nno entry in docs/CONSTANTS.md:');
  for (const c of missing) console.error(`  ${c.name}  (${c.file})`);
  console.error('\nA constant with no provenance is a constant nobody can review.');
  process.exit(1);
}
