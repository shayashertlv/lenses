import assert from 'node:assert/strict';
import test from 'node:test';
import {FPS_MODES, optionsForFpsMode, parseFpsMode} from '../runtime/options.ts';

test('new tests isolate CPU work from previously tested query and identity changes', () => {
  for (const mode of ['compose', 'bookkeeping', 'next-combined'] as const) {
    const options = optionsForFpsMode(mode);
    assert.equal(options.queries, false);
    assert.equal(options.identity, false);
    assert.equal(options.cpuCompose, mode !== 'bookkeeping');
    assert.equal(options.bookkeeping, mode !== 'compose');
    assert.equal(Object.isFrozen(options), true);
  }
});

test('G and earlier tests do not activate new CPU paths', () => {
  for (const mode of ['g', 'queries', 'identity', 'combined'] as const) {
    const options = optionsForFpsMode(mode);
    assert.equal(options.cpuCompose, false);
    assert.equal(options.bookkeeping, false);
    assert.equal(options.queries, mode === 'queries' || mode === 'combined');
    assert.equal(options.identity, mode === 'identity' || mode === 'combined');
  }
});

test('explicit known test routes select one mode; unknown routes retain G', () => {
  for (const mode of FPS_MODES) assert.equal(parseFpsMode(`?fps=${mode}`), mode);
  for (const query of ['', '?fps=unknown', '?fps=COMPOSE', '?cpuCompose=true']) assert.equal(parseFpsMode(query), 'g');
});
