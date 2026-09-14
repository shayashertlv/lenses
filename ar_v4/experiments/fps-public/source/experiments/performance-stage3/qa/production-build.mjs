import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export async function freezeProductionBuild(directory) {
  const root = path.resolve(directory), files = [];
  const scan = async folder => {
    for (const entry of await fs.readdir(folder, {withFileTypes: true})) {
      const filename = path.join(folder, entry.name);
      if (entry.isDirectory()) await scan(filename);
      else if (entry.isFile()) {
        const bytes = await fs.readFile(filename);
        files.push({path: filename, relative: path.relative(root, filename).replaceAll('\\', '/'), bytes: bytes.length, sha256: sha(bytes)});
      }
    }
  };
  await scan(root); assert.ok(files.length > 0, 'The selected production build is empty.');
  return {root, createdAt: new Date().toISOString(), files};
}
export async function verifyProductionBuild(snapshot) {
  const current = await freezeProductionBuild(snapshot.root);
  assert.deepEqual(current.files, snapshot.files, 'Compiled production files changed during QA.');
  return {verifiedAt: new Date().toISOString(), files: current.files.length};
}
export async function verifyServedEntries(base, pagePath, snapshot) {
  const checked = [], files = new Map(snapshot.files.map(file => ['/' + file.relative, file]));
  const check = async pathname => {
    const expected = files.get(pathname); assert.ok(expected, `Served entry is not present in the compiled manifest: ${pathname}`);
    const response = await fetch(base + pathname); assert.equal(response.status, 200);
    const bytes = Buffer.from(await response.arrayBuffer());
    assert.equal(sha(bytes), expected.sha256, `The local server is not serving the frozen production entry: ${pathname}`);
    checked.push({url: base + pathname, sha256: expected.sha256, bytes: bytes.length}); return bytes;
  };
  const html = (await check(pagePath)).toString('utf8');
  const entries = [...html.matchAll(/(?:src|href)=["']([^"']+\.(?:js|css))["']/g)].map(match => new URL(match[1], base + pagePath));
  assert.ok(entries.some(url => url.pathname.endsWith('.js')), 'The production page has no compiled JavaScript entry.');
  for (const url of entries) {assert.equal(url.origin, base); await check(url.pathname);}
  return checked;
}
