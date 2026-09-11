# Efficiency comparison QA

This optional local harness compares the unchanged G renderer from `speed-lab`
at `b9142b2` with the separately configured renderer in `efficiency-lab`. Both
receive exact independently owned copies of the same frozen image, original
detection/pose and category mask. No face or hair inference runs in this study.
The production browser suite separately exercises real local workers.

The new `g-base-manifest.json` pins 239 G and accepted dependency files, retaining
their Git-blob SHA-256 and their actual local-byte SHA-256 independently. A CRLF
checkout is identified explicitly. Old manifests and receipts are unchanged.
Current manifests also recheck the committed Git blobs; optional reproduction
therefore requires the G Git object and the original private frozen archives.

```powershell
node experiments/efficiency-lab/qa/preservation.mjs --verify
node --test experiments/efficiency-lab/qa/protocol.test.mjs
node experiments/efficiency-lab/qa/matched.mjs --preflight --profile=combined --allow-async-fallback
```

Start the efficiency development server on port 8094. Run heavy browser jobs
one at a time, separately from camera previews and performance measurement.

```powershell
node experiments/efficiency-lab/qa/matched.mjs --profile=combined --allow-async-fallback
node experiments/efficiency-lab/qa/matched.mjs --profile=scratch --allow-async-fallback
node experiments/efficiency-lab/qa/matched.mjs --profile=temples --allow-async-fallback
python experiments/efficiency-lab/qa/audit-pngs.py <new-report.json>
```

Profiles `scratch`, `temples`, `combined`, `deferred`, `region`, and `lens` are
supported. All use the four G optimizations; extra flags come directly from
`profiles.ts`. Baseline G receives its unchanged original options and renderer.
The default study covers all 56 archived cases: 32 generated and 24 recorded,
both glasses, both hair models, down/up/both yaw, exact accepted/hair/background
pixels, source/detection/mask identity, geometry and nose/front/outside-arm guards.
Generated D3D11 and recorded SwiftShader decoding retain their old boundaries.
All private input receipts are checked before and after; archives are read only.
New PNGs/reports go exclusively to unique ignored `qa/output` directories.

Default cases keep one presentation per pair, reusing sessions per glasses and
phase. Return-to-first and lifecycle controls then repeat owned pairs and check
exact guards. The first valid pair retains G's completed synchronous temple
prewarm; later eligible nonzero-drop cases must use an actual early branch PBO.
Readback counts include native PBO retrieval, synchronous fallback, branch PBO
retrieval and prewarming. Scratch allocations, reuse and independent output
allocations are checked separately. An asynchronous function is not proof of GPU
concurrency. The mechanism proves earlier submissions, not simultaneous GPU work.

`--allow-async-fallback` permits only the documented bounded 500 ms SwiftShader
fence timeout with exact synchronous same-pair transfer accounting. It applies
independently to G/native and candidate branch downloads. Hardware cases still
require actual PBO completion. A fallback never counts as a fast-path result.

Controls cover hair off, invalid-mask rejection, no face/reacquisition, private
preparation, overlapping prepare, double finish, cancellation and pre-aborted
creation. Borrowed-source revocation must retain independent held/export pixels.
Additional browser-only input checks compare exact fresh/reused canvas and
bitmap bytes, source hashes, transparent source-over-black, downsampling and
size changes. These procedural checks contain no scan or anatomical claims.

The independent Python audit rereads every saved PNG and archive, verifies all
pixel/geometry protections and raw identities, checks G blobs/local bytes,
runtime/frozen-input hashes, and reconstructs actual readback mechanisms from
raw counters. It writes a new audit and refuses to replace an existing one.
Lifecycle control PNGs are not saved; those remain browser observations.

After building this isolated preview, run its production browser suite:

```powershell
npx playwright test --config experiments/efficiency-lab/playwright.config.ts
```

It starts a separate production preview on port 8098. All fourteen live/held modes,
both glasses/hair models, exact held pixels/geometry/masks, failed full-mask
upgrades, pending switch/Hold, pending stop/restart and startup cancellation are
checked. G is initially selected. The automatic reversed G-versus-selected
benchmark uses its real 5-second warmups and 30-second measurement windows.
That scenario validates UI/coverage/export behavior, not physical-camera speed.

The focused `review-preview.spec.ts` covers G/Q/R/S/T with real workers and a
synthetic static camera. It checks actual publication/UI suppression, cropped
download and omitted-lens counters, exact held pairing and guards, and stop,
restart and cancellation. Q/T held outputs share G; the live counters and focused
unit checks test their changes separately. This is not moving-wearer evidence.

The `region` matrix additionally requires positive cropped downloads for each
glasses/hair combination in each phase, alongside valid zero-byte cases where
all editable pixels are protected. Retained-source controls cross a task boundary
and verify full diagnostic rerender after borrowed-source revocation. The `lens`
matrix saves actual raw branch differences and requires them to remain inside
the protected region, with nonzero differences for both glasses in each phase.
The independent PNG audit reconstructs these checks from the saved artifacts.

Generated/recorded still comparisons do not establish moving-wearer acceptance,
sustained physical-camera throughput, mobile smoothness, heat, or physical
display latency. Earlier warmed-pixel variance and failed receipts remain intact.
G remains the default outside this comparison; no new candidate is promoted.

## September 10 matched results

All three independent full studies pass 56 exact cases and 16 lifecycle control
groups apiece. Their independent PNG audits each verify 666 artifact/base files,
221 runtime hashes, 302 frozen inputs and the 239-file G local/Git boundary.
Each matrix contains 36 actual hair edits and 12 nonzero rear-temple cases.

| Profile | Ignored output directory | Recorded G/candidate beauty fallbacks | Actual early temple PBO |
| --- | --- | --- | --- |
| Combined | `matched-2026-09-10T06-30-31.656Z` | 20 / 20 of 24 | 9 of 10 warmed eligible cases |
| Scratch | `matched-2026-09-10T06-36-24.049Z` | 18 / 12 of 24 | Not requested |
| Temples | `matched-2026-09-10T06-41-44.363Z` | 19 / 17 of 24 | 8 of 10 warmed eligible cases |

All 32 generated hardware cases use actual beauty PBO downloads on both sides.
The recorded software phase uses only the explicitly allowed bounded fallback;
one Combined and two Temples branch downloads also use that exact fallback.
Two initial nonzero cases retain G's serial completed prewarm, so only ten of
the twelve nonzero cases are eligible for the early branch in each study.
Scratch reuse totals are 156,825,600 bytes for Combined and 167,884,800 bytes for
Scratch across saved cases. These are accounted reuse bytes, not speed results.
Browser input controls also pass all fresh/reused canvas, bitmap and hash checks.

Every report and independent audit remains unchanged in its own output folder.
These receipts describe the captured runtime boundary. Subsequent UI, telemetry
or orchestration corrections must be documented as an explicit delta and tested
with the final production browser suite; the old runtime hashes must not be
rewritten to imply that later code ran in these studies.
