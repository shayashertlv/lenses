# Continue the ar_v2 work

*Paste this whole file as your opening prompt, or just say: "read
`ar_v2/docs/NEXT-SESSION.md` and continue."*

---

## 0. Who you are and what this is

You are picking up `C:\Users\Shay\PycharmProjects\lenses\ar_v2` on branch
**`ar-v2-primary`**. It is a TypeScript eyewear try-on: scan the face once, track
against the scan, seat the frame by contact physics. The owner is Shay. He wants
a real pair of glasses on a real face and is not interested in process detail —
**lead with what he can see, not with what you verified.**

Start here:

```bash
cd C:\Users\Shay\PycharmProjects\lenses\ar_v2
node scripts/fetch-vendor.mjs      # only if vendor/ is absent
npm test                            # expect 270/270 and FOUR green gates
```

Read, in this order: `docs/HANDOFF.md` (the ten-stage migration and its traps),
`docs/SCALE.md` (the scale investigation — do **not** re-derive it, and note the
banner at the top saying which two of its own claims did not survive), then
`docs/ARCHITECTURE.md` and `docs/CONSTANTS.md`.

## 1. House doctrine — match it or the review is worse than useless

- **"A check that cannot fail is a bug."** Before letting a new gate go green,
  sabotage what it guards, watch it go red, restore. Show the red/green table.
  Sabotage in **both** directions where the gate could be too eager: the reports
  gate has an R2 case proving a comment edit does *not* fire it.
- **Adopt on ≥4 of 5 independent seeds**, median-of-seeds with the per-seed
  spread. A single draw is a coin flip. **The tree has now reversed a verdict
  FIVE times by forgetting it, and two of those were `docs/SCALE.md` reversing
  its own claims from the session that wrote them.** Before quoting any number
  from a doc, check whether it was pooled or median-of-seeds.
- **Results that went against the change stay in the headline**, never a footnote.
- Every exported constant needs a row in `docs/CONSTANTS.md`
  (`measured`/`derived`/`stated`/`published`/`physics`/`assumed`).
  `check-constants.mjs` fails otherwise — **but it cannot see an orphaned ROW, or
  an orphaned EXEMPTION.** Sweep both by hand when you delete a constant.
- **The isolation boundary is mechanised.** `core/ enroll/ track/ fit/ detect/
  testkit/` must load in Node with no browser; `check-isolation.mjs` actually
  `import()`s every built module. `render/` and `app/` own the browser.
  `render/ → fit/` is the legal direction.
- Comments carry the reasoning and the measurement, not a restatement of the
  code. Long docstrings are the style — but they must be **true**. Six separate
  places have now been found asserting the opposite of the code beside them.

## 2. What landed in the last session (4 commits)

| commit | what |
| --- | --- |
| `2328f47` | the scale-honesty cluster — a second ruler, and a caveat sized to what scale actually moves |
| `85b4a9a` | ranking next to the frame on the face, where the scale cancels |
| `3100165` | `docs/SCALE.md` correcting two of its own claims |
| `bc28773` | stage 9 — `scripts/check-reports.mjs`, and the two stale reports it caught |

**Three results went against the plan, and they are the most useful things in
the session:**

1. **`ScaleEstimate.sigma` is NOT under-reported.** `docs/NEXT-SESSION.md` A3
   and `SCALE.md` §2 said the iris "prints 4.72% while its p90 implies 5.72% and
   its median 7.68%". That pair was pooled over 150 rows; per seed the two routes
   imply 4.02/8.58/5.28/8.41/5.28 and 7.73/4.30/5.11/5.32/5.88 — each figure
   reproduces on 2 of 5 seeds and **the sign of the gap between them flips
   across seeds**. Median-of-seeds they agree at 5.28% and 5.32%. It is also
   scored on temple width (where the pipeline residual is 2.94% against 0.89% on
   the eye span) and 20% of its rows are the two hard-coded named extremes. Three
   measurements in the same original session said the sigma was well calibrated
   and none of them reached the doc.
2. **The catalogue ranking's scale sensitivity is the SEAT, not the width
   target.** Dropping the width measure entirely from the parametric catalogue
   changes the top-ranked-frame count *cell for cell* — 16/10/7/17 either way —
   because all five `TEST_FRAMES` default to `frontWidthMm` 138 and the width
   verdict is byte-identical across them. Ranking against a reference fixes the
   width channel exactly (0/60 at every factor) and halves the whole ranking at
   ±2.5%, and **cannot** meet the ±1% gate, because what is left is a contact
   equilibrium landing somewhere else on a resized wedge.
