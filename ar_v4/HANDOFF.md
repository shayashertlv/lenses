# Current AR development handoff — September 8, 2026

Work inside `ar_v4/` on `codex/ar-v4-nose-occlusion`. The parent Python Lenses
application and Railway deployment remain separate and unchanged. The owner
accepted the current temple corrections and authorized committing and pushing
them as `perfecto`. The preceding nose checkpoint is `0f95deb` (`perfect nose
occlusion`). These labels do not establish anatomical or all-face accuracy.
No merge or deployment is requested. Preserve recordings, recovery evidence
and worktrees.

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

Glasses placement, projection, rigid pose and assets remain unchanged. The
original frame/lenses retain ordinary depth testing; the separate temple overlay
described below does not write depth. Amber Horizon stays the default;
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
Temple/ear investigation is now authorized, with findings below. Pose jitter and
reconstruction experiments remain deferred.

The product targets desktop/mobile ecommerce, provisionally sustained 30 FPS for
the complete app. Physical mobile, thermal, display-FPS and end-to-end latency
measurements are unavailable. Software rendering, callback cadence and historical
RGB-stage timing do not establish those results.

## Temple endpoints, fade and pitch visibility

The owner prefers the previous side-view improvement and the fixed near-ear
caps: **Amber −105 mm**, **Tom Ford −110 mm**, in original GLB Z. Preserve these
lengths. Head contact must not restore the rejected global near-root truncation.
The glasses remain rigid; no deformation, reconstruction or pose smoothing was
introduced.

The endpoint now uses `temple-end-blend-v3`: a 15 mm RGB dissolve to the current
paired sRGB background texture, with matching viewport and UV transform. It
retains ordinary depth and does not accumulate duplicate transparent tail layers.
The historical hard-v1 and 4 mm coverage-v2 paths retain their saved meanings.

`temple-side-depth-v3` keeps the lateral color-only overlay but conservatively
requires lateral camera bearing and head orientation to agree. For frontal tilt,
it hides posterior-temple color with the current face/head silhouette, including
four projected samples toward the optical rear to avoid detached ends. This
additional image composition changes neither the original nasal surface nor frame/lens geometry.
The optical front is excluded. It is a rendering convention, not a measured head
volume or proof of physical contact.

The overlay remains excluded from internal lens transmission. The frontal RGB
correction runs only in the mapped native main pass: Three's transmission pass
uses NoToneMapping, and production uses ACES. Ordinary offscreen beauty targets
therefore omit it; diagnostics use the native canvas for appearance. Do not
change that output contract without revisiting transmission isolation.

Captures store actual side/frontal weights rather than recomputing them on
replay. Null, v1 and v2 replay clear the new frontal effect and borrowed camera
source; new policies borrow only the current presentation's image. Failure,
no-face, restart and disposal clear or restore owned state. Saved multisample
coverage still requires a compatible context.

See [current review](docs/REVIEWS.md) and the
[pitch/fade report](.recovery/temple-pitch-fade-2026-09-08/REPORT.md).
The available recordings lack a paired steep frontal-up view matching the new
screenshot. Static comparisons cannot establish that angle, real motion, hair/ear
occlusion, all-face fit or mobile performance. Only one heavy browser/test process
should run at a time.

The accepted rendering code matches the sealed pitch/fade evaluation. Commit
review found no runtime blockers; the replay test additionally verifies that
different recorded frames produce different native images before checking an
exact return to the first frame. No ranking JSON is pending.

## Recovery

This correction's starting source and prior documentation are preserved in
[its snapshot](.recovery/temple-pitch-fade-2026-09-08/before/). Its starting receipt
records 281 workspace files and 9,678 protected earlier evidence files. Final
identities belong in the preservation audit and evidence receipt. Previous
studies, including rejected alternatives, remain private in their original
recovery directories.

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
