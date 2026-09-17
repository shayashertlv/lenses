import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ADDRESS_OPTIONS, APPLE_PHONE_HAIR_DELEGATE, DEFAULT_CONFIG, describeConfig, isApplePhoneOrTablet, parseConfig, unrecognizedOptions} from '../src/config.ts';
import {parseExternalModel} from '../src/eyewear/external.ts';
import {DEFAULT_HAIR_INPUT_MAX_EDGE, HAIR_WAIT_MS} from '../src/pipeline/pipeline.ts';
import {DEFAULT_STEADY} from '../src/render/pose-stabilizer.ts';
import {DEFAULT_HAIR_SCHEDULE} from '../src/hair/mask-reuse.ts';

test('an empty search yields the accepted defaults', () => {
  assert.deepEqual(parseConfig(''), {...DEFAULT_CONFIG, faceDelegates: ['CPU', 'GPU']});
  assert.deepEqual(parseConfig('?unknown=1'), {...DEFAULT_CONFIG, faceDelegates: ['CPU', 'GPU']});
  assert.match(describeConfig(DEFAULT_CONFIG), /CPU delegate.*capture 1280 px max edge · capture source VideoFrame \(\?source=\) · hair start z -0.02 m · GPU completion gate on · camera exposure auto.*guard on · continuity cut on \(hair run ≥ 10 px/);
});

test('every lever parses with its bounds and its off spelling', () => {
  const config = parseConfig('?face=gpu&capture=960&hairz=-0.03&sync=0&exposure=312&guard=0&continuity=off&hairrun=16&eyewear=tom-ford-clear&hairModel=selfie-multiclass&hair=0');
  assert.deepEqual(config, {faceDelegates: ['GPU', 'CPU'], captureMaxEdge: 960, captureSource: 'videoframe', hairWaitMs: 120, hairInputMaxEdge: 640, hairDelegate: 'auto', hairStartZ: -0.03, sync: false, exposure: 312, guard: false, continuity: false,
    continuityRunPx: 16, eyewear: 'tom-ford-clear', hairModel: 'selfie-multiclass', hair: false, diagnostics: false, steady: DEFAULT_STEADY, hairSchedule: DEFAULT_HAIR_SCHEDULE});
  assert.equal(parseConfig('').diagnostics, false); assert.equal(parseConfig('?diag=0').diagnostics, false); assert.equal(parseConfig('?diag=1').diagnostics, true); assert.equal(parseConfig('?diag=on').diagnostics, true);
  assert.match(describeConfig(parseConfig('?diag=1')), /diagnostics ON \(\?diag=1\)\.$/); assert.doesNotMatch(describeConfig(DEFAULT_CONFIG), /diagnostics/);
  assert.match(describeConfig(config), /GPU delegate, then CPU \(\?face=\).*capture 960 px max edge \(\?capture=\).*OFF \(\?sync=0\).*locked at 312 × 100 µs.*guard OFF.*continuity cut OFF/);
  const out = parseConfig('?capture=100&hairz=5&exposure=auto&hairrun=0&face=cpu&hair=1');
  assert.deepEqual(out, {...DEFAULT_CONFIG, faceDelegates: ['CPU', 'GPU'], hair: true});
  assert.equal(parseConfig('?exposure=0').exposure, null);
  assert.equal(parseConfig('?capture=1000.4').captureMaxEdge, 1000);
  assert.equal(parseConfig('?source=videoframe').captureSource, 'videoframe'); assert.equal(parseConfig('?source=canvas').captureSource, 'canvas'); assert.equal(parseConfig('?source=gpu').captureSource, 'videoframe');
  assert.match(describeConfig(parseConfig('?source=canvas')), /capture source canvas \(\?source=\)/); assert.match(describeConfig(DEFAULT_CONFIG), /capture source VideoFrame \(\?source=\)/);
  assert.equal(parseConfig('?hairwait=48').hairWaitMs, 48); assert.equal(parseConfig('?hairwait=500').hairWaitMs, HAIR_WAIT_MS); assert.match(describeConfig(parseConfig('?hairwait=48')), /hair mask guard 48 ms \(\?hairwait=\)/);
});

test('iPhone and iPad default to the GPU delegate first, the order the phone tests ran; ?face= overrides it', () => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
  const ipadAsMac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15';
  const laptop = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36';
  assert.ok(isApplePhoneOrTablet(iphone) && isApplePhoneOrTablet(ipadAsMac, 5) && !isApplePhoneOrTablet(ipadAsMac, 0) && !isApplePhoneOrTablet(laptop, 10));
  assert.deepEqual(parseConfig('', iphone).faceDelegates, ['GPU', 'CPU']);
  assert.deepEqual(parseConfig('?face=cpu', iphone).faceDelegates, ['CPU', 'GPU']);
  assert.deepEqual(parseConfig('', laptop).faceDelegates, ['CPU', 'GPU']);
  assert.deepEqual(parseConfig('?face=gpu', laptop).faceDelegates, ['GPU', 'CPU']);
  assert.match(describeConfig(parseConfig('', iphone)), /GPU delegate, then CPU/);
});