3. **`enroll.txt` was not stale.** It reproduces every accuracy digit at its
   declared seed. `seat.txt` and `track.txt` were, badly.

## 3. The work queue, in priority order

### A. The seat's own scale sensitivity — now the largest fixable defect

This is where B's residual went. The ranking still changes its top pick on
16.7% of faces at ±1% of scale and 25–33% at ±2.5%, and every bit of that is the
seat. The mechanism is named but not measured: **a few percent of face/frame
pairs JUMP between catching the sidewall and sliding**, and for those the
movement is several times the median (`docs/SCALE.md` §2 counts 7/250 jumps at
1%, 28/250 at 2.5%).

What to measure first: which pairs jump, what distinguishes them, and whether
the jump is physical (a real bistability of the contact solve) or numerical (the
solver settling into a different basin). If it is numerical it is fixable and
the ranking gets materially more stable for free.

The probe from last session is a good starting shape —
`scratchpad/rank-scale.mjs` in the session's temp dir, or rebuild it: 5 seeds ×
12 subjects × 15 frames, ground-truth geometry with the factor imposed, reading
`assessFit` measures directly.

*Gate:* whatever the fix, re-run the top-ranked-frame measurement. Absolute
ranking currently sits at median-of-seeds 16.7% (±1%) and 41.7%/50.0% (±2.5%);
against a reference, 16.7% and 25.0%/25.0%.

### B. The tracker's smoothing lag at yaw — measured, unexplained, and in a report

`reports/track.txt` now records it. The solver is unchanged (every
`v2-no-smoothing` number reproduces to a hundredth of a degree); the filter is
2–3× worse in rotation through the middle of the yaw sweep than the pre-port
baseline, while losing no frames at all and cutting jitter by a third.

`PRIOR_MISS_EMA_RATE`'s ledger row already records the motion prior costing
7.1× at 1 Hz ±10° and 18.9× at 1.5 Hz ±8°, and this protocol is a deliberately
fast sweep. **Whether that is the same mechanism here is unmeasured.** It is the
first thing to look at if Shay says the frame lags a turn — and the
2026-08-23 note about the locked-latch default feeling "stuck/choppy" may be the
same thing seen from the wearer's side.

### C. Stage 10 — retire v1, in one act

**Never module by module** — `ar/src/main.js:34-54` imports eight modules by
direct ES import, so piecemeal deletion makes v1 unbootable. Five preconditions,
four met: `check-selfcontained` has run on every commit since stage 1 ✓; the
recorder no longer needs v1 ✓; the pad ground truth is committed ✓; the parity
ledger must be closed with each row naming a test or report line that exists ✗;
**a written side-by-side verdict and screenshot pair per asset ✗ — needs Shay.**

`ar/serve.py` still reaches into `ar_v2` for `/assets/` and `/vendor/`. v1 must
stay bootable until this stage.

## 4. Blocked on Shay — tell him, don't wait silently

- **One physical day for stage 8: eleven weighings and calipers.** This got more
  valuable last session, not less. The comparative width verdict is worth **0.09
  confidence instead of 1.0** purely because nine of ten catalogue assets and all
  five parametric frames declare `dimensionSource: 'assumed'` — the caveat now
  applies to *both* frames in a comparison. One measured number per asset turns
  the tree's only scale-free width claim from nearly worthless into exact.
  Nine of ten assets refuse `derivePads` today and **every refusal is correct**.
- **One scan session.** Set PD → scan → **Save this scan**. Still the only route
  by which a real face reaches an otherwise entirely synthetic harness. It is now
  also the only way to see `ScaleEstimate.disagreementPct` do its job on a real
  wearer, since it needs two rulers and a real PD.
- **A written side-by-side verdict + screenshots per asset**, for stage 10.
- Answered last session, recorded here so nobody re-asks: the ranking reference
  is **the frame currently on screen**, and the PD ask **stays in the Instruments
  drawer** for now.

## 5. Open, measured, unfixed

- **The PD rung's confidence moves the WRONG WAY, and only a second ruler
  catches it.** `sigma = opticianSigmaMm / knownPdMm`, and the wearer TYPES that
  number, so a PD typed 10% high gives a 10.00% scale error at sigma 0.714%
  against 0% error at 0.786% when it is right. Deliberately not patched with an
  invented recall term — `disagreementPct` is the defence and it works — but on
  a scan where no iris resolved, a mistyped PD has nothing checking it at all.
- **The synthetic harness cannot grade a scale estimator.** The null — "assume
  the wearer is template-sized" — beats the shipping iris rung on 5/5 seeds,
  because `generatePopulation` draws from the same N(0,1) the shape prior charges
  against. Any future scale work needs a population **not** drawn from
  `basis.sigma`. `docs/SCALE.md` §3.
