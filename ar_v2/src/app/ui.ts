/**
 * The controls and readouts.
 *
 * Two principles, both inherited from what v1 got right and one thing it did
 * not:
 *
 *  - **Say what is measured and what is assumed.** v1 rendered its width verdict
 *    with a leading tilde when the scale was iris-derived, which was exactly the
 *    right instinct. Here every verdict carries a `confidence` computed from the
 *    scan's own covariance, and anything soft is marked. A number the system
 *    cannot stand behind is worse than no number.
 *
 *  - **Show the price.** The mirror delay readout exists because the frame lock
 *    trades latency for correctness, and hiding the latency would make the trade
 *    unreviewable. Same for the dropped-frame count.
 *
 * The thing v1 did not have: a **privacy control that actually does something**.
 * The face model is one object under one storage key, so "forget me" is one
 * line, and it is in the interface rather than in a policy document.
 */

import type { FaceModel } from '../core/facemodel.js';
import type { SeatResult } from '../fit/contact.js';
import type { FitAssessment, RankedFrame } from '../fit/advice.js';
import { SOFT_VERDICT } from '../fit/advice.js';
import type { ProtocolStep } from '../enroll/protocol.js';
import { TEST_FRAMES } from '../fit/frame-asset.js';

export interface Readouts {
  fps: number;
  mirrorDelayMs: number;
  droppedFrames: number;
  backend: string;
  phase: string;
  model: FaceModel | null;
  seat: SeatResult | null;
}

export interface UI {
  status(text: string): void;
  guide(step: ProtocolStep | null): void;
  tracked(on: boolean, reason?: string): void;
  verdicts(assessment: FitAssessment): void;
  catalogue(ranked: RankedFrame[]): void;
  readouts(values: Readouts): void;
  onAction(handler: (action: string) => void): void;
}

