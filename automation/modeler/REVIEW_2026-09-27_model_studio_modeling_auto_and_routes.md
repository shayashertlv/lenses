# Review: what model_studio and modeling_auto did with transparent glasses, and whether the modeler should have per-kind routes

Date: 2026-09-27. Read-only review of `ar_v4/model_studio` (local, untracked, 2026-09-08..13), `ar_v4/modeling_auto`
(local, untracked, 2026-09-13..16), the archived `ar_v4` AR runtime (`C:/Users/Shay/lenses-archive-2026-09-17/ar_v4-essentials/src`)
and the live runtime `lenses/ar/src`. Three read-only agent investigations plus four harness runs; every claim below carries its
source. Evidence files copied to `data/modeler/reviews/2026-09-27-crystal-frame-runtime/` (README.json lists them).

## 0. Verdict in five lines

1. The owner's memory is right for **model_studio + the retired ar_v4 runtime**, and wrong for **modeling_auto**. model_studio built
   a crystal Tom Ford (FT1123-D 26E 49) with real physical transmission on the frame and temples, and the ar_v4 runtime rendered it
   on a synthetic face as convincing see-through crystal (2026-09-09). modeling_auto never allowed a transparent frame.
2. That capability was **lost in the ar_v4 → lenses/ar transition**, not never built: the live runtime dropped the frame/temple role
   vocabulary and keeps only "lens = transmission > 0 or descriptor". Today the unchanged Tom Ford asset is refused at handover, and
   with opaque temples its crystal front is classified as a lens.
3. The mechanism ar_v4 used (`part_role` extras, roles independent of transmission, a front guard built from roles) is exactly
   what `PLAN_2026-09-27_runtime_rules_and_helpers.md` §1A proposes. §1A is therefore a **port with a reference implementation**,
   not a new design. It is still larger than ar_v4's version because the live runtime has nine lens consumers, ar_v4 had four.
4. The only routing precedent in the old stacks is modeling_auto's owner-stated **lens layout** (pair / single / shield), added
   because the pipeline had *forbidden* the model to decide. It fixed the shield failure and diverged three prompt/acceptance paths.
5. Recommendation: **one loop, per-kind profiles, Astra classifies on turn 0 with the intake's measured layout as a cross-check** —
   not separate pipelines. The five accepted assets already span three kinds under one loop; what failed on other kinds were
   capability gaps (runtime rules, helpers, contract), which profiles carry, and which forks would only duplicate.

## 1. Transparency in the old stacks: facts

### model_studio (Meshy scan + Astra Blender edits, 40-call budget)

- Role contract: `set_role(obj, role)` writes `obj["part_role"]`; roles `frame, rim_left, rim_right, bridge, temple_left,
  temple_right, lens_left, lens_right, hardware, pad_left, pad_right` (`blender/worker.py:22-25, 67-72`). Required set at
  `worker.py:336-338`; unassigned objects fail. Exported through `export_extras=True` (`worker.py:1009-1013`) as glTF node extras.
  "Transmission does not determine role: clear acetate is still frame/temple" (`blender/API_GUIDE.md:243`) holds at code level:
  every role read is `obj.get("part_role")`, no material→role inference (`worker.py:77,169,315,448,745,761,763,799`).
- `transmissive_material(name, color, roughness=0.08, transmission=1.0, ior=1.49, thickness_m=0.003, attenuation_color,
  attenuation_distance_m)` (`worker.py:107-136`): Principled Transmission Weight + IOR, Alpha 1, Volume Absorption, glTF Material
  Output with Thickness, tag `studio_expected_volume`. After export the GLB is re-imported and `KHR_materials_transmission` /
  `KHR_materials_volume` presence is checked (`worker.py:990-1030`); IOR is recorded, not checked.
