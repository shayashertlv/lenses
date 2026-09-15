import {test} from 'node:test';
import assert from 'node:assert/strict';
import {APPLE_PHONE_HAIR_DELEGATE, DEFAULT_CONFIG, describeConfig, isApplePhoneOrTablet, isPhoneOrTablet, parseConfig, PHONE_HAIR_INPUT_MAX_EDGE, PHONE_HAIR_WAIT_MS} from '../src/config.ts';

test('an empty search yields the accepted defaults', () => {
  assert.deepEqual(parseConfig(''), {...DEFAULT_CONFIG, faceDelegates: ['CPU', 'GPU']});
  assert.deepEqual(parseConfig('?unknown=1'), {...DEFAULT_CONFIG, faceDelegates: ['CPU', 'GPU']});
  assert.match(describeConfig(DEFAULT_CONFIG), /CPU delegate.*capture 1280 px max edge · hair start z -0.02 m · GPU completion gate on · camera exposure auto.*guard on · continuity cut on \(hair run ≥ 10 px/);
});

test('every lever parses with its bounds and its off spelling', () => {
  const config = parseConfig('?face=gpu&capture=960&hairz=-0.03&sync=0&exposure=312&guard=0&continuity=off&hairrun=16&eyewear=tom-ford-clear&hairModel=selfie-multiclass&hair=0');
  assert.deepEqual(config, {faceDelegates: ['GPU', 'CPU'], captureMaxEdge: 960, hairWaitMs: 8, hairInputMaxEdge: 1280, hairDelegate: 'auto', hairStartZ: -0.03, sync: false, exposure: 312, guard: false, continuity: false,
    continuityRunPx: 16, eyewear: 'tom-ford-clear', hairModel: 'selfie-multiclass', hair: false, diagnostics: false});
  assert.equal(parseConfig('').diagnostics, false); assert.equal(parseConfig('?diag=0').diagnostics, false); assert.equal(parseConfig('?diag=1').diagnostics, true); assert.equal(parseConfig('?diag=on').diagnostics, true);
  assert.match(describeConfig(parseConfig('?diag=1')), /diagnostics ON \(\?diag=1\)\.$/); assert.doesNotMatch(describeConfig(DEFAULT_CONFIG), /diagnostics/);
  assert.match(describeConfig(config), /GPU delegate, then CPU \(\?face=\).*capture 960 px max edge \(\?capture=\).*OFF \(\?sync=0\).*locked at 312 × 100 µs.*guard OFF.*continuity cut OFF/);
  const out = parseConfig('?capture=100&hairz=5&exposure=auto&hairrun=0&face=cpu&hair=1');
  assert.deepEqual(out, {...DEFAULT_CONFIG, faceDelegates: ['CPU', 'GPU'], hair: true});
  assert.equal(parseConfig('?exposure=0').exposure, null);
  assert.equal(parseConfig('?capture=1000.4').captureMaxEdge, 1000);
  assert.equal(parseConfig('?hairwait=48').hairWaitMs, 48); assert.equal(parseConfig('?hairwait=500').hairWaitMs, 8); assert.match(describeConfig(parseConfig('?hairwait=48')), /hair wait 48 ms \(\?hairwait=\)/);
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

test('phones wait longer for their own hair mask; laptops keep 8 ms; ?hairwait= overrides both', () => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1';
  const android = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Mobile Safari/537.36';
  const laptop = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36';
  assert.ok(isPhoneOrTablet(iphone) && isPhoneOrTablet(android) && !isPhoneOrTablet(laptop, 10));
  assert.equal(parseConfig('', iphone).hairWaitMs, PHONE_HAIR_WAIT_MS); assert.equal(parseConfig('', android).hairWaitMs, PHONE_HAIR_WAIT_MS); assert.equal(parseConfig('', laptop).hairWaitMs, 8);
  assert.equal(parseConfig('?hairwait=8', iphone).hairWaitMs, 8); assert.equal(parseConfig('?hairwait=60', laptop).hairWaitMs, 60);
  assert.equal(parseConfig('', iphone).hairInputMaxEdge, PHONE_HAIR_INPUT_MAX_EDGE); assert.equal(parseConfig('', laptop).hairInputMaxEdge, 1280);
  assert.equal(parseConfig('?hairinput=640', laptop).hairInputMaxEdge, 640); assert.equal(parseConfig('?hairinput=100', laptop).hairInputMaxEdge, 1280); assert.equal(parseConfig('?hairinput=1280', iphone).hairInputMaxEdge, 1280);
  assert.equal(parseConfig('?hairdelegate=cpu').hairDelegate, 'CPU'); assert.equal(parseConfig('?hairdelegate=GPU').hairDelegate, 'GPU'); assert.equal(parseConfig('?hairdelegate=npu').hairDelegate, 'auto');
  assert.equal(parseConfig('', iphone).hairDelegate, APPLE_PHONE_HAIR_DELEGATE); assert.equal(parseConfig('', android).hairDelegate, 'auto'); assert.equal(parseConfig('?hairdelegate=gpu', iphone).hairDelegate, 'GPU');
  assert.match(describeConfig(parseConfig('', iphone), iphone), /hair delegate CPU \(Apple phone default\) \(\?hairdelegate=\)/);
  assert.match(describeConfig(parseConfig('', iphone)), /hair input 640 px max edge \(phone default\) \(\?hairinput=\)/); assert.match(describeConfig(parseConfig('?hairdelegate=cpu')), /hair delegate CPU \(\?hairdelegate=\)/);
  assert.match(describeConfig(parseConfig('', iphone)), /hair wait 60 ms \(phone default\) \(\?hairwait=\)/);
});
