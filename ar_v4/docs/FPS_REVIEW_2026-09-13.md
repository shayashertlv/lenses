# FPS review experiments — September 13, 2026

The owner requested implemented, tested, published experiments from the attached
`G_FPS_REVIEW_2026-09-13.md`. This is a separate candidate study. G Combined at
`b9142b2a3b957445f378d8012eea7e27ca68fd0b` remains accepted; U remains rejected
for promotion. No candidate is promoted by these tests.

The source is the isolated deployed checkout identified in HANDOFF.md. The
original working tree, private recordings and recovery archives are preserved.
The review's dirty original speed-lab edits were not copied into G dependencies.

## Assessment of the attached review

The two existing laptop summary receipts under the original checkout's ignored
`experiments/speed-lab/qa/output/fps-review-2026-09-13/` support testing CPU face
first. At 640×427 the stored G / CPU / repeated-G rates are approximately
9.85 / 10.73 / 9.49; at 1280×854 they are 5.42 / 5.92 / 5.45. These are
synthetic-camera summary receipts, not a new reproduction or physical-phone
evidence. The summaries report complete masks but **zero visible hair edits**;
they do not establish quality at moving hair/temple boundaries.

The reported long readback/extraction times support investigating contention and
main-thread blocking. Summing medians of overlapping stages does not prove one
critical path, and the receipts do not establish that every GPU command executes
on one shared thread. Moving work to a worker can improve responsiveness while
failing to improve completed AR throughput; new transfers can erase its benefit.
The review's 17–20 FPS projection is a hypothesis, not a measured result.

Byte-preserving opportunities have not been proved exhausted. The separately
audited September 13 Amber phone comparison gave V 14.10 versus G 13.57 completed
updates/s across full measurement windows, with matching masks on 100% versus
64.61% of tracked frames. Tom Ford's final G warmup was incomplete, so it supplies
no balanced winner. The review's other phone rates use a different denominator;
this preview consistently retains full wall-clock windows and missing coverage.

Power is a useful control variable. Different days' receipts do not isolate
power as the cause of the difference. The new run records a user-selected power
state; compare within the same state and keep energy-saving settings fixed.

## Ranked separate candidates

| Order | Option and suspected cost | Implemented change and risk | Fair experiment |
| --- | --- | --- | --- |
| 1 | CPU face tracking: face GPU work may contend with hair and render/readback work. The attached laptop summaries are measured evidence; phone gains are unknown. | `review-options/face-detector.ts` forces CPU in the unchanged face worker. Failure is explicit; G retains its original GPU/CPU fallback. Startup and switching drain the old pump and initialize the correct detector before admitting a frame. CPU landmarks can differ, affecting tracking and glasses placement. | G / CPU / CPU / G at the same resolution, glasses, hair model and power state. Compare actual delegate, completed updates, tracking, frame age, masks and stalls. Review live movement; shared held detections cannot validate CPU tracking. |
| 2 | Worker rendering: Three submission, PBO retrieval, temple work, composition and guards occupy or block the main thread. The benefit after transfers is hypothetical. | `render-worker/` mechanically adapts G canvas boundaries to OffscreenCanvas and keeps original algorithms and guards. One worker/RPC/prepared pair, explicit session/generation/source/detection identity, independently transferred source/mask/output storage, prompt cancellation. Both output variants remain immediately available. Copy and transport costs may outweigh responsiveness gains; unsupported graphics reports an error. | G / worker / worker / G. Include all source/mask/output transfers and async completion in publication age/rate. Inspect worker counters, first-use setup, completion gaps and synchronous toggle response. Independently compare held RGBA, geometry and guards. |
| 3 | Compositor reuse: repeated allocation and detailed region/category logic are potential CPU costs. The attached 4 ms saving is an estimate. | `review-compose/` and the candidate renderer reuse leased output memory and restrict detailed membership/feather work to conservative row spans. The full initial RGBA residual/background validation and full final guard audit remain. This goes beyond X's first word-comparison scan. Guard copies and the initial full image copy still cost time; old held/export storage must not be overwritten. | G / compositor / compositor / G. Require positive actual-use/reuse counters, fewer detailed scanned pixels when geometry permits, and exact independent pixels/guards on both edited and unedited regions. Compare whole-pipeline rate, not only compose time. |
| 4 | VideoFrame camera copy: synchronous capture/readback may block the main thread. Savings are hypothetical and browser dependent. | `review-options/video-frame-capture.ts` snapshots VideoFrame synchronously at admission, awaits RGBA copy before any hash/bitmap/inference, writes those authoritative pixels to the same owned canvas, and closes the frame. One copy at a time and at most two owned image leases. Unsupported formats, dimensions or transforms use a reported fallback from that same frozen frame; missing API uses synchronous original-task capture. RGBA conversion may differ from G and the canvas write remains. | G / camera copy / camera copy / G. Separate actual RGBA-copy from fallback rows; a fallback is not successful optimization evidence. Compare colors, frame age, stalls and total updates with unchanged full source/detector/render dimensions. Hold shares captured pixels, so live conversion needs separate review. |

Existing V remains available as a fifth reference option. It is not combined with
these experiments; this keeps one changed mechanism per comparison. Alternate
frame hair with an older mask is excluded because it violates exact pairing.
Reduced resolution, altered transmission, reduced capture rate and model swaps
are not part of these four candidates.

## iPhone testing ground

Open `/ar_testing/experiments/efficiency-lab/live.html?study=fps-review`.
G is selected first. Choose the test option while the camera is closed. Select
glasses and hair model, record power state, open the camera and inspect the live
G/candidate toggle. The page reports the actual selected path or fallback.

Use **Measure only** for throughput. Each of four windows requires at least
5 seconds and three tracked, matching-mask images, then measures 30 seconds.
Startup/switching and warmup remain separate. The measurement denominator is the
full window, including stalls, lost tracking and unmasked frames. A timed-out
warmup produces a partial ZIP; it is not silently shortened or scored as zero FPS.
Use video on a separate run to review motion; encoding adds load.

Repeat each candidate for Amber/hair-only, Amber/multiclass, Tom Ford/hair-only
and Tom Ford/multiclass. Follow the repeated front/nose, down, up, left-yaw and
right-yaw cues. Inspect both side arms, hair transitions, frame stability, nose
and front protection, immediate toggles and recovery after tracking loss. Save
each ZIP before starting another test. Keep lighting, power, recording policy,
movements and temperature comparable; repeat questionable or close results.

Hold compares one exact source/detection/full SDK mask. Worker and compositor
produce independent held rendering; CPU face, camera copy and V share G rendering
on those held inputs. Matching those aliases is not independent evidence about
CPU landmarks, camera conversion or live mask extraction.

Local tests exercise real browser workers and installed SDK models, synthetic
pose controls, frozen recorded cases, source ownership, cancellation and exact
pixel guards. They do not establish physical iPhone speed, thermal behavior,
display scanout latency, personal fit or owner acceptance of moving visuals.
Current executed checks and published fingerprint are recorded in REVIEWS.md.