- **Tom Ford FT1123-D 26E 49, job `d92b404c`** (2026-09-08): frame `Crystal acetate` = transmission 1, ior 1.49, thickness 5 mm,
  attenuation (0.94, 0.925, 0.88) at 90 mm; lenses `Warm brown optical tint` = transmission 1, thickness 1.5 mm, attenuation
  (0.58, 0.43, 0.28) at 3 mm. Applied to `Crystal_front` (frame) and both temples. Run blocked at 40/40 with r8 selected; r9 approved
  in a separate one-call review (`data/evaluations/20260909-r9-independent-review/review-summary.md`, master SHA `b9d76aa2…`).
  Owner note: "The owner considers the displayed model satisfactory" (`data/audits/d92b404c…/run-audit.md:3`). Cycles proofs
  (`…/operations/de26df4dfeff4ed3997acf52c26ab7f4/material-*.png`) show the checker through the rim. The shape is Meshy's
  (rounder, thinner rim than the reference photo), the material is right.
- **Ray-Ban RB4455 Zuri, job `d922bba1`** (2026-09-09): tortoise shell authored as transmission 0.58 → 0.62 with volume absorption
  (`data/audits/20260909-rayban-materials/material-history.md:23-30, 41`). Reviewer accepted "physical transmission and lens/hardware
  character"; the owner rejected the colouring (pattern), not the translucency (`HANDOFF.md:650`).
- Both viewers rendered these frames with stock three.js physical materials (`web/viewer.ts:110`, plain GLTFLoader). The AR preview
  (`ar_preview/`) is the ar_v4 app with a swapped catalog (`ar_preview/vite.config.ts:8-21`), entries carry
  `volumeAttenuationScale: 100`, `semanticTempleParts: true`, `templeClipLocalZM: -0.11` (`ar_preview/eyewear.ts:16-31`).
- model_studio's own plan already said the live integration was missing: "The current AR temple code infers lens role from
  transmission … Integrating new translucent frames into live AR will require a separately reviewed role contract. No such
  integration is included in this prototype." (`PLAN.md:310-313`).
- Only two products ever ran (Tom Ford, Ray-Ban ×5 jobs). No type routing: `JobSpec = name, notes, dimensions`
  (`studio/domain.py:48-52`); prompts branch in text only ("For a full-rim product …", `studio/prompts.py:92-94`).

### The retired ar_v4 runtime

- Default lens identity was `MeshPhysicalMaterial && transmission > 0` at four sites (`temple-clip.ts:76`, `temple-visibility.ts:130`,
  `eyewear-volume.ts:12`, `renderer.ts:255`).
- Opt-in role policy for one asset: `temple-parts.ts:4-8` (`OPTICAL_ROLES`, `FRAME_ROLES`), `:15` reads `userData.part_role`
  (three's GLTFLoader copies extras to userData), `:27-34` per-material ownership (shared with an optical owner → lens; frame-only →
  not lens; else transmission fallback). Gate `semanticTempleParts` true only for `test_` (`eyewear.ts:19, 48`; `renderer.ts:266-301`).
  `trial-temple-visibility.ts:44-46` builds the front guard from roles, not from lens vertices. Tests pin "transmission alone does not
  turn authored temples into optics" (`tests/unit/test-temple-parts.test.ts:56-89`).
- `test_` = the Tom Ford r9 master (`candidate (1).blend`, SHA `b9d76aa2…`, provenance `public/models/test_.provenance.json`).
  Motivation recorded at `docs/REVIEWS.md:966-1000`: near-black lenses from a m→cm attenuation mismatch (fixed by ×100), and
  "the owner reported that temple limits did not affect the crystal arms" (fixed by the role policy).
- The synthetic-face preview `model_studio/data/previews/ar-r14/qa/2026-09-09T16-28-45.134Z/test_-comparison-synthetic-front.png`
  shows skin and brows through the rim and temples, light-brown lenses with the eyes visible. **No live-AR owner verdict on it exists
  in the archive**; the experiment `experiments/test-model-temples` measured pixel deltas on a synthetic pose, not appearance.

### modeling_auto (Meshy scan + Astra scripts, six stages)

- Never a transparent frame: "Do not make any frame surface invisible" (`app/prompts.py:188`, also `:96, :208, :250-251`;
  `blender/API_GUIDE.md:44-45`). Export gives frames an opaque material (`blender/worker.py:1853-1869`); a frame material with
  transmission ≥ 0.5 would be reclassified as a lens (`worker.py:1651-1652, 1945-1946`); alpha ≤ 0.05 parts are dropped as hidden
  (`:1655-1664`). The runner requires "at least one transmissive lens material and one opaque frame material"
  (`app/blender_runner.py:127-128`).