export function createUI(root: HTMLElement): UI {
  const el = (id: string) => root.querySelector<HTMLElement>(`#${id}`);
  const statusEl = el('status');
  const promptEl = el('prompt');
  const progressEl = el('progress-bar');
  const verdictEl = el('verdicts');
  const adjustEl = el('adjustments');
  const readoutEl = el('readouts');
  const catalogueEl = el('catalogue');
  const dotEl = el('guide-dot');
  const readingEl = el('reading');

  let handler: (action: string) => void = () => {};

  // Frame buttons, built from the catalogue rather than hand-written, so adding
  // an asset needs no HTML change.
  const framesEl = el('frames');
  if (framesEl) {
    for (const frame of TEST_FRAMES) {
      const button = document.createElement('button');
      button.textContent = frame.name;
      button.dataset.action = `frame:${frame.id}`;
      framesEl.appendChild(button);
    }
  }

  root.addEventListener('click', (event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-action]');
    if (target?.dataset.action) handler(target.dataset.action);
  });

  return {
    status(text) { if (statusEl) statusEl.textContent = text; },

    /**
     * The guide, and — new — a live reading of what the beat is watching.
     *
     * Without the reading, "the scan is stuck" and "you are nearly there" look
     * identical from the outside. That cost two round trips of a wearer
     * reporting a stall with no way to say what the scan thought it was seeing,
     * so the number is on screen now rather than behind a console command.
     */
    guide(step) {
      if (!promptEl || !progressEl || !dotEl) return;
      if (!step || !step.beat) {
        promptEl.textContent = '';
        if (readingEl) readingEl.textContent = '';
        dotEl.style.opacity = '0';
        progressEl.style.width = '0%';
        return;
      }
      promptEl.textContent = step.prompt;
      progressEl.style.width = `${Math.round(step.progress * 100)}%`;

      if (readingEl) {
        const r = step.reading;
        if (!r) {
          readingEl.textContent = 'no face';
        } else if (r.kind === 'reach') {
          // A `reach` beat has no number the wearer must hit, so showing one
          // ("30 / 60") is the thing that told a wearer to do the impossible.
          // Show how far they have got and whether it has settled.
          readingEl.textContent = step.settling
            ? `${r.best.toFixed(0)} ${r.unit} — holding…`
            : `${Math.max(r.value, 0).toFixed(0)} ${r.unit} (best ${r.best.toFixed(0)})`;
        } else {
          readingEl.textContent = r.atMost
            ? `${r.value.toFixed(0)} ${r.unit} — needs under ${r.goal.toFixed(0)}`
            : `${r.value.toFixed(0)} / ${r.goal.toFixed(0)} ${r.unit}`;
        }
        readingEl.classList.toggle('struggling', step.struggling && step.toward < 0.9);
        readingEl.classList.toggle('settling', step.settling);
      }

      dotEl.style.opacity = '1';
      dotEl.style.left = `${step.beat.target.x * 100}%`;
      dotEl.style.top = `${step.beat.target.y * 100}%`;
      // The dot grows toward the target and fills once held, so a wearer can see
      // BOTH that they are moving the right way and that they should keep still.
      const grow = 0.7 + step.toward * 0.5;
      dotEl.style.transform =
        `translate(-50%, -50%) scale(${grow * step.beat.target.scale})`;
      dotEl.classList.toggle('holding', step.beatProgress > 0.1);
      dotEl.classList.toggle('near', step.toward > 0.75);
    },

    tracked(on, reason) {
      root.classList.toggle('untracked', !on);
      if (!on && reason && statusEl) statusEl.textContent = reason;
    },

    verdicts(assessment) {
      if (!verdictEl) return;
      verdictEl.innerHTML = '';
      for (const v of assessment.verdicts) {
        const row = document.createElement('div');
        row.className = `verdict ${v.grade}`;
        const soft = v.confidence < SOFT_VERDICT && v.value !== null;
        row.innerHTML =
          `<span class="label">${escapeHtml(v.label)}</span>` +
          `<span class="value">${
            v.value === null ? '' : `${soft ? '~' : ''}${v.value.toFixed(1)} ${v.unit}`
          }</span>` +
          `<span class="detail">${escapeHtml(v.detail)}</span>`;
        verdictEl.appendChild(row);
      }
      const score = document.createElement('div');
      score.className = 'score';
      score.textContent = `fit score ${assessment.score}`;
      verdictEl.prepend(score);

      if (adjustEl) {
        adjustEl.innerHTML = '';
        if (assessment.adviceWithheld) {
          const row = document.createElement('div');
          row.className = 'adjust none';
          row.textContent = assessment.adviceWithheld;
          adjustEl.appendChild(row);
        } else if (assessment.adjustments.length === 0) {
          adjustEl.innerHTML = '<div class="adjust none">Nothing to adjust — it fits as it comes.</div>';
        } else {
          for (const a of assessment.adjustments) {
            const row = document.createElement('div');
            row.className = 'adjust';
            row.textContent = a;
            adjustEl.appendChild(row);
          }
        }
      }
    },

    catalogue(ranked) {
      if (!catalogueEl) return;
      catalogueEl.innerHTML = '<h3>Ranked for your face</h3>';
      for (const entry of ranked) {
        const row = document.createElement('button');
        row.className = 'ranked';
        row.dataset.action = `frame:${entry.frame.id}`;
        const poor = entry.assessment.verdicts.filter((v) => v.grade === 'poor');
        row.innerHTML =
          `<span class="rank-score">${entry.assessment.score}</span>` +
          `<span class="rank-name">${escapeHtml(entry.frame.name)}</span>` +
          `<span class="rank-why">${
            poor.length ? escapeHtml(poor.map((v) => v.label).join(', ')) : 'no complaints'
          }</span>`;
        catalogueEl.appendChild(row);
      }
    },

    readouts(values) {
      if (!readoutEl) return;
      const model = values.model;
      const lines = [
        `${values.fps.toFixed(0)} fps · ${values.backend} · ${values.phase}`,
        `mirror ${values.mirrorDelayMs.toFixed(0)} ms behind` +
          (values.droppedFrames ? ` · ${values.droppedFrames} frames dropped` : ''),
      ];
      if (model) {
        lines.push(
          `scale: ${model.scale.source}` +
          (model.scale.source === 'assumed' ? '' : ` ±${(model.scale.sigma * 100).toFixed(1)}%`),
        );
        if (model.pdMm !== null) {
          lines.push(
            `PD ${model.pdMm.toFixed(1)} mm` +
            (model.pdSigmaMm ? ` ±${model.pdSigmaMm.toFixed(1)}` : '') +
            (model.scale.source === 'iris' ? ' (iris — not for ordering lenses)' : ''),
          );
        }
        lines.push(
          `nose measured to ${(model.quality.nose?.sigmaMm ?? NaN).toFixed(2)} mm, ` +
          `field ${model.displacementRmsMm.toFixed(2)} mm rms`,
        );
        if (!model.intrinsicsSolved) lines.push('camera field of view assumed, not solved');
      }
      if (values.seat) {
        lines.push(
          `seat: ${values.seat.descentMm >= 0 ? 'sits' : 'perches'} ` +
          `${Math.abs(values.seat.descentMm).toFixed(1)} mm ` +
          `${values.seat.descentMm >= 0 ? 'lower' : 'higher'} than a landmark would put it · ` +
          `${(values.seat.padLoadFraction * 100).toFixed(0)}% on the nose`,
        );
      }
      readoutEl.textContent = lines.join('\n');
    },

    onAction(h) { handler = h; },
  };
}

const escapeHtml = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string
  ));
