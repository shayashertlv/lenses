# Isolated performance-candidate QA

The matched harness compares the actual current and candidate `LiveHairRenderer`
implementations with the 32 accepted generated cases and 24 exact wearer pairs.
It verifies retained source images, detections, poses, category masks and original
recording files before use and again afterward. It does not rerun inference,
resize or rewrite a recording, or substitute poses.

Start the separate candidate Vite server on 8066 first. Run only one heavy browser
job at a time. The harness attaches to that server and routes private artifact
bytes locally through Playwright; it never adds private inputs to a served public
directory. All new output goes to a unique ignored `qa/output/matched-*` directory.

```powershell
node experiments/performance-candidate/qa/matched.mjs --preflight
node experiments/performance-candidate/qa/matched.mjs --base=http://127.0.0.1:8066
python experiments/performance-candidate/qa/audit-pngs.py <new-report.json>
```

The completed correctness run used `--warmup=0 --samples=0`: one presentation per
pair, with renderer sessions reused within each eyewear model and phase. It does
not mean that every pair was rendered in a newly initialized renderer. To repeat
the bounded variance diagnostic without retrying until success:

```powershell
node experiments/performance-candidate/qa/matched.mjs --phase=generated32 --diagnostic-case=05-left-profile-waves/tom-ford-clear/selfie-multiclass --repetitions=12
```

Use `--phase=generated32` or `--phase=recorded24` for a separate phase. A completed
phase is not a complete 56-case evaluation. Failed receipts remain in output.

The generated phase uses hardware Intel D3D11 and the recorded phase uses the
archived SwiftShader/default-canvas decode path. Each generated pair receives two
warmup presentations and five timed presentations per implementation, alternating
the implementation order. `--warmup=N --samples=N` override those counts.
Diagnostic snapshots, comparisons and PNG encoding occur outside the measured
presentation interval. These are renderer wall measurements, not live FPS or
sustained mobile-camera results. Recorded SwiftShader rendering is measured only
for correctness and contributes no performance samples.

The receipt schema is `ar-performance-candidate-matched-v1`. Each case contains
whole-image current/candidate and archived pixel comparisons, full pose/surface/
protection geometry comparisons, source/detection/mask ownership, optical/nose/
outside-editable/background checks and native RGBA hashes. Four native PNGs are
saved per case for independent inspection. Lifecycle controls cover invalid-pair
fallback, no-face clearing/reacquisition, private preparation, overlapping prepare,
double finish, abort during a prepared frame and pre-aborted initialization.
The independent Python audit uses Pillow and NumPy to reread every current,
candidate and archived PNG, recompute full-image equality and safeguards, compare
the full saved geometry and reverify runtime/private-input hashes. It creates a
new `independent-png-audit.json` beside the report and refuses to overwrite one.

The disposable Playwright context grants local-network permission only to its
local QA origin. Its request routing replaces `/@vite/client` with inert HMR
hooks, so an unrelated UI edit cannot reload a running native comparison. Actual
renderer module imports, local model assets and normal app pages are unchanged.

The live scheduler and current/test switch are checked by the separate live tests.
Generated images and selected historical stills do not establish realistic motion,
physical hair depth, anatomical fit or owner acceptance of the candidate.

## September 9 candidate evidence

The [completed first-presentation receipt](output/matched-2026-09-09T10-31-18.167Z/report.json)
passes all **32 generated and 24 exact recorded comparisons**, covering both
glasses, both hair models, down/up/both yaw and nose/front checks. Current,
candidate and archived accepted/hair RGBA, full geometry and source/detection/mask
ownership match. Both phases also pass the lifecycle controls and return to their
first pair after other poses. The recorded `original-55` pair has all three
visibility weights and rear drop exactly zero and is tested first after renderer
creation for each glasses model, before any head-mask target has been rendered.
The [independent audit](output/matched-2026-09-09T10-31-18.167Z/independent-png-audit.json)
rereads 426 files and verifies full PNG equality, raw hashes, geometry and
protected/nose/outside/background/alpha invariants; 129 runtime and 302 frozen
input receipts were reverified. Native generated Tom Ford/down and Amber/up output
images were also inspected visually. This is bounded still preservation, not
owner acceptance of live motion.

The earlier [warmed comparison remains failed](output/matched-2026-09-09T10-24-15.890Z/report.json).
Nine cases passed, then `05-left-profile-waves / tom-ford-clear /
selfie-multiclass` differed at one **protected optical/nose pixel**, `(452,262)`,
with maximum channel delta 28. The current renderer produced RGBA
`[189,181,167,255]`; candidate and archive both had `[201,159,139,255]`. The preceding
current hair-only case used the same source/pose/frame and matched the archive.
Hair-stage guards preserved each renderer's own native pixel; that does not
establish cross-pipeline protected-pixel equality in this failed run. No tolerance
was relaxed, and no renderer change was made in response.

A [fixed 12-presentation probe per implementation](output/matched-2026-09-09T10-29-41.705Z/report.json)
on that exact original pair found one stable accepted/hair hash per implementation:
all 24 presentations matched the archive and each other. It did not reproduce the
earlier multi-pose warmed variance and does not establish its cause or resolve it.
The successful 56-case run does not replace that failure. Warmed renderer timing
evidence remains incomplete; this QA does not claim a complete speed improvement.

Two tooling failures are also retained: the [first HMR run](output/matched-2026-09-09T10-22-02.200Z/report.json)
was stopped after 18 exact cases because localhost HMR permission errors would
fail the harness; the [initial bounded-probe attempt](output/matched-2026-09-09T10-28-27.069Z/report.json)
was interrupted by a development reload before rendering any case. The scoped
permission/HMR isolation above fixes only the QA environment. Context-loss and
GPU-initialization failure injection are outside this renderer-only harness;
live scheduling and worker lifecycle remain separate tests.
