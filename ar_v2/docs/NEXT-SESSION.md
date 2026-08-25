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
npm test                            # expect 256/256 and three green gates
```

Read, in this order: `docs/HANDOFF.md` (the ten-stage migration and its traps),
`docs/SCALE.md` (the scale investigation — do **not** re-derive it), then
`docs/ARCHITECTURE.md` and `docs/CONSTANTS.md`.

## 1. House doctrine — match it or the review is worse than useless

- **"A check that cannot fail is a bug."** Before letting a new gate go green,
  sabotage what it guards, watch it go red, restore. Show the red/green table.
- **Adopt on ≥4 of 5 independent seeds**, median-of-seeds with the per-seed
  spread. A single draw is a coin flip; this tree has reversed a verdict three
  times by forgetting it (`fieldPriorScale`, the seat, and fusion in §3 below).
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
  code. Long docstrings are the style — but they must be **true**. Four separate
  places have now been found asserting the opposite of the code beside them.

## 2. What landed in the last session (5 commits)

| commit | what |
| --- | --- |
| `586a2a2` | stages 5+7 — `navigator.glb` loads, derives a measured layout, seats, renders with materials, environment and a contact shadow |
| `b933490` | stage 4 — `fit/frame-layout.ts`; the frame is described once |
| `a31ad9d` | stage 6 — `core/head.ts`, the head proxy |
| `3ad348d` | stage 2's recorder — `enroll/telemetry.ts` + "Save this scan" |
| `3a8a799` | `docs/SCALE.md` and the card cleanup |

Six of the ten stages are done. **Read `docs/HANDOFF.md` §5 for the findings** —
especially that the ear rest is the temple's **bend**, not its tip, and that the
occlusion instrument was blind to the missing head because both arms rasterised
with the same face mesh.

## 3. The work queue, in priority order

### A. The scale-honesty cluster — three small fixes, one file each

All three are **defects**, all measured, none require new physics. Do them
together; they are one idea. Evidence: `docs/SCALE.md` §2.

**A1. Every confidence in the tree is blind to scale error by construction.**
`scaleTrust` reads `model.scale.sigma` and never the factor, so a wearer whose
true HVID is 11.10 mm carries a 5.4% error at *exactly* the same confidence as
one the 11.70 mm ruler fits. Nothing can notice. The only signal that can see it
is a disagreement between two rulers.

**A2. The scale caveat is on the wrong verdicts.** Only `width` and `vertex`
carry `scaleConfidence` — and **vertex is the least scale-sensitive claim
measured** (0.035 mm per 1%, against a 4 mm band). `height`, `panto`, `depth` and
`load` carry none, and they move: descent 0.19 mm and panto 0.16° per 1%. Move
it off vertex and onto the ones that move.

**A3. Every rung under-reports its own sigma.** The iris prints **4.72%** while
its p90 implies **5.72%** and its median implies **7.68%**. That is the number
lens ordering gates on. Fix the reported sigma, or say in the note why it is a
lower bound.

**A4 (the one with teeth).** `solveScale` returns `disagreementPct` and it is
`null` on every path since the card rung was deleted. Wire it to carry
**PD-against-iris**, and when the gap exceeds ~2%, say so out loud rather than
averaging it into a symmetric interval. The iris error is **+2.59% signed** —
one-sided — and the sigma is two-sided.

*Red recipes:* A2 — assert `vertex` does NOT carry scaleConfidence and `panto`
does; A4 — a synthetic scan with a known PD 5% from the iris factor must produce
a non-null disagreement and a note.

### B. Make the ranking comparative — the largest scale defect that is fixable

`rankCatalogue` (`src/fit/score.ts:363`) scores every frame against a **fixed
metric target** (`FRAME_TO_FACE_WIDTH` = 0.90). Measured: **12% of faces get a
different top recommendation at 1% scale error**, and the promoted frame is
genuinely worse, not a tie-break. Nothing prop-free delivers 1%.

The physics says this is avoidable. Scale is a common factor:

    widthDelta(A) − widthDelta(B) = W_A − W_B          exact, scale cancels
    widthDelta(A)                 = W_A − 0.90 × F     carries the whole error

Both frames' true widths are known to the millimetre. **"This pair is 4 mm wider
than that one on you" is exact; "this pair is 4 mm too wide for you" is not.**
Rank against a reference the wearer has confirmed rather than an absolute target
and the scale cancels out of the ordering.

This is a real change to `score.ts` and it needs a design decision from Shay
about what the reference is (their current frame? a frame they liked in the
session?). **Ask before building it.**

*Gate:* re-run the top-ranked-frame-changes measurement at ±1% and ±2.5%; the
comparative ranking must move materially fewer than 6/50 and 16/50.

**What this does NOT license:** resizing each frame to fit the scanned
silhouette. Shay proposed it and it breaks the product — if every frame is scaled
to fit, every frame fits, a 145 mm and a 130 mm frame render identically, and the
one question a customer is buying an answer to becomes unanswerable. The size
mismatch is not noise in the render; **it is the signal**. `docs/SCALE.md` §5.

### C. Stage 9 — re-baseline the reports, and gate them

`reports/occlusion.txt` was regenerated last session. `enroll.txt`, `seat.txt`
and `track.txt` all describe a configuration that no longer exists (the head
proxy, the frame-layout collapse and the mesh-backed frame all landed since).

Then build `scripts/check-reports.mjs`: hash each report's declared inputs into
its header and fail the build when they drift. Wire into `npm test`.

Generate reports **without the npm banner** — `npm run report:X` prefixes its
output and the committed files have no banner:

```bash
npx tsc -p tsconfig.json
node -e "import('./dist/src/testkit/report-seat.js').then(m=>console.log(m.runSeatReport()))" > reports/seat.txt
```

### D. Stage 10 — retire v1, in one act

**Never module by module** — `ar/src/main.js:34-54` imports eight modules by
direct ES import, so piecemeal deletion makes v1 unbootable. Five preconditions,
four now met: `check-selfcontained` has run on every commit since stage 1 ✓; the
recorder no longer needs v1 ✓ (that dependency is retired — `enroll/telemetry.ts`
touches nothing in v1); the pad ground truth is committed ✓; the parity ledger
must be closed with each row naming a test or report line that exists ✗; **a
written side-by-side verdict and screenshot pair per asset ✗ — needs Shay.**

`ar/serve.py` still reaches into `ar_v2` for `/assets/` and `/vendor/`. v1 must
stay bootable until this stage.

## 4. Blocked on Shay — tell him, don't wait silently

- **One scan session.** Set PD → scan → **Save this scan**. That fixture is the
  only route by which a real face reaches a harness that is otherwise entirely
  synthetic, and the only thing that can settle the 6.7 mm PD disagreement across
  three captures of one person.
- **One physical day** for stage 8: eleven weighings and calipers. Nine of ten
  catalogue assets refuse today and **every refusal is correct** — a run where
  all eleven produce pads is a run that failed.
- **A product call** on §3B's reference, and on whether the PD ask moves from a
  buried Instruments button into the scan flow (0.79% against the iris's 4.7%,
  and it is not a prop — it is a number on their prescription).

## 5. Open, measured, unfixed

- **The synthetic harness cannot grade a scale estimator.** The null — "assume
  the wearer is template-sized", no ruler — beats the shipping iris rung on 5/5
  seeds, because `generatePopulation` draws from the same N(0,1) the shape prior
  charges against, with the template 0.27% from the population mean. Any future
  scale work needs a population **not** drawn from `basis.sigma`. `docs/SCALE.md` §3.
- **`model.intrinsics.f` is not a physical focal length** — median 5.45% out,
  worst **43.72%**, even with `canSolveIntrinsics` true. It is accurate only in
  combination with the solved depth (they are correlated −0.9992 and the iris
  ruler consumes only the ratio). Anything reading it as a lens property is wrong.
- **`derivePads`' `padAngleRad` is biased +8.7°** (navigator) / +6.1° (khronos):
  the rearward gate discards the forward-leaning 42% of the pad by face count.
  Harmless today — nothing in `src/` reads it; `contact.ts` uses the mean pad
  normal — but it is not the quantity it names.
- **navigator exceeds `PAD_CURVATURE_LIMIT_MM` on 2–3 of 7 subjects at every
  sample count**, against the parametric standard's 1. Its pads are genuinely
  more curved than the flat rectangle 0.9 was set against. New open question.
- **`worstClearanceMm` is identically 0.000 across 2250 rows** — a check that
  cannot fail. The synthetic population cannot exercise it.
- **The frame is described three times, not two.** `contact.ts`'s
  `clearanceSamples` stays separate deliberately — collapsing it fires the
  clearance term at 19–20 mm on every catalogue frame, because the drawn rim is a
  flat ellipse with no dish and no pantoscopic tilt. **The fix is to give the rim
  a dish, not to merge.** See that function's header.
- **`compareToTruth`'s `scaleErrorPct` is on temple width** (`metrics.ts:89`),
  the span furthest from where the iris is read. The residual is sd 2.94% there
  and 0.89% on the eye span — a third of what `report:enroll` calls scale error
  is temple-region shape recovery.

## 6. Traps — each cost real time

1. **`fixtures.ts`'s two `TEMPLATE_PATHS` are both load-bearing.** `src/testkit/`
   and `dist/src/testkit/` are different depths. Cutting one silently dropped the
   suite from 216 tests to 70. The same pattern is in `tests/asset.test.ts`.
2. **Never test Z against an asset's depth midpoint.** Temples run ~140 mm back,
   so a frame's depth midpoint is behind the wearer's ears and every nose pad is
   in front of it.
3. **Mirroring a mesh reverses winding**, which inverts every normal — after
   which the inward-facing test finds the *back* of each pad and returns a
   plausible number. Re-wind indices when you build flipped fixtures, or you are
   testing the volume guard instead of the thing you meant.
4. **v1 is in CENTIMETRES, this tree is in millimetres.** Every ported constant
   is ×10. It has already bitten the shadow frustum and the head loft, and the
   symptom is a feature that silently does nothing rather than an error.
5. **Comments survive into `dist/`** (`tsconfig` sets no `removeComments`), so a
   textual gate on an English word is a check that cannot fail. Instantiate the
   compiled function instead — `tests/scene.test.ts` and `tests/layout.test.ts`
   show the pattern.
6. **`dist/` is never cleaned.** A deleted module's artefact lingers and will
   still resolve at runtime.
7. **The Bash tool chokes on large heredocs and on apostrophes inside them.**
   Write Python patch scripts to a file and run them, or use Write/Edit.
8. **Do not run `npm run build` from two places at once** — `dist/` is shared,
   and a background report run reads it.
9. **`ar_v2/serve.py` honours `PORT`**; `.claude/launch.json` has `autoPort`.
   Port 8020 usually has one of Shay's own servers on it — do not evict it.

## 7. Where the evidence lives

The two investigation workflows persist on disk with every probe's full return
value — read these before re-deriving anything:

```
C:\Users\Shay\.claude\projects\C--Users-Shay-PycharmProjects-lenses-ar-v2\
  59c106cc-a485-4a94-a9f8-5cab29c0f667\subagents\workflows\
    wf_f8992775-8b9\journal.jsonl   asset pipeline (stages 4/5/7)
    wf_a37e9568-955\journal.jsonl   absolute scale (docs/SCALE.md)
```

Each line is one agent's result. They cost ~4.5M subagent tokens between them.
