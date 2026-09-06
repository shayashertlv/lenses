import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import type { Detection, DetectorRequest, DetectorResponse } from '../../src/runtime/protocol.ts';

interface CameraHarness {
  streams: MediaStream[];
  workers: { terminated: boolean; detections: number }[];
  results: { capturedAt: number; detection: Detection; marker: number[] }[];
  marker: 'red' | 'blue';
  blank: boolean;
  holdNextResult: boolean;
  delegates: string[];
  held: (() => void)[];
  releaseHeld(): void;
}

interface ExportedFrame {
  index: number;
  capturedAt: number;
  relativeMs: number;
  width: number;
  height: number;
  jpegDataUrl: string;
  detection: Detection;
  metadata: { surfacePositions: number[]; [key: string]: unknown } | null;
  yawDegrees: number | null;
}

/** Synthetic camera pixels with the actual local MediaPipe worker and WebGL renderer. */
async function installCamera(page: Page, permissionDelayMs = 0, failGpu = false): Promise<void> {
  const portrait = await readFile(new URL('../fixtures/face-a.jpg', import.meta.url));
  await page.route('**/test-fixtures/camera-face.jpg', route =>
    route.fulfill({ contentType: 'image/jpeg', body: portrait }));
  await page.addInitScript(({ permissionDelayMs, failGpu }) => {
    const fixture = document.createElement('canvas');
    fixture.width = 960;
    fixture.height = 720;
    const context = fixture.getContext('2d')!;
    const portrait = new Image();
    const ready = new Promise<void>((resolve, reject) => {
      portrait.onload = () => resolve();
      portrait.onerror = reject;
    });
    portrait.src = '/test-fixtures/camera-face.jpg';
    const state: CameraHarness = {
      streams: [], workers: [], results: [], marker: 'red', blank: false,
      holdNextResult: false, delegates: [], held: [], releaseHeld: () => {},
    };
    state.releaseHeld = () => {
      for (const deliver of state.held.splice(0)) deliver();
    };
    Object.assign(window, { cameraHarness: state });
    const frameCallback = HTMLVideoElement.prototype.requestVideoFrameCallback;
    HTMLVideoElement.prototype.requestVideoFrameCallback = function (callback) {
      return frameCallback.call(this, (now, metadata) => callback(now, { ...metadata, mediaTime: 0 }));
    };
    setInterval(() => {
      context.fillStyle = '#778878';
      context.fillRect(0, 0, 960, 720);
      if (!state.blank && portrait.complete && portrait.naturalWidth)
        context.drawImage(portrait, 120, 0, 720, 720);
      // A changing patch outside the face lets the export test independently
      // match compressed camera pixels to the exact worker submission.
      context.fillStyle = state.marker === 'red' ? '#f02020' : '#2030f0';
      context.fillRect(0, 0, 80, 80);
    }, 33);
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async () => {
        await ready;
        await new Promise(resolve => setTimeout(resolve, permissionDelayMs));
        const stream = fixture.captureStream(30);
        state.streams.push(stream);
        return stream;
      },
    });

    const NativeWorker = window.Worker;
    class ObservedWorker extends NativeWorker {
      private readonly index: number;
      private readonly requests = new Map<number, { capturedAt: number; marker: number[] }>();
      private readonly sample = document.createElement('canvas');

      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        this.index = state.workers.length;
        state.workers.push({ terminated: false, detections: 0 });
        this.sample.width = this.sample.height = 1;
        this.addEventListener('message', (event: MessageEvent<DetectorResponse>) => {
          if (event.data.type !== 'result') return;
          const request = this.requests.get(event.data.id);
          if (request) {
            state.results.push({ ...request, detection: structuredClone(event.data.detection) });
            this.requests.delete(event.data.id);
          }
          if (state.holdNextResult) {
            state.holdNextResult = false;
            event.stopImmediatePropagation();
            // Retain an already queued callback even after terminate(), just
            // as the existing lifecycle regression does for late deliveries.
            const callback = this.onmessage;
            state.held.push(() => callback?.call(this,
              new MessageEvent('message', { data: event.data })));
          }
        });
      }

      override postMessage(message: unknown, transferOrOptions: Transferable[] | StructuredSerializeOptions = []): void {
        const request = message as DetectorRequest;
        if (request.type === 'initialize') {
          state.delegates.push(request.delegate);
          if (failGpu && this.index === 0 && request.delegate === 'GPU')
            message = { ...request, modelUrl: new URL('/test-fixtures/unavailable.task', location.href).href };
        }
        if (request.type === 'detect') {
          state.workers[this.index]!.detections++;
          const context = this.sample.getContext('2d', { willReadFrequently: true })!;
          context.drawImage(request.image, 10, 10, 1, 1, 0, 0, 1, 1);
          this.requests.set(request.id, {
            capturedAt: request.timestampMs,
            marker: Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3),
          });
        }
        if (Array.isArray(transferOrOptions)) super.postMessage(message, transferOrOptions);
        else super.postMessage(message, transferOrOptions);
      }

      override terminate(): void {
        state.workers[this.index]!.terminated = true;
        super.terminate();
      }
    }
    Object.defineProperty(window, 'Worker', { configurable: true, value: ObservedWorker });
  }, { permissionDelayMs, failGpu });
}

