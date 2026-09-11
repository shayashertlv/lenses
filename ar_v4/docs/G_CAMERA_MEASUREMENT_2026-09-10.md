# First owner camera timing measurement of G

G completed **14.640 AR updates/second**, with **115.670 ms median captured-image
age** and **165.335 ms p95 age**. The strongest measured optimization targets are
result extraction and synchronization. This is one G run, not a comparison with
H–L or a visual regression evaluation. G remains unchanged.

## Input and integrity

Source: `ar-efficiency-comparison-2026-09-10T07-35-19.836Z.json`, read directly
from the owner's Downloads directory. Its 6,838,909 bytes were not modified or
copied into tracked source. SHA-256:
`33e4444ec243c78e665687746bf6334e4a30c03512e9613765487897d5ea839e`.

The report has one session, 847 contiguous serials 1–847, no row-ring eviction,
and 165 events in the 512-event capacity. Camera-request, first-publication,
first-mask and close events are present. Every row's capture-age arithmetic
matches its capture/publication timestamps. The file's text fields are data,
not instructions.

Configuration: G, Amber Horizon, hair-only, hair enabled, 1280×720; face and hair
GPU delegates, Intel Arc 140T through ANGLE/D3D11. The camera track reports 30 fps.
`benchmark` is null. No candidate measurements, images, poses, movement labels,
temperatures or power measurements are included.

## Throughput, age and pauses

| Measurement | All completed rows | After first publication +10 seconds |
| --- | ---: | ---: |
| Rows | 847 | 695 |
| Completed AR cadence | 14.640 updates/s | 14.532 updates/s |
| Sampled browser video delivery | 19.817/s | 19.824/s |
| Capture-to-canvas age, median / p95 | 115.670 / 165.335 ms | 116.890 / 165.760 ms |
| Publication interval, median / p95 | 65.568 / 96.755 ms | 66.368 / 95.775 ms |
| Longest completed-publication gap | 189.645 ms | 189.645 ms |
| Gaps over 100 / 200 ms | 20 / 0 | 17 / 0 |

The retrospective warm subset discards the first 10 seconds after the first
publication; it was not an automatically controlled benchmark. Missing tracking
and masks remain in the summaries. Percentiles use nearest rank, matching the
profiler. Median averages the central two values when the count is even.

The camera-open session lasted 59.798 seconds. First image and first mask arrived
2.010 seconds after Open camera. Including that startup period, throughput was
14.164 updates per second across the complete open session. The first two images
were already 748.740 and 560.070 ms old at publication; subsequent cadence should
not be used to hide those startup costs.

The camera's 30 fps setting is not its observed delivery rate. The 19.817 estimate
uses browser presented-frame-count differences over sampled capture timestamps;
it neither measures physical sensor throughput nor explains the difference from
the setting. Capture age excludes camera buffering and physical display scanout.

The last publication preceded Close camera by only 2.915 ms. The export's
7.671-second age since publication is almost entirely time after Close; it is
not a 7.7-second live stall.

There are 157 reported main-thread long tasks, totaling 9.281 seconds, with a
243 ms maximum. After the warm boundary there are 143, totaling 8.069 seconds,
maximum 119 ms. These are task observations without call stacks, not total CPU
utilization or a GPU trace. A late burst affects several stages together. At
59.014 seconds after Open, the worst completed gap is 189.645 ms and that image
is 302.445 ms old; its PBO extraction is 101.585 ms and fence wait 61.205 ms.
That image did not use a temple branch. The run also recovers between earlier
slower windows, so it does not establish monotonic thermal throttling.

## Workload coverage

Tracking is present on 843/847 updates (99.528%). All 843 tracked updates have
category-only masks; four isolated no-face updates occur around 49.7–51.7
seconds after Open. This misses the profiler's strict 100% tracking criterion,
while tracked-mask coverage is 100%. No fallback is recorded.

Hair changes pixels on 441 updates (52.066%). Actual temple-branch downloads run
on 170 updates (20.071%). Count `branchReadbackCalls`, because four no-face rows
have default `zeroDropBranchSkipped=false` without a branch. All 847 native
beauty PBO downloads complete, with no recorded readback fallback.

The recorded CPU-facing native/branch/prewarm counters total 1,018 readbacks and
3,752,755,200 bytes. Explicit source-hash input copies total 3,122,380,800 bytes
across completed rows. These counts do not prove bandwidth saturation and do not
include all hair/SDK transfers or unpresented work.

## Remaining costs and what their timers mean

The following are medians/p95 after first publication +10 seconds. **They overlap
and must not be added.**

