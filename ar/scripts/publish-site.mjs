/** Builds the deployable site: the page bundled with the given base (default `/ar/`, where the Lenses web app serves
 *  it) into `site/`, plus `site/public-manifest.json` listing every served file with its size and SHA-256. The web app
 *  serves only the manifest's files and verifies their hashes. Dotfiles are left out of the manifest.
 *    node scripts/publish-site.mjs [--base=/ar/] */
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const base = process.argv.find(value => value.startsWith('--base='))?.slice(7) ?? '/ar/';
if (!/^\/[A-Za-z0-9_-]+\/$/.test(base) && base !== '/') throw new Error(`The base must look like /ar/ (got ${base}).`);
const site = path.join(root, 'site');
execFileSync(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), 'build', '--base', base, '--outDir', 'site', '--emptyOutDir'],
  {cwd: root, stdio: 'inherit'});
const files = [];
async function walk(directory) {
  for (const entry of (await readdir(directory, {withFileTypes: true})).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name.startsWith('.')) continue;
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {await walk(full); continue;}
    const relative = path.relative(site, full).split(path.sep).join('/');
    if (relative === 'public-manifest.json') continue;
    const bytes = await readFile(full);
    files.push({path: relative, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')});
  }
}
await walk(site);
if (!files.some(file => file.path === 'index.html')) throw new Error('The build produced no index.html.');
const manifest = {schemaVersion: 1, base, builtAt: new Date().toISOString(), files};
await writeFile(path.join(site, 'public-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
// The published bytes are verified by SHA-256 on every request: git must never convert their line endings.
await writeFile(path.join(site, '.gitattributes'), '# Published bytes are verified by SHA-256; never convert line endings.\n* -text\n');
const total = files.reduce((sum, file) => sum + file.size, 0);
console.log(`site: ${files.length} files, ${(total / 1024 / 1024).toFixed(1)} MB, base ${base}, manifest written.`);