async function resources(page: Page): Promise<{ closed: boolean; workers: number; detections: number }> {
  return page.evaluate(() => {
    const harness = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    return {
      closed: harness.streams.length > 0
        && harness.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))
        && harness.workers.every(worker => worker.terminated),
      workers: harness.workers.length,
      detections: harness.workers.reduce((total, worker) => total + worker.detections, 0),
    };
  });
}

async function storedFrames(page: Page): Promise<number> {
  return Number(await page.locator('#capture').getAttribute('data-frames'));
}

async function startRecording(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await expect(page.locator('#record-turn')).toBeEnabled();
  await page.locator('#record-turn').click();
  await expect(page.locator('#capture')).toHaveAttribute('data-state', 'recording');
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(3);
}

async function finishRecording(page: Page): Promise<number> {
  await page.locator('#finish-recording').click();
  await expect(page.locator('#capture')).toHaveAttribute('data-state', 'replay');
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'replay');
  await expect(page.locator('#replay-controls')).toBeVisible();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  return storedFrames(page);
}



test('recorded image and detection pairs replay locally without restarting inference', async ({ page }) => {
  const errors: string[] = [];
  const origins = new Set<string>();
  const downloads: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.url().startsWith('http')) origins.add(new URL(request.url()).origin);
  });
  page.on('download', download => downloads.push(download.suggestedFilename()));
  await installCamera(page);
  await page.goto('/');
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#record-turn')).toBeDisabled();
  await startRecording(page);
  const redCount = await storedFrames(page);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.marker = 'blue';
  });
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(redCount + 3);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.blank = true;
  });
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'searching');
  const beforeBlank = await storedFrames(page);
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(beforeBlank + 2);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.blank = false;
  });
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  const afterBlank = await storedFrames(page);
  await expect.poll(() => storedFrames(page)).toBeGreaterThanOrEqual(afterBlank + 2);
  const count = await finishRecording(page);
  const stopped = await resources(page);
  expect(count).toBeGreaterThanOrEqual(6);
  expect(count).toBeLessThanOrEqual(96);
  await expect(page.locator('#replay-frame')).toHaveAttribute('min', '0');
  await expect(page.locator('#replay-frame')).toHaveAttribute('max', String(count - 1));
  await page.locator('#replay-frame').focus();
  await page.locator('#replay-frame').press('Home');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', '0');
  const firstImage = await page.locator('#mirror').screenshot();
  await page.locator('#replay-frame').press('End');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', String(count - 1));
  await page.locator('#replay-frame').press('ArrowRight');
  await expect(page.locator('#replay-frame')).toHaveValue(String(count - 1));
  const lastImage = await page.locator('#mirror').screenshot();
  expect(lastImage.equals(firstImage)).toBe(false);
  await writeFile(test.info().outputPath('replay-baseline.png'), lastImage);

  expect(await resources(page)).toEqual(stopped);
  expect(downloads).toEqual([]);
  await page.screenshot({ path: test.info().outputPath('desktop-replay.png'), fullPage: true });

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-capture').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^ar-v4-.+\.json$/);
  const capturePath = test.info().outputPath('synthetic-capture.json');
  await download.saveAs(capturePath);
  const artifact = JSON.parse(await readFile(capturePath, 'utf8')) as {
    schemaVersion: number; projectId: string;
    limits: { maxFrames: number; maxCompressedBytes: number; maxDurationMs: number };
    header: Record<string, unknown>;
    frames: ExportedFrame[];
  };
  expect(artifact.schemaVersion).toBe(1);
  expect(artifact.projectId).toBe('ar_v4');
  expect(artifact.frames).toHaveLength(count);
  expect(artifact.frames.length).toBeLessThanOrEqual(artifact.limits.maxFrames);
  expect(artifact.header).toEqual(expect.objectContaining({
    camera: expect.any(Object), projection: expect.any(Object),
    assets: expect.any(Object),
  }));
  const delivered = await page.evaluate(() =>
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.results);
  let compressedBytes = 0;
  for (const [index, frame] of artifact.frames.entries()) {
    expect(frame.index).toBe(index);
    expect(frame.relativeMs).toBeGreaterThanOrEqual(0);
    expect(frame.relativeMs).toBeLessThanOrEqual(artifact.limits.maxDurationMs);
    if (index > 0) expect(frame.capturedAt).toBeGreaterThan(artifact.frames[index - 1]!.capturedAt);
    expect(frame.width).toBeGreaterThan(0);
    expect(frame.width).toBeLessThanOrEqual(1280);
    expect(frame.width / frame.height).toBeCloseTo(4 / 3, 2);
    if (frame.metadata) {
      expect(frame.detection.landmarks).toHaveLength(478);
      expect(frame.detection.matrix).toHaveLength(16);
      for (const key of ['rawMatrix', 'correctedMatrix', 'eyewearMatrix']) {
        const matrix = frame.metadata[key];
        expect(matrix).toHaveLength(16);
        expect(Array.isArray(matrix) && matrix.every(Number.isFinite)).toBe(true);
      }
      expect(frame.metadata.rawMatrix).toEqual(frame.detection.matrix);
      expect(frame.metadata.surfacePositions).toHaveLength(1404);
      expect(Number.isFinite(frame.yawDegrees)).toBe(true);
    } else {
      expect(frame.detection.landmarks).toHaveLength(0);
      expect(frame.detection.matrix).toBeNull();
      expect(frame.yawDegrees).toBeNull();
    }
    expect(frame.jpegDataUrl).toMatch(/^data:image\/jpeg;base64,/);
    compressedBytes += Buffer.from(frame.jpegDataUrl.split(',')[1]!, 'base64').byteLength;
    const original = delivered.find(result => result.capturedAt === frame.capturedAt);
    expect(original).toBeDefined();
    expect(frame.detection).toEqual(original!.detection);
    const decoded = await page.evaluate(async url => {
      const image = new Image();
      image.src = url;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d')!;
      context.drawImage(image, 10, 10, 1, 1, 0, 0, 1, 1);
      return { width: image.naturalWidth, height: image.naturalHeight,
        marker: Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3) };
    }, frame.jpegDataUrl);
    expect(decoded.width).toBe(frame.width);
    expect(decoded.height).toBe(frame.height);
    for (let channel = 0; channel < 3; channel++)
      expect(Math.abs(decoded.marker[channel]! - original!.marker[channel]!)).toBeLessThanOrEqual(5);
  }
  expect(artifact.frames.filter(frame => frame.metadata !== null).length).toBeGreaterThanOrEqual(6);
  const blank = artifact.frames.find(frame => frame.metadata === null);
  expect(blank).toBeDefined();
  await page.locator('#replay-frame').focus();
  await page.locator('#replay-frame').press('Home');
  for (let step = 0; step < blank!.index; step++) await page.locator('#replay-frame').press('ArrowRight');
  await expect(page.locator('.stage')).toHaveAttribute('data-replay-frame', String(blank!.index));
  await expect(page.locator('#replay-metrics')).toContainText('estimated yaw unknown');
  const blankImage = await page.locator('#mirror').screenshot();
  const blankPixel = await page.evaluate(async url => {
    const image = new Image();
    image.src = url;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d')!;
    context.drawImage(image, image.naturalWidth / 2, image.naturalHeight / 2, 1, 1, 0, 0, 1, 1);
    return Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3);
  }, `data:image/png;base64,${blankImage.toString('base64')}`);
  for (const [channel, expected] of [0x77, 0x88, 0x78].entries())
    expect(Math.abs(blankPixel[channel]! - expected)).toBeLessThanOrEqual(5);
  expect(compressedBytes).toBeLessThanOrEqual(artifact.limits.maxCompressedBytes);
  expect(downloads).toHaveLength(1);
  expect(await resources(page)).toEqual(stopped);
  expect([...origins]).toEqual([new URL(page.url()).origin]);
  await page.locator('#discard-capture').click();
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});

