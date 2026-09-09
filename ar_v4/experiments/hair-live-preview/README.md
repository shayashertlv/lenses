# Accepted long-hair mirror

The owner approved this reviewed implementation as **perfecto long hair but slow**
on September 9, 2026. The historical directory name remains stable for saved links
and tooling. The default AR launch/build now includes this page.

Run `npm run assets`, then `npm run dev` from ar_v4. The root route opens
`experiments/hair-live-preview/live.html`. Select Amber or Tom Ford and one hair
model, then Open camera. Toggle hair on/off, or Hold the exact displayed pair.
Hold closes camera/workers and completes full diagnostics on that same image.
Download is explicit and local; Resume starts a fresh session.

## Reviewed processing

One immutable capture (maximum dimension 1280) feeds the face input (maximum
dimension 640) and one selected hair worker. Hair runs alongside face detection
and private reference rendering. The same reviewed 8 ms completion timer follows
accepted preparation; browser scheduling can extend it. A mask can be applied
only to its exact source/detection pair. Late/missing/invalid results fall back
to the protected reference; no stale mask is reused.

The live path requests category winners, not unused confidence outputs. Full
confidence is requested only for the exact held diagnostic. Hardware GPU is
preferred when available, with a fresh CPU worker after GPU initialization
failure. Hair off makes no new hair requests. Only the selected model runs.
This architecture reduces work but does not establish smooth mobile performance.

`fast-compose.ts` retains the reviewed category winner, inward two-pixel feather,
accepted-versus-camera residual, and native background replacement. The source
models and input/pose pipeline are unchanged. `continuity.ts` checks paths from
the original GLBs and removes only a rear fragment newly separated by hair from
an originally connected same-side arm. Existing depth/fade gaps and ambiguous
arm associations remain unchanged.

Every optical/nasal, outside-arm, background and alpha guard remains active.
The optical front, nose configuration, geometry, projection, visibility, rear
curve and 15 mm end fade are preserved. Hair across protected front pixels is
limited by this contract; category masks are not hair depth or physical alpha.

## Reproducible reference and assets

`accepted-reference.ts` verifies and resolves 45 exact files from b26b558 under
`references/perfect-temples/`. The tracked relative-path manifest is itself
pinned. It no longer reconstructs source into a private archive at startup and
does not require Git history or writable recovery directories.

Both unchanged model binaries, hashes, versioned download URLs and Apache 2.0
attribution are under `public/models/hair/`. The offline
`scripts/prepare-hair-assets.mjs` verifies them. The Vite plugin serves the same
`/hair-preview-models/` URLs in dev/preview and emits them for static builds.
Camera use downloads no models from an external service.

`vite.hair.config.ts` supplies the default landing route and multipage build.
The original `index.html` and `experiments/temple-sagittal/live.html` remain
available. Reference renderings are isolated from unrelated shared trial work.

## Validation

`npm test` checks strict types, the existing reference and focused hair unit
tests, the baseline browser flows, and two production hair lifecycle flows.
`integration.spec.ts` uses the existing checked-in synthetic face fixture with
real local workers and tests both models, category/full held ownership,
toggle/download, stop and restart. It requires no private portrait or recording.
Its simulated camera does not establish wearer appearance or frame rate.

Private historical runs and harnesses remain local. The acceptance verification
replays the saved 32 generated-still cases and 24 original wearer cases with
their exact images, detections, poses and masks. Source hashes, before/after PNGs,
geometry and final guards must remain equal to the reviewed implementation.
See `docs/REVIEWS.md` and the private
`.recovery/perfecto-long-hair-acceptance-2026-09-09/` receipts.

Short desktop simulated-camera samples measured approximately 5.35–5.48 updates
per second with hair versus 8.18–8.57 without. Mobile, physical-camera, sustained
motion, thermal/battery behavior, true hair depth and measured personal fit remain
unverified. Future optimization must be a separate candidate preserving this
accepted visual checkpoint.
