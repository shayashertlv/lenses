/** The first resolved hair crossing hides the rest of a fixed temple. All coordinates are original
 * model metres (+Z forward), so a larger cutoff hides more. No ear position is inferred. */
import type {ProjectedTemplePath, TempleContinuityModel, TempleStation} from './continuity.ts';
import type {CategoryMask} from '../hair/protocol.ts';
import {applyAffine} from '../hair/mask-reuse.ts';
import type {MaskWarp} from '../hair/mask-reuse.ts';

export const TEMPLE_ENDPOINT = Object.freeze({frontZM: -.035, coverage: .7, fadeM: .005,
  clearDwellMs: 100, clearObservations: 3, holdMs: 350, lengthenMPerSecond: .015, resetAngleDegrees: 18,
  edgeCoverage: .4});
export type TempleEndpointState = 'fallback' | 'held' | 'tracking';
export interface TempleEndpointReport {
  negativeZM: number; positiveZM: number; negativeState: TempleEndpointState; positiveState: TempleEndpointState;
  negativeFadeM: number; positiveFadeM: number;
  maximumZM: number; zoneStartZM: number; negativeCandidateZM: number | null; positiveCandidateZM: number | null;
}
export interface TempleEndpointInput {
  paths: readonly ProjectedTemplePath[] | null; mask: CategoryMask | null;
  render: {width: number; height: number}; warp?: MaskWarp | null;
  timestampMs: number; yawDegrees: number; pitchDegrees: number;
  /** Use the segmentation sequence, not the drawn frame sequence. Defaults to category-buffer identity. */
  evidenceId?: unknown;
}
interface SideMemory {zM: number; fadeM: number; clearSinceMs: number; clearCount: number; lastClearMs: number; state: TempleEndpointState;}
interface OrderedStation {index: number; station: TempleStation; distanceM: number;}
/** Any cutoff in [rearZM, frontZM] has its complete fade inside this observed hair interval. */
interface EndpointInterval {
  rearZM: number; frontZM: number; coveredFrontZM: number; fadeM: number;
  /** Front of the contiguous partial-coverage lead-in belonging to this strong interval. */
  hairFrontZM: number;
}
interface Sample {zM: number; distanceM: number; x: number; y: number; valid: boolean; hair: number; centerHair: boolean; crossTexelCount: number;}
interface Observation {interval: EndpointInterval | null; samples: Sample[]; resolved: boolean;}
const clamp = (value: number, low: number, high: number): number => Math.max(low, Math.min(high, value));
const angleDifference = (a: number, b: number): number => Math.abs(((a - b + 540) % 360 + 360) % 360 - 180);
const emptyObservation = (): Observation => ({interval: null, samples: [], resolved: false});

export class TempleEndpointTracker {
  readonly maximumZM: number;
  readonly zoneStartZM: number;
  private readonly sides: readonly (readonly OrderedStation[])[];
  private memory: SideMemory[];
  private previousTimestampMs: number | null = null;
  private previousPose: {yaw: number; pitch: number} | null = null;
  private seenEvidence: unknown[] = [];

  constructor(model: TempleContinuityModel, maximumZM: number) {
    if (!Number.isFinite(maximumZM) || maximumZM < model.cutoffZM || maximumZM >= model.startZM
      || model.sides.length !== 2) throw new Error('The shortened temple maximum must lie inside both model arms.');
    this.maximumZM = maximumZM;
    // Search the usable shaft, not an arbitrary rear window. The front guard also leaves room for
    // the clip's fade without reaching the optical/front-frame region (z >= -30 mm).
    this.zoneStartZM = Math.min(model.startZM, TEMPLE_ENDPOINT.frontZM);
    this.sides = model.sides.map(stations => {
      let distanceM = 0;
      return stations.map((station, index) => ({index, station})).sort((a, b) => b.station.zM - a.station.zM)
        .map((item, index, ordered) => {
          const previous = ordered[index - 1]?.station, current = item.station;
          if (previous) distanceM += Math.hypot(current.centerXM - previous.centerXM,
            current.centerYM - previous.centerYM, current.zM - previous.zM);
          return {...item, distanceM};
        });
    });
    this.memory = [this.emptySide(), this.emptySide()];
  }

