# Current AR review — September 8, 2026

The owner accepted the current temple corrections and requested the `perfecto`
commit. Review found no runtime blockers. The current renderer retains
**Amber −105 mm / Tom Ford −110 mm** original-GLB caps and dissolves the last
**15 mm** into the exact paired camera background.

Frontal tilt now uses the current face/head silhouette to suppress posterior-arm
color that incorrectly passes ordinary depth. Four projected samples toward the
optical rear continue occlusion through terminal gaps. Lateral relief remains
available when camera bearing and head heading agree. The original geometry,
normals, rigid pose, optical front and Raw + Option 17 nasal source are unchanged.
The frontal correction is excluded from internal lens transmission; its current
appearance output contract is the native ACES canvas. This is approximate visual
occlusion, not a personal scan, measured head volume or physical fit correction.

Final `CI=1 npm test` passes **52 unit tests**, **8 browser flows**, strict
TypeScript, asset verification and production build. New capture metadata owns
actual per-frame side/frontal weights and dissolve length. Legacy hard-v1,
coverage-v2, visibility-v1/v2 and null replay retain their meanings. Cancellation,
failure, no-face, replay and restart clear borrowed image/state correctly.

Replay compares native canvas output, with a distinct-frame check before exact
A/B/A restoration so a cleared framebuffer cannot satisfy the equality check.
UI screenshots are retained separately. The earlier CSS comparison diagnostic
remains in the sealed pitch/fade study. Commit preparation preserves the accepted
runtime bytes and the recorded rendering evidence.

Fifteen original image/detection/pose pairs across both models give **30 cases /
60 before-after images**, including all **18 byte-exact earlier baseline
overlaps**. Four additional zero-MSAA cases pass. Optical front/lens coverage
and far-arm gains within nasal guards are unchanged. Guard and full-lens RGB
changes are at most **1/255**, including lens edges; two Amber guard exceptions
are optical edge pixels, so complete optical RGB identity is not claimed.
Replay/lifecycle permutations run on four main smoke cases and four fallback
cases; the remaining static cases verify exact pairing and baseline restoration.

Both models suppress the prominent shaft and detached tip in the strongest
available near-frontal down recording. Earlier angled shafts stay continuous;
profile views show a softer endpoint at the same cap length. Milder mixed-angle
views intentionally receive partial suppression and may retain visible arms.
The down source is blurred, and no paired recording matches the owner's steep
frontal-up screenshot. Those screenshots were not used for detector inference.
Live steep-up/down motion, all-face fit and mobile performance remain unverified.

See the [current report](../.recovery/temple-pitch-fade-2026-09-08/REPORT.md) and
[before/after comparison](../.recovery/temple-pitch-fade-2026-09-08/review/final-span/live-review.png).
Previous reviews/source and rejected candidates remain private in recovery.
Recordings and preceding sealed evidence stay protected; work remains inside
`ar_v4`. The preceding pushed nose checkpoint is `0f95deb`; the accepted temple
checkpoint is named `perfecto`.
