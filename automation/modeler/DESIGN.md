# Modeler — an AI runtime author writes Blender construction programs (experiment, 2026-09-26)

Hypothesis under test: a photo-to-AR glasses pipeline whose geometry is written, observed and revised by an AI
acting as a full Blender modeler beats the fixed constructions (extruded front plate, donor temples) of the BSA
route on the visible failures the owner and Astra's look stage both called geometry problems (flat rims, wrong
temples, missing hardware).

This folder is an isolated development path. It imports from `bsa/` and `reconstruction/` (numeric image tools,
camera fitting, the contract GLB writer, the AR harness driver). Changes to them (the translucent export and
contract, the AR runtime's translucent twin) are made there, with their own tests. It changes nothing in the
deployed application.

## The loop

```
photos -> intake (mattes, lens outlines, measurements, mm)          modeler/intake.py
       -> author turn: request package -> ONE decision              modeler/author.py (+ author_astra.py)
       -> submit_program: modules -> Blender (fresh process)        modeler/candidates.py, worker.py, blender/harness.py
       -> parts (mm) -> contract GLB (metres, bridge origin)        modeler/export.py  (bsa.export.write_glb, bsa.contract.check)
       -> observe: fitted photo cameras, metrics, renders, AR       modeler/observe.py (bsa.cameras, bsa.raster, bsa.archeck)
       -> next turn sees the observations; incumbent = best score
       -> finish -> independent evaluator -> status -> manifest     modeler/evaluate.py, job.py
```

Run: `python -m modeler.job --request <request.json> --output data/modeler/jobs/<name> [--author package|scripted]`.
The request lists image paths, optional view labels, optional dimensions, limits and the author driver
(`modeler/request.py`). No product id is interpreted anywhere in the code.

## Frames and units

* Authoring: 1 Blender unit = 1 mm; +X = viewer's right in the front photo (the wearer's left), +Y up, +Z toward the
  front camera; lenses face +Z; temples run toward -Z (the BSA MODEL frame).