  private emptySide(): SideMemory {return {zM: this.maximumZM, fadeM: TEMPLE_ENDPOINT.fadeM, clearSinceMs: 0, clearCount: 0,
    lastClearMs: -Infinity, state: 'fallback'};}
  private hold(memory: SideMemory): void {
    memory.clearCount = 0; memory.lastClearMs = -Infinity;
    memory.state = memory.zM === this.maximumZM ? 'fallback' : 'held';
  }

  reset(): TempleEndpointReport {
    this.memory = [this.emptySide(), this.emptySide()]; this.previousTimestampMs = null;
    this.previousPose = null; this.seenEvidence = [];
    return this.report([null, null]);
  }

  update(input: TempleEndpointInput): TempleEndpointReport {
    const {timestampMs: now, yawDegrees: yaw, pitchDegrees: pitch} = input;
    if (![now, yaw, pitch].every(Number.isFinite)) {
      this.memory.forEach(memory => this.hold(memory)); return this.report([null, null]);
    }
    const gapMs = this.previousTimestampMs === null ? 0 : now - this.previousTimestampMs;
    this.previousTimestampMs = now;
    if (gapMs < 0 || gapMs > TEMPLE_ENDPOINT.holdMs || (this.previousPose
      && (angleDifference(yaw, this.previousPose.yaw) >= TEMPLE_ENDPOINT.resetAngleDegrees
      || angleDifference(pitch, this.previousPose.pitch) >= TEMPLE_ENDPOINT.resetAngleDegrees))) {
      // A changed view invalidates permission to reveal. It does not reveal a previously hidden tail.
      this.memory.forEach(memory => this.hold(memory));
    }
    this.previousPose = {yaw, pitch};
    const validMask = input.mask && Number.isInteger(input.mask.width) && input.mask.width > 0
      && Number.isInteger(input.mask.height) && input.mask.height > 0
      && input.mask.category.length === input.mask.width * input.mask.height
      && Number.isFinite(input.render.width) && input.render.width > 0
      && Number.isFinite(input.render.height) && input.render.height > 0;
    const identity = input.evidenceId ?? input.mask?.category;
    const fresh = !!validMask && !this.seenEvidence.some(value => Object.is(value, identity));
    if (fresh) {this.seenEvidence.push(identity); if (this.seenEvidence.length > 32) this.seenEvidence.shift();}
    const observations = [0, 1].map(side => validMask ? this.observe(side, input) : emptyObservation());
    for (const side of [0, 1]) {
      const memory = this.memory[side]!, observation = observations[side]!, interval = observation.interval;
      if (interval && memory.zM < interval.rearZM) {
        // Local hair hiding also uses valid warped/reused masks. If that same evidence puts a
        // substantial gap earlier on the currently projected shaft, hide its downstream tail now.
        // Reuse can conservatively hide more; it never supplies extra permission to reveal.
        memory.zM = (interval.rearZM + interval.frontZM) / 2;
        memory.fadeM = interval.fadeM;
        this.hold(memory); memory.state = 'tracking'; continue;
      }
      if (interval && memory.zM <= Math.max(interval.frontZM, interval.coveredFrontZM - memory.fadeM)) {
        // Keep a covered endpoint even when the preferred fade changes. If a narrower observed
        // crossing covers this end, its fade must fit too; never extend a 5 mm dissolve beyond it.
        memory.fadeM = Math.min(memory.fadeM, interval.fadeM, interval.coveredFrontZM - memory.zM);
        this.hold(memory); memory.state = 'tracking'; continue;
      }
      const target = interval ? interval.coveredFrontZM - Math.min(memory.fadeM, interval.fadeM) : this.maximumZM;
      // Release requires an observed, clear corridor up to the destination patch. Its contiguous
      // partial-coverage edge belongs to that patch, not to a separate obstruction. With a resolved
      // destination, light contacts at the shaft's outer edges may stay locally occluded without
      // severing its clear centre. Centre/thick crossings and invalid samples still block release;
      // without a supported destination every corridor sample must remain completely clear.
      const clearUntil = interval?.hairFrontZM ?? this.maximumZM;
      const clearSamples = observation.samples.filter(sample => sample.zM <= memory.zM + memory.fadeM
        && sample.zM > clearUntil + 1e-10);
      // A final sub-sample step can have no strictly interior samples. Permit it only within the
      // observed lead-in, or between adjacent valid samples bracketing a clear-to-hair boundary.
      const fadeFrontZM = memory.zM + memory.fadeM;
      let before = -1;
      for (let i = 0; i < observation.samples.length; i++) if (observation.samples[i]!.zM >= fadeFrontZM - 1e-10) before = i;
      const after = observation.samples.findIndex(sample => sample.zM <= clearUntil + 1e-10);
      const reachesSupportedInterval = interval !== null && (fadeFrontZM <= clearUntil + 1e-10
        || (before >= 0 && after === before + 1 && observation.samples[before]!.valid
          && observation.samples[before]!.hair === 0 && observation.samples[after]!.valid));
      const clear = observation.resolved && (clearSamples.length > 0 || reachesSupportedInterval)
        && clearSamples.every(sample => sample.valid && (sample.hair === 0
          || (interval !== null && !sample.centerHair && sample.hair <= TEMPLE_ENDPOINT.edgeCoverage)))
        && observation.samples.some(sample => sample.valid && sample.zM <= clearUntil + 1e-10);
      if (target >= memory.zM || !clear) {this.hold(memory); continue;}
      if (!fresh) {
        // Reuse can preserve already clear evidence, never advance it. An uncertain carried mask
        // was rejected above, so its gap cannot later be credited as fresh clear time.
        if (now - memory.lastClearMs > TEMPLE_ENDPOINT.holdMs) this.hold(memory);
        else memory.state = memory.zM === this.maximumZM ? 'fallback' : 'held';
        continue;
      }
      const clearGapMs = now - memory.lastClearMs;
      const continuesClear = memory.clearCount > 0 && clearGapMs >= 0 && clearGapMs <= TEMPLE_ENDPOINT.holdMs;
      if (!continuesClear) {
        memory.clearSinceMs = now; memory.clearCount = 0;
      }
      // Integrate between distinct supported observations, not draw frames: hair every second
      // frame must not halve the release speed. Hold/reset clears this clock; long steps stay capped.
      const releaseDt = continuesClear ? clamp(clearGapMs / 1000, 0, .1) : 0;
      memory.lastClearMs = now; memory.clearCount++;
      if (memory.clearCount >= TEMPLE_ENDPOINT.clearObservations && now - memory.clearSinceMs >= TEMPLE_ENDPOINT.clearDwellMs) {
        memory.zM = Math.max(target, memory.zM - TEMPLE_ENDPOINT.lengthenMPerSecond * releaseDt);
      }
      memory.zM = clamp(memory.zM, this.maximumZM, this.zoneStartZM);
      if (interval && memory.zM <= target + 1e-10)
        memory.fadeM = Math.min(memory.fadeM, interval.fadeM, interval.coveredFrontZM - memory.zM);
      else if (memory.zM === this.maximumZM) memory.fadeM = TEMPLE_ENDPOINT.fadeM;
      memory.state = memory.zM === this.maximumZM ? 'fallback' : 'held';
    }
    return this.report(observations.map(observation => observation.interval
      ? (observation.interval.rearZM + observation.interval.frontZM) / 2 : null));
  }

