import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import type {Plugin} from 'vite';

export const ACCEPTED_COMMIT = 'b26b5584c0dccbc2b30e4f12cdd432f10df577ea';
const workspace = fileURLToPath(new URL('../../', import.meta.url));
export const ACCEPTED_REFERENCE_ROOT = path.join(workspace, 'references/perfect-temples');
export const ACCEPTED_REFERENCE_MANIFEST = path.join(ACCEPTED_REFERENCE_ROOT, 'manifest.json');
const manifestSHA256 = 'eb34c6073741d8d9b76a37e6946dadebd7cf82ba0c0b2c07d6a2c828c4cb71c2';
const hash = (bytes: Uint8Array): string => createHash('sha256').update(bytes).digest('hex');

/** Resolve the reviewed source snapshot without Git history or private archives. */
export async function acceptedReferencePlugin(): Promise<Plugin> {
  const manifestBytes = await readFile(ACCEPTED_REFERENCE_MANIFEST);
  if (hash(manifestBytes) !== manifestSHA256) throw new Error('The pinned perfect_temples reference manifest differs.');
  const receipt = JSON.parse(manifestBytes.toString('utf8')) as {
    schema: string; acceptedCommit: string;
    files: {relativePath: string; sha256: string; bytes: number}[];
  };
  if (receipt.schema !== 'hair-live-accepted-reference-v2' || receipt.acceptedCommit !== ACCEPTED_COMMIT || receipt.files.length !== 45)
    throw new Error('The pinned perfect_temples reference manifest is invalid.');
  const names = receipt.files.map(file => file.relativePath);
  if (!names.includes('src/render/renderer.ts') || !names.includes('experiments/temple-sagittal/renderer.ts'))
    throw new Error('The pinned perfect_temples reference is incomplete.');
  const redirects = new Map<string, string>();
  for (const file of receipt.files) {
    const name = file.relativePath;
    const target = path.resolve(ACCEPTED_REFERENCE_ROOT, name), relative = path.relative(ACCEPTED_REFERENCE_ROOT, target);
    if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Accepted reference path escapes its directory.');
    const bytes = await readFile(target);
    if (bytes.length !== file.bytes || hash(bytes) !== file.sha256)
      throw new Error(`The immutable perfect_temples reference differs: ${name}`);
    redirects.set(path.resolve(workspace, name).toLowerCase(), target);
  }
  return {
    name: 'hair-preview-immutable-accepted-runtime', enforce: 'pre',
    resolveId(source, importer) {
      if (source.startsWith('\0')) return null;
      const question = source.indexOf('?'), clean = question < 0 ? source : source.slice(0, question);
      const suffix = question < 0 ? '' : source.slice(question);
      let candidate: string | null = null;
      if (clean.startsWith('/src/') || clean.startsWith('/experiments/temple-sagittal/')) candidate = path.resolve(workspace, clean.slice(1));
      else if (path.isAbsolute(clean)) candidate = path.resolve(clean);
      else if (clean.startsWith('.') && importer) candidate = path.resolve(path.dirname(importer.split('?')[0]!), clean);
      const destination = candidate ? redirects.get(candidate.toLowerCase()) : undefined;
      return destination ? destination + suffix : null;
    },
  };
}
