# Shared photographic lighting experiment

The question is whether one lighting/exposure solution per photograph removes
inconsistent explanations produced by fitting each lens independently. This is
a controlled development experiment, not an unseen-product benchmark or an
automatic material acceptance test.

## Fixed comparison

Use the retained Victoria Beckham preparation and original two photographs:

- Original source GLB SHA-256: `a30f94b3d8c516a9e6e7687c3fd715127079b6abee4b154078f486529745b3e5`.
- Prepared GLB SHA-256: `b366a271e230eb5571bc069ead68c70873fef322640b94d9bcfc8aacb97d97de`.
- Independent baseline: `data/vb-optical-fit-v1/results/`.
- Joint output: `data/vb-joint-photo-fit-v1/results/`.
- Keep original masks, all twelve hypotheses, native pixel samples, optical UVs,
  interpolated normals, camera hypotheses, rear-content classifications and
  spatial train/validation split. Compare saved observation arrays before
  interpreting any numerical difference.
- Keep the five material families, both lighting families, three starts,
  eighty evaluations per start and existing eight-code/90%-channel photo policy.
- Explore every combination of the two groups' mask branches: 81 branches,
  five same-family assignments and two lighting families require 2,430 starts.
  Each group has independent material parameters. Mixed-family assignments are
  supported explicitly but are outside this experiment's declared scope.
- Retain both roughness priors per group without repeating optimization. This
  represents four roughness combinations for each completed joint candidate;
  these are not four independent measurements or fits.

The experiment must preserve every declared start, including nonconvergence and
numerical failures. No mask, difficult pixel, photo or lens may be removed to
improve the reported result. Interruption may resume verified completed starts
from the same source snapshot. Changing the implementation requires a new run.

## Interpretation decided before results

Report convergence, numerical failures, exact policy matches, and per-region
training/validation errors. Compare matching family, lighting and mask support;
different supports cannot establish an improvement. Independent and joint
objectives have different pixel and nuisance-prior normalization, so their raw
objective values are not comparable quality scores.

Each diagnostic GLB must use one entire joint candidate. Combining the best left
lens from one lighting solution with the best right lens from another would
reintroduce the inconsistency the experiment tests. The fixed preview ranking
uses validation measurements and therefore does not provide an independent
validation result for those selected previews.

Record disagreements between candidates under the existing AR response probes.
An envelope over a finite collection of starts is a diagnostic, not a certified
bound over all possible materials. Run every exported preview through the actual
AR loader and rendering harness. Passing that harness establishes supported
transport and rendering, not photographic fidelity.

A shared field may worsen photo error because it removes freedoms from the old
model. That does not itself establish that the new field is physically correct.
Near-field reflections can differ across lenses; incorrect geometry, masks,
photo pose, arm articulation, backdrop and rear-frame color remain possible
causes of mismatch. The composition diagnostic evaluates these separately.

## What this cannot identify

One connected photo/group graph and one exposure anchor remove duplicated
calibration gauges; they do not calibrate lighting or recover unique materials.
Even a common reflected environment can trade off against the independent
absorption of both lenses. A total mirror reveals no transmission density, and
this pixel model does not measure roughness. Clipped colors provide inequalities.

The source-part grouping and prepared front surfaces are retained development
hypotheses. The current general front-envelope preparer fails the fixed corpus;
this run uses an earlier pinned preparation. It cannot establish complete
reconstruction from fresh photos, generic optical geometry, reliable semantic
identity, physical calibration or accurate appearance on unseen glasses.

Cross-view optical identity and scene composition are separate diagnostic
modules. They preserve ambiguity in region association and structures behind
the lenses; they do not infer a hinge axis or authorize an automatic mesh edit.

## Completed development result

The pinned experiment completed in 2,038.68 seconds. Every declared branch/start
was retained: 2,430 optimizer records, 2,159 converged, 271 unresolved and zero
numerical failures. **No candidate satisfied the existing photo policy.** The
four factorized roughness combinations per candidate represent 9,720 material
combinations, not additional fits or independent evidence. The finite AR probe
spread remains 1.0 and 0.9881 linear RGB for the two source groups. Neither this
spread nor the failed local fits is a global uncertainty or infeasibility proof.

The read-only comparison verifies the original arrays, bindings, camera reports
and spatial split, then matches 810 complete configurations with no unmatched
joint configurations. It retains 49 distinct observation/height/rear cells
without validation support. The gradient family alone has 486 starts, of which
485 converged and none met policy. Shared lighting therefore addresses a real
model inconsistency, but does not solve the color, rear-content and coverage
problems. Existing display choices sometimes use different masks and reuse
validation for ranking; their numerical differences are not unbiased quality
comparisons.

All five exported family previews loaded in the actual `TryOnRenderer`, passed
exact geometry/material checks and completed three poses each. Source snapshots
were stable. No browser errors or failed HTTP responses occurred; one PMREM
compiler precision warning is preserved. Visual inspection shows competing
gradient, uniformly dark and strongly mirrored appearances, including different
left/right reflections. None is accepted as the product's inferred appearance.

The immutable runtime contains 36 reconstruction modules. A fresh-process
completed-stage replay verified all 2,462 saved stage files without byte or
modification-time changes. Evidence:

| Artifact | SHA-256 |
| --- | --- |
| `data/vb-joint-photo-fit-v1/runtime/snapshot.json` | `9b2cb12d6b90c55ee9d26e829eceb7b11a5741d64ddde7c1a6a92fc05064bae6` |
| `data/vb-joint-photo-fit-v1/results/report.json` | `4dfaad11a997ab16c2a2c1d2dd606a2d4211c7b34495d909c2817dbdc9e5e54d` |
| `data/vb-joint-photo-fit-v1/results/attempts/attempt-001/fit.json` | `643722dd38eefc005cc1a4560a3caa0293494c6610d5939aabe7da0735e5e0f3` |
| `ar/qa/output/prepared-optics-vb-joint-v1/report.json` | `a46600148687c2ae2a52b920b28962c0b0e0f969ff4a0db9bb14b334b2ce8b1e` |

The comparison is under `data/vb-joint-photo-fit-v1/comparison-v1/`; the final
runtime contact sheet is beside its AR report. This completes the declared
shared-lighting experiment, not the photo-to-model automation.
