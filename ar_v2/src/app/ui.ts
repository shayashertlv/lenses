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
import type { FitAssessment, RankedFrame } from '../fit/score.js';
import type { ProtocolStep } from '../enroll/protocol.js';
import { TEST_FRAMES } from '../fit/frame-asset.js';

export interface Readouts {
  fps: number;
  /** Mean luminance of the detected frames, 0..255. NaN before the first
   *  sample. */
  brightness: number;
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
  fit(assessment: FitAssessment): void;
  /**
   * Adds a frame to the picker after boot.
   *
   * The buttons used to be built once from `TEST_FRAMES` and there was no way
   * in afterwards — so a real asset, which arrives over the network several
   * seconds later, had no route to the DOM at all. `id` must be the same string
   * `handleAction` resolves, since the button carries it as `frame:<id>`.
   */
  addFrame(id: string, name: string): void;
  /** Marks which frame is currently on the face, so the picker shows state. */
  selectFrame(id: string): void;
  /**
   * The one line under the fit score saying what this frame's numbers are worth.
   *
   * The seat's most sensitive input is where the arms rest on the ears, and
   * eight of the ten catalogue assets cannot measure their own — two because
   * they are wraps with no rest point at all. `FrameAsset.earRestSource` carries
   * that distinction all the way from the geometry, and this is where it stops
   * being an internal field and becomes something the wearer can read. A fit
   * that is a picture rather than a measurement has to say so on the same
   * screen as the picture.
   */
  frameNote(text: string): void;
  /** Whether the scan/average-face controls are offered, and what they say. */
  face(state: { hasModel: boolean; scanning: boolean; hint: string }): void;
  /**
   * Renders a ranked catalogue. `relativeTo` names the frame the ordering was
   * taken AGAINST, when there was one — the heading is a different claim in
   * each case and must say which. See `rankCatalogue`.
   */
  catalogue(ranked: RankedFrame[], relativeTo?: string): void;
  readouts(values: Readouts): void;
  showDiagnostics(text: string): void;
  /** Asks the wearer for their PD in mm. Null if they cancelled, 0 to clear. */
  askPd(): number | null;
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
  const diagnosticsEl = el('diagnostics');

  let handler: (action: string) => void = () => {};

  // Frame buttons, built from the catalogue rather than hand-written, so adding
  // an asset needs no HTML change.
  //
  // **Two lists, and the split is real rather than cosmetic.** `#frames` holds
  // the eyewear — real scanned and authored assets, which arrive over the
  // network several seconds after boot and come in through `addFrame`.
  // `#shapes` holds `TEST_FRAMES`, which are parametric stand-ins generated
  // from a `FrameSpec` and are not a product anybody can buy. Mixing them put a
  // frame called `wide-pads` next to one called `Navigator (black acetate)` in
  // one row, and nothing said which of the two was a real pair of glasses.
  const framesEl = el('frames');
  const shapesEl = el('shapes');
  const frameButtons = new Map<string, HTMLElement>();
  const addFrameButton = (id: string, name: string, into: HTMLElement | null) => {
    if (!into || frameButtons.has(id)) return;
    const button = document.createElement('button');
    button.textContent = name;
    button.dataset.action = `frame:${id}`;
    into.appendChild(button);
    frameButtons.set(id, button);
  };
  for (const frame of TEST_FRAMES) addFrameButton(frame.id, frame.name, shapesEl);

  const detailsEl = el('details');
  const detailsToggle = el('details-toggle');
  const noteEl = el('frame-note');
  const faceHintEl = el('face-hint');
  const scanButton = el('btn-scan');
  const averageButton = el('btn-average');

