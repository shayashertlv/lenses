# Current AR development handoff — September 7, 2026

Work inside `ar_v4/` on `codex/ar-v4-nose-occlusion`. The parent Python Lenses
application and Railway deployment remain separate and unchanged. The owner
authorized committing and pushing this baseline as `perfect nose occlusion`.
That requested label is not a claim of anatomical or all-face accuracy. No merge
or deployment is requested. Preserve recordings, recovery evidence and worktrees.

## Current decision and implementation

The owner explicitly chose to integrate **Raw + Option 17 (ablation)** after the
bounded repair/shape-composition experiment failed to improve marked Bad262.
That rejected mechanism remains private; it is not the implemented path.

The current renderer reconstructs the original raw `FaceSurface`, then applies
the frozen `central-wp020-dp015` shape through `src/render/nasal-shape.ts`:
central width parameter 0.20 and forward depth parameter 0.15. Both Amber Horizon
and Tom Ford use this fixed path. Original geometry guards are retained; rejected
shapes show the exact raw surface. There is no RGB repair, yaw switch, lighting
classifier, parameter retuning or added temporal state in this path.

Glasses placement, projection, rigid pose, hard depth/material behavior, assets,
tracking and session lifecycle remain unchanged. Amber Horizon stays the default;
the pre-existing Tom Ford clear-lens option and model-locked sessions are retained.
The old `src/render/nasal-boundary.ts` remains byte-identical only for historical
private harness imports and hash checks. The active renderer does not import it.

New captures keep the original image/detection/pose and actual shown surface.
Optional per-frame `occlusion` metadata records method `raw-nasal-shape-v1`,
selected/applied shape IDs, status and rejection reasons. A shape fallback is
identified as raw. Replay restores the saved surface without applying 17 again;
runtime provenance is `recorded-surface`, and old captures are not relabelled.
No-face presentations keep null geometry. Schema identifiers and download names
stay compatible; immutable storage and session ownership remain required.

## Evidence and remaining acceptance

The owner preferred raw+17 on exact right-turn Bad262, while slightly preferring
RGB+17 on the previously displayed opposite frame476. Integration follows the
owner's explicit later choice and removes that small opposite-angle RGB
contribution. Static preference is not anatomical depth truth or all-angle
acceptance. Physical-camera verification of this integrated path is still needed
for far cut-through, near rim/bridge/pad preservation and motion in both turns.
The previously resolved rapid nose-cutoff flicker must be checked for regression.
Temples/ear contact, pose jitter and reconstruction experiments remain deferred.

The product targets desktop/mobile ecommerce, provisionally sustained 30 FPS for
the complete app. Physical mobile, thermal, display-FPS and end-to-end latency
measurements are unavailable. Software rendering, callback cadence and historical
RGB-stage timing do not establish those results.

## Validation and recovery

Final `CI=1 npm test` passed: strict TypeScript, asset verification, production
build, 23 unit tests and all 8 browser flows. The port matches the frozen raw17
generator exactly on 145 original paired surfaces; 19 no-face entries were
excluded. All 15 matched native rendered views are byte-identical to the reviewed
ablation images. Exact saved-surface replay, no RGB geometry readback, raw fallback
and cancellation/failure/stop/restart behavior are covered. Renderer loading stays
lazy; the legacy RGB module and private experiments are absent from the bundle.
Final diff and preservation checks passed: 24 protected originals, 1,073 prior
evidence files, parent files and the 76-entry dependency graph are unchanged.
Evidence and hashes are in the new recovery directory below. The owner performs
physical-camera acceptance; use only one heavy browser/test process at a time.
Current review details are in [docs/REVIEWS.md](docs/REVIEWS.md).

The complete pre-integration working files, including the prior uncommitted
clear-lens changes, are archived under
[.recovery/nose-ablation-integration-2026-09-07/before/](.recovery/nose-ablation-integration-2026-09-07/before/).
`start-receipt.json` and `before-diff.patch` beside it preserve hashes and context.
The former lengthy handoff and reviews are retained verbatim in that archive;
older experiments remain in their original sealed recovery directories. Existing
private live playgrounds are unchanged. Do not rewrite or delete these archives.

Especially preserve `recordings/ar-v4-nose-2026-09-05T08-04-00-213Z.json` and
`.recovery/nose-fresh-2026-09-06-160512/capture.json` byte-for-byte. Their expected
SHA256 values are respectively
`8a16382c541093562eb1fc817f3d77889ae46c8450deacc3b792740fffe46e9c` and
`6001ead5e37bf6791b6a1d97ca7f642b75b0b944d7d588cc5225140e0a07ae2f`.
Git is not their backup. Root restoration archives remain under `../.recovery/`.
