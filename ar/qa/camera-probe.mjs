/** What does the real camera deliver right now? Opens the default camera in a Chromium page with several constraint
 *  sets and counts requestVideoFrameCallback frames for a few seconds each. No image is drawn, stored or uploaded.
 *    node qa/camera-probe.mjs [--seconds=6] [--headed]
 *  Auto-grants the permission prompt (--use-fake-ui-for-media-stream); the device itself is the real one. */
import {chromium} from '@playwright/test';
const args = process.argv.slice(2);
const option = (name, fallback) => args.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const seconds = Number(option('seconds', '6'));
const CONSTRAINTS = [
  ['app (facingMode user, 1280x720 ideal, no frameRate)', {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}}],
  ['1280x720 ideal + frameRate ideal 30', {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 30}}],
  ['1280x720 ideal + frameRate min 30', {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}, frameRate: {min: 30, ideal: 30}}],
  ['1280x720 ideal + frameRate exact 30', {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}, frameRate: {exact: 30}}],
  ['960x540 ideal + frameRate ideal 30', {facingMode: 'user', width: {ideal: 960}, height: {ideal: 540}, frameRate: {ideal: 30}}],
  ['640x480 ideal + frameRate ideal 30', {facingMode: 'user', width: {ideal: 640}, height: {ideal: 480}, frameRate: {ideal: 30}}],
  ['app constraints + manual exposure ≤ 1/30 s (if the camera exposes it)', {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}, lockExposure: true}],
];
const browser = await chromium.launch({headless: !args.includes('--headed'), channel: 'chromium', args: ['--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required']});
const page = await browser.newPage();
// mediaDevices needs a secure context; the loopback preview page qualifies and opens nothing by itself.
await page.goto(option('base', 'http://127.0.0.1:8241') + '/', {waitUntil: 'load'});
const devices = await page.evaluate(async () => (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput').map(d => d.label || d.deviceId.slice(0, 8)));
console.log(`video inputs: ${devices.length ? devices.join(' | ') : 'none visible'}`);
for (const [label, video] of CONSTRAINTS) {
  const result = await page.evaluate(async ({video, seconds}) => {
    let stream;
    const {lockExposure: _lock, ...constraints} = video;
    try {stream = await navigator.mediaDevices.getUserMedia({video: constraints, audio: false});}
    catch (error) {return {error: `${error.name}: ${error.message}`};}
    const track = stream.getVideoTracks()[0];
    const element = document.createElement('video'); element.muted = true; element.playsInline = true; element.srcObject = stream;
    await element.play();
    const capabilities = track.getCapabilities ? track.getCapabilities() : {};
    // Optional: lock the exposure to at most 1/30 s (exposureTime is in units of 100 µs) when the camera exposes it.
    let exposure = null;
    if (video.lockExposure && Array.isArray(capabilities.exposureMode) && capabilities.exposureMode.includes('manual') && capabilities.exposureTime) {
      const target = Math.max(capabilities.exposureTime.min, Math.min(333, capabilities.exposureTime.max));
      try {await track.applyConstraints({advanced: [{exposureMode: 'manual', exposureTime: target}]}); exposure = {applied: target, mode: track.getSettings().exposureMode, time: track.getSettings().exposureTime};}
      catch (error) {exposure = {error: `${error.name}: ${error.message}`};}
    } else if (video.lockExposure) exposure = {unavailable: true, exposureMode: capabilities.exposureMode ?? null, exposureTime: capabilities.exposureTime ?? null};
    const settings = track.getSettings();
    // Skip the first second (exposure settles), then count presented frames and media-time steps.
    await new Promise(resolve => setTimeout(resolve, 1000));
    const steps = []; let first = null, last = null, callbacks = 0;
    const done = new Promise(resolve => {
      const started = performance.now();
      const tick = (_now, metadata) => {
        callbacks++;
        if (first === null) first = metadata; last = metadata;
        if (steps.length < 2000 && last !== metadata) steps.push(0);
        if (performance.now() - started >= seconds * 1000) {resolve(); return;}
        element.requestVideoFrameCallback(tick);
      };
      element.requestVideoFrameCallback(tick);
    });
    await done;
    const elapsedS = (last.presentationTime - first.presentationTime) / 1000;
    const framesByCounter = last.presentedFrames - first.presentedFrames;
    const mediaS = last.mediaTime - first.mediaTime;
    for (const t of stream.getTracks()) t.stop();
    return {settings: {width: settings.width, height: settings.height, frameRate: settings.frameRate}, capabilityFrameRate: capabilities.frameRate ?? null,
      capabilityWidth: capabilities.width ?? null, capabilityKeys: Object.keys(capabilities), exposure,
      deliveredFps: framesByCounter / elapsedS, mediaFps: framesByCounter / mediaS, callbacksPerSecond: callbacks / elapsedS, elapsedS};
  }, {video, seconds});
  if (result.error) {console.log(`${label}: ${result.error}`); continue;}
  console.log(`${label}: settings ${result.settings.width}x${result.settings.height} @ ${result.settings.frameRate} · delivered ${result.deliveredFps.toFixed(1)} fps (counter/wall), ${result.mediaFps.toFixed(1)} (counter/media) · rVFC ${result.callbacksPerSecond.toFixed(1)}/s · capability frameRate ${JSON.stringify(result.capabilityFrameRate)} width ${JSON.stringify(result.capabilityWidth)}${result.exposure ? ` · exposure ${JSON.stringify(result.exposure)}` : ''}`);
  if (label.startsWith('app (')) console.log(`  capability keys: ${result.capabilityKeys.join(', ')}`);
}
if (args.includes('--sweep')) {
  // Manual-exposure sweep: which exposure times does the driver honour, and what frame rate does each deliver?
  const sweep = await page.evaluate(async seconds => {
    const stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}}, audio: false});
    const track = stream.getVideoTracks()[0], capabilities = track.getCapabilities();
    const element = document.createElement('video'); element.muted = true; element.playsInline = true; element.srcObject = stream; await element.play();
    const measure = async () => {
      await new Promise(resolve => setTimeout(resolve, 800));
      let first = null, last = null;
      await new Promise(resolve => {const started = performance.now(); const tick = (_now, metadata) => {first ??= metadata; last = metadata; if (performance.now() - started >= seconds * 1000) resolve(); else element.requestVideoFrameCallback(tick);}; element.requestVideoFrameCallback(tick);});
      return (last.presentedFrames - first.presentedFrames) * 1000 / (last.presentationTime - first.presentationTime);
    };
    const out = {range: capabilities.exposureTime ?? null, modes: capabilities.exposureMode ?? null, auto: {fps: await measure(), settings: track.getSettings().exposureTime ?? null}, steps: []};
    if (!capabilities.exposureTime || !capabilities.exposureMode?.includes('manual')) {for (const t of stream.getTracks()) t.stop(); return out;}
    const candidates = [...new Set([capabilities.exposureTime.min, 100, 156, 200, 250, 312, 333, 400, 500, 625].map(v => Math.max(capabilities.exposureTime.min, Math.min(v, capabilities.exposureTime.max))))].sort((a, b) => a - b);
    for (const value of candidates) {
      try {await track.applyConstraints({advanced: [{exposureMode: 'manual'}]}); await track.applyConstraints({advanced: [{exposureTime: value}]});}
      catch (error) {out.steps.push({requested: value, error: `${error.name}: ${error.message}`}); continue;}
      const settings = track.getSettings();
      out.steps.push({requested: value, actual: settings.exposureTime ?? null, mode: settings.exposureMode ?? null, fps: await measure()});
    }
    try {await track.applyConstraints({advanced: [{exposureMode: 'continuous'}]});} catch {/* leave as is */}
    for (const t of stream.getTracks()) t.stop();
    return out;
  }, Math.max(2, Math.min(seconds, 3)));
  console.log(`exposure sweep: range ${JSON.stringify(sweep.range)} (units of 100 µs) modes ${JSON.stringify(sweep.modes)} · auto: ${sweep.auto.fps.toFixed(1)} fps at exposureTime ${sweep.auto.settings}`);
  for (const step of sweep.steps) console.log(`  requested ${step.requested} → ${step.error ?? `actual ${step.actual} (${step.mode}) · ${step.fps.toFixed(1)} fps`}`);
}
await browser.close();
