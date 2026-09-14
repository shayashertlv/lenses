import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {dirname, relative, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';

const root = dirname(fileURLToPath(import.meta.url));
const baseline = resolve(root, '../../speed-lab');
const paths = ['renderer.ts', 'temples/renderer.ts', 'native/renderer.ts'] as const;
const redirects = new Map([...paths, 'native/pbo-readback.ts'].map(path => [resolve(baseline, path), resolve(root, path)]));
for (const path of paths) test(`G diagnostic ${path} differs only in deterministic import rebasing`, async () => {
  const original = resolve(baseline, path), copy = resolve(root, path);
  const source = await readFile(original, 'utf8');
  const expected = source.replace(/(from\s+['"])([^'"]+)(['"])/g, (whole: string, before: string, specifier: string, after: string) => {
    if (!specifier.startsWith('.')) return whole;
    const dependency = resolve(dirname(original), specifier);
    let rebased = relative(dirname(copy), redirects.get(dependency) ?? dependency).split(sep).join('/');
    if (!rebased.startsWith('.')) rebased = './' + rebased;
    return before + rebased + after;
  });
  assert.equal((await readFile(copy, 'utf8')).replace(/\r\n/g, '\n'), expected.replace(/\r\n/g, '\n'));
});