* Export: `bsa.export.write_glb` — metres, +Y up, +Z front, origin at the bridge underside (derived from the front
  parts; an author declaration is used only when it agrees within the contract tolerance), identity node
  transforms, nodes frame / temple_R / temple_L / lens_R+lens_L or lens_C, lenses as +Z front sheets with the
  canonical `LENSES_lens_appearance` descriptor (from the author's `gl.lens_optics`).
* The photo camera model (`reconstruction.camera.Camera` in a `NormFrame`) is reproduced exactly by a Blender
  camera (`harness.camera_object`), verified by `tests/test_modeler_camera.py` (silhouettes agree within 1 px).

## Part identities and targeted repair

Every visible object is registered with a part (`frame`, `temple_R`, `temple_L`, `lens_R`, `lens_L`, `lens_C`) and a
component label. Programs are sets of modules executed in a fixed order (`setup, frame, lenses, temples, hardware,
materials, finish`); a submission replaces named modules and inherits the rest byte-identical from a base
candidate, so a repair of the temples cannot disturb the front. Each candidate is built by a fresh Blender process
into its own folder (program, build result, `candidate.blend`, parts arrays, GLB, observations); nothing is ever
edited in place.

## Evidence and leakage

Intake copies the originals immutably (sha256), computes the matte and lens proposal per view (BSA S0), the
symmetrised lens outlines, rim widths and frame measurements of the front photo (BSA S2) and side outlines scaled
by the front height, all in mm of the nominal scale (`scale.source` records `stated_in_request` or
`assumed_default` with its uncertainty). Fit views (front, back, left, right) condition the author, the cameras and
the incumbent score. Held-out views (default `angled`) are copied but never placed in an author package; they are
fitted and measured into `candidates/*/heldout/` for the evaluator and the report only. No donor mesh, previously
solved asset or texture enters a job unless declared in the request (`donor`), and none is used by default.

## Observations per candidate

* Camera per fit view: `bsa.cameras.ViewFit` (multi-start silhouette fit, coarse/fit/final levels), warm-started
  from the previous candidate's camera; the same procedure for every candidate.
* Metrics: silhouette IoU, symmetric contour mean/p95 in px and mm and % of width per view; the VISIBLE lens
  region (front-photo pixels whose first hit is a lens face) vs the measured lens outlines. Until 2026-09-26 midday
  the lens metric rendered the lens parts alone, which penalized a lens edge tucked under the rim; job `vb-run1`
  (a long-running process started before the fix) keeps the old definition for all its candidates, `vb-astra1` and
  later jobs use the visible-lens definition. Cross-job lens numbers are therefore not comparable; baselines are
  measured with the current code.
* Renders (EEVEE, Standard view transform): photo-matched textured render per fit view with the fitted camera,
  composited as [photo | render | overlay]; neutral clay renders (front, right, top, back, three-quarter); textured
  front and three-quarter; requested extra views on demand.
* The exported GLB in the actual TryOnRenderer through `bsa.archeck.run` (front, yaw 35, roll 25, asset-back).

Incumbent rule: valid = contract ok AND `runtime_compatible` AND >= 1 optical mesh detected; score = weighted mean
contour error (mm) over the fit views plus the lens outline error; lower wins. The delivered candidate is the
author's choice when it is valid and within 25 % of the incumbent's score, else the incumbent.

## Run policy

`limits`: `max_turns` (default 10), `blender_time_limit_s` (300), `wall_time_limit_min` (180), `stagnation_turns`
(3): after that many submissions without a >= 3 % score improvement the author is told to try a materially different
construction or finish; after twice that the host stops. `request_views` turns count as turns.

## Statuses

`accepted` (a job awards it on its own only with a calibrated automatic visual bar whose owner verdicts also cover
every tag of the delivered asset — its kind from the intake, `translucent` / `mirrored` from its materials
(`modeler.tags`, coverage in `calibration.json`); the owner can award it after the run by judging the delivered asset
in the live try-on, `python -m modeler.owner_verdict`, which records the verdict against the asset's digest, keeps
the evaluator's verdict beside it and appends the pair to `data/modeler/calibration/owner_verdicts.jsonl`, the data
the automatic bar must reproduce),
`best_effort` (valid asset, measured and evaluated; provisional thresholds and evaluator verdict recorded),
`quality_unverified` (valid asset, evaluation missing), `execution_failed` (`inconsistent_inputs` was removed
2026-09-29: its input flag `lens_count_mismatch` had no producer, so the status was unreachable). The manifest
(`manifest.json`) names the exact delivered GLB (sha256), mounting metadata, scale provenance, measurements,
the evaluator's verdict and every candidate.

## Author drivers

* `package`: file exchange per turn (`turns/turn-NNNN/request.json`, `images/`, `response.json`); a freshly
  started external agent answers each turn; its transcript is stored beside the turn for the provenance audit.
* `astra`: `gpt-6-astra` over the OpenAI Responses API, three strict function tools, `tool_choice: required`,
  `store: false`, one reservation per call in a shared ledger capped by the authorization (1..10); receipts and
  raw responses kept; no retries. Constructed only with an explicit credential and cap. First exercised
  2026-09-26 (vb-astra1); see RESULTS.
* `scripted`: replay, tests and dry runs.

## Interventions during the measured runs (2026-09-26)

Every change below is a generic host fix found by watching the runs; no program, mask, parameter or candidate choice
of any product was edited by the development agent. Long-running job processes keep the code they loaded, so each
fix took effect in later jobs (or, for the Blender-side files, in the next build of a running job).

| found in | defect | fix |
|---|---|---|
| vb-astra1 turn 0 | the author package showed `outline_mm_64` but the evidence dict inside Blender only had `outline_mm` (KeyError) | `intake.augment_evidence` adds the downsampled keys to `evidence.json`; applied to both running jobs' evidence files |
| vb-run1 turn 0 | the package driver read a half-written template response | `PackageDriver._stable_read`: quiet-time, JSON validity and placeholder checks before parsing; module files `{"file": ...}` accepted |
| vb-astra1 end | calls refused by the exhausted call ledger were charged their worst-case estimate | charge 0 when no reservation was made; ledger corrected with explicit reversal rows (true spend 4.75 USD) |
| vb-astra1 finalize | a refused attempt's `api/request.json` blocked a fresh evaluator call as a "changed replay" | `_attempt_dir`: unsent attempts yield to `api-N`; sent attempts keep the no-retry replay rule; `--astra-usd-ledger` lets a second call ledger charge the same dollar cap; finalize reuses a stored evaluation |
| vb-astra1 turn 3 | flat white clay at high zoom carried no shape cues | darker clay material, dimmer ambient for clay renders; textured ambient reduced |
| vb-astra1 turns 4/7 | non-finite normals from degenerate triangles | export drops zero-area triangles, but only from objects that are already open (dropping from a closed object opened it: found by the Miu author in miu-astra1 turn 3, job stopped and restarted as miu-astra2) |
| miu-astra2 turn 0 | zero-area ear triangles from Blender's ear clipping of n-gon caps with collinear boundary points | `glasses_lib._evaluated_mesh` fans every n-gon around its centroid before export |
| miu-astra1 turn 0 | the lens optics energy rule raised on T + R > 1, costing a paid turn | `gl.lens_optics` caps the transmission and records a note |
| vb-run1 c0000 | the lens metric rendered lens parts alone, penalising a lens tucked under the rim | visible-lens metric (first-hit lens faces); vb-run1 keeps the old definition (process predates the fix) |
| dry runs for the 3 new products (2026-09-26 evening) | `--author scripted` was silently replaced by the package driver in the CLI (the else branch), so a dry run waited for a human | CLI selects the scripted driver correctly; dry runs restarted |
| finalize (2026-09-26 evening) | a job evaluated by an external agent through `modeler.evaluate_asset` had no way into the manifest | `finalize` adopts `candidates/<delivered>/evaluation_v2/evaluation.json` when it names the delivered asset's digest, ahead of a stored evaluation |
| oakley-astra1 re-finalize | `--finalize` ignored the author's recorded finish decision and delivered the incumbent (c0004) instead of the author's choice (c0007), so the adopted evaluation no longer matched the asset | `finalize_only` recovers the finish decision from the last turn folder; `--finalize` no longer needs an author driver or credential |

## Evaluator protocol v2 and the calibration set (2026-09-26, after the owner's live verdicts)

The owner tried the assets on their own face in the AR app (`modeler.tryon`) and judged: `vb-astra1` c0004 and
`miu-astra2` c0002 perfect, `vb-run1` c0003 a bit worse, the BSA and Tripo baselines of both products worst. The v1
evaluator had rejected the accepted VB asset with five majors, three of them about the EEVEE preview sheets and the
asset-back panel; the silhouette metrics ranked the rejected BSA assets best. Consequences, all generic:

* `modeler.owner_verdict` records a verdict (accept / borderline / reject) against an asset's digest, for delivered
  assets and baselines, and appends a row (owner verdict, evaluator verdict, gates, metrics, runtime flags) to
  `data/modeler/calibration/owner_verdicts.jsonl`. An owner accept upgrades the job to `accepted` (`accepted_by: owner`).
* Evaluator v2 (`evaluate.py`) judges what the wearer sees: the GLB in the actual runtime on the harness's skin-toned
  solid fixture (`observe/ar_wearer/`), wearer poses only (front, yaw 35, roll 25; no asset-back), at the mirror's
  scale and enlarged, beside all photos; photo-match and held-out overlays are labelled shape-only; no clay or
  textured EEVEE sheets. 'major' is defined as what a wearer notices in the mirror. The v1 evaluations stay stored.
* Gates refit from the seven verdicts: front contour <= 0.6 mm and visible lens outline <= 0.8 mm (both accepts <= 0.51,
  both Tripo rejects >= 1.1), runtime temple continuity must pass (both BSA rejects fail it); the held-out contour is
  report-only (it rejected an accepted asset at 1.24 mm and every reject scored better on it).
* `modeler.evaluate_asset` prepares the v2 package for any candidate or baseline of a finished job (a fresh external
  agent answers it) and collects the answer into `<asset>/evaluation_v2/evaluation.json`; an answered package is
  kept under `previous/run-N/` (request, response, evaluation, transcript audit) when the package is rewritten.
* v2 run 1 on the seven owner-judged assets (fresh agents, transcripts audited clean): all four owner rejects were
  rejected (three by the evaluator, the Miu Tripo asset by the lens gate alone), but both owner accepts were rejected
  too: the evaluators read the runtime's hard-edged room-light reflections in the smooth lenses as opaque white
  blocks, the runtime's ear clip as missing temple tips, and the invisible clear rimless lens as an absent lens.
  Protocol v2.1 names those three runtime behaviours as non-defects in the task text; the run-1 answers stay under
  `previous/run-1/`. Because the task text was tuned on these same seven assets, agreement on them is consistency,
  not validation; the first validation is the owner's verdict on the next products' jobs.
* That validation (three new products, 2026-09-26 evening): the owner accepted all three delivered assets in the
  live try-on ("perfect", with notes: Oakley and Invu lens colours close but not right, the Ray-Ban lens branding
  odd). v2.1 + gates agreed on Ray-Ban and Invu and disagreed on Oakley twice over: the front-contour gate (0.71 mm
  vs a 0.6 mm limit set from two accepts) and the evaluator's one major, dark straight-edged wedges on the mirrored
  shield, which are the runtime's dark room walls reflected. Protocol v2.2: the front contour is report-only (it
  never separated accepts from rejects), dark as well as bright hard-edged reflections are named as runtime
  behaviour; the author rules gain two generic lines from the owner's notes (lens colour from the photos, branding
  small and printed once). All ten owner-judged assets are re-evaluated under v2.2 for the calibration table.
