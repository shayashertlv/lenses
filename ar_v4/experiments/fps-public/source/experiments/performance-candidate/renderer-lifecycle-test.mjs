import {build} from 'rolldown';
import {mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Node's strip-only TS loader cannot load the accepted renderer's parameter
// properties. Compile this isolated test without altering any rendering source.
const workspace = fileURLToPath(new URL('../../', import.meta.url));
const directory = path.join(workspace, 'experiments/performance-candidate/test-results');
await mkdir(directory, {recursive: true});
const output = path.join(directory, `renderer-lifecycle-${new Date().toISOString().replaceAll(':', '-')}.mjs`);
await build({input: fileURLToPath(new URL('./renderer-lifecycle.test-source.ts', import.meta.url)), platform: 'node',
  external: ['three', /^three\//, /^node:/], output: {file: output, format: 'esm'}});
execFileSync(process.execPath, ['--test', output], {cwd: workspace, stdio: 'inherit'});
