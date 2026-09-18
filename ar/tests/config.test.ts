import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ADDRESS_OPTIONS, APPLE_PHONE_HAIR_DELEGATE, DEFAULT_CONFIG, describeConfig, isApplePhoneOrTablet, isPhoneOrTablet, parseConfig, unrecognizedOptions} from '../src/config.ts';
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
    continuityRunPx: 16, eyewear: 'tom-ford-clear', hairModel: 'selfie-multiclass', hair: false, diagnostics: false, steady: DEFAULT_STEADY, hairSchedule: DEFAULT_HAIR_SCHEDULE, fit: 'original', temples: 'depth', templeKeepCm: 0.6, templeDropCm: 2.6, templeBendMm: 0});
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

test('the temple fit is a two-value selector, original by default, and the settings line names the running one', () => {
  // The live comparison: exactly two modes, and anything else is the shipped geometry rather than a guess.
  assert.equal(parseConfig('').fit, 'original');
  assert.equal(parseConfig('?fit=width').fit, 'width'); assert.equal(parseConfig('?fit=WIDTH').fit, 'width');
  assert.equal(parseConfig('?fit=original').fit, 'original'); assert.equal(parseConfig('?fit=1').fit, 'original');
  assert.equal(parseConfig('?fit=').fit, 'original'); assert.equal(parseConfig('?fit=wide').fit, 'original');
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1';
  for (const agent of ['', iphone]) assert.equal(parseConfig('', agent, 5).fit, 'original', 'no device opts in to the experiment');
  // The experiment must not disturb the measured capture, delegate, hair schedule and pose defaults of either device.
  for (const agent of ['', iphone]) {
    const {fit, ...rest} = parseConfig('?fit=width', agent, 5);
    const {fit: plainFit, ...plain} = parseConfig('', agent, 5);
    assert.equal(fit, 'width'); assert.equal(plainFit, 'original'); assert.deepEqual(rest, plain);
  }
  assert.match(describeConfig(parseConfig('')), /temple fit original \(\?fit=width for the experiment\)/);
  assert.match(describeConfig(parseConfig('?fit=width')), /temple fit WIDTH FIT, experimental: the head occluder and the posterior arm spread follow a stable face-width ratio \(\?fit=original restores the shipped geometry\)/);
  assert.deepEqual(unrecognizedOptions('?fit=width'), []); assert.deepEqual(unrecognizedOptions('?fits=width'), ['fits']);
});

