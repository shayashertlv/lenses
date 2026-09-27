# Astra-inclusive pipeline test results — 2026-09-23

**Ready for full pipeline testing with Astra included.** The dedicated canonical
editing stage is implemented and has completed live tests. It retains the
pre-optics source, recompiles optical exports after supported edits, renders in
the current AR engine, and supplies those exact images to Astra for another
decision. Commands and all ten typed operations are in the
[current runbook](SEGMENTED_ASTRA.md).

## Live execution

Six paid `gpt-6-astra` Responses requests were made against the explicit OpenAI
credential. All six returned HTTP 200 and complete validated plans. They share
one durable ten-call authorization ledger; four authorized calls remain unused.
No additional generation, segmentation or semantic-provider calls were made.
These tests began from completed source-photo/provider jobs.

| Product | Actual Astra decisions | Final result |
| --- | --- | --- |
| Miu | Inspect both lenses; try guarded smooth normals; review post-edit images and finish | `r0001`, 126,123 triangles, 5,464,760 bytes |
| Oakley | Try guarded smooth normals; edit absorption/coating response; review post-edit images and finish | `r0002`, 119,983 triangles, 6,174,628 bytes |

Both final candidates passed strict optical readback and the actual-AR matrix:
front, both obliques, back and rolled, under room, broad and side lighting.
Each observation preserves 30 baseline/candidate renders, original source-photo
pins and labelled source-part evidence. Both final candidates were seen by
Astra before it finished. Both decisions were `best_effort`, not quality acceptance.

The geometry, part membership, source textures and frame materials remained
unchanged in these live runs. Normal fields changed in both; Oakley's optical
descriptor also changed. The original base GLBs remain byte-identical. The
main pipeline reports point to the separately compiled edited candidates.

- Miu: [session report](../data/segmented-astra-v1/live-miu-001/report.json),
  [room comparison](../data/segmented-astra-v1/live-miu-001/edits/turn-0001/attempt-0001/observation/comparison-room.png).
- Oakley: [session report](../data/segmented-astra-v1/live-oakley-001/report.json),
  [room comparison](../data/segmented-astra-v1/live-oakley-001/edits/turn-0001/attempt-0001/observation/comparison-room.png).
- [Machine-checked verification](../data/segmented-astra-v1/verification.json)
  binds final candidates, observations, model receipts, budget and implementation.

Both completed live sessions were reopened through `reconstruction.job
--with-astra` with the same arguments. Their current results were delivered
without another paid request or edit. The earlier scripted main-entry-point
test changed lens descriptors and an opaque frame material, compiled and
rendered both revisions, restored the original candidate exactly, then resumed
without calls. Its finish is correctly recorded as scripted rather than model review.

## Verification

- 71 dedicated Astra tests passed, plus 61 parameterized subtests: typed plans,
  geometry/material preservation, compiler/observer integrity, shared budgets,
  failed-request replay, initialization recovery, atomic rejection, checkpoint
  restoration and crashes immediately before/after revision promotion.
- 99 existing segmented/optical/compaction tests passed, plus 19 subtests.
- All 415 AR tests passed; `npx tsc --noEmit` passed.
- Eleven legacy input-guard tests passed. The incompatible Blender route still
  refuses canonical optical assets and now points to the current editor.
- [Four extra compiler probes](../data/segmented-astra-v1/compiler-probes/report.json)
  passed: INVU's recovered normal policy, Oakley's smooth alternative, VB's
  original membership and its retained lens-rim alternative. Source lineage
  and strict exports were checked. VB's alternative changes the derived lens
  height/gradient coordinates, so it remains a distinct visual hypothesis.

No new AR source or deployment changes were needed for this integration.

## Remaining appearance work

The live comparisons show smoother reflections. They also expose limits that
successful pipeline execution does not resolve:

1. **Clear-lens edges:** Miu's smoother effective normals make its contours
   faint. `propose_smooth_optical_group()` currently supplies one smooth field
   across a whole group. A future edge-preserving normal hypothesis needs
   evidence that separates real bevel/sidewall response from noisy source
   normals, while still passing coincident-patch validation. Increasing tint
   simply to reveal the lenses would be an unsupported correction.
2. **Mirror color:** Oakley's rear is closer to rose/plum in this test but
   remains darker and more saturated than the product photos; the frontal
   green region is narrower. Unknown photographic lighting leaves the
   reflection/absorption split ambiguous. More views and controlled alternative
   renders can test hypotheses; this result is not a recovered physical coating.
3. **Frame and detail fidelity:** distorted contours, texture lighting and
   imperfect branding originate in the generated asset. Bounded part edits and
   scalar material factors cannot repaint textures or reconstruct missing detail.
4. **Broader validation:** independent photo alignment, unseen products,
   real-face behavior and mobile performance remain unmeasured by these tests.

The host therefore keeps `accepted:false`, `requires_review:true` and
`production_ready:false`. This checkpoint establishes a working, resumable
Astra test pipeline with preserved evidence; it does not establish perfect
reconstruction from ordinary product photos.