* v2.2 result (ten fresh agents, transcripts audited clean): the evaluators' overall verdicts match the owner on all
  five accepts and, with the lens gate, on all four rejects. On the three new assets they list exactly the owner's
  notes as one "major" each (lens colour close, lens logo typeface) while accepting overall, so the status rule now
  follows the evaluator's overall verdict plus the gates; majors and absent features are reported as the list of
  what the wearer will notice, not a block. `modeler.calibration`: 5 accepts, 4 rejects, 1 borderline, 0
  disagreements, `calibrated: true`. Caveat, stated wherever the flag is used: the task text and the rule were
  adjusted three times on these ten assets; the flag means the automatic bar reproduces every owner verdict so far,
  and every future owner verdict that disagrees must be recorded (`modeler.owner_verdict`) so the rule is refit or
  the flag drops. A job freezes the flag at creation; from now on a clean job may award `accepted` on its own, with
  `status_detail.accepted_by` absent (automatic) rather than `owner`.
* A re-finalize keeps an owner verdict already recorded on the same asset digest (`write_manifest`), so
  `--finalize` never silently demotes an owner-accepted job.
* `modeler.calibration` compares the owner's latest verdict per asset with the automatic verdict (v2 evaluation +
  gates through `decide_status`). The bar counts as calibrated with >= 3 owner accepts and >= 3 owner rejects, all in
  agreement (borderline rows reported, not counted). A job freezes this result into `evaluation_protocol.json`
  (`visual_bar_calibrated`) and awards `accepted` on its own only when it is true.

