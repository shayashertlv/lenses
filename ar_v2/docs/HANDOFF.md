# Handoff: finish making ar_v2 the AR system and retire v1

You are picking up a migration that is 2 of 10 stages done. Read this whole file
before touching anything. It is written to save you the day I spent finding
things out, and every number in it was measured on this tree rather than
recalled.

---

## 0. The task, in the owner's words

> "implement v2 into the main pipeline instead of v1. make v2 the main system
> and implement the rest around it (the rendering, etc). then we'll have a solid
> state to work on. after implementing v2 you can get rid of v1 and outscope
> view the ar project and review it and clean it."

He has since asked, plainly, whether v2 is the engine yet. **It is not**, and he
is not interested in process detail — he wants a real pair of glasses rendering
on a real face. When you report, lead with what he can see, not with what you
verified.

---

## 1. What this repository is

`C:\Users\Shay\PycharmProjects\lenses`. Three things that do not depend on each
other as much as their names suggest:

- `UI/`, `face_analysis/`, `optimal_configuration/`, … — a Python product
  (`Procfile`: `web: python -m UI.app`). **It does not reference either AR tree.**
  No imports, no templates, nothing. Do not go looking for an integration point;
  there isn't one. "The main pipeline" means the AR project itself.
- `ar/` — **v1**. JavaScript, 16,407 lines of `src`. The AR try-on that works
  today. It has the rendering: eleven real glTF eyewear assets, materials, lens
  glass, a light probe. Its estimator is the one being replaced.
- `ar_v2/` — **v2**. TypeScript, 222 tests, three mechanised gates. A much
  better estimator (scan once, track against the scan, seat by contact physics)
  that draws only a **parametric** frame — tubes and ellipses — and until this
  week had no way to read an asset at all.

Both are standalone static apps, each with its own `serve.py`. You open one page
or the other.

---

## 2. Branch and PR state

- Working branch: **`ar-v2-primary`**, off `ar-tryon`. Working tree clean.
- Three commits on it: `da2938e` (pure rename), `ec9c315` (custody wiring),
  `bf1f84e` (mesh reader + ground truth + derivePads rewrite).
- Two stacked PRs are open and **must not be disturbed**: **#2** (`main` ←
  `ar-v1`, 51,125 lines — v1 plus the vendor-untracking) and **#3** (`ar-v1` ←
  `ar-tryon`, 31,997 lines — ar_v2). `ar-v2-primary` sits on top of #3's head.
- `gh` is **not installed**; the repo is public, so the GitHub API works with the
  token in the Windows credential manager. To use it without printing it:
  `printf "protocol=https\nhost=github.com\n\n" | git credential fill | <your script>`

---

## 3. House doctrine — match it or the review will be worse than useless

This tree has an unusually strict epistemic culture. Violating it is the main
way to do damage here.

- **"A check that cannot fail is a bug."** Before you let a new gate go green,
  demonstrate it red. Sabotage the thing it guards, watch it fail, restore.
- **Every exported constant needs a provenance row in `docs/CONSTANTS.md`**
  (`measured` / `derived` / `stated` / `published` / `physics` / `assumed`).
  `scripts/check-constants.mjs` fails the build otherwise. **Trap: it only
  catches constants with no row. An orphaned ROW with no constant passes
  silently** — so when you delete a constant, sweep the ledger by hand.
- **Adopt on ≥4 of 5 independent seeds**, report median-of-seeds with the
  per-seed spread. A single synthetic draw is a coin flip; this tree has
  reversed a verdict twice by forgetting that.
- **Results that went against the change stay in the headline**, not a footnote.
  Read any recent commit message for the register.
- **The isolation boundary is mechanised.** `core/ enroll/ track/ fit/ detect/
  testkit/` must run in Node with no browser. `scripts/check-isolation.mjs`
  actually `import()`s every built module. `render/` and `app/` own the browser.