  root.addEventListener('click', (event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-action]');
    if (!target?.dataset.action) return;
    // The details drawer is pure presentation, so it is opened here rather than
    // being routed through `handleAction` in `main.ts`. Nothing in the app's
    // state depends on whether it is open, which is the property that lets the
    // whole drawer be deleted without touching the loop.
    if (target.dataset.action === 'details') {
      if (!detailsEl || !detailsToggle) return;
      const open = detailsEl.hasAttribute('hidden');
      if (open) detailsEl.removeAttribute('hidden');
      else detailsEl.setAttribute('hidden', '');
      detailsToggle.setAttribute('aria-expanded', String(open));
      detailsToggle.textContent = open ? 'Hide details' : 'Details and instruments';
      return;
    }
    handler(target.dataset.action);
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

    fit(assessment) {
      // The fit panel is a SCORE and its parts, not a report. The prose
      // verdicts and the optician instructions that used to render here were
      // removed on 2026-08-25: a try-on shows a wearer the frame on their
      // face, and telling them in sentences what an optician would bend is a
      // different product with a different duty of care behind it.
      if (!verdictEl) return;
      verdictEl.innerHTML = '';
      const score = document.createElement('div');
      score.className = 'score';
      // **A score of 50 on every frame is not a score, and it printed as one.**
      //
      // `scoreOf` shrinks each measure toward neutral in proportion to how
      // little the scan knows: `points * c + 0.5 * (1 - c)`. On the average
      // face every confidence is 0, so the weighted mean is exactly 0.5 and the
      // headline reads `fit score 50` for all fifteen frames — a number that
      // cannot move, sitting above per-measure rows that all do. That is the
      // shape of a defect this tree has a name for, even when the arithmetic
      // underneath is right.
      //
      // The measures themselves are still worth showing: they are millimetres
      // and degrees off a known geometry, and they differ per frame. It is only
      // the GRADE that requires knowing whose face it is.
      const graded = assessment.measures.some((m) => m.value !== null && m.confidence > 0.02);
      score.textContent = graded ? `fit score ${assessment.score}` : 'not graded';
      score.classList.toggle('ungraded', !graded);
      verdictEl.appendChild(score);
      if (!graded) {
        const why = document.createElement('p');
        why.className = 'hint';
        why.textContent = 'Grading needs a scan of your own face. The measurements below '
          + 'are real, but they are against an average head.';
        verdictEl.appendChild(why);
      }
      for (const m of assessment.measures) {
        if (m.value === null) continue;
        const row = document.createElement('div');
        row.className = `verdict ${m.grade}`;
        // A relative verdict has to READ as one. `4.0 mm` under a "width"
        // label means "4 mm from the ideal"; the same number against a
        // reference means "4 mm wider than that one", and rendering them
        // identically is the two-meanings-one-number defect this file's width
        // block already carries a scar from.
        row.innerHTML =
          `<span class="label">${escapeHtml(m.id)}${
            m.relativeTo ? ` vs ${escapeHtml(m.relativeTo)}` : ''
          }</span>` +
          `<span class="value">${m.relativeTo && m.value > 0 ? '+' : ''}${
            m.value.toFixed(1)} ${escapeHtml(m.unit)}</span>`;
        verdictEl.appendChild(row);
      }
    },

    addFrame(id, name) { addFrameButton(id, name, framesEl); },

    selectFrame(id) {
      for (const [key, button] of frameButtons) {
        button.classList.toggle('selected', key === id);
      }
    },

    frameNote(text) {
      if (!noteEl) return;
      noteEl.textContent = text;
      noteEl.hidden = text === '';
    },

    face({ hasModel, scanning, hint }) {
      // The scan button is the same control before and after a scan and only
      // its label changes: "Scan my face" is an invitation, "Scan again" is a
      // correction, and offering both at once was two buttons doing one job.
      if (scanButton) {
        scanButton.textContent = scanning ? 'Scanning…' : hasModel ? 'Scan again' : 'Scan my face';
        (scanButton as HTMLButtonElement).disabled = scanning;
      }
      // The average face is a shortcut past the scan, not an alternative to it,
      // so it stops being offered once a real scan exists — otherwise the most
      // prominent thing on the panel is a control that throws the wearer's own
      // measurements away.
      if (averageButton) averageButton.hidden = hasModel;
      if (faceHintEl) { faceHintEl.textContent = hint; faceHintEl.hidden = hint === ''; }
    },

    catalogue(ranked, relativeTo) {
      if (!catalogueEl) return;
      // Say what the ordering is AGAINST. "Ranked for your face" is a claim
      // about absolute fit and needs a ruler the scan does not have; "ranked
      // next to the Navigator" is a claim about differences between two frames
      // whose widths are both known, and it is exact.
      catalogueEl.innerHTML = relativeTo
        ? `<h3>Ranked next to ${escapeHtml(relativeTo)}</h3>`
        : '<h3>Ranked for your face</h3>';
      for (const entry of ranked) {
        const row = document.createElement('button');
        row.className = 'ranked';
        row.dataset.action = `frame:${entry.frame.id}`;
        const poor = entry.assessment.measures.filter((m) => m.grade === 'poor');
        row.innerHTML =
          `<span class="rank-score">${entry.assessment.score}</span>` +
          `<span class="rank-name">${escapeHtml(entry.frame.name)}</span>` +
          `<span class="rank-why">${
            poor.length ? escapeHtml(poor.map((m) => m.id).join(', ')) : 'no complaints'
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
      if (Number.isFinite(values.brightness)) {
        // 0..255. Below ~55 a webcam is working near the bottom of its range and
        // the landmarks get materially noisier; below ~30 it is mostly noise.
        const b = values.brightness;
        lines.push(
          `image brightness ${b.toFixed(0)}/255` +
          (b < 30 ? ' — very dark, add light' : b < 55 ? ' — dim' : ''),
        );
      }
      if (model) {
        // The disagreement is shown beside the sigma rather than folded into
        // it, because they answer different questions. The sigma is what the
        // ruler claims about the POPULATION and is the same number for every
        // wearer on the iris rung; the gap is what a second ruler saw about
        // THIS one, and it is signed. Averaging a known direction into a
        // symmetric interval throws away the only thing the second ruler
        // supplied. Absent on the shipping single-ruler path, and absent is
        // the honest state — an unchecked ruler should not read as a checked one.
        const gap = model.scale.disagreementPct;
        lines.push(
          `scale: ${model.scale.source}` +
          (model.scale.source === 'assumed' ? '' : ` ±${(model.scale.sigma * 100).toFixed(1)}%`) +
          (gap == null || !Number.isFinite(gap) ? ''
            : ` · the ruler it replaced read you ${Math.abs(gap).toFixed(1)}% ` +
              `${gap > 0 ? 'large' : 'small'}`),
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

    askPd() {
      // The accuracy threshold is in the prompt because it decides whether the
      // answer helps or hurts, and a wearer cannot know it otherwise. Measured:
      // the pooled-iris assumption gives 4.4% median scale error, an exact PD
      // gives 0.5%, and the two cross over at about 2.5 mm of PD error. A
      // guessed PD is worse than no PD.
      const raw = prompt(
        'Distance between your PUPILS, in millimetres.\n\n'
        + 'Not your distance from the screen. For an adult it is 54 to 74 mm, '
        + 'usually around 63.\n\n'
        + 'It is on a spectacle prescription as PD, DPD or IPD, either as one '
        + 'number or as two that add up to it. An optician measures it with a '
        + 'pupilometer, good to half a millimetre, and that is nine times better '
        + 'than the iris size this app otherwise has to assume.\n\n'
        + 'If you would be guessing by more than about 2 mm, cancel instead — a '
        + 'guessed PD is WORSE than the assumption it replaces.\n\n'
        + 'Blank cancels. Enter 0 to go back to the assumption.',
      );
      if (raw === null || raw.trim() === '') return null;
      const value = Number(raw.trim());
      return Number.isFinite(value) ? value : null;
    },

    showDiagnostics(text) {
      if (!diagnosticsEl) return;
      diagnosticsEl.textContent = text;
      diagnosticsEl.hidden = false;
      // Pre-selected, so a wearer whose browser refuses the clipboard write can
      // still take it with one keystroke rather than dragging over 60 lines.
      const range = document.createRange();
      range.selectNodeContents(diagnosticsEl);
      const selection = getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    },

    onAction(h) { handler = h; },
  };
}

const escapeHtml = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string
  ));
