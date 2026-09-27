# Reserving existing photos for evaluation

The complete `semantic_ar_v1` job can now reserve part of the existing photo set before reconstruction. It does not require additional photography. Reserving a view trades reconstruction evidence for a more useful final check; at least two distinct source photographs must remain for reconstruction.

Add one of these fields to the ordinary request:

```json
"reserved_photo_ids": ["catalog-back"]
```

The ID must occur in `photos` or `initializer.provider_views`. Alternatively, supply separate existing files:

```json
"evaluation_photos": [
  {"id": "catalog-back", "view": "back", "path": "back.jpg"}
]
```

These options are mutually exclusive. They currently require the complete `--appearance-mode semantic_ar_v1` path with its usual explicit client, region engine and physical-group settings. Without either field, all available photos remain reconstruction inputs and independent evaluation stays unmeasured.

`evaluation_reservation.prepare_evaluation_reservation()` snapshots original and normalized pixels, removes reserved sources from both fitting and provider inputs, and rejects decoded duplicates across the two sets. Exact crops can declare `source_photo_id` and `crop_xyxy`; ancestry stays attached to the original source, and two crops do not become two independent photographs. Unknown transformations cannot establish isolation.

`evaluation_stage` records the actual image arguments before initializer, geometry, material, semantics, candidate selection and frame stages. A saved semantic interpretation is checked against its original image manifest before initialization; rebinding its labels cannot hide prior exposure to a reserved photograph. Stage inventories come from orchestration, not a caller's boolean.

Cached interpretation inputs are snapshotted in full, including source views absent from fitting, and the validated interpretation is rebound to those owned copies. An interrupted reserved job can resume after external semantic image files disappear. Changed files or modified owned snapshots are rejected.

At delivery, the selected final GLB is committed before scoring. An immutable `evaluation-consumption.json` binds this reservation to those candidate bytes. Recomputing the same candidate is allowed; scoring a repaired/different candidate as newly independent under the same reservation is rejected. A fresh candidate needs unused evaluation evidence or a development-only comparison.

The held-out geometry measurement fits a nuisance camera to the reserved silhouette while keeping geometry/material fixed. Its foreground evidence is still a contrast/alpha hypothesis, especially for transparent glasses. It measures conditional agreement, not ground-truth 3D shape.

Independence also requires verified initializer history. The built-in Meshy verifier checks submitted image bytes, the exact HTTP payload, task association, successful download and split/generation artifact chain. Existing GLB imports, generic caches, custom backend histories and missing legacy receipts remain unverified. Merely owning the photos does not prove an imported model never used them. The current job's input ledger alone no longer grants independence.

Delivery writes `reserved-evaluation/report.json`, `candidate-seal.json` and a recomputed measurement in `validation/evidence.json`. Required-part evidence is separate: `required-parts/report.json` measures front/temple extent against independent image support using the final asset and frozen per-photo poses. It does not require reserved views, and therefore does not claim held-out performance.

Physical color/transmission and gradient-identification producers remain incomplete. Their acceptance fields stay unmeasured; photo residuals and AI preference cannot fill them. This reservation path makes final evaluation usable without asserting that ordinary JPEGs uniquely identify lens optics.
