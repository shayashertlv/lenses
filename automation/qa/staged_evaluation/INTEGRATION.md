# Evaluation reservation integration

Installed production module: `reconstruction/evaluation_reservation.py`.
Focused tests: `tests/test_evaluation_reservation.py` (26 passing).
Staged files remain a record of the implementation during the source freeze.

## Public request

Use either additional `evaluation_photos: [{id, path, view?}, ...]` or
`reserved_photo_ids: [id, ...]` referring to existing `photos` or
`initializer.provider_views`. Do not supply both. No option preserves the old
request exactly and creates no reservation files.

Exact crop records may include `source_photo_id` and integer `crop_xyxy` in the
normalized parent grid. The parent must be in the owned inventory; decoded
pixels must exactly reproduce its crop. Reserving a crop reserves the entire
parent lineage. Unknown/lossily transformed images have no verified lineage and
cannot produce independent acceptance. This is not an automatic detector of
undisclosed edits/crops already presented by the caller as unrelated photos.

`prepare_evaluation_reservation(request, base_dir, output)` must run before
initialization or any interpretation. Its `reconstruction_request` contains only
owned immutable reconstruction snapshots and no new reservation fields, so it
passes the existing strict `prepare_input_bundle` reader. Provider-only inputs
use owned normalized PNGs. The return also includes `receipt`, `receipt_path`,
and immutable normalized `evaluation_photos`. Reserving too much raises before
writing files or calling a backend: two distinct fit photographs must remain.

## Actual stage use

Call `record_photo_usage(reservation, stage_id, phase, actual_photos, output,
external_history=...)` on the arguments actually about to enter each relevant
stage. Supported phases are provider, semantic, geometry, material, frame,
candidate_selection. Pass every original semantic/cache image, not only a later
rebound subset. Generated AR cards are model artifacts, not product photos.

Provider history takes `{initializer_folder, model_path}`; paths are normalized
to absolute strings. It does not accept a caller boolean. Capturing before the
provider returns initially records unverified history; final verification may
recompute the completed built-in transport chain. Once a captured provider
history is verified, changing it fails verification.

`verify_photo_usage(reservation, usages, expected_stages={id: phase},
sealed_candidate={path, sha256})` requires the expected stage inventory from the
job orchestrator, rather than deriving it from whichever receipts happen to be
present. Missing/unknown inputs keep eligibility false. `status=verified` means
the usage ledger is verified; `independent_evaluation_eligible` additionally
requires a pinned candidate. Seal that result before scoring evaluation photos.
Using a score to choose or repair another candidate consumes that holdout;
orchestration must not reuse it to certify the next candidate.

## Narrow external provenance proof

`verified_external_history(initializer_folder, model_path)` reconstructs the
exact Meshy data-URI request body, checks its submission and actual POST body
hash, checks POST task identity, successful task response/download bytes, and
generation-to-split linkage. All read artifacts are pinned. Both selected and
unused local selection inputs count as prior usage; a photo already considered
by the provider selector cannot be retroactively reserved. Existing/cached
imports, custom backends without this chain, missing legacy photo bytes and
unknown transformed models remain unverified.

Real cached proofs succeeded for `miumiu-fresh-v1` and `oakley-fresh-v1`: each has
four submitted photos plus one considered but unused photo; 108/96 source files
were checked in under one second. See `real-history-check.json`. These receipts
establish only the original initializer's usage; they do not retroactively make
the already-developed complete jobs independent.