- **`model.intrinsics.f` is not a physical focal length** — median 5.45% out,
  worst 43.72%. Accurate only in combination with the solved depth (correlated
  −0.9992). Anything reading it as a lens property is wrong.
- **`derivePads`' `padAngleRad` is biased +8.7°** (navigator) / +6.1° (khronos).
  Harmless today — nothing in `src/` reads it — but it is not what it names.
- **navigator exceeds `PAD_CURVATURE_LIMIT_MM` on 2–3 of 7 subjects** at every
  sample count, against the parametric standard's 1.
- **`worstClearanceMm` is identically 0.000 across 2250 rows** — a check that
  cannot fail. The synthetic population cannot exercise it.
- **The frame is described three times, not two.** `contact.ts`'s
  `clearanceSamples` stays separate deliberately. **The fix is to give the rim a
  dish, not to merge.**
- **`compareToTruth`'s `scaleErrorPct` is on temple width** (`metrics.ts:89`),
  the span furthest from where the iris is read — sd 2.94% there against 0.89%
  on the eye span. A third of what `report:enroll` calls scale error is
  temple-region shape recovery. This is now known to have corrupted a published
  conclusion (§2.1 above); it is worth fixing or renaming.
- **The 6.7 mm PD disagreement has no underlying measurement.** `HANDOFF.md:307`
  asserts it, `telemetry.ts:65` and `main.ts:1465` both cite HANDOFF.md back, and
  `NEXT-SESSION.md` repeated it. Every hit in both workflow journals is an agent
  reading the doc. Do not cite it as evidence.

## 6. Traps — each cost real time

1. **`fixtures.ts`'s two `TEMPLATE_PATHS` are both load-bearing.** `src/testkit/`
   and `dist/src/testkit/` are different depths. Cutting one silently dropped the
   suite from 216 tests to 70. Same pattern in `tests/asset.test.ts`.
2. **Never test Z against an asset's depth midpoint.** Temples run ~140 mm back.
3. **Mirroring a mesh reverses winding**, which inverts every normal.
4. **v1 is in CENTIMETRES, this tree is in millimetres.** Every ported constant
   is ×10.
5. **Comments survive into `dist/`**, so a textual gate on an English word is a
   check that cannot fail. Instantiate the compiled function instead.
6. **`dist/` is never cleaned.** A deleted module's artefact lingers.
7. **The Bash tool chokes on heredocs containing apostrophes or backticks** —
   and it will fail with `unexpected EOF` rather than anything informative. Write
   the Python patch script to a file with `Write`, then run it. This cost time
   again last session.
8. **Do not run `npm run build` from two places at once** — `dist/` is shared.
9. **`ar_v2/serve.py` honours `PORT`**; `.claude/launch.json` has `autoPort`.
   Port 8020 usually has one of Shay's own servers on it — do not evict it.
10. **`generatePopulation(mesh, basis, { count: N })` returns N+2 subjects**, and
    the last two are named extremes with irises hard-coded at **11.10 and
    11.90 mm, identical in every seed**. At small `count` they are 20% of the
    sample and they produce the same two scale errors (+5.41%, −1.68%) in every
    draw. **Any population statistic at small N is contaminated by them** — this
    is what put a 2-of-5-seed number into two documents as fact.
11. **The committed reports are CRLF and a regenerated body is LF**, so a naive
    `diff` shows every line changed and tells you nothing. `tr -d '\r'` both
    sides, and find the body's start by its title line, not by a fixed offset.
12. **`npm run report:<name>` now regenerates AND stamps** through
    `scripts/check-reports.mjs --write`. Do not hand-edit a `[provenance]` line;
    the canary is what makes it mean anything.

## 7. Where the evidence lives

Three investigation workflows persist on disk with every probe's full return
value — read these before re-deriving anything:

```
C:\Users\Shay\.claude\projects\C--Users-Shay-PycharmProjects-lenses-ar-v2\
  59c106cc-a485-4a94-a9f8-5cab29c0f667\subagents\workflows\
    wf_f8992775-8b9\journal.jsonl   asset pipeline (stages 4/5/7)
    wf_a37e9568-955\journal.jsonl   absolute scale (docs/SCALE.md)
  a47a49b8-a8c8-421e-984b-244928c95f51\subagents\workflows\
    wf_5998756d-034\journal.jsonl   the scale-honesty recon, including the
                                    re-check that overturned A3
```

Each line is one agent's result. **The third one is the important one**: it
contains the per-seed breakdown that nobody in the original session computed,
and the three independent measurements saying the sigma was fine.