## Lens colour (2026-09-26, after the owner's notes on the mirrored shields)

Diagnosis on the delivered Oakley and Invu assets (front-photo lens core vs the runtime's front render): Invu had the
right hue at 0.65 of the photo's brightness; Oakley was olive instead of mint, darker, and without the pink-to-green
shift. Three causes, three generic capabilities:

* `gl.lens_optics(mirror_angular=[(0, rgb), (45, rgb), (90, rgb)])`: the mirror coat's reflectance colour by viewing
  angle, exported as the runtime descriptor's angular reflectance table (`export.material_spec`), which the runtime
  already rendered but no helper could express; the head-on row replaces `mirror_rgb`, a missing 90-degree row is
  appended as near-white.
* **Superseded (note of 2026-09-29; the two items below are kept as history).** `modeler/lens_colour.py` no longer
  compares the lens pixels of the actual-AR render. It fits `out = T_eff bg + A` per channel from two solid-fixture
  renders (`fit_fixtures`), predicts the runtime's lens over the front photo's own backdrop and compares that with the
  photo's lens core (the module docstring). The summary keys are `observe.LENS_COLOUR_SUMMARY_KEYS` (observe.py:69-71).
  `lens_env_intensity_recommended` is recommended only for a near-opaque mirror (T_eff below `OPAQUE_TRANSMISSION`);
  for a transmissive lens it is None.
* `modeler/lens_colour.py`: for every observed candidate with an AR check, the lens pixels of the actual-AR front
  render are found by projecting the lens meshes with the harness's recorded `mesh_to_world` and camera matrices,
  and their core colour is compared with the photo's lens core (`summary.lens_colour`: hue_error, saturation_ratio,
  value_ratio, both HSV triples). The author sees it every turn beside the millimetre errors, the evaluator in its
  measurements. A transmissive lens includes its background on both sides; the hue is the fair comparison.