test('every device waits for its own hair mask behind one guard and feeds the hair segmenter a 640 px copy; Apple phones run hair on the CPU', () => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1';
  const android = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36';
  const laptop = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36';
  assert.equal(HAIR_WAIT_MS, 120); assert.equal(DEFAULT_HAIR_INPUT_MAX_EDGE, 640);
  assert.equal(parseConfig('', laptop).captureSource, 'videoframe'); assert.equal(parseConfig('', iphone).captureSource, 'canvas'); assert.equal(parseConfig('', android).captureSource, 'canvas');
  assert.equal(parseConfig('?source=videoframe', iphone).captureSource, 'videoframe'); assert.equal(parseConfig('?source=canvas', laptop).captureSource, 'canvas');
  for (const agent of [iphone, android, laptop]) {assert.equal(parseConfig('', agent).hairWaitMs, HAIR_WAIT_MS); assert.equal(parseConfig('', agent).hairInputMaxEdge, DEFAULT_HAIR_INPUT_MAX_EDGE);}
  assert.equal(parseConfig('?hairwait=8', iphone).hairWaitMs, 8); assert.equal(parseConfig('?hairwait=60', laptop).hairWaitMs, 60);
  assert.equal(parseConfig('?hairinput=1280', laptop).hairInputMaxEdge, 1280); assert.equal(parseConfig('?hairinput=100', laptop).hairInputMaxEdge, 640); assert.equal(parseConfig('?hairinput=960', iphone).hairInputMaxEdge, 960);
  assert.doesNotMatch(describeConfig(parseConfig('', laptop)), /hair mask guard|hair input/); assert.match(describeConfig(parseConfig('?hairinput=1280', laptop)), /hair input 1280 px max edge \(\?hairinput=\)/);
  assert.equal(parseConfig('?hairdelegate=cpu').hairDelegate, 'CPU'); assert.equal(parseConfig('?hairdelegate=GPU').hairDelegate, 'GPU'); assert.equal(parseConfig('?hairdelegate=npu').hairDelegate, 'auto');
  assert.equal(parseConfig('', iphone).hairDelegate, APPLE_PHONE_HAIR_DELEGATE); assert.equal(parseConfig('', android).hairDelegate, 'auto'); assert.equal(parseConfig('?hairdelegate=gpu', iphone).hairDelegate, 'GPU');
  assert.match(describeConfig(parseConfig('', iphone), iphone), /hair delegate CPU \(Apple phone default\) \(\?hairdelegate=\)/);
  assert.match(describeConfig(parseConfig('?hairdelegate=cpu')), /hair delegate CPU \(\?hairdelegate=\)/);
});

test('the address options list is exactly what the page reads, and any other key is named as ignored', () => {
  const read = new Set<string>(), get = URLSearchParams.prototype.get, has = URLSearchParams.prototype.has;
  URLSearchParams.prototype.get = function (this: URLSearchParams, name: string) {read.add(name); return get.call(this, name);};
  URLSearchParams.prototype.has = function (this: URLSearchParams, ...args: [string, string?]) {read.add(args[0]); return has.apply(this, args);};
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1';
  try {
    for (const agent of ['', iphone]) {parseConfig('', agent); parseConfig('?hairframes=2&exposure=312&hair=1', agent);}
    parseExternalModel('?model=/m.glb', 'https://example.test');
  } finally {URLSearchParams.prototype.get = get; URLSearchParams.prototype.has = has;}
  assert.deepEqual([...read].sort(), [...ADDRESS_OPTIONS].sort());
  // The iPhone B run that ran A: one letter short.
  assert.deepEqual(unrecognizedOptions('?hairframe=2&diag=1'), ['hairframe']);
  assert.deepEqual(unrecognizedOptions('?hairframes=2&diag=1'), []);
  assert.deepEqual(unrecognizedOptions(''), []); assert.deepEqual(unrecognizedOptions('?&&=1'), []);
  assert.deepEqual(unrecognizedOptions('?HairFrames=2&x=1&x=2&hairModel=selfie-multiclass&model=/m.glb'), ['HairFrames', 'x']);
  // The measurement harness forwards only options the page reads.
  const forwarded = readFileSync(new URL('../qa/measure.mjs', import.meta.url), 'utf8').match(/for \(const name of \[([^\]]+)\]\)/)?.[1];
  assert.ok(forwarded, 'qa/measure.mjs forwards a list of options');
  const names = [...forwarded.matchAll(/'([^']+)'/g)].map(match => match[1]!);
  assert.ok(names.length > 10 && names.every(name => ADDRESS_OPTIONS.includes(name)), names.filter(name => !ADDRESS_OPTIONS.includes(name)).join(', '));
});