- Its transparency work was all about **lenses**: see-through share ≥ 0.6 (`worker.py:394-433`), the emissive-card proof render
  (`:998-1047`), the optical-skin restore after retexture (`:1282-1302`).
- 14 jobs, four products (Ray-Ban RB4455, Miu Miu rimless, Oakley OO9208 ×9, Invu ×4); no clear or translucent product.

## 2. Today's runtime on the same crystal asset (harness `bsa.archeck`, live `lenses/ar` renderer, 2026-09-27)

| Variant | Result |
|---|---|
| Tom Ford r9 GLB, unchanged bytes | `asset_handover_rejected`: "The rear-drop start must be forward of the accepted cap." (`ar/src/render/rear-drop.ts:151`). The transmissive temples count as lens vertices, `lensRearZM` reaches the temple tips. Same failure model_studio's own probe recorded on 2026-09-08 (`data/imports/test_/ar-compatibility-report.json`). |
| Temples given an opaque material (script `make_opaque_temples.py`, roles untouched) | `runtime_compatible`, fit settles, no continuity failure, **3 optical meshes**: `Crystal_front` is treated as a lens (hair occlusion skips it, shadow uses the lens caster branch, lens env intensity applies to it). On the checker fixture the front reads pale and milky. |
| Front and temples opaque | `runtime_compatible`, 2 optical meshes. Lenses still near-black → the black lenses are the asset's legacy closed-solid lens, not an interaction with the crystal rim. |
| Shipped legacy references (amber-horizon, tom-ford-clear) | Both show the checker through the lens, so the legacy transmission path composites the background correctly. |
| Solid skin-tone fixture (`#cba68d`), crystal front vs opaque front | Decisive: the crystal front takes the fixture's skin colour with faint rim highlights (it transmits what is behind it); the opaque front stays cream. **Today's runtime does render a translucent front see-through once the temples are opaque**; the milky look on the checker was roughness blur of a high-frequency pattern, the same blur three applied in ar_v4. |

Renders and the two variant GLBs: `data/modeler/reviews/2026-09-27-crystal-frame-runtime/`. The opaque-temple variant can be
served to the live mirror with `python -m modeler.tryon 8793` for an owner look, with the caveat that the runtime treats the front as
a lens until §1A of the plan lands.

## 3. What this changes in the runtime plan

- §1A (translucent frames) is now a **port of ar_v4's `temple-parts.ts` policy** into the live runtime's `isOpticalMaterial`
  consumers, with ar_v4's tests as the template. Keep the plan's differences: apply only to assets carrying lens descriptors
  (shipped legacy assets byte-identical), handle the transmission prepass in the stencil pass, the shadow caster and the overlay
  clones (ar_v4 had none of these), and start with translucent fronts + opaque temples.
- Unit handling is no longer a risk: the live runtime is real-size metres, so `KHR_materials_volume` attenuation distances need no
  ×100 (ar_v4 was centimetres).
- The role vocabulary should be the exporter's `partRole` extras already written by `bsa.export`, extended with `frame` / `temple_R`
  / `temple_L` / `hardware` values the modeler already knows from `gl.register`. Do not reintroduce model_studio's snake_case roles.
- The lenses in the Tom Ford asset are closed solids and render black today; the modeler's front-sheet contract avoids that path
  entirely, so nothing to fix there.

## 4. The routing question

### The one precedent: modeling_auto's lens layout

`LENS_LAYOUTS = {'pair', 'single', 'shield'}` is an **owner input** at job creation (`app/workflows.py:67-78`, HTTP field and a
required radio group `web/main.ts:457-459`). Origin (`docs/REVIEWS.md:785-793`, v14): "the lens prompt demanded exactly two lenses
and the worker refused anything else, so Astra split the shield into two half lenses… Astra was not deciding badly, it was forbidden
to decide." Per layout it switched prompt blocks (`app/prompts.py:124-164, 211-238, 277-288`), the worker's role counts and
placement checks (`blender/worker.py:67-100, 636-641`) and the skin cap (`:111-115`). Metrics, scripts and export stayed shared.
It fixed the shield failure; it did not classify anything automatically, and nothing else in either stack classified glasses type.