test('capture cancellation rejects late detection and replay context loss permits restart', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await installCamera(page);
  await page.goto('/');
  await startRecording(page);
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.holdNextResult = true;
  });
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.held.length)).toBe(1);
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await page.evaluate(() => {
    (window as unknown as { cameraHarness: CameraHarness }).cameraHarness.releaseHeld();
  });
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await startRecording(page);
  await finishRecording(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('mobile-replay.png'), fullPage: true });
  const oldCanvas = await page.locator('#mirror').elementHandle();
  expect(await page.locator('#mirror').evaluate(canvas => {
    const context = (canvas as HTMLCanvasElement).getContext('webgl2');
    const extension = context?.getExtension('WEBGL_lose_context');
    extension?.loseContext();
    return Boolean(extension);
  })).toBe(true);
  await expect(page.locator('#guidance')).toContainText('graphics connection was interrupted');
  await expect(page.locator('#capture')).toHaveAttribute('data-frames', '0');
  await expect(page.locator('#replay-controls')).toBeHidden();
  await expect(page.locator('#mirror')).toBeHidden();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  expect(await oldCanvas!.evaluate(canvas => canvas.isConnected)).toBe(false);
  await oldCanvas!.dispose();
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(page.locator('#mirror')).toBeHidden();
  await page.evaluate(() => { Reflect.deleteProperty(document, 'hidden'); });
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});


