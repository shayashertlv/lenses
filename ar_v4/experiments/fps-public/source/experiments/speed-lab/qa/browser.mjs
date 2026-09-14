/** Browser-only matched QA. Imports the two actual implementations; never detects faces or hair. */
import {createOwnedSourceFrame} from '../speed-options.ts';
import {PROFILES} from '../profiles.ts';
import {checkProtocol, mechanismUse, noAuxiliaryCamera} from './protocol.mjs';
export async function createMatchedStudy({currentModule, candidateModule, generated, profile, allowAsyncFallback = false, renderer}) {
  const options = PROFILES[profile].options;
  const protocolPolicy = {allowAsyncFallback, generated, renderer};
  const classes = {
    current: (await import(/* @vite-ignore */ currentModule)).LiveHairRenderer,
    candidate: (await import(/* @vite-ignore */ candidateModule)).LiveHairRenderer,
  };
  const check = (value, message) => { if (!value) throw new Error(message); };
  const hash = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
  const canonical = value => ArrayBuffer.isView(value) ? Array.from(value) : Array.isArray(value) ? value.map(canonical)
    : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
  const pixels = canvas => canvas.getContext('2d', {willReadFrequently: true}).getImageData(0, 0, canvas.width, canvas.height).data;
  const read = async item => {
    const response = await fetch(item.url); check(response.ok, 'Frozen artifact request failed.');
    const bytes = new Uint8Array(await response.arrayBuffer());
    check(await hash(bytes) === item.sha256 && (item.bytes === undefined || bytes.length === item.bytes), 'Frozen artifact receipt differs.');
    return bytes;
  };
  const decodeURL = async url => {
    const image = new Image(); image.src = url; await image.decode(); const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    // Recorded sources must retain their original default-canvas/SwiftShader decode.
    canvas.getContext('2d', generated ? {alpha: false, willReadFrequently: true, colorSpace: 'srgb'} : undefined).drawImage(image, 0, 0);
    return canvas;
  };
  const decode = async item => {
    const url = URL.createObjectURL(new Blob([await read(item)], {type: item.mimeType ?? 'image/png'}));
    try { return await decodeURL(url); } finally { URL.revokeObjectURL(url); }
  };
  const diff = (a, b, width, include = () => true) => {
    check(a.length === b.length, 'Pixel dimensions differ.'); let testedPixels = 0, changedPixels = 0, maxDelta = 0;
    for (let offset = 0; offset < a.length; offset += 4) {
      const index = offset / 4; if (!include(index % width, Math.floor(index / width), offset)) continue; testedPixels++;
      let delta = 0; for (let c = 0; c < 4; c++) delta = Math.max(delta, Math.abs(a[offset + c] - b[offset + c]));
      if (delta) changedPixels++; maxDelta = Math.max(maxDelta, delta);
    }
    return {testedPixels, changedPixels, maxDelta};
  };
  const inside = (rect, x, y) => x >= rect.x0 && x < rect.x1 && y >= rect.y0 && y < rect.y1;
  const sources = new Map(), masks = new Map(); let sessions = {}, eyewear;
  const disposeSessions = () => {
    for (const session of Object.values(sessions)) { if (session.lease) session.lease.current = false; session.renderer.dispose(); session.abort.abort(); session.display.width = session.display.height = 0; }
    sessions = {};
  };
  const request = input => {
    const source = sources.get(input.id), mask = masks.get(`${input.id}|${input.hairModel}`);
    check(source && mask && eyewear === input.eyewearModel, 'Matched input/session missing.');
    return {source, mask, pair: {sourceSHA256: source.sourceSHA256, detectionSHA256: source.detectionSHA256, eyewearModel: eyewear}};
  };
  const frameInput = (name, source, pair, canvas = source.canvas) => {
    if (name === 'current') return undefined;
    const session = sessions[name]; if (session.lease) session.lease.current = false;
    const lease = {current: true}; session.lease = lease;
    const rgba = canvas.getContext('2d', {willReadFrequently: true}).getImageData(0, 0, canvas.width, canvas.height);
    session.frameInput = {options, source: createOwnedSourceFrame(canvas, rgba, {
      sourceSHA256: pair.sourceSHA256, generation: ++session.generation, sessionId: session.id,
      isCurrent: () => lease.current && !session.abort.signal.aborted,
    })};
    return session.frameInput;
  };
  const presentOwned = async (name, source, detection, mask, pair, model) =>
    await sessions[name].renderer.present(source.canvas, detection, mask, pair, model, frameInput(name, source, pair));
  const prepareOwned = async (name, source, detection, pair, model) =>
    await sessions[name].renderer.prepare(source.canvas, detection, pair, model, frameInput(name, source, pair));
  const renderOne = async (name, input, pair, source, mask) => {
    const session = sessions[name], owned = frameInput(name, source, pair), started = performance.now();
    const visible = await session.renderer.present(source.canvas, structuredClone(source.detection), mask, pair, input.expectedModel, owned);
    const presentMs = performance.now() - started;
    return {visible, presentMs, timings: structuredClone(session.renderer.stats?.timings)};
  };  return {
    async addSource(input) {
      const canvas = await decode(input.source), rawSHA = await hash(pixels(canvas));
      check(rawSHA === input.decodedRgbaSHA256, `Exact source decode differs: ${input.id}: ${rawSHA}`);
      check(canvas.width === input.width && canvas.height === input.height, 'Source dimensions differ.');
      const saved = JSON.parse(new TextDecoder().decode(await read(input.detectionFile))), detection = saved.detection;
      check(detection?.matrix && detection.landmarks.length === 478, 'Saved face/pose missing.');
      const serialized = input.serializedDetection ? new TextDecoder().decode(await read(input.serializedDetection)) : null;
      if (serialized) check(serialized === JSON.stringify(detection), 'Saved generated detection serialization differs.');
      const expectedText = generated ? JSON.stringify(detection) : JSON.stringify(canonical(detection));
      check(await hash(new TextEncoder().encode(expectedText)) === input.detectionSHA256, 'Detection identity differs.');
      sources.set(input.id, {canvas, detection, detectionText: JSON.stringify(detection), rawSHA,
        sourceSHA256: input.sourceSHA256, detectionSHA256: input.detectionSHA256});
      return {id: input.id, width: canvas.width, height: canvas.height, sourceSHA256: input.sourceSHA256, decodedRgbaSHA256: rawSHA, detectionSHA256: input.detectionSHA256};
    },
    async addMask(input) {
      const category = await read(input.categoryU8);
      check(category.length === input.width * input.height && await hash(category) === input.categorySHA256, 'Category dimensions/hash differ.');
      masks.set(`${input.id}|${input.model}`, {...input, category, outputMode: 'category-only'});
    },
    async setModel(id) {
      disposeSessions(); eyewear = id;
      try {
        for (const [name, Renderer] of Object.entries(classes)) {
          const abort = new AbortController(), display = document.createElement('canvas');
          try { sessions[name] = {abort, display, renderer: await Renderer.create(display, abort.signal, id), id: crypto.randomUUID(), generation: 0, lease: null, frameInput: null}; }
          catch (error) { abort.abort(); display.width = display.height = 0; throw error; }
        }
      } catch (error) { disposeSessions(); throw error; }
    },
    async render(input) {
      const {source, mask, pair} = request(input), samples = {current: [], candidate: []};
      const samplesPerRenderer = input.samples, iterations = input.warmup + Math.max(1, samplesPerRenderer);
      // Alternate ordering; the same frozen pair is used for both implementations.
      for (let iteration = 0; iteration < iterations; iteration++) {
        for (const name of iteration % 2 ? ['candidate', 'current'] : ['current', 'candidate']) {
          sessions[name].renderer.selectVariant('hair');
          const sample = await renderOne(name, input, pair, source, mask);
          check(sample.visible, `${name}: expected face did not render.`);
          if (iteration >= input.warmup && samplesPerRenderer > 0) samples[name].push(sample);
        }
      }
      const expectedBefore = await decode(input.before), expectedAfter = await decode(input.after), expectedBackground = await decode(input.background);
      const captures = {}, checks = {}, geometryChecks = {}, diagnosticChecks = {}, pngs = {};
      const geometryFields = ['eyewearModelId', 'rawMatrix', 'correctedMatrix', 'eyewearMatrix', 'surfacePositions', 'yawDegrees', 'occlusion', 'templeClip', 'templeVisibility', 'rearDrop', 'protection'];
      for (const [name, {renderer, display}] of Object.entries(sessions)) {
        renderer.selectVariant('accepted'); const before = pixels(display).slice(); pngs[`${name}Before`] = display.toDataURL('image/png');
        renderer.selectVariant('hair'); const after = pixels(display).slice(); pngs[`${name}After`] = display.toDataURL('image/png');
        const diagnostic = renderer.exportDiagnostic(), rawGeometry = renderer.captureSnapshot;
        check(diagnostic?.cleanBackgroundPngDataUrl && rawGeometry, `${name}: paired diagnostics absent. ${JSON.stringify(renderer.stats)}`);
        const geometry = {...rawGeometry, surfacePositions: Array.from(rawGeometry.surfacePositions)};
        const backgroundCanvas = await decodeURL(diagnostic.cleanBackgroundPngDataUrl), background = pixels(backgroundCanvas).slice();
        const protection = geometry.protection;
        checks[`${name}FrozenAccepted`] = diff(before, pixels(expectedBefore), display.width);
        checks[`${name}FrozenHair`] = diff(after, pixels(expectedAfter), display.width);
        checks[`${name}FrozenBackground`] = diff(background, pixels(expectedBackground), display.width);
        checks[`${name}Protected`] = diff(before, after, display.width, (x, y) => protection.protectedRects.some(rect => inside(rect, x, y)));
        checks[`${name}Nose`] = diff(before, after, display.width, (x, y) => inside(input.noseRoi, x, y));
        checks[`${name}OutsideEditable`] = diff(before, after, display.width, (x, y) => !protection.editableRects.some(rect => inside(rect, x, y)));
        checks[`${name}BackgroundPreservation`] = diff(before, after, display.width, (_x, _y, i) => [0, 1, 2, 3].every(c => before[i + c] === background[i + c]));
        for (const [index, historical] of (input.historicalNoseChecks ?? []).entries()) {
          const [x0, y0, x1, y1] = historical.roi;
          checks[`${name}HistoricalNose${index}`] = diff(before, after, display.width, (x, y) => inside({x0, y0, x1, y1}, x, y));
        }
        renderer.selectVariant('accepted'); checks[`${name}Toggle`] = diff(before, pixels(display), display.width); renderer.selectVariant('hair');
        geometryChecks[name] = Object.fromEntries(geometryFields.map(field => [field, equal(geometry[field], input.expectedGeometry[field])]));
        diagnosticChecks[name] = {pair: equal(diagnostic.pair, pair), detection: equal(diagnostic.detection, source.detection),
          nose: equal(diagnostic.noseRoi, input.noseRoi), source: diagnostic.sourcePngDataUrl === source.canvas.toDataURL('image/png'),
          category: Boolean(diagnostic.mask) && await hash(Uint8Array.from(atob(diagnostic.mask.categoryBase64), c => c.charCodeAt(0))) === mask.categorySHA256,
          geometryRawPose: equal(geometry.rawMatrix, source.detection.matrix)};
        const stats = renderer.stats;
        diagnosticChecks[name].pairedMaskApplied = stats?.hasMask === true && stats.maskOutputMode === 'category-only' && !stats.fallbackReason;
        Object.assign(diagnosticChecks[name], checkProtocol(name, options, stats, geometry, display.width, display.height, protocolPolicy));
        for (const field of ['backgroundReferenceCheck', 'protectedCheck', 'noseCheck', 'outsideEditableCheck', 'backgroundPreservationCheck'])
          diagnosticChecks[name][field] = stats[field]?.changedPixels === 0;
        captures[name] = {before, after, background, geometry, stats, nativeSamples: renderer.nativeSamples};
        backgroundCanvas.width = backgroundCanvas.height = 0;
      }
      const {current, candidate} = captures;
      checks.matchedAccepted = diff(current.before, candidate.before, source.canvas.width);
      checks.matchedHair = diff(current.after, candidate.after, source.canvas.width);
      checks.matchedBackground = diff(current.background, candidate.background, source.canvas.width);
      geometryChecks.matched = Object.fromEntries(geometryFields.map(field => [field, equal(current.geometry[field], candidate.geometry[field])]));
      diagnosticChecks.original = {sourceUnchanged: await hash(pixels(source.canvas)) === source.rawSHA, detectionUnchanged: JSON.stringify(source.detection) === source.detectionText,
        categoryUnchanged: await hash(mask.category) === mask.categorySHA256};
      const passed = Object.values(checks).every(value => value.changedPixels === 0)
        && Object.values(geometryChecks).every(group => Object.values(group).every(Boolean))
        && Object.values(diagnosticChecks).every(group => Object.values(group).every(Boolean));
      for (const canvas of [expectedBefore, expectedAfter, expectedBackground]) canvas.width = canvas.height = 0;
      return {id: input.id, eyewearModel: eyewear, hairModel: input.hairModel, pair, width: source.canvas.width, height: source.canvas.height,
        passed, profile, options, checks, geometryChecks, diagnosticChecks, samples, pngs,
        mechanism: mechanismUse(options, candidate.stats, candidate.geometry, source.canvas.width, source.canvas.height, protocolPolicy),
        current: {geometry: current.geometry, stats: current.stats, nativeSamples: current.nativeSamples,
          beforeRgbaSHA256: await hash(current.before), afterRgbaSHA256: await hash(current.after)},
        candidate: {geometry: candidate.geometry, stats: candidate.stats, nativeSamples: candidate.nativeSamples,
          beforeRgbaSHA256: await hash(candidate.before), afterRgbaSHA256: await hash(candidate.after)}};
    },
    async controls(input) {
      const {source, mask, pair} = request(input), result = {};
      for (const [name, {renderer, display}] of Object.entries(sessions)) {
        renderer.selectVariant('hair'); await renderOne(name, input, pair, source, mask); const good = pixels(display).slice();
        renderer.selectVariant('accepted'); const accepted = pixels(display).slice(); renderer.selectVariant('hair');
        renderer.selectVariant('accepted'); await presentOwned(name, source, source.detection, null, pair, input.expectedModel);
        const hairOff = diff(accepted, pixels(display), display.width), hairOffNative = structuredClone(renderer.stats.candidatePerformance?.nativePipeline?.native ?? null);
        const hairOffNoAtlas = noAuxiliaryCamera(hairOffNative);
        await presentOwned(name, source, source.detection, mask, pair, input.expectedModel);
        const acceptedFirst = diff(accepted, pixels(display), display.width), acceptedFirstHasMask = renderer.stats.hasMask;
        renderer.selectVariant('hair'); const acceptedFirstHairToggle = diff(good, pixels(display), display.width);
        const invalidMask = {...mask, sourceSHA256: 'f'.repeat(64)};
        await presentOwned(name, source, source.detection, invalidMask, pair, input.expectedModel);
        const invalid = diff(accepted, pixels(display), display.width), invalidRejected = !renderer.stats.hasMask && Boolean(renderer.stats.fallbackReason);
        const noFace = {landmarks: [], matrix: null, inferenceMs: 0};
        const noFacePair = {...pair, detectionSHA256: await hash(new TextEncoder().encode(JSON.stringify(noFace)))};
        const noFaceVisible = await presentOwned(name, source, noFace, null, noFacePair, input.expectedModel);
        const noFaceCleared = !noFaceVisible && renderer.captureSnapshot === null && renderer.exportDiagnostic() === null && !renderer.stats.hasMask;
        const noFaceNative = structuredClone(renderer.stats.candidatePerformance?.nativePipeline?.native ?? null);
        const noFaceNoAtlas = noAuxiliaryCamera(noFaceNative);
        await renderOne(name, input, pair, source, mask); const restart = diff(good, pixels(display), display.width);
        const beforePrepare = pixels(display).slice(); await prepareOwned(name, source, source.detection, pair, input.expectedModel);
        const preparePrivate = renderer.stats === null && renderer.captureSnapshot === null && renderer.copyHeldInput() === null && renderer.exportDiagnostic() === null
          && diff(beforePrepare, pixels(display), display.width).changedPixels === 0;
        let overlappingRejected = false; try { await renderer.prepare(source.canvas, source.detection, pair, input.expectedModel, sessions[name].frameInput); } catch { overlappingRejected = true; }
        renderer.finish(mask); const finish = diff(good, pixels(display), display.width);
        let doubleFinishRejected = false; try { renderer.finish(mask); } catch { doubleFinishRejected = true; }
        const featureChecks = {}, featureMetrics = {};
        if (name === 'candidate' && options.fewerCopies) {
          const borrowedCanvas = document.createElement('canvas'); borrowedCanvas.width = source.canvas.width; borrowedCanvas.height = source.canvas.height;
          borrowedCanvas.getContext('2d', {alpha: false, willReadFrequently: true, colorSpace: 'srgb'}).drawImage(source.canvas, 0, 0);
          const owned = frameInput(name, source, pair, borrowedCanvas);
          await renderer.present(borrowedCanvas, source.detection, mask, pair, input.expectedModel, owned);
          featureMetrics.borrowedBeforeRelease = structuredClone(renderer.stats.candidatePerformance.speedLab);
          sessions[name].lease.current = false; borrowedCanvas.width = borrowedCanvas.height = 0;
          const held = renderer.copyHeldInput(), exported = renderer.exportDiagnostic();
          featureChecks.heldAfterLeaseRevocation = Boolean(held) && await hash(pixels(held.source)) === source.rawSHA;
          featureChecks.exportAfterLeaseRevocation = Boolean(exported)
            && equal(exported.pair, pair) && exported.hairPngDataUrl === display.toDataURL('image/png');
          if (held) {held.source.width = held.source.height = 0;}
          featureChecks.heldCopyIndependent = renderer.exportDiagnostic()?.sourcePngDataUrl === exported?.sourcePngDataUrl;
        }
        if (name === 'candidate' && (options.reuseSourcePixels || options.fewerCopies)) {
          if (sessions[name].lease) sessions[name].lease.current = false;
          await renderer.present(source.canvas, source.detection, mask, pair, input.expectedModel, {options});
          featureMetrics.missingSource = structuredClone(renderer.stats.candidatePerformance);
          featureChecks.missingSourceKeepsExactHair = diff(good, pixels(display), display.width).changedPixels === 0 && renderer.stats.hasMask;
          featureChecks.missingSourceExplicit = Boolean(featureMetrics.missingSource.speedLab.sourceFallbackReason);
          if (options.reuseSourcePixels) featureChecks.nativeSourceFallbackExplicit =
            featureMetrics.missingSource.nativePipeline.native.speedLab.reuseSourcePixelsUsed === false
            && Boolean(featureMetrics.missingSource.nativePipeline.native.speedLab.sourceReuseFallback);
        }
        result[name] = {hairOff, hairOffNative, hairOffNoAtlas, acceptedFirst, acceptedFirstHasMask, acceptedFirstHairToggle,
          invalid, invalidRejected, noFaceCleared, noFaceNative, noFaceNoAtlas,
          restart, preparePrivate, overlappingRejected, finish, doubleFinishRejected, featureChecks, featureMetrics};
      }
      result.passed = ['current', 'candidate'].every(name => {
        const row = result[name]; return row.hairOff.changedPixels === 0 && row.hairOffNoAtlas && row.noFaceNoAtlas
          && row.acceptedFirst.changedPixels === 0 && row.acceptedFirstHasMask && row.acceptedFirstHairToggle.changedPixels === 0
          && row.invalid.changedPixels === 0 && row.invalidRejected && row.noFaceCleared && row.restart.changedPixels === 0
          && row.preparePrivate && row.overlappingRejected && row.finish.changedPixels === 0 && row.doubleFinishRejected
          && Object.values(row.featureChecks).every(Boolean);
      });
      return result;
    },
    async repeatDiagnostic(input) {
      const {source, mask, pair} = request(input), expectedBeforeCanvas = await decode(input.before), expectedAfterCanvas = await decode(input.after);
      const expectedBefore = pixels(expectedBeforeCanvas).slice(), expectedAfter = pixels(expectedAfterCanvas).slice();
      const previous = {}, first = {}, samples = [], pngs = {};
      const details = (a, b) => {
        const difference = diff(a, b, source.canvas.width), coordinates = [];
        for (let i = 0; i < a.length && coordinates.length < 12; i += 4) if ([0, 1, 2, 3].some(c => a[i + c] !== b[i + c])) {
          const index = i / 4; coordinates.push({x: index % source.canvas.width, y: Math.floor(index / source.canvas.width),
            actual: Array.from(a.subarray(i, i + 4)), expected: Array.from(b.subarray(i, i + 4))});
        }
        return {...difference, firstChangedCoordinates: coordinates};
      };
      for (let iteration = 0; iteration < input.repetitions; iteration++) {
        const row = {iteration, temperature: iteration === 0 ? 'first presentation after creation' : 'subsequent presentation', implementations: {}}, captures = {};
        for (const name of iteration % 2 ? ['candidate', 'current'] : ['current', 'candidate']) {
          const {renderer, display} = sessions[name]; renderer.selectVariant('hair');
          const timing = await renderOne(name, input, pair, source, mask); check(timing.visible, 'Expected diagnostic face was not presented.');
          renderer.selectVariant('accepted'); const before = pixels(display).slice();
          renderer.selectVariant('hair'); const after = pixels(display).slice();
          const frozenAccepted = details(before, expectedBefore), frozenHair = details(after, expectedAfter);
          row.implementations[name] = {...timing, beforeRgbaSHA256: await hash(before), afterRgbaSHA256: await hash(after),
            frozenAccepted, frozenHair,
            previousAccepted: previous[name] ? details(before, previous[name].before) : null,
            previousHair: previous[name] ? details(after, previous[name].after) : null,
            firstAccepted: first[name] ? details(before, first[name].before) : null,
            firstHair: first[name] ? details(after, first[name].after) : null,
            stats: structuredClone(renderer.stats), nativeSamples: renderer.nativeSamples};
          if (iteration === 0 || iteration === input.repetitions - 1 || frozenAccepted.changedPixels || frozenHair.changedPixels) {
            renderer.selectVariant('accepted'); pngs[`${iteration}-${name}-before`] = display.toDataURL('image/png');
            renderer.selectVariant('hair'); pngs[`${iteration}-${name}-after`] = display.toDataURL('image/png');
          }
          captures[name] = {before, after}; previous[name] = captures[name]; first[name] ??= captures[name];
        }
        row.matchedAccepted = details(captures.current.before, captures.candidate.before);
        row.matchedHair = details(captures.current.after, captures.candidate.after); samples.push(row);
      }
      expectedBeforeCanvas.width = expectedBeforeCanvas.height = expectedAfterCanvas.width = expectedAfterCanvas.height = 0;
      return {id: input.id, eyewearModel: eyewear, hairModel: input.hairModel, pair, repetitions: input.repetitions, samples, pngs,
        sourceUnchanged: await hash(pixels(source.canvas)) === source.rawSHA, detectionUnchanged: JSON.stringify(source.detection) === source.detectionText,
        categoryUnchanged: await hash(mask.category) === mask.categorySHA256};
    },
    async cancellation(input) {
      const {source, mask, pair} = request(input), result = {};
      for (const [name, session] of Object.entries(sessions)) {
        const {renderer, abort, display} = session;
        const preparing = prepareOwned(name, source, source.detection, pair, input.expectedModel);
        abort.abort(); if (session.lease) session.lease.current = false; renderer.dispose();
        let pendingPrepareSettled = false;
        try { await preparing; pendingPrepareSettled = true; }
        catch (error) { pendingPrepareSettled = error.name === 'AbortError'; }
        const closed = renderer.finish(mask) === false && renderer.stats === null && renderer.captureSnapshot === null && renderer.copyHeldInput() === null;
        const preAborted = new AbortController(); preAborted.abort(); let rejected = false;
        try { const unexpected = await classes[name].create(document.createElement('canvas'), preAborted.signal, eyewear); unexpected.dispose(); }
        catch (error) { rejected = error.name === 'AbortError'; }
        result[name] = {closed, pendingPrepareSettled, preAbortedRejected: rejected, canvasWidthAfterDisposal: display.width};
      }
      result.passed = ['current', 'candidate'].every(name => result[name].closed && result[name].pendingPrepareSettled && result[name].preAbortedRejected); return result;
    },
    dispose() { disposeSessions(); for (const source of sources.values()) source.canvas.width = source.canvas.height = 0; sources.clear(); masks.clear(); },
  };
}