### What the current modeler already does

- Intake measures `layout ∈ {pair, single}` and `rim_class ∈ {full, half, rimless, mixed}` per lens from the front photo
  (`bsa/front.py:960-1184`) and hands them to the author as evidence (`modeler/author.py compact_evidence`).
- The five owner-accepted assets under one loop and one rule set: VB pair/full, Ray-Ban pair/full, Miu pair/rimless, Oakley and
  Invu single/mixed (shield). Nothing forbade the author to decide; the shield reruns needed *capabilities* (lens colour metric,
  angular mirror table, seed program), not a route.
- The things that still fail are capability gaps, not kind confusion: translucent frames (runtime rule + contract), half-rim / nylor
  edges (edge ring helper), thin wire and rimless edge polish (helpers), sizes nominal. A fork per kind would carry the same gaps.

### Assessment

Separate pipelines per kind would (a) duplicate the loop and the evaluator, (b) split the calibration set — each route needs its own
≥ 3 accepts / ≥ 3 rejects before it may say `accepted`, so most routes would start uncalibrated, (c) reintroduce product-shaped code
paths, which the task rules forbid, and (d) not compose: real products combine traits (translucent rimless, half-rim sport shield,
mirrored wrap). modeling_auto's three layout blocks already showed the divergence cost after one week.

The idea is right at one layer: **the recipe, the contract variant, the observation set and the evaluator's non-defect list differ by
kind, and the author should commit to a kind before writing geometry.** That is a profile, not a pipeline.

## 5. Proposed shape: one loop, composable profiles, Astra classifies first

- `modeler/profiles.py`: a small table of profiles as data. Base kinds `pair_full`, `pair_half` (nylor / semi-rimless), `rimless`,
  `shield` (single lens, includes sport wraps as a base-curve parameter); modifiers `translucent`, `mirrored`, `wrap`. Each carries:
  extra RULES lines, the helper subset and one worked example program (the accepted programs are already de-facto recipes: the
  shield reruns were seeded from `oakley-astra1`), the lens form the contract expects (front sheet pair / single sheet / edge ring),
  the observation set (`lens_colour` for mirrored, edge-ring check for rimless, a see-through metric for translucent), the
  evaluator checklist template with the kind's non-defects, and handover flags.
- Turn 0 becomes a **classification decision**: the author names base kind + modifiers with a one-line justification from the photos;
  the product name and notes are priors, the photos govern (the existing rule). The host cross-checks against intake's `layout` /
  `rim_class`; a contradiction is returned to the author once, then recorded as `inconsistent_inputs` evidence in the manifest.
  The owner may pin a profile in the request (`"profile": ...`) exactly as modeling_auto's layout field did.
- Calibration stays one set with **per-profile counts** in `calibration.json`: a profile with fewer than 3 accepts and 3 rejects is
  reported as uncalibrated, and jobs of that profile top out at `best_effort` / `quality_unverified` until owner verdicts arrive. This is
  the honest form of "if the visual bar has not been calibrated, say so": today's calibration covers pair/full, rimless and shield only.
- What stays shared and unforked: intake, the build/contract/observe/evaluate loop, the incumbent rule, the ledgers, the report.

Effort: profiles as data plus the classification turn and the per-profile calibration report is about a day; the translucent profile
waits on the runtime work in the plan (§1A), now with ar_v4's implementation as the reference.

## 6. Decisions for the owner

1. Whether the profile layer is wanted before or after the plan's helper work (they are independent; profiles first makes the helper
   gaps explicit per kind).
2. Whether to look at the opaque-temple crystal variant in the live mirror now (evidence for §1A's "front first" order), knowing the
   runtime currently treats the crystal front as a lens.
3. The two decisions already open in the plan: runtime changes in the AR working tree, and the first translucent product to model.