- `render/convert.ts` is the **only** place CV convention (+Y down, +Z forward)
  becomes GL. Face space and frame space are +Y up, +Z out of the face and need
  no conversion.
- Comments here carry the reasoning and the measurement, not a restatement of
  the code. Long docstrings are the style, not clutter — but they must be
  **true**, and a 2026-08-24 review found four that asserted the opposite of the
  code beside them.

Verify everything with:

```bash
cd ar_v2 && npm test
```

That runs build → isolation → constants → self-contained → 222 tests. All four
must pass. Do **not** run `npm run build` concurrently from two places; `dist/`
is shared.

---

## 4. Read these first

- `docs/ARCHITECTURE.md` — the shape of v2. **Its "what is not built" section is
  stale**; the frame IS drawn (parametrically).
- `docs/OPEN-QUESTIONS.md` — every number the system is guessing.
- `docs/CONSTANTS.md` — the provenance ledger.
- `assets/glasses/ground-truth.json` — the only independent pad measurement in
  either tree. Read its `uncertainty` and `corroboration` fields.
- Claude's memory directory for this project has files
  `ar-v2-becomes-the-system`, `ar-v2-review-2026-08-24`, `ar-v2-perfect-campaign`
  — they carry context that is not in the repo.

---

## 5. What is already done

### Stage 1 — custody (`da2938e`, `ec9c315`)

`assets/` (77 MB, 15 tracked files), `scripts/fetch-vendor.mjs` and
`ATTRIBUTION.md` moved from `ar/` to `ar_v2/` as a **pure rename** (17 files,
R100, zero insertions). `vendor/` moved on disk (it is untracked; fetched and
SHA-256 verified by `node scripts/fetch-vendor.mjs`).

**The borrow was inverted, not removed.** `ar_v2/serve.py` serves only itself;
`ar/serve.py` gained the mirror-image mapping and now reaches into `ar_v2` for
`/assets/` and `/vendor/`. **v1 must stay bootable until the very end** — it is
the only working reference for what a rendered frame should look like, and
stage 10's precondition is a written side-by-side verdict.

New standing gate: `scripts/check-selfcontained.mjs` (text pass + physical
pass), wired into `npm test`.

### Stage 3 — the asset bridge (`bf1f84e`)

- **`src/fit/mesh-io.ts`** — a headless GLB reader. No three.js, so `fit/` stays
  on the right side of the boundary. Deliberately narrow: measured across the
  catalogue there is **no Draco, no meshopt, no quantisation, no sparse
  accessors and no external buffers**, so anything else throws rather than
  guessing.
- **`assets/glasses/ground-truth.json`** + `scripts/extract-pad-truth.mjs`
  (has a `--check` mode). navigator **13.843 mm / 0.6031 rad**, khronos
  **10.635 mm / 0.2927 rad**.
- **`derivePads` rewritten** around the criterion that defines a pad: *two
  surfaces facing each other across the midline, leaning back toward the
  wearer.* New constants, all with ledger rows: `PAD_INWARD_COS` 0.35,
  `PAD_REAR_COS` 0.04, `PAD_MIN_FACES` 20, `PAD_FRONT_FRACTION` 1/3.

**Result: navigator went from 39% of samples on the real pads and +6.1 mm of
separation error, to 100% and +0.42 mm.**

---

## 6. Traps — each of these cost real time. Do not re-discover them.

1. **`fixtures.ts` `TEMPLATE_PATHS` has two entries and both are load-bearing.**
   `src/testkit/` and `dist/src/testkit/` are **different depths** because the
   build adds a level. I cut them as a redundant "fallback ladder" and the suite
   silently dropped from 216 tests to 70.
2. **Never test Z against an asset's depth midpoint.** The temples run ~140 mm
   back, so a pair of glasses' depth midpoint lies *behind the wearer's ears*
   and every nose pad on earth is in front of it. A guard written that way
   refuses the entire catalogue. Use `PAD_FRONT_FRACTION`.
