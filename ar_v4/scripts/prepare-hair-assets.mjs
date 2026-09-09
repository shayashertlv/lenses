import {createHash} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const directory = new URL('../public/models/hair/', import.meta.url);
const pins = [
  {id: 'hair-only', bytes: 781618, sha256: '2628cf3ce5f695f604cbea2841e00befcaa3624bf80caf3664bef2656d59bf84',
    sourceUrl: 'https://storage.googleapis.com/mediapipe-models/image_segmenter/hair_segmenter/float32/1/hair_segmenter.tflite'},
  {id: 'selfie-multiclass', bytes: 16371837, sha256: 'c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0',
    sourceUrl: 'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/1/selfie_multiclass_256x256.tflite'},
];
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const args = process.argv.slice(2);
if (args.some(arg => arg !== '--download') || args.length > 1) throw new Error('Usage: node scripts/prepare-hair-assets.mjs [--download]');
const downloadMissing = args.includes('--download');
const manifest = JSON.parse(await readFile(new URL('manifest.json', directory), 'utf8'));
if (manifest.schema !== 'ar-v4-hair-model-assets-v1' || manifest.models.length !== pins.length)
  throw new Error('Unexpected hair model manifest.');
const results = [];
for (const pin of pins) {
  const filename = `${pin.id}.tflite`, target = new URL(filename, directory);
  const model = manifest.models.find(model => model.id === pin.id);
  if (!model || model.path !== filename || model.bytes !== pin.bytes || model.sha256 !== pin.sha256 ||
      model.sourceUrl !== pin.sourceUrl || model.license !== 'Apache-2.0' || model.modified !== false)
    throw new Error(`Hair model manifest differs from reviewed pins: ${pin.id}`);
  let bytes;
  try { bytes = await readFile(target); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    if (!downloadMissing) throw new Error(`Missing ${filename}; restore tracked assets or run with --download.`);
    const response = await fetch(pin.sourceUrl, {redirect: 'error', signal: AbortSignal.timeout(120_000)});
    if (!response.ok) throw new Error(`Model download failed (${response.status}): ${pin.id}`);
    bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length !== pin.bytes || sha(bytes) !== pin.sha256) throw new Error(`Downloaded hair model mismatch: ${pin.id}`);
    await mkdir(directory, {recursive: true});
    await writeFile(target, bytes, {flag: 'wx'});
  }
  if (bytes.length !== pin.bytes || sha(bytes) !== pin.sha256) throw new Error(`Tracked hair model mismatch: ${pin.id}`);
  results.push({id: pin.id, path: fileURLToPath(target), bytes: bytes.length, sha256: sha(bytes)});
}
const license = await readFile(new URL('LICENSE-2.0.txt', directory));
if (sha(license) !== 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30')
  throw new Error('Hair model license copy differs.');
await readFile(new URL('ATTRIBUTION.md', directory));
console.log(JSON.stringify({schema: 'ar-v4-hair-assets-verification-v1', verified: true, models: results}, null, 2));
