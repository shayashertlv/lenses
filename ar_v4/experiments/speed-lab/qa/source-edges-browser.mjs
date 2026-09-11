/** Browser-only pixel-pipeline conformance. The saved pose/category are deliberate
 * fixed test inputs on procedural colors, not detections of these patterns. */
import {createOwnedSourceFrame} from '../speed-options.ts';
import {PROFILES} from '../profiles.ts';
import {checkProtocol} from './protocol.mjs';

export function patternPixels(width, height, pattern, transparent = false) {
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1
    || !['A', 'B'].includes(pattern)) throw new Error('Invalid source-edge pattern.');
  const bytes = new Uint8ClampedArray(width * height * 4);
  const palette = [[0, 0, 0], [255, 255, 255], [255, 0, 0], [0, 255, 0],
    [0, 0, 255], [255, 0, 255], [0, 255, 255], [255, 255, 0]];
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    const rgb = pattern === 'A' ? [x & 255, y & 255, (x + y) & 255]
      : palette[((x >> 3) + (y >> 3) * 3) % palette.length];
    bytes.set(rgb, i); bytes[i + 3] = 255;
  }
  // A colored half-alpha pixel catches more than merely transparent black.
  // It is deliberately outside the face and is used only in fallback controls.
  if (transparent) bytes.set([200, 100, 40, 128], 0);
  return bytes;
}

export function diffPixels(a, b, width, include = () => true) {
  if (a.length !== b.length || a.length % 4 !== 0) throw new Error('Pixel dimensions differ.');
  let testedPixels = 0, changedPixels = 0, maxDelta = 0;
  const firstChanged = [];
  for (let offset = 0; offset < a.length; offset += 4) {
    const index = offset / 4, x = index % width, y = Math.floor(index / width);
    if (!include(x, y, offset)) continue;
    testedPixels++;
    let delta = 0;
    for (let c = 0; c < 4; c++) delta = Math.max(delta, Math.abs(a[offset + c] - b[offset + c]));
    if (delta) {
      changedPixels++;
      if (firstChanged.length < 8) firstChanged.push({x, y,
        actual: Array.from(a.subarray(offset, offset + 4)), expected: Array.from(b.subarray(offset, offset + 4))});
    }
    maxDelta = Math.max(maxDelta, delta);
  }
  return {testedPixels, changedPixels, maxDelta, firstChanged};
}

const check = (value, message) => {if (!value) throw new Error(message);};
const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
  byte => byte.toString(16).padStart(2, '0')).join('');
const canonical = value => ArrayBuffer.isView(value) ? Array.from(value) : Array.isArray(value) ? value.map(canonical)
  : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const inside = (rect, x, y) => Boolean(rect) && x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;
const pixels = canvas => canvas.getContext('2d', {willReadFrequently: true}).getImageData(0, 0, canvas.width, canvas.height);
const geometryFields = ['eyewearModelId', 'rawMatrix', 'correctedMatrix', 'eyewearMatrix', 'surfacePositions',
  'yawDegrees', 'occlusion', 'templeClip', 'templeVisibility', 'rearDrop', 'protection'];
const guardFields = ['backgroundReferenceCheck', 'protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'];