3. **Mirroring a mesh reverses triangle winding**, which inverts every computed
   normal — after which the inward-facing test cheerfully finds the *back* of
   each pad and returns a plausible number. `derivePads` now has a signed-volume
   guard (all ten assets measure positive, 5.2e3 to 4.2e7; all ten
   mirrored-without-rewinding measure negative). When you build flipped
   fixtures, **re-wind the indices** or you are testing the wrong thing.
4. **`padSeparationMm` is not a physics input.** `contact.ts` reads `padSamples`
   and `padNormals` directly. The field is consumed only by the renderer's rim
   sizing, the occlusion instrument and the seat report. It means the separation
   of the two pads' **contact-sample centroids** — whole-mesh centroids give
   18.48 mm on navigator where the contact faces give 13.84.
5. **The lens aperture wall faces the midline exactly as squarely as a pad
   does** — it is the inside of a hole. What separates them is the rearward
   lean: aperture and frame front read mean nz of **exactly 0.000**, authored
   pads read **−0.106**.
6. `check-constants.mjs` cannot see an orphaned ledger row (see §3).
7. The Bash tool on this machine chokes on large heredocs. Write Python patch
   scripts to a file and run them, or use the Write/Edit tools.

---

## 7. The remaining eight stages

Stage 2 must land before you trust any measurement taken after it. Stages 4–7
are the agent-executable spine. **Every stage must leave a working system.**

### Stage 2 — baseline the real face *(BLOCKED ON SHAY)*
Needs one recording session. Also rewrite the telemetry recorder:
`ar/tests/record-telemetry.js` imports ten v1 modules, eight of which get
deleted — it is a **~150-line rewrite** against `detect/mediapipe.ts` and
`app/sources.ts`, not a move, and it is **impossible after v1 dies**. While v1
still lives, capture one fixture with an ID-1 card in frame and a measured PD in
its header; that settles a 6.7 mm PD disagreement across three captures of one
person.
*Gate:* replay asserts within-capture PD spread ≤7% on ≥4 of 5 seeds, and a
committed cross-capture figure pinned to ±1 mm.

### Stage 4 — one surface, one description
`frame-geometry.ts` (renderer) and `report-occlusion.ts`'s `buildFrameSamples`
(instrument) describe the same object independently, are documented as twins
that must agree, and a review found them 4 mm apart at the bridge. A mesh-based
frame breaks the twin by construction. Collapse both onto one `frameLayout()`.
*Gate:* the twin-agreement check goes **vacuous** the moment both read one
buffer, so it must be replaced, not deleted — every part the renderer emits must
have a sample set (red today on endpieces and lens discs), and the clearance
term must be shown *able to fire*.
*Expect `tsc` to go red at ~6 mechanical sites. That is the gate working.*

### Stage 5 — navigator end to end, headless. **HARD-STOPPED AT TWO WEEKS.**
Load navigator through `mesh-io`, build a real `FrameAsset`, run `solveSeat`.
**At two weeks, derivation is pre-agreed to demote from *producer* to
*checker*:** each asset declares its pad geometry in the catalogue and
`derivePads` must either agree within 1 mm or refuse the asset. The decision is
pre-agreed precisely so it is not made under sunk cost. That fallback unblocks
stages 6–10 unchanged.
*Gate:* ≥90% of returned samples land on author-named pad parts. **navigator is
at 100% today; khronos is at ~48% and +2.24 mm.** One of the two gradeable
assets clears the bar and nine of eleven cannot be graded at all.

### Stage 6 — the back of the head, before the real temple
v2 has no skull, so at yaw a temple has nothing to hide behind. Port v1's
`head.js` (396 lines): the face mesh's own boundary is a closed 36-vertex loop
lofted to an occipital pole, sharing the rim's vertices so there is no seam.
Ears are an **open dish per side, never a ball** — v1 records that a solid ear
fills the crevice the temple runs through.
*Gate:* non-vacuity as a rule — the extended instrument must show a large
temple-corridor error with the proxy **disabled** and a small one enabled, on
the same face and frame.

