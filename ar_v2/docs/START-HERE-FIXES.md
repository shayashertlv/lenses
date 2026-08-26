# Opening prompt — implementing the specified fixes

*Paste this whole file as the first message of a new session, or say: "read
`ar_v2/docs/START-HERE-FIXES.md` and begin."*

---

You are picking up `C:\Users\Shay\PycharmProjects\lenses\ar_v2` on branch
**`ar-v2-primary`**. It is a TypeScript eyewear try-on: scan the face once,
track against the scan, seat the frame by contact physics. The owner is Shay.

Your job this session is to **implement a queue of fixes that have already been
specified and measured**. You are not being asked to find defects, decide
whether they are real, or work out what to change. All three were done on
2026-08-26 by a review pass and then by an independent specification pass, and
the results are in the tree.

## Read these three files before touching anything

1. **`docs/FIX-SPECS.md`** — 1,880 lines, and it is the whole brief. For each
   finding: the verdict, the exact patch as a before/after snippet, what the fix
   measurably moves, which tests or report bars go red, and the falsifiable
   assertion that should guard it. The "after" numbers in it were produced by
   copying `dist/` into a scratch directory, patching the copy, and running it —
   so **a fix that reproduces those numbers is a fix that landed correctly**, and
   one that does not is a fix that did something else.
2. **`docs/NEXT-SESSION.md` §3b** — the same queue, ranked, with one-line
   verdicts. Start here for the order.
3. **`docs/REAL-FACE.md`** — what two real scans of one wearer say about this
   system. Two of the items in the queue (C2, C3) are confirmed as facts there
   rather than as inferences.

Then skim `docs/NEXT-SESSION.md` §1 (house doctrine) and §6 (traps). Both are
short and both will cost you time if you skip them.

## The single most important instruction

**Read the verdict line before the finding.** The specification pass corrected
the review's own wording on nine of fourteen items and found two of its proposed
fixes to be wrong. If you implement what the finding says instead of what the
spec says, you will:

- apply a **sign error** to `D1` (the review wants `u`; the correct component is
  `-u`, because `u` is the gradient of the residual, not the direction of the
  force);
- change a counter in `B3` that must not be changed (the fix is the comment);
- implement something worthless in `C1` and miss the real defect next door;
- treat `C2` as affecting every frame when it affects one frame per scan;
- treat `C4c` as a solver defect when 72 paired cells say it does nothing;
- go looking for a ledger row in `D3` that does not exist.

The specs say all of this in their first paragraph. Trust them over the finding
titles, and over anything you infer from the code in five minutes.

## Order of work

Take them in this order. It is priority, and it is also roughly
least-entangled-first.

**P1, and do this one first — it is live and reproducible today.**

- **A2** — a stored model's intrinsics are planted on a camera of a different
  size. Reproducer needing no hardware change: scan on a camera, reload with the
  camera unavailable, and `startSource` falls back to a **1024x1024** sample
  still while the model carries 1280x720. PnP absorbs the wrong focal length
  into depth, so reprojection stays at 4.95–5.90 px against a bar of 22 and
  **every gate reads green while the frame is drawn a third of a screen off the
  face**. There is a second site the original review missed, and the review's
  proposed remedy is also wrong — `scaleIntrinsics` is exact only when the
  aspect ratio survives. The spec has the correct rescale and explains why
  `max(sx, sy)` is the right focal factor for a webcam mode change.

**P2 — real accuracy, or a number a wearer is shown.**

- **C3** — the silhouette term is dead in production and is worth a replicated
  **0.29 mm** off the standoff p90. Verdict is WIRE IT UP, not remove. Note the
  consequence the spec draws out: with the term inert, the harness's
  `no-silhouette` variant *is* the production configuration, so **every
  published enrolment figure was measured on the other arm** and will need
  re-running once this lands.
- **C4a** — a PD the app accepts as its absolute ruler is then withheld from the
  readout, because the correction gates on [45, 85] and the readout on [46, 80].
  Worse than the review stated; the spec has the table.
- **D1** — the wearer-facing "% on the nose" is computed from a different
  direction than the solve balances. **Mind the sign.**
- **B1** — `snapOffsets` skips its ridge gate whenever the peak lands at a band
  end. Two corrections in the spec: "at full confidence" understates it (band-end
  accepts carry *higher* confidence than interior ones), and "clamp it" is not
  available because the code already clamps.
- **D4** — `padAngleRad` is two different angles under one name. Decide which
  definition keeps it and change `assets/glasses/ground-truth.json`'s stated
  definition with it.

**P3 — smaller, or different from what the review said.**

D2, C1, C2, B3, C4b, C4c, D3, A3. Each has a spec entry; several are one-line
comment corrections rather than code changes.

**Then the prose batch.** `docs/NEXT-SESSION.md` §3b lists eleven places where a
comment or a doc asserts the opposite of the code beside it, with file:line.
These are cheap and they matter: this tree has now found that failure mode more
than a dozen times, and every instance was written by somebody who believed it.

## How to work

- **One fix, one verification, one commit.** Do not batch unrelated fixes into a
  commit. The specs give you a measurable "after" for most of them — reproduce
  it and put the number in the commit message.
- **Sabotage every new gate.** House doctrine, and it is not optional: before
  letting a new assertion go green, break what it guards, watch it go red,
  restore, and show the red/green table. Two tests written this week passed
  under sabotage on their first draft — one of them mine — because the fixture
  did not exercise the thing being asserted.
- **`npm test` is the bar**: 304 tests and four gates, all green as of `4b0b117`.
  It runs a build first, so never run `npm run build` concurrently from
  elsewhere — `dist/` is shared.
- **When a report moves**, regenerate it with `npm run report:<name>`.
  `check-reports.mjs` now hashes the committed body, so it will go red until you
  do. That is the gate working, not a problem.
- **Comments carry the reasoning and the measurement**, not a restatement of the
  code. Long docstrings are the style here — but they must be TRUE, and a
  measured number in a comment is worth more than an adjective.
- **If a spec turns out to be wrong, say so and stop.** Two of the review's
  fixes were wrong and a third pass caught them. That can happen again. A
  correction with evidence is a better outcome than a confident patch.

## What NOT to do

- **Do not delete `ar/`** — already done, on 2026-08-26. It is recoverable from
  `origin/ar-v1` and `origin/ar-tryon` if you need to read v1 for reference.
- **Do not commit a capture file.** `.ndjson` captures are 478-point facial
  landmark streams of a named person. `docs/PRIVACY.md` records what happened
  the last time this repository held one. The owner's live captures are in his
  own `Downloads`; `scripts/replay-capture.mjs` reads them and
  `docs/REAL-FACE.md` is the record.
- **Do not re-derive `docs/SCALE.md`** or re-litigate settled questions
  (Q15, Q18, Q21, Q24). They are settled on measured records.
- **Do not touch the identity watch's constants** without reading
  `src/track/identity.ts`'s header. They were calibrated end to end on the
  scenario they exist for, and the first calibration of them was wrong in a way
  that is recorded there.

## Where the bar is now

    npm test    ->  304/304, four green gates
    HEAD        ->  4b0b117

Every accuracy figure except `docs/REAL-FACE.md` is synthetic, measured on a
population drawn from the same shape basis the estimator fits. `REAL-FACE.md` is
the one document with a real wearer behind it, and it says the system is
calibrated — every region repeats inside its own claimed uncertainty, and the
nose repeats to 0.397 mm against a claimed 0.734.

Keep it that way. If a fix moves a published number, the number moves — say so
in the commit and regenerate the report. If a fix makes the system claim more
than it can show, that is the one kind of change this tree does not accept.