test('closing pending camera permission disposes the late stream and permits restart', async ({ page }) => {
  await installCamera(page, 1400);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect((await resources(page)).workers).toBe(0);
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'idle');
  await expect(page.locator('#mirror')).toBeHidden();
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
});

test('missing model closes resources, retry recovers, and a disconnected camera releases tracking', async ({ page }) => {
  await installCamera(page);
  await page.route('**/models/face_landmarker.task', route => route.fulfill({ status: 404, body: 'Missing model' }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'error');
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeEnabled();
  await page.unroute('**/models/face_landmarker.task');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  await page.evaluate(() => {
    const state = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    state.streams.at(-1)!.getVideoTracks()[0]!.dispatchEvent(new Event('ended'));
  });
  await expect(page.locator('#guidance')).toContainText('disconnected');
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
});

test('GPU startup failure tracks through a fresh real CPU worker', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await installCamera(page, 0, true);
  await page.route('**/test-fixtures/unavailable.task', route => route.fulfill({ status: 404, body: 'Intentional GPU failure' }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Open camera' }).click();
  await expect(page.locator('.stage')).toHaveAttribute('data-state', 'tracking');
  expect(await page.evaluate(() => {
    const state = (window as unknown as { cameraHarness: CameraHarness }).cameraHarness;
    return { delegates: state.delegates, terminated: state.workers.map(worker => worker.terminated) };
  })).toEqual({ delegates: ['GPU', 'CPU'], terminated: [true, false] });
  await page.getByRole('button', { name: 'Close camera' }).click();
  await expect.poll(async () => (await resources(page)).closed).toBe(true);
  expect(errors).toEqual([]);
});
