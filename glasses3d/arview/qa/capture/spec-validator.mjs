/** The Python §2 validator (`python -m glasses3d.g3d_host.spec --full <glb>`, glasses3d/.venv), the single source of truth
 *  for the owner's spec rows (WP1_DESIGN.md §1), run as a child process with a deadline. Shared by `arview capture`
 *  (qa/capture.mjs) and `arview serve` (qa/serve.mjs). Never rejects: an unavailable validator is a result that says why,
 *  and every caller turns it into a loud "VALIDATOR UNAVAILABLE" warning. */
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

export const LENSES_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
export const SPEC_TIMEOUT_MS = 120_000;
const messageOf = error => error instanceof Error ? error.message : String(error);

/** spec.py --full on `glbPath`, or why it is unavailable. Resolves {available: true, report, command, ms} or
 *  {available: false, reason, command, ms}; a run past `timeoutMs` is killed and reported as unavailable. */
export function runSpecValidator(glbPath, timeoutMs = SPEC_TIMEOUT_MS) {
  const started = Date.now();
  const python = [path.join(LENSES_ROOT, 'glasses3d/.venv/Scripts/python.exe'), path.join(LENSES_ROOT, 'glasses3d/.venv/bin/python')].find(file => fs.existsSync(file));
  if (!python) return Promise.resolve({available: false, reason: 'no Python interpreter in glasses3d/.venv', command: null, ms: 0});
  const command = [python, '-m', 'glasses3d.g3d_host.spec', '--full', glbPath];
  return new Promise(resolve => {
    let stdout = '', stderr = '', settled = false;
    const done = value => {if (!settled) {settled = true; resolve({...value, command, ms: Date.now() - started});}};
    let child;
    try {child = spawn(command[0], command.slice(1), {cwd: LENSES_ROOT, windowsHide: true});}
    catch (error) {done({available: false, reason: `spec.py could not start (${messageOf(error)})`}); return;}
    const timer = setTimeout(() => {child.kill(); done({available: false, reason: `spec.py did not finish within ${timeoutMs / 1000} s (killed)`});}, timeoutMs);
    timer.unref?.();
    child.stdout.on('data', chunk => {stdout += chunk;});
    child.stderr.on('data', chunk => {stderr += chunk;});
    child.on('error', error => {clearTimeout(timer); done({available: false, reason: `spec.py could not run (${messageOf(error)})`});});
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 && code !== 1) {done({available: false, reason: `spec.py exited ${code}: ${stderr.trim().slice(-600) || stdout.trim().slice(-600)}`}); return;}
      try {
        const parsed = JSON.parse(stdout);
        const report = Array.isArray(parsed) ? parsed[0] : null;
        if (!report || typeof report !== 'object' || typeof report.ok !== 'boolean') throw new Error('no report object in its output');
        done({available: true, report});
      } catch (error) {done({available: false, reason: `spec.py output is not its --full JSON (${messageOf(error)}): ${stdout.slice(0, 300)}`});}
    });
  });
}