  private report(candidates: readonly (number | null)[]): TempleEndpointReport {
    return {negativeZM: this.memory[0]!.zM, positiveZM: this.memory[1]!.zM,
      negativeFadeM: this.memory[0]!.fadeM, positiveFadeM: this.memory[1]!.fadeM,
      negativeState: this.memory[0]!.state, positiveState: this.memory[1]!.state,
      maximumZM: this.maximumZM, zoneStartZM: this.zoneStartZM,
      negativeCandidateZM: candidates[0] ?? null, positiveCandidateZM: candidates[1] ?? null};
  }

  private observe(side: number, input: TempleEndpointInput): Observation {
    const path = input.paths?.find(item => item.side === side), ordered = this.sides[side]!, mask = input.mask!;
    if (!path || path.points.length !== ordered.length || ordered.length < 2) return emptyObservation();
    const map = (x: number, y: number): [number, number] => {
      if (!input.warp) return [x * mask.width / input.render.width, y * mask.height / input.render.height];
      const {width, height, toMask} = input.warp;
      const [mx, my] = applyAffine(toMask, x * width / input.render.width, y * height / input.render.height);
      return [mx * mask.width / width, my * mask.height / height];
    };
    const samples: Sample[] = [];
    for (let segment = 1; segment < ordered.length; segment++) {
      const a = ordered[segment - 1]!, b = ordered[segment]!, pa = path.points[a.index]!, pb = path.points[b.index]!;
      if (a.station.zM < this.maximumZM || b.station.zM > this.zoneStartZM) continue;
      const dz = a.station.zM - b.station.zM;
      if (!(dz > 0)) return emptyObservation();
      const lo = clamp((a.station.zM - this.zoneStartZM) / dz, 0, 1),
        hi = clamp((a.station.zM - this.maximumZM) / dz, 0, 1);
      const [ax, ay] = map(pa.x, pa.y), [bx, by] = map(pb.x, pb.y);
      const valid = [pa.x, pa.y, pa.radiusPx, pb.x, pb.y, pb.radiusPx, ax, ay, bx, by].every(Number.isFinite)
        && pa.radiusPx >= 0 && pb.radiusPx >= 0;
      if (!valid) {
        samples.push({zM: (a.station.zM + b.station.zM) / 2, distanceM: (a.distanceM + b.distanceM) / 2,
          x: 0, y: 0, valid: false, hair: 0, centerHair: false, crossTexelCount: 0}); continue;
      }
      // Refine the 3.6–4 mm asset stations: at most half a mask texel per step, and enough model
      // samples to resolve the fade. Repeated texels never count as extra spatial confidence.
      const steps = Math.max(1, Math.ceil(Math.max(Math.hypot(bx - ax, by - ay) * 2,
        (b.distanceM - a.distanceM) / (TEMPLE_ENDPOINT.fadeM / 4)) * (hi - lo)));
      if (steps > 4096) return emptyObservation();
      const length = Math.hypot(pb.x - pa.x, pb.y - pa.y), nx = length > 1e-10 ? -(pb.y - pa.y) / length : 0,
        ny = length > 1e-10 ? (pb.x - pa.x) / length : 0;
      for (let step = 0; step <= steps; step++) {
        if (step === 0 && samples.length && Math.abs(samples.at(-1)!.zM - (a.station.zM - dz * lo)) < 1e-10) continue;
        const amount = lo + (hi - lo) * step / steps, zM = a.station.zM - dz * amount,
          distanceM = a.distanceM + (b.distanceM - a.distanceM) * amount,
          px = pa.x + (pb.x - pa.x) * amount, py = pa.y + (pb.y - pa.y) * amount,
          radius = pa.radiusPx + (pb.radiusPx - pa.radiusPx) * amount, [x, y] = map(px, py);
        const sample: Sample = {zM, distanceM, x, y, valid: length > 1e-10 && px >= 0 && py >= 0
          && px < input.render.width && py < input.render.height, hair: 0, centerHair: false, crossTexelCount: 0};
        const pixels = new Set<number>();
        // Across the shaft, not a square window that also samples ahead/behind a hair boundary.
        for (const offset of [-1, -.5, 0, .5, 1]) {
          const [mx, my] = map(px + nx * radius * offset, py + ny * radius * offset), ix = Math.floor(mx), iy = Math.floor(my);
          if (!Number.isFinite(mx) || !Number.isFinite(my) || ix < 0 || iy < 0 || ix >= mask.width || iy >= mask.height) {
            sample.valid = false; continue;
          }
          const index = iy * mask.width + ix; pixels.add(index);
          if (offset === 0) sample.centerHair = mask.category[index] === mask.hairIndex;
        }
        let hair = 0; for (const index of pixels) if (mask.category[index] === mask.hairIndex) hair++;
        sample.crossTexelCount = pixels.size;
        sample.hair = pixels.size ? hair / pixels.size : 0; samples.push(sample);
      }
    }
    let interval: EndpointInterval | null = null, run: Sample[] = [], partialLead: Sample | null = null,
      runHairFrontZM: number | null = null;
    const finish = (): void => {
      let first = run[0], last = run.at(-1);
      const spatiallyResolved = (samples: Sample[], spanPx: number, texels: number): boolean => {
        const a = samples[0], b = samples.at(-1);
        return !!a && !!b && Math.hypot(b.x - a.x, b.y - a.y) >= spanPx
          && new Set(samples.map(sample => `${Math.floor(sample.x)},${Math.floor(sample.y)}`)).size >= texels;
      };
      const broad = spatiallyResolved(run, 2, 3);
      // Two fully covered cross-sections can already sever the actual rendered arm. They are
      // stronger evidence than a partial-width strand: require independent longitudinal texels
      // and a full texel of movement, and place the fade between the fully covered end stations.
      const full = broad ? [] : run.filter(sample => sample.hair === 1);
      const narrow = !broad && spatiallyResolved(full, 1, 2);
      if (narrow) {first = full[0]; last = full.at(-1);}
      // A single longitudinal mask column can still be a resolved barrier across a wide shaft.
      // Require three distinct transverse texels and consecutive fully covered stations, so one
      // isolated mask pixel or a partial-width strand cannot trigger this narrower exception.
      let fullFirst: Sample | null = null, barrierFirst: Sample | null = null, barrierLast: Sample | null = null;
      if (!broad && !narrow) for (const sample of run) {
        if (sample.hair === 1 && sample.crossTexelCount >= 3) {
          fullFirst ??= sample;
          if (sample !== fullFirst) {barrierFirst = fullFirst; barrierLast = sample;}
        } else fullFirst = null;
      }
      if (barrierFirst && barrierLast) {first = barrierFirst; last = barrierLast;}
      if (!interval && first && last && first.zM - last.zM > 1e-9 && (broad || narrow || barrierFirst)) {
        // Samples run hinge to tip. Keep the first resolved crossing: replacing it with a
        // later patch would expose bare shaft between them after local hair already hid it.
        // Continue collecting samples for the existing clear-corridor release checks.
        // Local occlusion already hides a resolved cross-shaft gap regardless of its metric length.
        // Use the supported band itself to bound the dissolve, rather than rejecting narrow gaps
        // and leaving an unrelated visible tail beyond them. Half the band leaves placement room.
        const fadeM = Math.min(TEMPLE_ENDPOINT.fadeM, (first.zM - last.zM) / 2);
        interval = {rearZM: last.zM, frontZM: first.zM - fadeM, coveredFrontZM: first.zM, fadeM,
          hairFrontZM: runHairFrontZM ?? first.zM};
      }
      run = []; runHairFrontZM = null;
    };
    for (const sample of samples) {
      if (sample.valid && sample.centerHair && sample.hair >= TEMPLE_ENDPOINT.coverage) {
        if (!run.length) runHairFrontZM = partialLead?.zM ?? sample.zM;
        partialLead = null; run.push(sample);
      } else {
        finish();
        // Only uninterrupted, valid partial coverage immediately leading into the selected strong
        // run is its edge. A clear/invalid sample or a prior strong run separates other obstructions.
        if (sample.valid && sample.hair > 0) partialLead ??= sample;
        else partialLead = null;
      }
    }
    finish();
    const first = samples[0], last = samples.at(-1);
    return {interval, samples, resolved: !!first && !!last && Math.hypot(last.x - first.x, last.y - first.y) >= 2};
  }
}
