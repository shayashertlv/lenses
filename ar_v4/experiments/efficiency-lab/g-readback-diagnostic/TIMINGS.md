# G readback diagnostic

This is an observer of the accepted G pipeline, not an optimization. The three
renderer files are exact copies of `speed-lab/renderer.ts`,
`speed-lab/temples/renderer.ts` and `speed-lab/native/renderer.ts`, with only their
relative imports rebased. Their copied child chain reaches the instrumented
`native/pbo-readback.ts`. Every other dependency resolves to G's existing module.
`copy-parity.test.ts` regenerates those import rewrites and compares exact text.

The original G path remains separate. The diagnostic adds no GL calls, state
queries, image reads, workers, scheduling yields or reusable image buffers. It
retains G's existing fence/flush/zero-timeout polling, 500 ms default deadline,
state restoration, two independently owned typed arrays per returned image,
vertical row flip, and error/current-generation checks. Differential tests compare
G and the diagnostic's entire fake-GL call trace, arguments, state and exact pixels
for success, reuse/resizing, failure, revoked ownership, cancellation and disposal.
They are not a substitute for matched browser output or wearer motion evidence.

## Export fields

Fields travel through the existing per-frame renderer native PBO statistics and
are flattened by the comparison exporter. The leaf names below are all scalar
numbers. `diagnosticVersion = 1` identifies an observed PBO readback; an absent
marker does not establish that the diagnostic PBO path was exercised. Existing
`completed`, `fallbackReason`, `queuedCalls/Bytes`, `retrievedCalls/Bytes`, `polls`
and `bufferBytesAllocated` retain their meanings.

| Fields | Existing work inside the measured interval |
| --- | --- |
| `submitStateQueryMs`, `extractStateQueryMs` | Six `getParameter` calls saving read framebuffer, pixel-pack buffer and four PACK settings, including the original keys/values arrays. |
| `submitStateSetupMs`, `extractStateSetupMs` | Four `pixelStorei` calls setting tightly packed readback. |
| `submitStateRestoreMs`, `extractStateRestoreMs` | Original GL/Three framebuffer restoration, pixel-pack buffer restoration and four PACK restorations. |
| `submitBufferCreateMs`, `submitBufferAllocateMs` | Existing `createBuffer` and `bufferData` calls. Allocation error checks are separate. These calls occur only when G would create/resize its PBOs. |
| `submitReadPixelsMs` | Existing `readPixels` call submitting into a PBO. |
| `submitCheckMs`, `waitCheckMs`, `extractCheckMs` | Existing `getError` and conditional `isContextLost` checks for each named phase, including their error construction on failure. |
| `currentCheckMs` | Existing ownership/generation and context checks in `finish`, accumulated across both waiting and extraction. |
| `fenceSyncMs`, `fenceFlushMs` | Existing fence creation and flush calls. |
| `clientWaitMs` | Accumulated elapsed time inside the original nonblocking `clientWaitSync(sync, 0, 0)` calls. |
| `pollYieldMs`, `pollYields` | Elapsed time/count of the original `await setTimeout(resolve, 0)` intervals. This includes event-loop delay and other work while suspended; GPU work can proceed concurrently. |
| `extractBufferBindMs` | Existing per-slot pixel-pack buffer bind preceding CPU retrieval. |
| `extractGetBufferSubDataMs` | Existing `getBufferSubData` call, including any driver synchronization and byte transfer experienced by the caller. |
| `extractAllocationMs` | Existing bottom `Uint8Array` and top `Uint8ClampedArray` allocations. The original `Array.from` result allocation is outside this field. |
| `extractRowFlipMs` | Original vertical row loop, including its `subarray` views and `top.set` copies. |
| `extractImageDataMs` | Existing `new ImageData(top, width, height)` call. |
| `waitObservationMs` | Wait envelope from entering `finish` through signaled fence, or through failure/cancellation if no signal was observed. |
| `extractObservationMs` | Extraction envelope, including state save/setup/restore, returned image construction and final current check; retained on extraction failure. |
| `diagnosticClockReads` | Number of additional `performance.now()` reads used by this observer. It excludes G's preexisting timer reads. It is a count, not a measured overhead duration. |

The existing `submitMs`, `waitMs` and `extractMs` fields are preserved. G's
`extractMs` is assigned only after successful extraction, so a failed attempt may
have `extractMs = 0` but positive `extractObservationMs`. `waitObservationMs` does
not include extraction; both extraction fields nest inside the existing native
preparation time. `currentCheckMs` spans wait and extraction and therefore cannot
be added to both. Timer envelopes include bookkeeping and unassigned work; their
subfields are not an exhaustive partition. A state query exception before setup
can leave that query interval unfinished, and `completed = false` identifies such
partial attempts. Do not sum stage medians or nested envelopes.

Every duration is CPU wall-clock elapsed time. None is a GPU timestamp or proves
whether GPU execution, driver synchronization, garbage collection or competing
work caused a delay. `pollYieldMs` is particularly not an isolated GPU wait.

Each finish captures its own metrics object before awaiting. This is telemetry
ownership only: after cancellation followed by `begin`, the old poll cannot add
late measurements or a fallback reason to the new pair. Output ownership checks,
pending state and GL cleanup remain G's. `metrics` returns an independent scalar
snapshot; unsuccessful attempts never become successful samples.

## Experiment and interpretation

Compare unchanged G and G with this observer in fresh documents, with the same
rendering resolution, glasses, hair model, power state and camera. Keep startup,
five-second warmup and observed matching-mask readiness outside measurement. Use
sustained windows and completed AR updates/second, camera delivery, frame age,
publication stalls and matching-mask availability together. Repeat reversed order
and all four combinations of the two glasses and two hair models, following
front/nose, down, up, left-yaw and right-yaw cues.

Extra clocks and metric bookkeeping add work and can perturb the very scheduling
being measured. The uninstrumented G control estimates the resulting end-to-end
cost under the actual device/workload. Do not subtract a synthetic clock estimate
or promote the observer as an FPS improvement. A large difference between G and
the observer makes the diagnostic less representative. Compare within-run drift
and repeatability before selecting a follow-on optimization. Untracked samples,
readback fallbacks and missing markers need separate reporting.
