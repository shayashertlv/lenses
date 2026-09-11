import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {transformWithOxc} from 'vite';

interface Renderer {dispose(): void;}
interface Factory {
  create(display: HTMLCanvasElement, signal: AbortSignal, id?: string,
    onStage?: (stage: string) => void): Promise<Renderer>;
}
/** Exercise the real orchestration with injected renderer factories; native
 * rendering and its WebGL contexts belong to browser verification. */
async function factory(base: () => Promise<Renderer>, candidate: () => Promise<Renderer>): Promise<Factory> {
  const source = readFileSync(new URL('./comparison-renderer.ts', import.meta.url), 'utf8')
    .replace(/^import .*;\r?$/gm, '').replace(/^export type .*;\r?$/gm, '')
    .replace('export class ComparisonRenderer', 'class ComparisonRenderer');
  const compiled = await transformWithOxc(source, 'comparison-renderer.ts');
  return runInNewContext(`${compiled.code}\nComparisonRenderer;`, {BaseRenderer: {create: base},
    CandidateRenderer: {create: candidate}, DEFAULT_EYEWEAR_ID: 'amber-horizon', DOMException}) as Factory;
}

const display = {} as HTMLCanvasElement;

test('startup milestones preserve sequential G then candidate construction without drawing', async () => {
  const events: string[] = [];
  const base = {dispose() {events.push('dispose-g');}};
  const candidate = {dispose() {events.push('dispose-candidate');}};
  const ComparisonRenderer = await factory(async () => {events.push('load-g'); return base;},
    async () => {events.push('load-candidate'); return candidate;});
  const result = await ComparisonRenderer.create(display, new AbortController().signal, 'amber-horizon', stage => events.push(stage));
  assert.deepEqual(events, ['g-renderer', 'load-g', 'candidate-renderer', 'load-candidate']);
  result.dispose(); assert.deepEqual(events.slice(4), ['dispose-candidate', 'dispose-g']);
});

test('a late G renderer after cancellation is disposed before any candidate setup or milestone', async () => {
  const abort = new AbortController(), events: string[] = [];
  let resolveBase!: (value: Renderer) => void;
  const base = {dispose() {events.push('dispose-g');}};
  const ComparisonRenderer = await factory(() => new Promise<Renderer>(resolve => {resolveBase = resolve;}),
    async () => {throw new Error('Candidate must not start after cancellation.');});
  const pending = ComparisonRenderer.create(display, abort.signal, 'amber-horizon', stage => events.push(stage));
  abort.abort(); resolveBase(base);
  await assert.rejects(pending, error => error instanceof DOMException && error.name === 'AbortError');
  assert.deepEqual(events, ['g-renderer', 'dispose-g']);
});

test('a failed candidate or progress callback releases the already owned G renderer', async () => {
  let disposed = 0;
  const base = {dispose() {disposed++;}};
  const ComparisonRenderer = await factory(async () => base, async () => {throw new Error('Candidate startup failed.');});
  await assert.rejects(ComparisonRenderer.create(display, new AbortController().signal), /Candidate startup failed/);
  assert.equal(disposed, 1);
  await assert.rejects(ComparisonRenderer.create(display, new AbortController().signal, 'amber-horizon', stage => {
    if (stage === 'candidate-renderer') throw new Error('Startup owner rejected the milestone.');
  }), /owner rejected/);
  assert.equal(disposed, 2);
});