* `lens_env_intensity_recommended` (mirrored lenses only): the photo/render value ratio, clamped to the AR app's
  `lensenv` range, recorded in the manifest's mounting and handover and passed by `modeler.tryon` in the try-on link,
  so a mirror coat reads as bright in the live mirror as in the catalog shot. The harness cannot apply it, so the
  observation renders stay at the runtime's default.
* `--seed-program <candidate program dir>`: a job may start from an existing program (a previous job's delivered
  candidate), built, exported and observed as its own first candidate (`turns/seed/`, journal `seeded`, manifest
  `provenance.seed_program`), so the author begins from a measured incumbent and rewrites single modules. Nothing else
  is carried over; the seed's provenance is the previous job.

## API evaluator, first-turn waste, branding (2026-09-27)

* **API evaluator.** `modeler.evaluate_asset evaluate-verdicts --evaluator astra ...` has gpt-6-astra answer every
  owner-judged asset's v2.2 package through the author's client discipline (`AstraEvaluatorDriver`: one forced
  `report_evaluation` call, strict schema now carrying `mirror_overall`, receipts under `<evaluation dir>/api*/`, call
  and dollar caps). Answers go to `evaluation_v2_astra/` beside the agent answers, so `modeler.calibration
  --evaluation-dir evaluation_v2_astra` validates the API evaluator against the owner and against the agents. A job
  uses it with `--evaluator astra` as before. Validated 2026-09-27 on the 12 owner-judged assets ($4.02): Astra
  agrees with the owner on 7 of 11 accept/reject verdicts and with the agent evaluator on 8 of 12; it rejects all four
  mirrored shields the owner accepted, each time for the coating colour not matching the photographed shift. Its
  calibration is therefore false and a job using it cannot self-award `accepted` (calibration is per evaluator).