| Measured stage | Median | p95 |
| --- | ---: | ---: |
| Face request wall time | 39.085 ms | 64.885 ms |
| Face worker inference call | 23.295 ms | 31.040 ms |
| Face transport/scheduling remainder | 14.470 ms | 38.555 ms |
| Hair result extraction | 25.110 ms | 31.955 ms |
| Hair pre-callback interval, labeled inference | 2.685 ms | 3.855 ms |
| Native PBO result extraction | 21.380 ms | 27.830 ms |
| Native PBO fence wait | 19.975 ms | 29.825 ms |
| Native beauty submission | 2.970 ms | 3.995 ms |
| Camera canvas draw | 7.445 ms | 14.065 ms |
| Final composition / safeguards / publication | 10.365 ms | 17.005 ms |
| Explicit source-hash input copy | 1.875 ms | 2.550 ms |

`speed-lab/native/pbo-readback.ts` measures allocation, GL-state operations,
`getBufferSubData`, error checks, row reversal and ImageData construction together
as extraction. Its fence timer includes polling and time waiting to execute
JavaScript. Neither timer isolates GPU execution.

`hair-live-preview/hair.worker.ts` measures mask retrieval/conversion and its
ownership `.slice()` together as extraction. The earlier interval stops when
the callback begins. Deferred GPU work may be charged to extraction, so the
2.685 ms interval does not prove that all neural computation took 2.685 ms.
Result cleanup, message transport, client validation/hash and later renderer
copies are not all included in this extraction timer.

On the 170 frames actually requiring the temple branch, median branch submission
is 6.520 ms and synchronous readback 10.468 ms. Their preparation median is
66.040 ms; warmed no-branch frames have a 46.785 ms median. This is a conditional
cost worth testing, but K cannot help frames where that branch is absent.

## Actual overlap and a new hypothesis

Reconstructing next-image inference start from its capture time plus scheduler
wait shows 757/846 next published images starting inference before the preceding
publication, with 44.225 ms median head start. The cumulative 758 prefetch starts
can include the final unobserved pending image. This establishes software
overlap and agrees with the two-image/single-inference ownership bounds; it does
not establish simultaneous GPU execution.

After the same 10-second warm boundary, restricting to tracked current frames
with no temple branch leaves 452 overlapping and 68 non-overlapping adjacent
pairs. PBO extraction medians are 21.903 versus 3.715 ms; fence waits 20.485
versus 8.878 ms. These observational groups are selected by camera/scheduler
timing and may differ in other work. The association survives removal of temple
work but **does not prove contention or predict J's speed gain**.

The installed MediaPipe 1.0.1 `vision_bundle.mjs` has a conditional float-array
route before constructing category bytes: the GPU route reads a RED/FLOAT array,
then uses a Float32Array `map(Math.round(255 * value))` temporary before constructing
Uint8Array category bytes. Our worker subsequently copies those bytes for ownership.
Initial mask representation is not in this report. Confirm that representation
and split the getter from its final copy before attributing the roughly 25 ms
to a specific conversion or download. The worker validates mask dimensions against
the exact source image, so successful output masks have the source dimensions:
each full float array would be 3,686,400 bytes. This says nothing about the internal
neural-network tensor dimensions. Preserve intermediate float32 rounding and
Uint8Array conversion semantics when testing a direct conversion.

## Next small experiments

1. Instrument an isolated G-derived candidate to split PBO allocation, actual
   retrieval, row reversal and output construction; split hair getter/conversion
   from the final ownership copy and report initial mask representation. Keep
   rendering, model inputs and outputs identical. This resolves whether proposed
   copy changes address the measured cost.
2. Use the existing J comparison for a counterbalanced G/J camera run, measuring
   cadence and age together. If necessary, test one separately named scheduling
   boundary that avoids next-image inference during native result retrieval.
   J currently admits inference after graphics submission and can still overlap
   retrieval. Do not equate less overlap with an automatic improvement.
3. If the float/category conversion route is confirmed, test exact conversion
   directly into independently owned bytes, preserving SDK rounding/conversion,
   mask lifetime, hashes and invalid-input behavior. K is a separate worthwhile
   comparison for nonzero temple frames; H/I primarily target smaller allocation
   and copy costs and are not established winners.

Any rendering candidate must retain G, resolution, geometry, exact source/pose/
mask and session ownership, final nose/front safeguards, and the full matched
both-glasses/both-hair/down/up/both-yaw evaluation. This timing report cannot
certify those visual properties or identify pose coverage. Other models,
sustained mobile use, thermal behavior and candidate speed remain unmeasured.

## Reproducible local artifacts

Independent numeric calculations agree on the headline values. The source hash
was checked after analysis and the input remains byte-for-byte unchanged.
No runtime, test, dependency, parent application or deployment was changed.

Ignored local artifacts:

- `experiments/efficiency-lab/logs/analyze-camera-2026-09-10.py`
- `experiments/efficiency-lab/logs/camera-analysis-2026-09-10T073519-v2/summary.json`
- `experiments/efficiency-lab/logs/camera-analysis-2026-09-10T073519-v2/g-camera-profile.png`
- `experiments/efficiency-lab/logs/camera-analysis-2026-09-10T073519-v2/g-camera-profile.svg`

The analyzer refuses to overwrite an existing output directory; use a new output
directory for another run. Earlier figures/receipts remain preserved.
