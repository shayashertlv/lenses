# Current integration review — September 7, 2026

The owner explicitly requested **Raw + Option 17** integration and cleanup of
the AR development app. This changes the active nasal surface path; it is not
acceptance of the rejected RGB/shape-composition experiment. The owner later
authorized committing and pushing this prepared baseline with the exact message
`perfect nose occlusion`; the label does not change the evidence limits below.
No merge or deployment is requested. Parent Python/deployment files stay separate.

## Change under review

`src/render/nasal-shape.ts` ports only the frozen 17 shape
(`central-wp020-dp015`, width 0.20/depth 0.15) and its original geometry guards.
`TryOnRenderer` applies it to the original reconstructed raw surface for both
models. RGB boundary correction is removed from the active path; rejection
returns exact raw geometry. Glasses placement, materials, projection, tracking,
camera/session ownership and model selection remain unchanged, including default
Amber Horizon and the existing uncommitted Tom Ford clear-lens work.

The old `src/render/nasal-boundary.ts` is retained byte-identical for historical
private harness compatibility. Source and production bundle checks confirm that
it is outside the runtime path. Renderer loading remains lazy; capture provenance
does not import Three.js into the initial application chunk. Dependencies and
their 76-entry lock graph are unchanged.

New capture provenance identifies method `raw-nasal-shape-v1`, selected and
applied shape IDs, applied/raw-fallback status and reasons. Exact image/detection/
pose pairing and the actual shown surface are retained. Replay bypasses shape
generation and restores saved geometry; its runtime provenance is
`recorded-surface`. Existing recorded metadata is not rewritten or relabelled,
and no-face frames keep null geometry. Capture schema identifiers stay compatible.

## Validation status

**Final verification passed for this integration.** `CI=1 npm test` passed strict
TypeScript, verified local assets, the production build, 23 unit tests and all 8
browser flows. Tests cover known frontal/both-turn coordinates, whole-shape raw
fallback, malformed data, input/output ownership, exact replay and lifecycle
cancellation/failure/stop/restart for both models. No private recording or recovery
module is needed by the public tests.

Private parity checks matched the frozen raw17 generator exactly on 145 original
paired surfaces (19 no-face entries excluded). The actual integrated renderer's
15 matched native views are byte-identical to the previously reviewed ablation
PNGs. Saved-surface replay bypasses shaping, no-face frames clear geometry, and
returning to live restores the original result without RGB geometry readback.
These are existing matched recordings, not new held-out or physical motion data.

Independent code review and final diff/source checks found no blockers. All 44
pre-integration AR working files have exact backups; 24 protected originals and
1,073 prior evidence files retain their hashes. Parent files, dependencies, prior
clear-lens work and existing private playgrounds are preserved. Logs, image hashes,
the preservation audit and sealed evidence receipt are in the new archive below.
One heavy browser/test process ran at a time. Simulated/headless checks do not
replace physical-camera acceptance.

The static right-turn raw17 preference motivated the owner's choice. Earlier
opposite-angle feedback slightly favored RGB+17, so its small contribution is
lost by this selection. No claim of universally improved occlusion, anatomical
accuracy, measured fit or integrated live-motion acceptance follows. Preserve
near rim/bridge/pads while checking renewed far cut-through and the previously
resolved rapid cutoff flicker. Temples/ears and pose jitter remain deferred.
Physical mobile/thermal/display-FPS and end-to-end latency results are unavailable;
sustained 30 FPS is still a complete-app target.

## Preserved history

Before cleanup, the full README, HANDOFF and this review file were copied and
hashed verbatim with all pre-existing active work under
[the pre-integration archive](../.recovery/nose-ablation-integration-2026-09-07/before/).
See the archived [HANDOFF](../.recovery/nose-ablation-integration-2026-09-07/before/HANDOFF.md)
and [REVIEWS](../.recovery/nose-ablation-integration-2026-09-07/before/docs/REVIEWS.md)
for historical experiments and receipts. `start-receipt.json` and
`before-diff.patch` alongside that archive capture the starting hashes and dirty
worktree. New integration checks belong in that new recovery directory; older
sealed evidence and private live playgrounds remain untouched. Original wearer
recordings remain byte-for-byte preserved and excluded from Git/deployments.