* **First-turn waste.** Both shields lost turn 0 to a frame material on the lens and two products lost a turn to
  inconsistently wound faces. Fixes: the exporter already exports a shared material twice (lens copy with optics);
  `glasses_lib._consistent_normals` now makes inconsistent windings consistent at export (closed solids outward,
  open sheets keep their majority orientation) and notes it (holes and non-manifold edges stay the author's);
  export-time notes reach the build result; the author rules carry a first-turn checklist.
* **Branding.** `gl.text_mesh(..., font='script'|'sans'|'serif'|...)` loads system fonts by style (FONT_STYLES) and
  `gl.lens_print(lens, text, height_mm, at_xy, name, font='script')` lays a thin opaque mark on the lens's front
  surface as a frame part, sized for mirror distance. The Ray-Ban script cannot be reproduced exactly (no licensed
  font); a script face replaces the bold sans the author used.
* **Mirror-lens brightness** stays an app-side factor by design: the runtime reads `lensEnvIntensity` only from the
  handover / catalog entry, so the manifest's `lens_env_intensity_recommended` is the value a catalog publish step
  must carry; the asset cannot encode it.

## Review round (2026-09-27): what four independent reviewers found and what changed the same day

Reviewers (author loop, geometry coverage, evaluation statistics, external guidance) read the code and the job
folders; their full findings are in the session's workflow journal and summarised in RESULTS. Fixed the same day:

* Incumbent rule: silhouette scores within 1 % (camera-refit noise) count as equal and the better lens colour wins, so a
  materials-only repair with identical geometry no longer loses the incumbency to noise; a candidate whose temples the
  runtime cuts short is no longer valid (`Candidate.valid`).
* `submit_program.deliver_if_valid`: a submission can be the author's finish, so a one-turn repair costs one call.
  The delivery rule and the cost of a finish are stated to the author.
* Pre-call cost estimate uses the ledger's observed output tokens (x1.5, floor 6,000) instead of the 24,000 ceiling
  (the old estimate was 4x the actual charge and refused calls the cap could afford).
* Prompt-cache order: the static package (task, rules, helper reference, evidence, product photos) precedes this
  turn's state and candidate sheets, so the cached prefix is read, not rewritten, every call.
* Calibration is per evaluator: a job freezes the calibration of the evaluator it uses (agent answers in
  `evaluation_v2`, API answers in `evaluation_v2_astra`).
* `lens_colour` reports no hue error for neutral (grey, clear) lenses; a saturation difference replaces the ratio.
* Contract limits (triangle budget, closed parts, lens sheet rule, width range) are in the author rules.

## Astra at the start of the loop: the intake reading and the measurement review (2026-09-27)

The first paid run on a crystal frame (Tom Ford FT1123-D, `RESULTS_2026-09-26.md`) failed on the evidence, not on the
author: the contrast matte cannot see a near-white rim on a white backdrop, so the front partition called the frame
rimless (rim 0.66 mm), the lens outlines stopped under the flash-mirror reflection, the folded temple tips counted as
front height, and the author built what the numbers said. An inventory of every guess made before the first author turn
(77 items, three readers) sorted them into what a vision model reads better (classes, views, markings, what a photo
region is) and what the code measures better (edges, axes, contours, metrics). `modeler/intake_astra.py` adds two
strict-schema calls before the first turn and applies their answers as code actions with provenance; the plan and the
action table are in `PLAN_2026-09-27_astra_intake.md`, the review that shaped them found and fixed: the transport's
replay re-charging every Astra role, in-place re-runs overwriting finished jobs' evidence (now a preview), the gate
override diverging from the calibration's verdict (now read from the job's protocol), the lip bias of a lens-only scale
(lens + bridge over the outer-to-inner span is lip-invariant), the side scale's wrong denominator, and the temple tips
surviving in the fit mask. The hand-written product notes and identity checklists are replaced by the reading (an owner
file still wins), so the start of the loop no longer needs the development agent.

## Translucent fronts and calibration coverage by kind (2026-09-27)

The review of the retired stacks (`REVIEW_2026-09-27_model_studio_modeling_auto_and_routes.md`) showed that
model_studio and the old ar_v4 runtime had rendered a crystal frame through authored part roles, and that the live
runtime had dropped that vocabulary. The general pipeline now carries the capability without any route:

* **Runtime (lenses/ar, source only, unpublished):** `classifyAssetMaterials` in `ar/src/eyewear/optical-material.ts`
  reads the exporter's `partRole` extras once per loaded asset; in an asset with a canonical descriptor, a material used
  only by frame/temple parts is never optical, whatever its transmission. The canonical adapter, the continuity loader,
  the shadow (a material-coloured, arm-clipped `translucent-frame` caster), the visibility overlays (skipped) and the
  stencil-limited hair pass (opaque fallback, no background there) know the case. Shipped legacy assets carry no
  descriptor and are untouched (`ar/tests/frame-roles.test.ts`; the whole suite, 425 tests, passes).
* **Helper and rules:** `gl.material_translucent(name, tint_srgb, thickness_mm=...)` for frame AND temple parts
  (crystal or translucent acetate; since 2026-09-28 the temples too, it was frame-only on 09-27): physical
  transmission, IOR and volume absorption (`tint_srgb` = the colour seen through `thickness_mm`), exported as
  `KHR_materials_transmission` / `ior` / `volume`. Hardware (metal hinges, pins, logo plates) stays opaque. `bsa.export`
  forces a translucent frame or temple material single-sided and refuses any lens descriptor on a frame or temple part,
  and the contract check `frame_temple_materials` enforces it (translucent frame or temple parts are allowed only beside
  canonical lenses, single-sided, carrying the part's own role; `lens_materials_private` refuses a material shared
  between a lens node and another node, which would keep the part optical in the runtime).
