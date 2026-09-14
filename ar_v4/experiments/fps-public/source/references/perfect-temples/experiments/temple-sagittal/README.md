# Perfect temples

The owner accepted this version after live use on September 8, 2026 and requested
the checkpoint `perfect_temples`. The original perfecto renderer and app remain
preserved at `/` and commit `31df28eb8ca0c698467fd9bfb16f737f9a74e915`.
This accepted entry point remains isolated in this folder.

Run from `ar_v4`:

```powershell
npm run dev -- --config experiments/temple-sagittal/vite.config.ts
```

Open `/experiments/temple-sagittal/live.html`. Choose Tom Ford or Amber before
opening the camera. Switch Perfecto/Perfect temples live, or use **Hold problem frame**
to stop the camera/tracker and compare the exact last displayed source/detection
pair. **Download diagnostic** explicitly saves that pair, both outputs, the
experimental branch, original poses/surface and actual protection/drop metadata
locally. Close the held frame to start a new session. Recording and holding cannot
overlap. Recorded replay restores its actual saved configuration.

The candidate applies a smooth posterior Y curve. Its start is 15 mm behind the
rear lens plane; the default maximum lowering is 20 mm at the accepted cap.
Original X/Z, optical/proximal geometry, asset files, attachment, nose surface,
camera, native visibility policy, original Z cutoffs and 15 mm end fade stay fixed. Downward
head and camera directions must agree; upward and larger yaw views receive zero
drop. This is an authored preview convention, not a physical hinge or measured
wearer fit. Analytic normals/tangents follow the curve.

Two independent native renderers produce untouched perfecto and the experimental
branch. Final integer pixel selection uses the branch only inside bounded arm
corridors, outside complete depth-independent optical/proximal bounds and a
central eye/nose guard. Those protected pixels and all pixels outside the arm
corridors are copied from perfecto. Invalid masks, missing replay protection or
branch failures fall back to perfecto. Source pixels seen through lenses and
reflections retain their baseline appearance. Broad protection can limit the
correction or produce a visible join; inspect held and live views. This prototype
uses extra GPU/readback work and has no real-time performance claim.

The locally generated, Git-ignored `comparison.html` contains matched native before/after images. Its recorded and
synthetic sections are explicitly separate. Recordings reach about 20° and do not
reproduce the supplied steep screenshot. Synthetic 40–50° views demonstrate the
mechanism only. Acceptance comes from the owner's subsequent live review;
it does not establish performance across different faces or hairstyles.

Validation commands:

```powershell
npm test
npx tsc --noEmit -p experiments/temple-sagittal/tsconfig.json
node --test experiments/temple-sagittal/*.test.ts
npx playwright test --config experiments/temple-sagittal/playwright.config.ts
node experiments/temple-sagittal/replay.mjs
node experiments/temple-sagittal/synthetic-render.mjs
```

The evidence replay/audit/gallery commands require preserved private `.recovery/`
inputs; they are optional local workflows. The single private paired-fixture
browser test skips when that recording is absent. Other browser flows use the
checked-in synthetic camera fixture. No private evidence gallery or recordings
are committed. Generate the gallery locally with `make-review.mjs` and the two
complete recorded/synthetic report paths.

Run only one heavy browser job at a time. Replays verify original source hashes
and array indices, original detection/pose identity, unchanged reconstructed
perfecto surface, independent native baseline equality, hidden optical footprint
coverage, original nasal ROIs, final pixel preservation and saved replay/failure
ownership. Evidence and preservation receipts are under
`.recovery/temple-rethink-2026-09-08/`; originals and earlier archives are read-only.
No parent Python, Railway, linked-worktree or unrelated model_studio changes are
part of this checkpoint. Hair is not segmented; see the main handoff for limits.