export async function createSourceEdgeStudy(input) {
  const classes = {
    current: (await import(/* @vite-ignore */ input.currentModule)).LiveHairRenderer,
    candidate: (await import(/* @vite-ignore */ input.candidateModule)).LiveHairRenderer,
  };
  const fetchBytes = async artifact => {
    const response = await fetch(artifact.url); check(response.ok, 'Frozen fixture request failed.');
    const bytes = new Uint8Array(await response.arrayBuffer());
    check(await hash(bytes) === artifact.sha256 && bytes.length === artifact.bytes, 'Frozen fixture bytes differ.');
    return bytes;
  };
  const saved = JSON.parse(new TextDecoder().decode(await fetchBytes(input.detectionFile))), detection = saved.detection;
  check(detection?.matrix && detection.landmarks?.length === 478, 'Preserved pose is missing.');
  const detectionText = JSON.stringify(detection);
  check(await hash(new TextEncoder().encode(detectionText)) === input.detectionSHA256, 'Saved detection identity differs.');
  const category = await fetchBytes(input.mask.categoryU8);
  check(category.length === input.mask.width * input.mask.height, 'Saved category dimensions differ.');
  const sessions = {}, first = new Map();
  let eyewear = null, sequence = 0;
  const dispose = () => {
    for (const [name, session] of Object.entries(sessions)) {
      if (session.lease) session.lease.current = false;
      session.renderer.dispose(); session.abort.abort(); session.display.width = session.display.height = 0;
      delete sessions[name];
    }
    first.clear();
  };
  const makeSource = async spec => {
    const canvas = document.createElement('canvas'); canvas.width = spec.width; canvas.height = spec.height;
    const ctx = canvas.getContext('2d', {alpha: Boolean(spec.transparent), colorSpace: 'srgb', willReadFrequently: true});
    check(ctx, 'sRGB source canvas unavailable.');
    const procedural = patternPixels(spec.width, spec.height, spec.pattern, spec.transparent);
    ctx.putImageData(new ImageData(procedural, spec.width, spec.height, {colorSpace: 'srgb'}), 0, 0);
    // Hash the actual canvas decode, including premultiplication rounding on the
    // alpha control. Both renderers and the source lease receive these bytes.
    const rgba = pixels(canvas), sourceSHA256 = await hash(rgba.data);
    const pair = {sourceSHA256, detectionSHA256: input.detectionSHA256, eyewearModel: eyewear};
    const mask = {...input.mask, sourceSHA256, detectionSHA256: input.detectionSHA256, category,
      categorySHA256: input.mask.categoryU8.sha256, outputMode: 'category-only'};
    return {canvas, pair, mask, transparent: Boolean(spec.transparent), raw: rgba.data.slice(), proceduralSHA256: await hash(procedural)};
  };
  const owned = (source, options) => {
    const session = sessions.candidate;
    if (session.lease) session.lease.current = false;
    const lease = {current: true}; session.lease = lease;
    return {options, source: createOwnedSourceFrame(source.canvas, pixels(source.canvas), {
      sourceSHA256: source.pair.sourceSHA256, generation: ++session.generation, sessionId: session.id,
      isCurrent: () => lease.current && !session.abort.signal.aborted,
    })};
  };
  const capture = async (name, source) => {
    const {renderer, display} = sessions[name];
    renderer.selectVariant('accepted'); const before = pixels(display).data.slice(), beforePng = display.toDataURL();
    renderer.selectVariant('hair'); const after = pixels(display).data.slice(), afterPng = display.toDataURL();
    const stats = structuredClone(renderer.stats), rawGeometry = renderer.captureSnapshot;
    const diagnostic = renderer.exportDiagnostic();
    check(rawGeometry && diagnostic?.cleanBackgroundPngDataUrl, `${name}: paired export unavailable: ${JSON.stringify(stats)}`);
    const geometry = canonical(rawGeometry), image = new Image(); image.src = diagnostic.cleanBackgroundPngDataUrl; await image.decode();
    const backgroundCanvas = document.createElement('canvas'); backgroundCanvas.width = image.naturalWidth; backgroundCanvas.height = image.naturalHeight;
    backgroundCanvas.getContext('2d', {alpha: false, colorSpace: 'srgb', willReadFrequently: true}).drawImage(image, 0, 0);
    const background = pixels(backgroundCanvas).data.slice(); backgroundCanvas.width = backgroundCanvas.height = 0;
    const protection = geometry.protection, w = display.width, nose = diagnostic.noseRoi;
    const checks = {
      protected: diffPixels(before, after, w, (x, y) => protection.protectedRects.some(rect => inside(rect, x, y))),
      nose: diffPixels(before, after, w, (x, y) => inside(nose, x, y)),
      outsideEditable: diffPixels(before, after, w, (x, y) => !protection.editableRects.some(rect => inside(rect, x, y))),
      background: diffPixels(before, after, w, (_x, _y, i) => [0, 1, 2, 3].every(c => before[i + c] === background[i + c])),
    };
    const identities = {pair: equal(diagnostic.pair, source.pair), detection: equal(diagnostic.detection, detection),
      pose: equal(geometry.rawMatrix, detection.matrix), noseAvailable: Boolean(nose),
      // Unsupported alpha uses Test2's retained opaque source-over copy. Its
      // intermediate source is compared across implementations below.
      source: source.transparent || diagnostic.sourcePngDataUrl === source.canvas.toDataURL(),
      mask: Boolean(diagnostic.mask) && await hash(Uint8Array.from(atob(diagnostic.mask.categoryBase64), c => c.charCodeAt(0))) === input.mask.categoryU8.sha256};
    return {before, after, background, geometry, stats, checks, identities, sourceIntermediatePng: diagnostic.sourcePngDataUrl,
      hashes: {accepted: await hash(before), clean: await hash(background), final: await hash(after)},
      pngs: {[`${name}Accepted`]: beforePng, [`${name}Final`]: afterPng, [`${name}Clean`]: diagnostic.cleanBackgroundPngDataUrl}};
  };
  return {
    async setModel(id) {
      dispose(); eyewear = id;
      try {
        for (const [name, Renderer] of Object.entries(classes)) {
          const abort = new AbortController(), display = document.createElement('canvas');
          const renderer = await Renderer.create(display, abort.signal, id);
          sessions[name] = {renderer, display, abort, id: crypto.randomUUID(), generation: 0, lease: null};
        }
        const environments = {};
        // Read actual native contexts, without binding or reading a framebuffer.
        for (const [name, session] of Object.entries(sessions)) {
          const native = session.renderer.accepted.perfecto.renderer, gl = native.getContext();
          const debug = gl.getExtension('WEBGL_debug_renderer_info');
          environments[name] = {version: gl.getParameter(gl.VERSION), renderer: gl.getParameter(gl.RENDERER),
            unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null,
            context: gl.getContextAttributes(), drawingBufferColorSpace: gl.drawingBufferColorSpace,
            outputColorSpace: native.outputColorSpace};
        }
        return environments;
      } catch (error) {dispose(); throw error;}
    },
    async render(spec) {
      check(eyewear === spec.eyewear, 'Wrong source-edge session model.');
      const options = PROFILES[spec.profile].options, source = await makeSource(spec), rowSequence = ++sequence;
      try {
        for (const name of rowSequence % 2 ? ['current', 'candidate'] : ['candidate', 'current']) {
          const session = sessions[name]; session.renderer.selectVariant('hair');
          const visible = await session.renderer.present(source.canvas, structuredClone(detection), source.mask, source.pair,
            input.expectedModel, name === 'candidate' ? owned(source, options) : undefined);
          check(visible, `${name}: fixed fixture face did not render.`);
        }
        const current = await capture('current', source), candidate = await capture('candidate', source);
        const checks = {accepted: diffPixels(current.before, candidate.before, spec.width),
          clean: diffPixels(current.background, candidate.background, spec.width),
          final: diffPixels(current.after, candidate.after, spec.width)};
        if (!spec.transparent) checks.sourceIsNativeClean = diffPixels(current.background, source.raw, spec.width);
        const geometry = Object.fromEntries(geometryFields.map(field => [field, equal(current.geometry[field], candidate.geometry[field])]));
        const protocol = {current: checkProtocol('current', options, current.stats, current.geometry, spec.width, spec.height)};
        if (!spec.transparent) protocol.candidate = checkProtocol('candidate', options, candidate.stats, candidate.geometry, spec.width, spec.height);
        else {
          const metrics = candidate.stats.candidatePerformance, native = metrics.nativePipeline.native, speed = native.speedLab;
          const calls = options.asyncReadback ? speed.pbo?.retrievedCalls : native.sharedReadback?.readbackCalls;
          protocol.candidate = {sourceRejected: speed.reuseSourcePixelsRequested && !speed.reuseSourcePixelsUsed
              && /opaque/.test(speed.sourceReuseFallback ?? ''), nativeCleanReady: native.sharedCameraReady && native.sharedCameraFailure === null,
            twoNativeReads: calls === 2, async: speed.asyncReadbackUsed === options.asyncReadback && speed.asyncFallback === null,
            retrievalAccounting: metrics.cpuReadbackCalls === 2 + metrics.nativePipeline.branchReadbackCalls + metrics.nativePipeline.speedLab.prewarmReadbackCalls
              && metrics.cpuReadbackBytes === (2 + metrics.nativePipeline.branchReadbackCalls + metrics.nativePipeline.speedLab.prewarmReadbackCalls) * spec.width * spec.height * 4};
        }
        const key = `${eyewear}/${spec.profile}/${spec.width}x${spec.height}`;
        const returnChecks = {};
        if (spec.pattern === 'A' && !spec.transparent) {
          const prior = first.get(key);
          if (prior) for (const name of ['current', 'candidate']) returnChecks[name] = equal(prior[name], (name === 'current' ? current : candidate).hashes);
          else first.set(key, {current: current.hashes, candidate: candidate.hashes});
        }
        const inputs = {sourceUnchanged: await hash(pixels(source.canvas).data) === source.pair.sourceSHA256,
          sourceIntermediateMatched: current.sourceIntermediatePng === candidate.sourceIntermediatePng,
          detectionUnchanged: JSON.stringify(detection) === detectionText, categoryUnchanged: await hash(category) === input.mask.categoryU8.sha256};
        const passed = Object.values(checks).every(value => value.changedPixels === 0) && Object.values(geometry).every(Boolean)
          && Object.values(protocol).every(group => Object.values(group).every(Boolean)) && Object.values(returnChecks).every(Boolean)
          && Object.values(inputs).every(Boolean) && [current, candidate].every(capture => capture.stats.hasMask && capture.stats.fallbackReason === null
            && guardFields.every(field => capture.stats[field]?.changedPixels === 0)
            && Object.values(capture.checks).every(value => value.changedPixels === 0) && Object.values(capture.identities).every(Boolean));
        const summarize = capture => ({hashes: capture.hashes, geometry: capture.geometry, stats: capture.stats,
          checks: capture.checks, identities: capture.identities});
        return {...spec, sequence: rowSequence, passed, pair: source.pair, proceduralSHA256: source.proceduralSHA256,
          checks, geometry, protocol, returnChecks, inputs, current: summarize(current), candidate: summarize(candidate),
          pngs: {source: source.canvas.toDataURL(), ...current.pngs, ...candidate.pngs,
            ...(spec.transparent ? {currentSourceIntermediate: current.sourceIntermediatePng, candidateSourceIntermediate: candidate.sourceIntermediatePng} : {})}};
      } catch (error) {
        return {...spec, sequence: rowSequence, passed: false, error: error.stack ?? error.message,
          observed: Object.fromEntries(Object.entries(sessions).map(([name, session]) => [name,
            {stats: structuredClone(session.renderer.stats), geometry: canonical(session.renderer.captureSnapshot)}])),
          pngs: {source: source.canvas.toDataURL(), ...Object.fromEntries(Object.entries(sessions).map(([name, session]) => [`${name}FailureDisplay`, session.display.toDataURL()]))}};
      } finally {
        if (sessions.candidate.lease) sessions.candidate.lease.current = false;
        source.canvas.width = source.canvas.height = 0;
      }
    },
    async revokedLease(spec) {
      const source = await makeSource({...spec, pattern: 'B'}), session = sessions.candidate;
      const frameInput = owned(source, PROFILES.combined.options);
      let outcome = null;
      try {
        // prepare reaches its actual PBO wait synchronously before returning this
        // promise. Revoke immediately, with no GL mocking or readback injection.
        const pending = session.renderer.prepare(source.canvas, structuredClone(detection), source.pair, input.expectedModel, frameInput);
        const queuedPbo = structuredClone(session.renderer.accepted.perfecto.pbo?.metrics ?? null);
        session.lease.current = false;
        try {await pending; outcome = 'resolved';} catch (error) {outcome = error.name;}
        const displayPixels = pixels(session.display).data;
        const checks = {aborted: outcome === 'AbortError', actualPboQueued: queuedPbo?.queuedCalls === 1,
          publicPixelsCleared: displayPixels.every((value, index) => value === (index % 4 === 3 ? 255 : 0)),
          noStats: session.renderer.stats === null, noGeometry: session.renderer.captureSnapshot === null,
          noHeld: session.renderer.copyHeldInput() === null, noExport: session.renderer.exportDiagnostic() === null};
        return {eyewear, width: spec.width, height: spec.height, outcome, queuedPbo, checks, passed: Object.values(checks).every(Boolean)};
      } finally {source.canvas.width = source.canvas.height = 0;}
    },
    dispose,
  };
}