* **Observation:** `summary.frame_see_through` (`modeler/see_through.py`): only when a translucent material is present,
  the front view is rendered twice on two solid fixtures (skin `#cba68d`, blue `#3a4f6e`); the frame node's pixels
  (projected with the harness's root transform, lens pixels removed) change colour in proportion to what they transmit
  (0 opaque … 1 clear). The two renders form the see-through sheet the author sees. `summary.temple_see_through`, beside
  it and only when the temples are translucent: the same measure in the angled view (35 deg yaw) of the same two harness
  runs, over the NEAR arm only (the camera's side of the asset's X: temple_L at +35 deg), projected as the runtime
  deforms it (`see_through.runtime_arm_vertices`, a port of `ar/src/render` face-width.ts armSpreadCurve/spreadArmX,
  rear-drop.ts eligibility and hinge, temple-terminal-fit.ts return: the row's `armSpreadM` 0.018 m from its
  `armSpreadStartZM` pivot to the registered clip cap, then the inward return over the terminal band at the render's
  fit scale; production drops nothing vertically; on r0002 the port matches the runtime's own deformed vertices,
  evaluated under node, to 4e-9 m), only its geometry in front of the runtime's rear temple clip (the endpoint the
  harness row records, else `bsa.tryon.HARNESS_CLIP_ZM`), the arm's own opaque hardware removed (drawn over the
  crystal; removed after the silhouette erosion, since its projection overstates what is drawn), frame and lens pixels
  removed and every pixel at the bare fixture colour in both renders dropped (the synthetic head's occluders hide the
  arm there; the harness records no depth or visibility mask). Its two renders follow on the see-through sheet
  (`see-through temples <fixture>`); it is written even when the front has no number (`no_frame_pixels`), and a
  translucent temple that could not be measured is reported as `{status}` (`no_temple_pixels`, `no_temple_render`, a
  failure with its `error`). It is absent for opaque temples, and also when the front's harness run fails before the
  temples are rendered (`render_failed`, `no_front_render`, a front `render_mismatch`, observe's outer failure):
  `frame_see_through` then carries that status. Measured against a ground truth (r0002's
  crystal-temple variant through the local harness with the near crystal arm painted opaque green, same camera, pose
  and timing: 0.886 over 885 eroded drawn pixels, 0.919 on the strict interior): the undeformed, hardware-inclusive
  projection read 0.796 (757 pixels, 103 of them not arm, 388 drawn-arm pixels missed, the metal strip counted); the
  registered measure reads 0.885 over 776 pixels (0.876 with the hardware removed before the erosion). Remaining bias:
  the bare-fixture tolerance also drops the clearest crystal pixels (tolerance 0/3/6/10/20 read 0.897/0.891/0.885/
  0.873/0.860), the runtime fades the last 5 mm before the endpoint, and mixed edge pixels inside the erosion still count.
* **Coverage by kind:** every calibration row carries the asset's tags; `calibration.json` reports owner verdicts per
  tag (`covered` = at least 2 evaluated verdicts, no disagreement). A job whose delivered asset carries an uncovered tag
  gets `best_effort` with the tag named, even with the bar calibrated overall — today `translucent` has no owner
  verdict, so the first translucent product needs the owner's live verdict before any translucent job can be `accepted`.
  On the 2026-09-27 set: pair 3/4, rim_full 2/2, rim_rimless 1/2, single 4/0, rim_mixed 4/0, mirrored 4/0 (accepts/rejects),
  all covered.
* **Not done, on purpose:** alpha-blended frames and any per-kind pipeline route. (Translucent temples were on this
  list on 09-27 - the arm clip and hair blends would composite the camera twice - and landed on 09-28 through the
  camera-transmission twin below.)

Review of the same day (three reviewers, adversarial verification; 17 findings, 6 verified real, all fixed):

* The runtime scales the asset to centimetres and Three scales `thickness` with it but reads `attenuationDistance` in
  world units: every `KHR_materials_volume` material absorbed 100x too strongly (the first dry run's black front, the
  Tom Ford test asset's black lenses). `ar/src/render/eyewear-volume.ts` converts the distance once; the shadow divides
  it back. No shipped asset carries a volume extension.
* The tint was applied twice (base colour AND volume attenuation): the GLB base colour of a translucent front is now
  white. `thickness_mm` cancelled out (attenuation distance = thickness): the tint is now defined through a fixed 4 mm
  reference (`TRANSLUCENT_REFERENCE_MM`), so a thicker wall really reads denser; the author rule says so.
* Pass B toggled `transmission` across zero, which bumps the material version and rebuilds the shader program every
  guarded frame (three r185): the renderer swaps each translucent frame or temple mesh to a twin material for pass B.
  On 09-27 that twin was opaque (transmission 0), which drew a crystal arm opaque in exactly the corridors where an arm
  lives; since 09-28 it is the camera-transmission twin (`ar/src/render/translucent-twin.ts`): still transmission 0 and
  the original's wrappers (arm clip, cheek/lens input, hair occlusion, overlay relief), with Three's transmission chunk
  replaced by the paired camera texture at the fragment's own pixel, absorbed by the material's volume and mixed by its
  transmission, so pass B shows the camera through the tint as pass A does (no refraction offset or roughness blur). Translucent fronts are no longer tone-mapped (the camera seen through them is not).
* The contract judged roles by node name while the runtime reads `partRole` extras: `bsa.contract` now derives the
  runtime's role from the extras, allows a translucent front only with a `frame` role, and a new check `part_roles`
  requires the extras the names promise (and `lensSurfaceProfile` on canonical lens meshes).
* Coverage gate: a protocol frozen before 2026-09-27 has no coverage block and is not gated (a reason says so);
  modifier tags fall back to the exported GLB when no material record exists (baselines, unreadable builds); the
  see-through mask uses the translucent materials only (not the hardware on the frame part); a failed see-through
  measurement is visible in the summary.
* Left as documented limits: the volume conversion uses the fixed 100x, not the width-fit scale (up to 25 % on the
  absorption exponent); a translucent front's shadow follows the lens strength slider; in the canonical layered shadow a
  crystal front is an opaque depth stop for a lens sheet behind it along the light ray; where an arm corridor crosses the
  endpiece the pass-B twin shows a straight look-through without refraction or blur (an opaque notch before 09-28); the
  canonical opaque capture runs a second transmission pre-pass per frame for a translucent asset.

## The agentic route (2026-09-27, evening): one durable author conversation

The working-tree audit of the same day (`reviews/agentic/20260927-working-tree-review/REVIEW.md`) found that this
loop already had the geometry freedom, the strict export, the observation tools and an intake stage, but not a
connected author conversation: `author_astra` sends a fresh one-decision package every turn and discards the
response's items and call ids; images were copied by basename and sliced to 14; the dollar ledger checked and charged
in two steps; delivery could name a candidate whose images the author never received; the held-out photo had no
pixel-level boundary. `modeler/agentic/` (its own README) consolidates the loop around one Responses conversation with
`store: false` and full replay, strict tools that create immutable revisions, a worker protocol (fake, native fixed
fixtures, Docker) that never runs generated code on the host, a SQLite state with a fenced lease, a shared budget
gateway that reserves the worst case before every request of every role, an observation queue whose images are
acknowledged only by the completed response that carried them, a sealed evidence boundary by decoded pixels, a
fresh-context critic and a sealed final evaluator whose verdict never returns to the author, and byte-bound
delivery. `python -m modeler.job` is unchanged and remains the legacy route; the audited defects in the shared
modules (delivery flag, containment, image names, missing-metric acceptance, exact-byte binding of evaluations and
owner verdicts, the AR harness validator, the look session's observed-revision barrier and see-through bound, the
segmented reservation, the appearance resume, the deformation scene isolation) were fixed in place with regression
tests (`tests/test_agentic_legacy_*.py`). The offline demo (`python -m modeler.agentic demo`) proves the state machine
on synthetic inputs; paid inference, the Docker worker and unseen-product fidelity remain unverified on this machine.
