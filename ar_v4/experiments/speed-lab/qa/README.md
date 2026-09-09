# Speed lab QA

This is a separate renderer study against the committed Test 2 base. No camera,
face detector or hair inference runs in the matched harness. Previously frozen
images, detections, geometry and category masks remain byte-for-byte inputs.
Private files are routed locally from their original archive paths; new reports
and PNGs go only into unique ignored `output/` directories.

`base-manifest.json` pins 186 files from `8baa16c`. The harness verifies its exact
manifest hash and every listed file before and after a run. This replaces no
historical preservation boundary: earlier Test 1/2/3 receipts and manifests remain
unchanged. The original `.recovery` inputs are required for this optional study.

These historical hashes pin the original local byte representation. Six protected
text files use CRLF locally but LF in Git, so a raw staged export or LF checkout
fails this optional byte-level manifest without a content regression. Keep the
manifest and receipts unchanged; new portable studies need a separately recorded
Git-blob boundary. G promotion retains the tested renderer/options; initial
selection, labels, metadata and tests have since changed. Historical whole-tree
runtime receipts describe their recorded source boundary, not the new default UI.

```powershell
node experiments/speed-lab/qa/preservation.mjs --verify
node --test experiments/speed-lab/qa/protocol.test.mjs
python -B -m unittest discover -s experiments/speed-lab/qa -p test_audit_protocol.py
node experiments/speed-lab/qa/matched.mjs --preflight --profile=source
```

Start the separate speed-lab development server on port 8082. Run one heavy
browser job at a time, separately from live camera and performance measurements.

```powershell
node experiments/speed-lab/qa/matched.mjs --profile=source --phase=generated32 --source=03-up-blonde-waves
node experiments/speed-lab/qa/matched.mjs --profile=source
python experiments/speed-lab/qa/audit-pngs.py <new-report.json>
```

Profiles are `source`, `warm`, `copies`, `async` and `combined`, using the actual
options from `profiles.ts`. Defaults are zero warmup and zero timing samples:
one presentation per pair, with renderer sessions reused per glasses/phase.
Source preparation for the ownership factory occurs before any optional timed
renderer sample. Both implementations' `present` and `prepare` are awaited;
`finish` remains a synchronous publication. An asynchronous function's promise
is never mistaken for a successfully presented frame.

The complete set contains 32 generated and 24 wearer pairs, both glasses and hair
models, down/up/both yaw, and original nose/front checks. Generated cases retain
Intel D3D11 and opaque sRGB decoding; wearer cases retain their historical
SwiftShader/default-canvas JPEG decoding. Original-55 is first after creation
for both glasses, and return-to-first-pair controls exercise earlier poses again.
A source selection is explicitly partial evidence. No tolerances are relaxed.

Each pair compares full accepted/hair/background RGBA, source and detection
identity, exact geometry, alpha, optical/nasal/outside-arm/background protections
and original nasal regions against both the archive and unchanged Test 2.
Mechanism checks separately require the selected optimization to be used: source
reuse removes the clean draw and one full download; async uses completed PBO
retrieval with its actual calls/bytes; copies borrows the owned source; prewarm
records its first-use discarded read. Expected nonzero rear-temple work remains
an additional native read. Aggregate counters must include PBO retrieval and
prewarm, so omitted transfer bytes cannot appear as a speed improvement.

Controls retain invalid-mask fallback, no-face/reacquisition, hair off, accepted
first then hair toggle, private preparation, overlapping prepare, double finish,
pending cancellation and pre-aborted creation. Additional copy controls revoke
and zero the borrowed canvas after finish, then require exact independent Hold
and export. Missing-source controls require exact native fallback with an explicit
reason; fallback never passes an eligible fast-path check.

The optional `--allow-async-fallback` flag applies only to `async` and `combined`.
It permits the recorded SwiftShader phase to preserve correctness after the exact
declared 500 ms bounded-fence timeout, with no PBO bytes retrieved and the full
synchronous same-pair read counts accounted for. Other failures remain failures;
generated hardware cases must still complete their PBO reads. This flag changes
only QA acceptance of that explicit software fallback, with no runtime timeout
or pixel/geometry/guard tolerance change. Per-case `mechanism` and aggregate
`mechanismSummary` distinguish actual fast paths, actual PBO retrievals and
fallbacks. Counts describe saved case presentations, excluding controls and
earlier optional warmup/timing repetitions. A fallback is never a speed win.

```powershell
node experiments/speed-lab/qa/matched.mjs --profile=combined --allow-async-fallback
```

The independent Python audit rereads archive/output PNGs and rechecks full RGBA,
geometry, guarded regions, raw hashes, mechanism byte counts, runtime hashes and
the 186-file base manifest. It writes a new audit only and refuses to overwrite
one. Control PNGs are not retained, so their results remain browser observations.

This harness makes no claim about fresh-frame scheduling, worker contention,
moving-camera latency or phone smoothness. Those require the separate production
live tests and sustained measurements. Earlier warmed native protected-pixel
variance in `performance-candidate/qa/output/matched-2026-09-09T10-24-15.890Z`
remains a reproducibility limitation; later passing single-presentation cases
do not erase that failure.

Source-only full study `output/matched-2026-09-09T15-39-58.813Z/report.json`
passed all 56 cases and 16 control groups. Its independent PNG audit passed all
56 cases, 613 artifact/base files, 165 then-frozen runtime files, 302 preserved
inputs and the 186-file base manifest. Source reuse was used in all 56 cases:
the clean draw was skipped and one full RGBA read was removed. All 12 nonzero
rear-drop cases retained their additional branch read; 44 cases skipped it.
There were 36 visible hair edits and 20 expected no-edit pairs. These are exact
still/mechanism results, not live speed measurements.

The first strict combined study remains failed and unchanged at
`output/matched-2026-09-09T15-43-32.377Z/report.json`. Its 32 generated hardware
cases and eight control groups passed; the first recorded Original-55/Tom Ford/
hair-only case reached the 505.4 ms SwiftShader fence timeout (107 polls, one
queued RGBA image, zero retrieved PBO bytes). Its exact synchronous fallback
and first-use prewarm were both counted: two CPU reads totaling 7,372,800 bytes.
All 27 pixel comparisons and 33 geometry comparisons matched, and all four saved
PNGs were byte-identical; only the required actual-PBO protocol failed. This
receipt is retained as a strict mechanism failure. Future explicitly permitted
software fallbacks must remain separately counted and independently audited.

The final explicit-fallback combined study
`output/matched-2026-09-09T15-57-58.719Z/report.json` and independent PNG audit
pass all 56 cases and 16 control groups. All 32 hardware cases use PBO; 19/24
software cases use it and five preserve exact pixels through the declared
bounded-fence fallback. No fallback is counted as a PBO gain. Source reuse and
borrowing are used in all 56, with four initial prewarms. All 181 runtime/QA
hashes, 302 preserved inputs and 186 base files match.

The separate procedural source-edge study passes 36 comparisons and eight
controls; its independent audit verifies 302 PNGs. Its fixed pose has no actual
hair replacement, so hair-edit coverage comes from the archived matrix.
Current production/lifecycle findings are in [REVIEWS.md](../../../docs/REVIEWS.md).