test('the temple-occlusion rule is a two-value selector, per pixel by default, and the settings line names it', () => {
  assert.equal(parseConfig('').temples, 'depth');
  assert.equal(parseConfig('?temples=angles').temples, 'angles'); assert.equal(parseConfig('?temples=ANGLES').temples, 'angles');
  assert.equal(parseConfig('?temples=depth').temples, 'depth');
  assert.equal(parseConfig('?temples=1').temples, 'depth'); assert.equal(parseConfig('?temples=').temples, 'depth');
  assert.match(describeConfig(parseConfig('')), /temple occlusion per pixel from the head's own depth, v4: kept to 0.6 cm behind it, gone by 2.6 cm/);
  // The band is the only number deciding how much arm survives, so it is judgeable live.
  assert.deepEqual([parseConfig('?templekeep=1.2&templedrop=4').templeKeepCm, parseConfig('?templekeep=1.2&templedrop=4').templeDropCm], [1.2, 4]);
  assert.deepEqual([parseConfig('?templekeep=0').templeKeepCm, parseConfig('?templekeep=0').templeDropCm], [0, 2.6]);
  // A pair that is not a band, or either end out of range, falls back to both defaults rather than half of each.
  for (const bad of ['?templekeep=3&templedrop=2', '?templedrop=0.2', '?templekeep=9', '?templedrop=99', '?templekeep=x&templedrop=y'])
    assert.deepEqual([parseConfig(bad).templeKeepCm, parseConfig(bad).templeDropCm], [0.6, 2.6], bad);
  assert.match(describeConfig(parseConfig('?templekeep=1.2&templedrop=4')), /kept to 1.2 cm behind it, gone by 4 cm/);
  assert.deepEqual(unrecognizedOptions('?templekeep=1&templedrop=3'), []);
  // The manual outward bend at the tips, in millimetres, hinged at the front of the frame.
  assert.equal(parseConfig('').templeBendMm, 0);
  assert.equal(parseConfig('?templebend=6').templeBendMm, 6);
  assert.equal(parseConfig('?templebend=-4').templeBendMm, -4);
  assert.equal(parseConfig('?templebend=12').templeBendMm, 12);
  for (const bad of ['?templebend=13', '?templebend=-13', '?templebend=x', '?templebend='])
    assert.equal(parseConfig(bad).templeBendMm, 0, bad);
  assert.match(describeConfig(parseConfig('')), /temple bend none, the arms as authored \(\?templebend=\)/);
  assert.match(describeConfig(parseConfig('?templebend=6')), /temple bend 6 mm outward at the tip, hinged at the front/);
  assert.deepEqual(unrecognizedOptions('?templebend=6'), []); assert.deepEqual(unrecognizedOptions('?templebends=6'), ['templebends']);
  assert.match(describeConfig(parseConfig('?temples=angles')), /temple occlusion PER-SIDE PERCENTAGES from the head angles, the former v3 \(\?temples=depth\)/);
  assert.deepEqual(unrecognizedOptions('?temples=angles'), []); assert.deepEqual(unrecognizedOptions('?temple=angles'), ['temple']);
  // The rule must not disturb the measured capture, delegate, hair schedule and pose defaults of either device.
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1';
  for (const agent of ['', iphone]) {
    const {temples, ...rest} = parseConfig('?temples=angles', agent, 5);
    const {temples: fallback, ...plain} = parseConfig('', agent, 5);
    assert.equal(temples, 'angles'); assert.equal(fallback, 'depth'); assert.deepEqual(rest, plain);
  }
});

test('every device defaults to the CPU face landmarker first (iPhone and iPad since 2026-09-17); ?face=gpu puts the GPU first', () => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
  const ipadAsMac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15';
  const android = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36';
  const androidTablet = 'Mozilla/5.0 (Linux; Android 14; SM-X910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36';
  const laptop = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36';
  assert.ok(isApplePhoneOrTablet(iphone) && isApplePhoneOrTablet(ipadAsMac, 5) && !isApplePhoneOrTablet(ipadAsMac, 0) && !isApplePhoneOrTablet(laptop, 10));
  assert.ok([iphone, android, androidTablet].every(agent => isPhoneOrTablet(agent, 0)) && isPhoneOrTablet(ipadAsMac, 5) && !isPhoneOrTablet(ipadAsMac, 0) && !isPhoneOrTablet(laptop, 10));
  // Chrome's desktop site on a large Android tablet: a Linux desktop user agent; touch without a fine pointer is the tablet.
  const desktopSite = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
  const chromebook = 'Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36';
  assert.ok(isPhoneOrTablet(desktopSite, 10, true) && !isPhoneOrTablet(desktopSite, 10, false) && !isPhoneOrTablet(desktopSite, 0, true) && !isPhoneOrTablet(chromebook, 10, true), 'a Chromebook says CrOS, not Linux');
  assert.ok(!isPhoneOrTablet(laptop, 10, true) && !isPhoneOrTablet(ipadAsMac, 0, true), 'only the Linux desktop user agent takes the pointer into account');
  assert.equal(parseConfig('', desktopSite, 10, true).hairSchedule.mode, 'interval'); assert.equal(parseConfig('', desktopSite, 10, true).captureSource, 'canvas');
  assert.equal(parseConfig('', desktopSite, 10, false).hairSchedule.mode, 'every'); assert.equal(parseConfig('', desktopSite, 10, false).captureSource, 'videoframe');
  assert.match(describeConfig(parseConfig('', desktopSite, 10, true), desktopSite, 10, true), /phone and tablet default/);
  assert.doesNotMatch(describeConfig(parseConfig('', desktopSite, 10, false), desktopSite, 10, false), /hair every|hair on every frame/);
  for (const [agent, touch] of [[iphone, 5], [ipadAsMac, 5], [android, 5], [androidTablet, 5], [laptop, 0], [laptop, 10]] as const) {
    assert.deepEqual(parseConfig('', agent, touch).faceDelegates, ['CPU', 'GPU'], agent);
    assert.deepEqual(parseConfig('?face=gpu', agent, touch).faceDelegates, ['GPU', 'CPU'], agent);
    assert.deepEqual(parseConfig('?face=cpu', agent, touch).faceDelegates, ['CPU', 'GPU'], agent);
    assert.match(describeConfig(parseConfig('', agent, touch), agent, touch), /face landmarker CPU delegate, then GPU/);
  }
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
  // Phones and tablets draw reused masks by default: the guard is then read only for an audited frame, and the line says so.
  assert.match(describeConfig(parseConfig('?hairwait=60', iphone, 5), iphone, 5), /hair mask guard 60 ms \(\?hairwait=\) applies only to an audited frame: with hair every 2 frames no other frame waits/);
  assert.match(describeConfig(parseConfig('?hairwait=60&hairframes=1', iphone, 5), iphone, 5), /hair mask guard 60 ms \(\?hairwait=\) · /);
  assert.match(describeConfig(parseConfig('?hairwait=60', laptop, 0), laptop, 0), /hair mask guard 60 ms \(\?hairwait=\) · /);
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