### Stage 7 — navigator on a real face, rendered *(the one he wants to see)*
The renderer for a real mesh: materials, lens glass, an environment map, one
shadow-casting key. There is currently **no shadow map, no environment, no
tone mapping** in `scene.ts`. Lens identity is in the material names on 7 of 10
assets (`Lens_Prescription_Glass`, `Lens_Gradient_Rx`, `lens_interior`, …).
*Gate:* mechanised half in `app.test.ts`'s compiled-source style — `scene.ts`
must set `outputColorSpace`, tone mapping, a PMREM environment and
`shadowMap.enabled`. The other half is a screenshot pair and his verdict.

### Stage 8 — the other nine, and an honest refusal *(PARTLY BLOCKED)*
Needs one physical day: eleven weighings and calipers where a real pair exists.
*Gate:* the run must **refuse** pads on `shield-golden` (a wrap with no distinct
pads) and on the acetate saddles. **A run where all eleven produce pads is a run
that failed.** Every asset carries either a measured or a declared geometry, and
says which.

### Stage 9 — re-baseline the reports
Every checked-in report describes a configuration that no longer exists.
*Gate:* `scripts/check-reports.mjs` hashing each report's declared inputs into
its header, wired into `npm test`.

### Stage 10 — retire v1 in one act, then read it cold
**Never module by module** — `ar/src/main.js:34-54` imports `frame.js`,
`smoothing.js`, `occluder.js`, `nose.js`, `fit.js`, `layout.js`, `stab.js` and
`ui.js` by direct ES import, so piecemeal deletion makes v1 unbootable, and
stages 7–8 need both trees serving.
Five preconditions, all checkable: (1) `check-selfcontained` has run on every
commit since stage 1; (2) the recorder runs from its new home and has been used;
(3) a written side-by-side verdict and screenshot pair exists per asset;
(4) the pad ground truth is committed (**done**); (5) the parity ledger is
closed, each row naming a test or a report line that exists.
Then the cold review of the whole AR project.

**Honest total: about four months.** Roughly 77% of v1 (12,654 of 16,407 lines)
is superseded or dropped by construction — note that `frame.js` (2,550 lines) is
v1's *tracking* pipeline, **not** an asset loader; the loader is `models.js`
(995 lines) and it is worth reading before you write stage 7.

---

## 8. Open questions I could not settle

- **`aviator-amber` refuses while `aviator-tortoiseshell` derives**, and those
  two assets differ essentially only in texture. The upside-down guard is
  fragile on photogrammetry scans and I do not know why. Diagnose this before
  trusting any refusal on a scanned asset.
- **khronos sits at ~48% precision.** Its frame front is sculpted rather than
  flat and carries genuinely rearward-leaning faces of its own.
- `meshy-glasses.glb` is **one fused mesh, 106k triangles, no part names, no
  extensions** — the hard case for every part-based approach.
- The catalogue is ~1.05M triangles total and 36 MB of embedded texture. Fine
  for the GPU; **anything CPU-side that walks all vertices per frame is not.**

---

## 9. How to start

```bash
cd C:\Users\Shay\PycharmProjects\lenses\ar_v2
git checkout ar-v2-primary
node scripts/fetch-vendor.mjs      # if vendor/ is absent
npm test                            # expect 222/222 and three green gates
```

Then read `src/fit/frame-asset.ts`'s `derivePads` and
`scripts/extract-pad-truth.mjs` — between them they contain most of what stage 5
needs to know.

If you can only do one thing, do **stage 5 and then stage 7**: that is the
shortest path to a real pair of glasses on a real face, which is the only thing
the owner has actually asked to see.
