# glasses3d progress

Started fresh on 2026-10-10 when WP6 was rebuilt. The previous log (to 62b25e4) and the WP6 v1 design are in
[archive/20261010](archive/20261010/README.md); older snapshots are in [archive/20261008](archive/20261008/README.md).
Archived files are evidence, never instructions. The WP6 v1 handoff, the two session prompts and the Astra temple lab
plan were deleted on 2026-10-13 (tag `review-20261013-pre-cleanup`).

**Full-codebase review 2026-10-13: [REVIEW_20261013.md](REVIEW_20261013.md)** (status per WP from the code, the
automation gaps, ranked defects, cleanup). Read it before starting WP2 D–F, WP7 or WP8.

**Active plan from 2026-10-13: [ASSEMBLY.md](ASSEMBLY.md)** (owner: Claude plans, Codex executes). Assemble the tools
into one automation in milestones M0 (blocking fixes) → M1 (job intake, registry v1, offline end-to-end run) → M2
(instructions, image policy) → M3 (delivery, owner review ledger) → M4 (seating, temples, print as stages) → M5 (paid
validation, owner OK per run). **M0-M4 are complete and proven offline**, including the owner-approved rimless
matte expansion and the final full regression suite. **M5-prep items 1-4 are complete at $0**: the $20 working
budget, unfinished draft delivery, isolated one-request smoke support, and the real Oakley job with a proven
intake and unapproved run-1 estimate. **M5-prep and M5 step 0 are complete**: the one approved real smoke passed
at $0.211823. **M5 Oakley run 1 was approved and attempted on the corrected job; it failed at the first render's
checkpoint verification after $0.828903. No delivery was published.** The $0 reproduction and accounting audit
are below. **The owner authorized the checkpoint/failed-draft repairs and exactly one fresh run on the same
corrected job**, conditional on the repairs, proofs and full suite passing and a new estimate under $20. Terms:
$20 settled stop, $23 reserve cap, max effort; not a resume. **Repairs committed as `123dcca`; the authorized
fresh run completed and delivered at $19.161346 on c0021, with specification and seating passing. Webcam review
is running.** Appearance limitations remain below; M5 owner visual acceptance is pending. No other paid run is
authorized or started.
Claude's independent rerun of `tests.proofs.assembly_m1`
passed (90.0 s, 11 scripted requests, delivered, receipts clean, $0). The stray repo-root `data/assembly/
m1-proof-b8bb88d4` was moved to `glasses3d/data/assembly/m1-proof-b8bb88d4-from-root`. Every paid run still needs
an estimate and the owner's explicit approval.

## Assembly M5: authorized fresh Oakley run (2026-10-13; completed, awaiting owner review)

All five requested $0 repair/verification steps passed before this run, committed as **`123dcca`** on branch
`glasses3d`, clean checkout `C:/Users/Shay/PycharmProjects/lenses`. No worktree, push, paid reproduction or
hand-authored product edit. Only the corrected job **`oakley-oo9208-v2-3e057bab`** was used.

**Fresh start, not resume:** preserved the complete failed-run job in `data/assembly/m5r1frozen/` and moved only
active session/checkpoints/job-scene state/heartbeat to `data/assembly/m5r1active/`. The old run records,
receipts, money ledger, input photos and seed stayed intact. Each preserved file was byte-verified (detach
specifications copied opaquely, never inspected). The reset helper initially passed `objects:None`, selecting
no objects in its verification query; corrected the helper to `objects:"all"` and independently verified the
already prepared state, without repeating the reset. Evidence `data/assembly/m5r1-fresh-reset.json` confirms
empty c0000, only the EMPTY origin marker, no conversation, no product geometry and unchanged inputs/ledger.

Fresh request **`20261013T173404-2accf4`**, request SHA
`8ffc6c1f5ef996a9263d78cbcd78c9c807c1898e8bba4c5bf88aa053cf0a25f5`.
Estimate **$14.957821**, range **$13.200719-$20.117690**, met the owner's expected-cost-under-$20 condition.
Recorded these exact owner words through `job approve --max-usd 23`:

> I approve a fresh Oakley run on the same job (oakley-oo9208-v2-3e057bab), not a resume, on the same terms: $20 settled stop, $23 maximum reserve, effort max. Start it only after steps 1-5 pass and its new estimate is under $20. When it ends, start job review and report as before. Do not start any other paid run.

Bound settings were **$20 settled stop / $23 maximum reserve / max effort**, `resume:false`, zero history,
all six photos newly supplied. Independent live-start audit checked approval/config/consumed-ledger bindings,
all seven input hashes, empty c0000 and the real provider request's model/effort. Started once, detached through
`job run`, using only the existing runner's `--env-file` loader. No credential file was read or printed by the
agent. BLAS 1, descendant EcoQoS opt-out; no competing heavy pool. Keeper PID 32504, runner PID 30884, Blender
PID 46044. Forty-second process/heartbeat/money monitoring: `data/assembly/m5oakley/fresh-monitor.jsonl`.

**Outcome: completed, accepted finish (not a draft)**. Started **17:34:27 UTC / 20:34:27 Jerusalem**, finished
**18:32:43 UTC / 21:32:43 Jerusalem**, **3,496.759 s (58 min 16.8 s)**; detached runner exited 0 at 18:32:44 UTC.
**65 API requests, 65 tool calls**, no continuation or second run. No teardown errors. Checkpoint **c0021**;
the exact old fingerprint failure did not recur. Model-code refusals/geometry failures were returned to Astra
and corrected by Astra through the tools. A topology-valid lens was achieved before seating, then the normal
seating gates passed. The specialist temple stage repeatedly refused; no specialist result is claimed.

**Money and receipts:** actual fresh-run settled spend **$19.161346**, final held **$0**, unknown **$0**. All
**65** charges were re-priced from provider usage; **130** request/response receipts verified. Largest individual
hold **$3.700638**; every request began below the settled stop and total committed spend stayed within $23.
The same job's aggregate, including the original failed run's **$0.828903**, is **$19.990249**. This aggregate
does not include the earlier smoke on its separate job. Seven audit groups passed: approval binding,
money/ledgers, transport receipts, referenced output blobs, six initial photos, immutable delivery and evidence
unchanged (**263** read files). **27** unique blobs verified across **195** receipt documents. Evidence:
`data/assembly/m5oakley/fresh-audit.json`; source ledgers and receipts at
`data/jobs/oakley-oo9208-v2-3e057bab/runs/20261013T173404-2accf4/{budget.json,transport/,result.json,transcript.md}`
and `data/jobs/oakley-oo9208-v2-3e057bab/budget/job-ledger.json`.

**Immutable delivery:**
`data/jobs/oakley-oo9208-v2-3e057bab/deliveries/20261013T173404-2accf4-cf35d209a42d0149c739654e5e046f58a46d405c93ea362e11ef629a7f675904/`.
GLB SHA **`a2814e098ce88aafc17c746e05af288d54f73eb28d82cb1d9f61044912279c23`**, **1,605,328 bytes**, **74,150
triangles**; reported width **155.65 mm** (estimated scale, no supplied physical measurements). `report.json`
contains Astra's complete finish summary and the bound checks. **Specification 7/7 rows pass**, no errors or
warnings. **Seating passes**, no reasons/refusals: 541 PASS, 1 TRANSITION, 366 VISIBLE_BY_DESIGN;
`share_ge_pass=0.9982`, `share_ge_target=0.9945`. The embedded seating report also records an over-capture
warning: 150 points, maximum 1.2977 mm against its 0.6 mm warning limit; this does not fail seating and the
lens was not shrunk to hide it. Print check passes as not required. **Fixed-scale AR: seven
poses, exit 0, captured_with_warnings, no errors**. Five warnings cover two lens-decal classifications, two
overlap-window saturation readings, and the AR arm heuristic treating the rearward-wrapped brow as arm geometry.
The earlier zero-area temple-triangle warnings are absent from the final capture.

**Astra's summary, condensed (not an owner likeness verdict):** complete curved shield, vented black brow,
retaining rail/locking pods, hinge details, silver oval emblems, faceted swept temples, sleeves, nasal carrier,
rubber pads/ribs and small OAKLEY/PRIZM markings. The exact photographic interference coating is **not fully
matched**: Blender thin-film values were not carried into `KHR_materials_iridescence`; the delivered coloured
specular coating gives moving green/cyan highlights over violet transmission, but the green is too highlight-like
and photo-camera renders remain too violet. All six photos informed geometry. Final basis-01 contour means
are approximately 0.64 mm front, 1.06 angled, 1.42 right, 1.01 rear and 1.20 left; these do not certify likeness.
Top fitting retained an approximately 90-degree image-plane orientation error after a targeted retry (IoU
0.3233); Astra inspected custom rolled top renders instead. Facets, far-shield side silhouette and tiny lettering
remain approximate. The temple specialist never accepted the authored knuckle axes. The rearward brow still
triggers the AR positional-arm warning. Scale is estimated. These limitations remain open for owner review.

**Webcam review started** through `job review ... --port 0` and verified ready/rendered at port **52798**,
detached server PID **24984**, bound to the immutable delivery SHA above, fixed lens environment intensity 1.
The app browser-open request was queued. Full launch URL and identity are in the job's `review` records.
AR contact sheet (immutable final delivery):
`data/jobs/oakley-oo9208-v2-3e057bab/deliveries/20261013T173404-2accf4-cf35d209a42d0149c739654e5e046f58a46d405c93ea362e11ef629a7f675904/contact-sheets/01.jpg`.

**Additional host diagnostic issue found, left open:** `temple_lab/package.py` replaces any unsuccessful
`export_head` with "parent export does not match its current checkpoint". In call 25, c0005 had unsupported
`partRole='hinge'` values and no cache entry; the source evidence supports export refusal, not a stale cache.
A later general fix should preserve the original export refusal and reserve the mismatch message for an
otherwise successful export with a genuinely wrong binding. No active-run patch or role-gate relaxation made.
No other paid run was started. M5 owner visual acceptance remains pending.

## Assembly M5: checkpoint and failed-draft repair (2026-10-13; $0; commit `123dcca`)

Owner request: identify the exact unstable property, repair the general checkpoint method, prove a mixed Blender
scene and the original tool journals round-trip, salvage failed runs from their last verified checkpoint, run
the full suite and commit before any new paid request. The owner also approved one **fresh** run on
`oakley-oo9208-v2-3e057bab` after those steps pass and its fresh estimate is below $20, with the same $20 settled
stop / $23 maximum reserve / max effort. The older `cd068969` job remains out of scope.

**Exact cause:** the field-by-field $0 comparison of every `_curve_digest`/`_rna` component found only these
automatic texture-space changes in the orphan curves left by text-to-mesh conversion:

| Datablock | Property | Before save/reload | After reload |
| --- | --- | --- | --- |
| Centre Oakley micro etch | `texspace_location.x` | 1.8925000429153442 | 1.8924999237060547 |
| Centre Oakley micro etch | `texspace_size.x` | 1.847499966621399 | 2.8474998474121094 |
| Centre Oakley micro etch | `texspace_size.y` | 0.3500000238418579 | 1.3499999046325684 |
| Prizm lower corner etch | `texspace_size.x` | 1.3245002031326294 | 2.32450008392334 |
| Prizm lower corner etch | `texspace_size.y` | 0.3410000205039978 | 1.3410000801086426 |

Both have `use_auto_texspace=true`. Every other raw curve component matched, and the original checkpoint SHA
stayed unchanged. Evidence: `data/assembly/m5curve-components/{before-components,after-components,diagnostic}.json`.
The converted leftovers expose `Curve` RNA; the separate live/orphan/fake text fixtures retain FONT content.

`g3d/checkpoint.py` now excludes texture-space bounds **only in automatic mode**, retaining manual bounds and
the mode flag. `protect()` retains zero-user members of all 23 collections already fingerprinted. Broader
fixtures exposed two more serialization differences: factory `ParticleSettings.trail_count` 0 becomes 1 on
reload (canonicalized to the RNA minimum), and volume file paths are remapped by save-as (canonicalized to the
absolute target, including Blender's empty factory-path behavior). Valid trail-count and real path changes
remain detectable. Value/RGB node output values and action custom properties are now fingerprinted too.
No blanket exclusion, fingerprint re-stamping, original checkpoint edit or quality-gate relaxation.

Real Blender mixed-scene regression: **1/1 passed in 8.84 s**, with **43 explicitly retained ID fixtures and 64
authored-content mutation controls**: live/orphan/fake text, font and formatting, converted leftovers, Bezier
and NURBS geometry, meshes/modifiers, node materials/groups, generated/packed pixels, and absolute/relative
volume paths across save directories. Evidence: `data/assembly/m5-roundtrip7.log` and
`data/assembly/m5-roundtrip-pytest7/test_mixed_scene_roundtrip_and0/report.json`.

**Second independently reproduced host defect:** the exact replay exposed a render-only change to the user
scene's `length_unit` (MILLIMETERS to METERS). Setting the temporary scene's unit system makes Blender's update
callback act on the current context scene; the minimal reproducer also showed GRAMS to KILOGRAMS. The renderer
now scopes those writes with `bpy.context.temp_override(scene=sc)`. Authored unit fields remain fingerprinted.
Real Blender regression **1/1 passed**, covering **8 scenarios** (metric/imperial, either authored scene active,
success/injected exception); every unit field, fingerprint, original context and cleanup matched.
Evidence: `data/assembly/render-unit-callback.json` and
`data/assembly/m5unitstest/test_temporary_render_units_ne0/report.json`.

**Exact original journal replay passed at $0** in 12.484 s: all **5 tool calls** (of which **3 contained code**),
with the original byte-bound arguments/snippets and original static refusal preserved. Save/restore matched
after every call, rendering created 12 images without scene drift, a fresh worker reopened c0001 exactly, and
all 91 original evidence files stayed unchanged. No model transport was constructed or external network used.
Evidence: `data/assembly/m5r1replay3/proof.json`, log/status/receipt `data/assembly/m5fix_replay3.*`.

**Failed-run draft delivery:** `failed` now selects the newest checkpoint whose original bytes and reopened
fingerprint verify, in a private worker, with every attempted checkpoint recorded. Only an integrity failure
can fall back to an older checkpoint. An export or quality failure never selects a more flattering older
model: normal export refusals remain refusals, and validation/seating/print/AR failures are reported in drafts.
The active worker must be closed; job head, best marker and journals do not move. A finalized failed result
raised through `SystemExit` is also salvaged before the original signal propagates. Focused tests **139/139**
plus the SystemExit follow-up **1/1** passed. Real Blender/AR failed-draft proof **15/15 passed in 17.938 s**,
including failed checks kept visible, immutable delivery, review and source preservation. Evidence:
`data/assembly/m5fd/proof.json`, `data/assembly/m5_failed_draft.*`. Independent code reviews found no blockers.

Full Python suite: **3,463 distinct tests passed** across the required main run and reconciled reruns. Main run
used `-n 8 --dist loadfile`: 3,143 passed, two failures (the known EcoQoS-watcher interference and the 60-second
render check under load), four historical-fixture setup errors, 718.65 s. Photoprint then used `-n 2 --dist
loadfile`: **314/314 passed in 213.90 s**. The QoS test passed alone with the descendant watcher disabled
(1/1, 0.30 s); complete `test_wp2_seeing.py` and `test_seeing_v3.py` passed sequentially alone (**54/54**, 113.11 s).
No time limit or assertion was changed. Logs/status/receipts: `data/assembly/m5fix_main.*`, `m5fix_print.*`,
`m5fix_isolated_qos.*`, `m5fix_seeing.*`.

The four setup errors were a test fixture importing a historical run-7 checkpoint index made by the older
fingerprint algorithm. This test evaluates seeing on revision-7 geometry, not historical-session compatibility.
It now verifies archived c0007 bytes against the original SHA (`3b4435b27313854367aec10c389087c9cad3b36d3d9311b03c1d3aaeed5924e1`)
and uses those exact bytes as an isolated test seed with a fresh checkpoint chain. New SHA/fingerprint restore
is explicitly checked; original index, original model and copied seed bytes are checked unchanged at teardown.
All visual assertions, photos, cameras, geometry and thresholds are unchanged. Independently reviewed.

AR: **497 unit tests + 103 QA tests passed** after isolated reruns, and `tsc --noEmit` passed. The concurrent
first pass had one hair-scheduling timing failure (496/497) and one Chromium connect-abort timing failure
(102/103); the entire affected modules then passed alone (**12/12** in 19.72 s and **5/5** in 36.55 s) with no
code/assertion change. Logs: `data/assembly/m5fix_ar_{unit,qa,check,hair,stall}.*`. `git diff --check` passed;
the complete runtime/test diff was independently reviewed. Repairs, proofs and full-suite verification are
complete at **$0**. The accompanying implementation commit precedes the fresh estimate/approval/run.

The first run's model still has unsupported part roles; it was not hand-corrected into a deliverable. The fresh
run will start from the empty job seed after preserving all failed-run evidence. M5 owner visual acceptance
remains open; no product quality result is inferred from these tool tests.

## Assembly M5: corrected Oakley run 1 (2026-10-13; failed, preserved for diagnosis)

Verified the requested checkout `C:/Users/Shay/PycharmProjects/lenses`, branch `glasses3d`, clean working tree,
and HEAD `f3a0e10` before approval. Used only Claude's corrected job **`oakley-oo9208-v2-3e057bab`** and existing
run request **`20261013T163235-772629`** (request SHA `53181395cde6d5c2cd83a70bff7fc4f349ed9df9bdc9d61dd4a63be04e96c5df`).
The older `cd068969` job was untouched. Numeric estimate: **$14.957821**, range **$13.200719-$20.117690**.

Recorded these exact owner words with `job approve --max-usd 23`:

> I approve Oakley run 1: job oakley-oo9208-v2-3e057bab, run request 20261013T163235-772629 (estimate $14.96, range $13.20-$20.12), with the $20 settled stop and $23 maximum reserve, effort max. Do not use the older job oakley-oo9208-v2-cd068969; Claude replaced it (see PROGRESS.md Decisions, commit f3a0e10).

Approval bound **max effort, $20 settled stop, $23 reserve cap**. Started once through detached `job run` with
the existing `--env-file glasses3d/.env` loader; no credential file was read or printed by the agent. Keeper PID
9708, runner PID 20392, Blender PID 38984; detached start 16:34:56 UTC / 19:34:56 Jerusalem. BLAS threads 1,
descendant EcoQoS opt-out, no competing heavy pool; approximately 17 GiB free commit at launch. The monitor
checked heartbeat, requests, tool progress and worker activity every 40 seconds and reported to the owner.

**Outcome: failed**, exit 1 at **16:39:22 UTC / 19:39:22 Jerusalem** (run duration 262.78 s). Five API requests,
five tool calls, no continuation. One capability-inspection snippet was statically refused and Astra corrected
it; then Astra built **28 mesh objects** in checkpoint **c0001**. Its first `render_views` wrote 12 PNGs (four
views, native/clay/normal), but the read-only operation's checkpoint restore failed:
`c27ae78f2a5b93963b2ddde432591d37dc5eb90c19e17a8f1643f45af50d7261 != bbc084bc33c76e029e0ce48ca829b6641a54684c4240673c55beffcd19c7182c`.
The original c0001 `.blend` SHA is `fce890aea9ff39eff87c068d823f6abca43dc24618de4a44037e4a608b1b71de`;
the run records `exit_checkpoint.verified:false`. No subsequent paid request was started.

**Actual settled cost $0.828903**, maximum individual request hold **$1.167888**, final held **$0**, unknown **$0**.
Six independent evidence checks passed: bound approval/settings, re-priced run ledger, ten transport receipts,
result/job-ledger agreement, all six original photo blobs, and explicit absent delivery. The audit also confirms
33 evidence files unchanged. This is an accounting/integrity pass, not a successful product run.
Evidence: `data/assembly/m5oakley/run1-audit.json` and `data/assembly/audit_oakley_run1.py`; the job's
`runs/20261013T163235-772629/{result.json,budget.json,transcript.md,runner-status.json,monitor.jsonl,transport/}`
and `budget/job-ledger.json` hold the original run records.

**Astra summary:** no final summary was produced. Its public pre-build message was:

> I’ll reconstruct the curved shield, vented brow and faceted temples from all six views. For the lens, I’ll test curvature and an angle-dependent coating so the green/cyan region shifts with the view rather than becoming a painted patch. Since no measurements are supplied, the AR scale will be an estimate.

**Validation and seating:** neither was reached; there is no GLB validation result, no seating result and no AR
contact sheet. `delivery.json` honestly says `not_finished`, `run_status:failed`; `failed` is outside the current
host-draft statuses. Attempted `job review ... --port 0`; it refused `delivery file is missing or unreadable:
current.json`, so no webcam review server was started. The unvalidated native three-quarter render remains at
`data/jobs/oakley-oo9208-v2-3e057bab/work/renders/call_AcKMPcxcQz5kIXp9QdOcf30t/three-quarter_native.png`.

**$0 diagnosis on a COPY:** replayed Astra's exact saved construction code from the copied seed and compared
full scene states before/after save/reopen, independently of rendering. Reproduced both exact fingerprints;
only the two orphan FONT-curve datablocks (`Centre Oakley micro etch`, `Prizm lower corner etch`) differed.
All object, mesh and material component hashes matched. Original checkpoint SHA unchanged, zero API calls.
Independent review confirmed the diagnosis. Evidence: `data/assembly/m5r1diag/{before,after,restore,diagnostic}.json`
and `data/assembly/diagnose_run1_checkpoint.py`. The exact unstable curve field still needs a minimal reproducer.
No integrity check was bypassed, fingerprint re-stamped, source gate loosened, or product geometry changed.

Independent source review also found 18 visible objects tagged `hardware`/`nosepad`; the exporter permits only
`lens/frame/temple`, so these are a separate expected export refusal requiring Astra's correction, not a host
hand-edit. Repairing save/reopen alone would not complete this early model. Expanding M5 into checkpoint repair
and failure-draft handling needs the owner's scope decision under their stated rule. No tracked runtime code
changed and no full regression suite was rerun for this evidence-only record. M5 product acceptance remains open.

## Assembly M5-prep (2026-10-13; complete, implementation commit `bab7cb4`)

Verified the requested checkout and branch `glasses3d` at `68e1ebf`; the owner's subsequent instruction adds
`faee122`'s first-message working-limit correction and `4b9d6d2`'s temple-stage allowance correction. The owner explicitly approved exactly one smoke in advance:
**"Yes, cap $1"**, only after M5-prep items 1-4 pass and a fresh estimate is under $1, with both settled stop and
reserve cap at $1. Oakley run 1 remains unapproved and must not start. Preparatory implementation and tests are $0.

Item 1: the guard now reports the settled stop separately from reserve capacity and computes remaining runway
after settled spend plus held and unknown requests, clamped at zero. First-message run text and tool footers use
the working settled stop. At 75% of settled spend the footer tells Astra the remaining runway and to finish its
best checkpoint soon. Generic tools gain a separate final text footer, preserving their initial JSON and image
indices; ordinary SDK errors carry it too, while fatal exceptions still propagate. Nested stage contexts read the
shared guard. Legacy runs without a settled stop retain their old cap footer. Instructions add the requested
finish guidance within the unchanged 8,000-character limit (7,962 characters), preserving the owner's exact table.
No reservation or settled-stop enforcement rule changed. Initial baseline: 253 passed, four stale output-shape
assertions updated to check the added footer while retaining their prior JSON/image/error assertions. Final
targeted set passed **291 unique tests** across the final runs: 285/286 in 57.29 seconds, followed by 132/132
in 8.56 seconds after correcting one new fixture's existing `canonical:front` label and adding five boundary/error
checks. The notice uses integer micro-USD, including the exact $0.075/$0.10 boundary. Generic SDK timeouts use
their existing message plus the footer. Evidence: `m5prep_runway_{base,final,edges}.log`. The added `4b9d6d2`
requirement is implemented: a temple-stage allowance cannot exceed the smaller of remaining reserve capacity and
remaining money to the settled stop (including held/unknown requests). Fifteen new cases cover boundaries,
exhaustion, holds, invalid arguments and legacy runs. Item 1 is complete. All checks use offline transports.

Item 2 in progress: draft review accepts an explicitly unfinished immutable delivery with a failed specification
check while preserving all server, hash, appearance and infrastructure checks. Accepted deliveries still require
a passing live specification check. Saved review records include the draft's run status and failed checks; edits
that promote it to a finish or hide a failure refuse. Review unit checks: **37 passed, one real-server test
deselected, 3.23 seconds** (`m5prep_draft_review_unit.log`); exact draft changes notes enter resume input once.
The publisher now exports a hash-verified private copy of best (else head), checks specification/seating/print,
captures fixed-scale AR, and publishes immutable `finished:false` evidence without moving the source scene or
claiming an accepted finish. Each supported end status retains a delivery attempt, including a clear reason when
no usable checkpoint exists. `job show` includes the draft and its failed checks. Focused draft/delivery/CLI/review/
verdict suite: **179 passed in 16.42 seconds**, including the real review server (`m5prep_draft_unit2.log`). One
additional changed-head-SHA regression was added after that collection and awaits the next run. Independent
publisher review fixed source-variable shadowing and strengthened draft-only seating SHA/fingerprint validation;
accepted-delivery checks remain unchanged. The stage/draft edge run passed **74/74 in 15.45 seconds**, including
the additional changed-head-SHA case (`m5prep_temple_draft_edges.log`). The real Blender/AR proof passed all
**15 checks in 12.91 seconds** (`data/assembly/m5d1/proof.json`, `m5prep_draft_real2.log`): marked best c0001
was exported while head c0002 and the original checkpoint/index/history bytes stayed unchanged; failed seating
remained explicit in the report, `job show` and live review; fixed-scale AR, immutable snapshots and exact
verdict-to-resume notes all passed. The first fixture used a 260-character Windows image path; shorter fixture
names fixed that test-only path issue without changing assertions. Its diagnostic is retained in
`data/assembly/m5prep-draft-real-20261013a/path-length-diagnostic.json`. No model request, production transport,
external network attempt or paid spend occurred. Item 2 is complete.

Item 3: `job estimate --smoke` and `job run --smoke` use a distinct approval-bound policy, one request with
4096 output tokens, identical working stop/reserve cap, the full real job instructions/brief/photos, and literal
`tool_choice=required`. After the unchanged canonical tool-contract check, the executable registry contains only
`scene_summary` and `budget_status`; a shared host counter allows one call before ending. Smoke history stays in
its own run directory, leaving ordinary modeling history fresh. There are no retries, continuation requests,
mutating tools or product deliveries in this mode. The one-request forecast counts all photos as fresh input;
the historical two-turn Studio forecast remains unchanged. The live count/reservation remains the money bound.
Independent review found no blocker. Existing regression tests: **184 passed in 58.09 seconds**; new smoke
composition and SDK transport tests: **43 passed in 19.77 seconds**. First-run failures were two new fixture
mistakes (photo filenames and JSON tuple/list comparison), corrected without weakening checks. Evidence:
`m5prep_smoke_base.log`, `m5prep_smoke_new2.log`. Item 3 is complete at $0.

Item 4: visually inspected all six originals and created `oakley-oo9208-v2-cd068969` through job intake.
Labels (wearer's side, as in cameras.py): 01 front/both; 02 angled/left; 03 side/right; 04 rear/both;
05 side/left; 06 top/both. All are authoritative, copied byte-for-byte and SHA-bound. `open_frame` matte preserves
the semi-rimless shield's openings. The old `job.json.description` is empty; the actual user description is its
`specs.notes` (provenance kind `user`), copied verbatim into the new description and specifications. Old host prose
that downgraded photos was not carried forward. The specifications separately record ASSEMBLY M5 step 1's exact
scope, "frame + lenses + seating only." Intake provenance and views live in `data/assembly/m5oakley/`.
`job estimate` prepared run `20261013T155843-7c80ac`: expected **$14.966873**, growth-only range
**$13.209792-$20.127653**, 14,880 initial context tokens, 60 replies as forecast only, modelled last-request hold
$3.875263. Execution remains max effort, $20 settled stop/$23 reserve and no reply cap. The forecast is not a
spend guarantee; its historical corpus predates image pruning and contains one product. **Run 1 is unapproved.**
The real-photo scripted proof uses a byte-identical intake copy, as required for $0 tests, so the actual job does
not inherit simulated ledger spending or fabricated assistant history. The proof passed **12/12 checks in
8.25 seconds**: eight successful read-only tools/requests (six `read_image`, `scene_summary`, `budget_status`),
twelve verified image blobs, complete brief/photo/approval binding, and a host turn limit before a ninth request.
The original job, including its unapproved estimate, stayed byte-for-byte unchanged. Its copy's ledger settled
$2.314415 in **simulated** usage; **actual spend was $0**, with zero production transports or external network
attempts. Evidence: `data/assembly/m5i1/proof.json`, `m5prep_intake.log`. Item 4 is complete.

Final regression checks are running: eight workers/loadfile for non-print tests, then two workers for print;
no competing heavy pools, BLAS 1 and EcoQoS opt-out. AR unit **497/497**, QA **103/103**, TypeScript clean.
Final review found that a post-teardown Ctrl-C/task cancellation could re-raise before draft publication; the
interruption cleanup path is now implemented and independently reviewed: only a fresh, finalized stopped receipt
for this exact run can trigger salvage, then the original interruption is re-raised. Smoke/stale/malformed results
are excluded, and existing SystemExit failure classification stays unchanged. Focused draft/job/smoke/interrupt
checks passed **111/111 in 54.19 seconds** (`m5prep_interruptions.log`); the added actual SDK cancellation case
also passed **1/1 in 8.80 seconds** (`m5prep_cancel_sdk.log`).
The environment lacks an API key, but the owner confirmed "both .env have open ai keys" after
requesting their links. Added explicit `job run --env-file` plumbing to the existing runner credential loader;
the approved smoke will use `glasses3d/.env`, without exposing its contents. The agent has not read or printed
credential files. Credential-path propagation plus existing job/CLI/smoke checks passed **68/68 in 12.53 seconds**
(`m5prep_credentials.log`), with fake nonexistent paths and read tripwires. The new smoke estimate
`20261013T161246-478a2a` is **$0.256900**, estimated reservation **$0.390800**, with $1 settled stop and $1 cap;
its real live token count still controls admission. No paid request has started.

Main suite: **3,075 passed, five failed in 690.48 seconds**. Two stale mocks in the new draft tests were already
corrected and passed in the 111-test follow-up. The isolated seeing/temple-UV modules then passed **24/24 in
129.84 seconds**, including both unchanged render/subprocess timeouts. EcoQoS passed **1/1 in 0.30 seconds**
without the external descendant watcher that alters its measured initial state. No thresholds, timeouts or quality
gates were changed. Current non-print coverage is **3,120 unique tests passed**, including 22 interruption and
18 credential-path cases added after the main collection. Print passed **314/314 in 207.56 seconds** with two
workers. Final coverage: **3,434 Python tests passed** after the documented targeted/isolated reruns, plus
**497 AR unit + 103 AR QA**, and TypeScript clean. All preparatory work cost $0. No check was loosened.
Evidence: `m5prep_{main,isolated,qos_qos,collect,print,ar_unit,ar_qa,tsc}.log` and adjacent process receipts.

### Approved smoke (M5 step 0; 2026-10-13, passed)

After items 1-4 and the full suite passed, committed the implementation as `bab7cb4` on `glasses3d`, with explicit
paths and no push. Recorded the owner's exact **"Yes, cap $1"** in the smoke approval and executed
`job run --smoke oakley-oo9208-v2-cd068969 --run-id 20261013T161246-478a2a --env-file glasses3d/.env` through the
existing credential loader. There was **exactly one real request** and one successful read-only `scene_summary`
call; it ended `completed` with `smoke.ok:true`, no retry/continuation or modeling work.

**Actual settled cost: $0.211823 (211,823 micro-USD)**, against the fresh $0.256900 estimate. The live count was
16,227 input tokens; the guard reserved **$0.407638**, below the approved **$1 settled stop/$1 reserve cap**.
Both request/response receipts are present, all six original photo blobs re-hash correctly, and the run ledger
re-prices successfully. The job ledger contains one settled request, with **$0 held and $0 unknown**. Tool journal
and normal-history checks confirm read-only operation and an isolated smoke conversation. No product finish or
delivery was claimed. Runner exited 0 at 16:21:19 UTC. Audit: `data/assembly/m5oakley/smoke-audit.json`.

Receipts and run ledger are under
`data/jobs/oakley-oo9208-v2-cd068969/runs/20261013T161246-478a2a/` (`transport/`, `tools/`, `input-receipt.json`,
`transcript.md`, `budget.json`, `result.json`); the cumulative ledger is `budget/job-ledger.json` in that job.
This is usage-priced settled cost from the pipeline ledger; no secrets or environment files were committed.

Refreshed the **unapproved** Oakley run-1 request after smoke startup created the empty seed, so its input binding
is current: **`20261013T162219-edaa19`**, replacing the earlier pre-seed estimate for approval purposes. It remains
**$14.966873 expected**, growth-only range **$13.209792-$20.127653**, $20 settled stop/$23 reserve, max effort,
60 replies for forecasting only. The actual modeling run has no reply cap. The six labels above are unchanged.
**Oakley run 1 has no approval or run ledger and was not started.** No open M5-prep defect; further paid product
validation remains the owner's decision.

## Assembly M0 (2026-10-13, $0; complete, implementation commit `27db2c0`)

Started in the requested repository checkout on branch `glasses3d`, at `02b0a16`; no worktree, push or paid API call.
Implemented all eight M0 items:
- one fixed paid-run lock under `glasses3d/data/` for lab packages and ordinary jobs;
- every head export carries `validate_spec`, declared-seat checking and the validation's GLB sha256; cached by
  actual bytes within a Seeing instance, with old export indexes validated again (never trusting old reports);
- facet finish refuses an invalid export as `E_EXPORT` and keeps the run open;
- a bounded Git diff pathspec preserves snapshot exclusions/deletions without Windows argv growth;
- three free token-count attempts with 0.5/1-second backoff; inference still never retries;
- `turns_used` comes from the interaction log in `finally`, including abnormal exits;
- print fixture pools capped at two, with the split/loadfile full-suite commands documented;
- approval-bound `RunSettings.settled_stop_usd`, separate from the reservation cap. The generic job path will set
  $20 settled / $23 reserve; legacy requests keep their existing settings when the new field is absent.

Technical choices: validation failures remain inspectable by seeing tools; finish refuses them. A malformed declared
seat cannot silently skip checking. Offline tests isolate the fixed lock constant inside each test, while separate
job folders within a test still contend. No gate or numerical threshold changed.

Initial checks: export validation 6/6; snapshot regressions plus existing dirty-source check 4/4; facet-finish checks
8/8. Budget/loop/ledger targeted run: 187 passed, one new assertion corrected, then 8 stop/cap/approval cases passed.
Full suite: **2,465 passed after isolated reruns, 11 skipped**. Main run: 2,224 passed, 11 skipped, 4 failed in
603.37 s (8 workers/loadfile). The head-comparison, fit-speed and 28-image render timing checks passed alone;
the seeing module plus the two timing cases passed 24/24 in 83.71 s. The fourth failure was the local test launcher's
descendant EcoQoS opt-out changing the initial state that `test_opt_out_self_reads_back_and_is_not_inherited`
measures; without that external watcher it passed 1/1 in 0.25 s. No test was weakened. Print suite: 237/237 in
197.37 s (2 workers/loadfile, nested bake pools capped at 2). Arview: 497/497 unit, 103/103 QA, TypeScript clean.

Evidence: `data/assembly/m0_{main,rerun,qos,print,ar_unit,ar_qa,tsc}.log`, with arguments, UTC timestamps, PIDs and
exit codes in adjacent receipt/status JSON files. BLAS threads = 1, EcoQoS opt-out, no overlapping heavy pools.
Open: the 11 pre-existing stale WP4 proof skips remain for M4a; WP5/WP6 owner closeouts and historical quality
limitations are unchanged. No open M0 defect. Next: M1 job intake, generic scene hooks and registry, offline proof.

## Assembly M1 (2026-10-13, $0; complete, implementation commit `92c0187`)

Added the generic job CLI (`create`, `show`, `estimate`, `approve`, `run`, `stop`), explicit-view photo intake,
verbatim owner facts/model requirements, an empty millimetre template, persistent scene hooks and fourteen async
tools. Runs require a single-use bound approval; defaults are max effort, $20 settled stop and $23 reserve cap.
Photos retain their original bytes and hashes; large transport copies are also bound. Intake rejects missing views,
duplicates and colliding camera stems. EXIF orientation now agrees across intake, fitting, comparison and image reads.

The scene records mutations and per-tool journal receipts, recovers interrupted mutations from the checkpoint index,
and verifies the exit checkpoint. Finish requires a validated current export and successful fixed-scale AR capture;
delivery checks the finished checkpoint again. Worker failures propagate instead of becoming ordinary compare results.
Reference rendering now supports arbitrary filenames, spaces, Unicode and canonical-name collisions, using safe
output aliases while retaining the actual photo identity. Canonical camera names and path guards are unchanged.
Camera pools use free commit memory (1.3 GiB/worker with 2 GiB spare), CPU count and a ceiling of 16; BLAS threads = 1.

Proof: `python -m glasses3d.tests.proofs.assembly_m1` built synthetic wrapped glasses, rendered three truth photos in
Blender, created a fresh job, and drove the real SDK through eight scripted calls: build, summary, fit, reference
render, compare, validated export, AR, finish. The final proof took **71.735 s** (`m1_proof3.log`), delivered job
`synthetic-assembly-proof-926ab6dc`, and verified every reference render used its fitted camera (including `front.png`
and `angle photo.png`). All original fit gates passed: IoU >0.97, contour <1.5 px, yaw/pitch errors <1.5 degrees.
Receipt audit: 16 transport files, 8 successful tool journals, 8 settled mock requests, 9 verified image blobs and
14 AR images; no unknown holds, raw image URLs or fake key in text receipts. Mock settlement was $2.542402;
**actual API spend was $0**, with non-loopback networking and production transports blocked.

The first proof used native antialiased photo fringes and failed side-view IoU (0.9521, angle error <0.1 degrees).
The known-truth fixture now colours Blender's hard silhouette, matching the existing synthetic fit fixture;
no fit gate changed. The three overlaps on the passing run before filename expansion were 0.9972, 0.9972, 0.9881.

Full Python suite: **2,588 unique tests passed after isolated/added-regression runs, 11 pre-existing WP4 skips**.
Main (8 workers/loadfile): 2,307 passed, 25 failed, 11 skipped in 730.22 s. Failures were one timing check, the
launcher's EcoQoS interference, a 300-second shared renderer timeout and its 20 follow-on failures, the lab's variant
image check under load, and one outdated assertion that `budget_status` had no timeout entry. The assertion now
checks the new tool deadlines and still refuses an unknown tool. Isolated rerun: 58 passed in 65.79 s; EcoQoS:
1 passed in 0.29 s. After the filename fix, the full Seeing module, new routing regressions and registry tests:
74 passed in 85.49 s. Print (2 workers/loadfile): 237 passed in 184.16 s. Arview: **497 unit + 103 QA passed**;
TypeScript clean. No timeout, numeric gate or quality threshold was increased.

Evidence: `data/assembly/m1_{main,rerun,qos,print,ar_unit,ar_qa,tsc,seeing,proof3}.log`, with UTC timestamps, arguments,
PIDs and exit codes in adjacent receipt/status files. No push or paid call. The first failed proof's temporary
fixture is local/untracked at repository-root `data/assembly/m1-proof-b8bb88d4`; automatic approval review blocked
its cleanup move with the stated reason "blocked by policy". It was excluded from the explicit-path commit.
Open: M2 instructions/image policy/estimate, M3 owner review, M4 specialist stages, and the existing WP4 skips.
A pre-existing library-only compare crop-name collision (`crop-01` photo plus generated `crop-01`) is not exposed by
the generic registry (which has no crop argument); keep it in view when assembling M4 packages. No open M1 tool defect.

## Assembly M2 (2026-10-13, $0; complete, implementation commit `0f2d282`)

Replaced the old WP7 proposal with object/evidence/stage teaching and an actual registry contract. The prompt quotes
the owner's seven-row model table exactly, preserves D1-D4, explains each current report, and names specialist tools
only when registered. Size: 6,836 characters now, 7,923 with all seven planned specialists, under the 8,000 cap.
Sixty-eight instruction checks passed, including retired-language witnesses and real report-field contracts.
The brief has one structured source for host prose and verbatim owner data: the retired-language check scans host
prose, while the complete owner description/specifications/names/photos remain approval-bound. This implements the
design's owner-text exclusion; it does not relax any geometry, export, budget or quality check.

Generic runs now apply their image policy before request 1 and every three requests thereafter, plus the existing
pressure triggers. Image occurrences are keyed by call, output position and digest; a new cached image cannot be
mistaken for an old one. Every image has an adjacent call/checkpoint-or-source-revision/view label. Photos and their
crops stay; superseded generated evidence becomes recoverable notes with paths, retaining the latest evidence per
view, current picks and the selected best checkpoint. `save_checkpoint(best=True)` makes that selection explicit and
durable. A new best selection can restore old images from the unchanged session on resume. Legacy facet settings
without a cadence retain their existing behavior. M4's nested facet stage must opt into the assembled image policy
and carry these labels when that stage is registered.

The scripted ten-request proof exercised epochs 1/4/7/10, identical counted/sent payloads, duplicate image bytes,
best-state restoration at resume, retained photos and intact session history. Image-policy/tools/session-input checks:
88 passed before the final additions; final affected set 71 passed in 24.26 s, plus the path assertion and the new
unspecified-view label passed in the logged late run (2/2). Fixture-only correction: the best-state test's FakeSession
needed the real checkpoint index. No production gate was changed.

Recalibrated job estimation on all eight complete paid glasses runs: 231 requests, $48.768638 recorded settlement.
The token-only fixture is reproduced from verified canonical ledgers; no prompts, photos, keys or provider IDs are
copied. The causal model uses an uncached first request, reuse of the preceding prefix, and token-exposure growth.
Honest leave-one-run-out median absolute error is **6.4634%**, within the unchanged <=10% check; independently fitting
the same method to all thirteen Studio runs gives **8.6787%**. The original Studio forecast and exact tests remain.
All 34 estimate checks passed. Individual glasses-run errors range -26.07% to +31.14%; the forecast states that the
corpus covers one product's temple stage, predates pruning and assumes no pruning savings. Live money guards govern.

End-to-end offline assembly proof passed in **71.875 s**, job `synthetic-assembly-proof-2bf7632f`: eight scripted real
SDK tool calls, actual Blender fitting/rendering, validated export and fixed-scale AR. All fit gates unchanged and
passing; 16 transport receipts, 8 tool journals/settlements, 9 verified image blobs, 14 AR images, clean receipts.
Mock settlement $2.718465; **actual API spend $0**, production transports and non-loopback networking blocked.

Full suite: **2,690 unique Python tests passed after isolated/late reruns, 11 existing WP4 skips**. Main run:
2,448 passed, 4 failed, 11 skipped in 682.30 s (8 workers/loadfile). The failures were the already-fixed fake-index
fixture, 50 ms fit timing under load, the launcher's EcoQoS watcher changing a measured child default, and the lab's
variant-image check under load. First three applicable cases passed alone 3/3 in 34.45 s; EcoQoS passed without the
external watcher 1/1 in 0.25 s. One new unspecified-view test was collected after the main run and passed separately.
Print: **237/237 in 184.55 s**, two workers/loadfile. Arview: **497 unit + 103 QA passed**, TypeScript clean.

Evidence: `data/assembly/m2_{main,rerun,qos,print,proof,root,late,ar_unit,ar_qa,ar_tsc}.log` and adjacent UTC/argv/PID/
exit receipts. `m2_image_policy.receipt.json` records the agent's captured targeted-test output. Free commit before
main/print was 22.47/23.75 GiB; BLAS 1, EcoQoS opt-out, no overlapping heavy pools. No push, paid call, loosened gate
or open M2 defect. The M1 root `data/` artifact remains untracked after its blocked cleanup. Next: M3 delivery and
owner review; the existing WP4 proof skips remain scheduled for M4a.

## Assembly M3 (2026-10-13, $0; complete, implementation commit `ebd5f47`)

Finished jobs now publish immutable, content-bound deliveries: GLB, validation/seating/cost/checkpoint report,
raw AR evidence, original contact sheets and the images actually shown to Astra. Every file's size and SHA-256
are verified; an existing delivery is never overwritten. The current pointer and flat `deliverable/` copies remain
convenient entry points, while verdicts identify immutable delivery IDs and GLB/manifest hashes. Publication requires
an accepted finish, verified exit checkpoint and successful fixed-scale AR evidence for those exact bytes.

`job review` starts the existing AR server through its Windows WMI detach path, verifies the live report and served
GLB independently, and saves which delivery was reviewed. It uses the same fixed `lensenv=1` appearance as capture
(D2), survives the launcher, refuses busy ports and never stops an unrelated server. `job verdict accept|changes`
defaults to that reviewed delivery even after another delivery becomes current. An explicit ID or expected GLB SHA
is also supported. The append-only, locked verdict chain detects edited/truncated/incomplete evidence. Notes remain
verbatim, are bound into the next approval and enter a resumed SDK conversation once according to its actual history.
Adding notes after approval invalidates the approval; no separate sent flag can lose a note on a failed run.

Migrated the actual legacy ledger without modifying its source: **7 preserved records, 3 whole-product acceptances,
4 scoped print/method decisions archived without promotion to product acceptance**. Repeating migration produced the
same result. Source SHA-256 `9b73e59170c27bd83e0d9e8c1e3744cf2e3b93f34361b6d198b6b72964a8a5a5`;
local outputs `data/ledger/verdicts.jsonl`, `verdicts.legacy.json`, `verdicts.migration.json`.

Proof: `tests.proofs.assembly_m3` passed in **82.094 s**, using fresh synthetic job
`synthetic-assembly-proof-3fcb965a`. It ran M1's actual Blender/fit/render/AR path, reviewed the immutable model in a
real detached server, recorded a synthetic changes verdict and resumed twice through the scripted real SDK.
Notes sent: **1 then 0**; all saved inputs contained the note exactly once. Both resumptions created new delivery IDs;
the first model/report/manifest/contact sheet stayed byte-identical and the live server continued serving that first
GLB. Receipt/image audits remained clean. **Actual API spend $0**; production transports and non-loopback networking
were blocked. Targeted checks: CLI 23/23, delivery 45/45, verdicts 26/26, review plus AR tool checks 59/59.

Full suite: **2,787 unique Python tests passed after isolated reruns, 11 existing WP4 skips**. Main run:
2,548 passed, 2 failed, 11 skipped in 616.70 s (8 workers/loadfile). The unchanged 28-image renderer timing check
took 120.24 s under load against its 60 s gate, then passed alone (whole test 17.08 s). The other failure was the
external launcher's EcoQoS watcher changing the initial child state measured by the opt-out test; without that
watcher it passed in 0.25 s. Print: **237/237 in 182.83 s**, two workers/loadfile with capped nested pools.
Arview: **497 unit + 103 QA passed**, TypeScript clean. No gate or threshold changed.

Evidence: `data/assembly/m3_{proof,cli,main,rerun,qos,print,ar_unit,ar_qa,ar_tsc}.log` and adjacent UTC/argv/PID/exit
receipts; targeted agent logs `m3_delivery.log`, `m3_verdicts.log`. Free commit before main/print was 23.33/23.29 GiB;
BLAS 1, EcoQoS opt-out, no overlapping heavy pools. Reviewed the complete M3 changes and the notes/verdict/proof
integration independently. No push or open M3 defect. The earlier root `data/` fixture remains untracked after the
automatic approval review blocked its cleanup move (reason: "blocked by policy"). Next: M4a, then M4b/c/d in order.

## Assembly M4 (2026-10-13, $0; complete, implementation commit `876c439`)

M4a adds five strict JSON seating tools through the journaled `g3d_call` allowlist; those helpers are no longer
accessible through snippets. Applied partial results keep their full domain report and a checkpoint, while refusals
restore the exact head. Read checks preserve failed quality results without mutation. Every export now carries the
all-frame seating check (including crystal and undeclared construction), and new finish/delivery requires it to pass.
Historic immutable deliveries retain their recorded evidence. Profile rollback removes generated cutters/Booleans
and cords before restoring the mesh, original metadata and backups, including exceptions after profile creation.

Focused checks: rollback/hash 10 passed; trusted dispatcher/facade 307 passed; real worker metal/browline integration
2 passed; export/delivery 107 passed; generic tools/instructions/CLI 135 passed before the read-envelope follow-up;
the final tool module passed 46 tests. Fresh WP4 proof: 27 cases, 262 records, no execution errors, 142 seconds with
14 workers (23.29 GiB free commit before launch). All four original blend hashes and all measured geometry/verdicts
match the prior proof; only crystal Boolean timing changed (0.353 seconds, below the unchanged 2-second limit).
`test_seat_real`: 24 passed, zero skipped, 129.71 seconds. Evidence: `data/assembly/m4a_*` logs/receipts and the
fresh `data/wp4/proof.json`; no paid API call.

The ten known WP4 quality failures remain failures: P2 71/71, P3 34/40 (+2 report-only), P4 109/112
(+2 pending readings), P5 31/32 (+1 N/A +2 report-only). Technical recommendations, without gate changes:
- Metal needs a general curvature degree of freedom: interior 0.4253/0.4262 is below 0.44 even though endpoints
  reach 0.45; a uniform 0.45 target overshoots to 0.4748, above the existing 0.47 upper limit.
- INVU needs joint node/interior constraints: interior minima 0.44885/0.4489 miss the strict 0.45 proof floor
  despite nodes at 0.450048. Do not borrow the runtime 0.005 tolerance for the stricter proof.
- FOLD remains partial/refused where curvature cannot support the move (4 direct and 21 knock-on failures;
  ungrown nasal minimum -0.305). Keep the refusal; broader curvature/propagation work needs its own scope.
- The narrow web's nominal 0.5 mm has measured capacity 0.3046–0.3067, above the 0.30 limit, so 96 moves and
  partial status are honest. Keep the zero-move proof failure and the original fixture.
- Keep `CORRECTION_AIM_MM=1e-4`: it compensates float32 error of about 1e-5, not a measurement tolerance.

An additional generic workflow replay exposed that the camera proof's lens discs were open. Its closed fan was
then too sparse to support the recognizer's local cap normals (9.0% cap area). The assembly fixture now uses the
existing default ten-ring curved plate (95.1% cap area), preserving the 48-point ellipse, wrap and 1 mm thickness.
The procedural snippet exactly matches that fixture's vertices/triangles, at 45,100 bytes. Actual Blender build,
both seats and exported checks passed in 8 seconds. The full 11-call workflow plus delivery/review/two resumes then
passed in **92.828 seconds** (`m4a_generic_proof3`, exit 0), with no unknown holds and $0 actual spend. No production
quality gate or camera threshold changed. M4a is complete; M4b is active. M4c–d and full M4 suite/commit are pending.

M4b in progress: the generic job now binds a temple-stage contract in its approval. The private stage shares the
parent's client, money guard, ledger, review log, global interaction count and stop signal; its SDK history, scene,
tool journals and image retention are separate. A stage allowance refusal is recoverable before reservation;
unknown requests, global budget/stop/turn failures remain fatal. Child receipt IDs are namespaced so identical
provider call IDs cannot overwrite parent receipts. Cancellation waits for actual child cleanup before closing
workers. The macro has no arbitrary whole-stage wall-clock limit; inference and individual tools retain their
existing deadlines. No second approval or paid-run lock is acquired.

Packages capture the current parent checkpoint/export and copy all exact photo IDs, frozen cameras and matte
bytes. Owner text stays verbatim in the bound brief, with separate neutral host prose for the instruction contract.
Explicit pose metadata is preserved; missing openings are marked as-authored, never described as fitted. The trusted
apply endpoint refuses a changed parent fingerprint. A real FT1119 capture-only check retained a subsequent
Saddle_bridge edit and all six camera/matte records in 6.031 seconds; its final Lab preparation was explicitly
stubbed, so this is not the required second-product stage proof.

M4b checks so far: 279 unique shared-loop/budget/receipt checks (276 in 92.00 seconds, then three additional
regressions; final stage module 20 passed in 20.11 seconds); generic CLI/tools/instructions/image-policy checks
142 passed in 10.51 seconds; CLI plus changed-specialist-approval refusal 24 passed in 8.87 seconds; package/Lab/CLI
171 passed, then package/CLI 51 passed after bound contract text and supplied-matte copying; trusted stale apply
1 passed. Fit/arrangement fixes passed 43 host tests. Evidence: `data/assembly/m4b_*`. Prada's real T5 rehearsal
passed **47/47 checks, 23 requests/turns, completed, $0**, in 112.17 seconds including 26.0 seconds preparation
and 83.3 seconds in the run (`m4b-t5-20261013-152145/rehearsal.json`, wrapper exit 0).

Independent review added exact child exit id/SHA, finish validity and export-byte checks before parent application;
19 stage orchestration/image/cancellation tests passed in 2.15 seconds. Owner notes now reach the child verbatim
through the approval-bound parent input, once per child history. Renamed photos keep explicit source identity while
private camera records use their copied local name; 71 package/CLI/orchestration tests passed in 7.51 seconds.
Product-specific light experiment claims and inferred/copied hinge-angle claims for missing pose data were removed
from generic host prose; calibrated records retain their exact facts. After that description-only change, CLI 42
passed and package/stage 33 passed. The second-product nested proof remains pending while UV transfer is finished.

The copied FT1119 temples have a UV seam through a rebuilt outer polygon. Per-corner transfer correctly refuses
that discontinuity rather than smearing it. The in-scope implementation choice is to add seam tessellation only
where required for UV transfer, preserving the core geometric surface and existing gates; interpolated inserted
normals need a separate error audit, since normalizing a new vertex can change interpolation inside its triangle.
The non-UV path remains unchanged. The real copied FT1119 UV build, manifold check, export validation and exact
undo passed. Later texture-generation and scaled-source regressions are still in progress; M4b is not complete.

The first real nested proof (`m4b_nested1`, copy `m4b-01c19f29`) correctly refused its original compare with
`E_NO_POSE`: the saved rigid WP3 camera fit has no explicit temple transform. Its scripted follow-up then failed
because child tools were unavailable in the parent (2 mock requests, actual $0). No gate was changed. Independent
reviews agree that a proven rigid fit used delta-zero rotation, which can be recorded explicitly without claiming
measured photo hinge angles. The bounded fix requires an explicit rigid-fit source marker and SHA lineage; unknown
or incomplete imported poses still refuse. Derived mesh axes have separate provenance, and the brief must label
these records as authored, uncalibrated transforms. Identity of posed vertices and projections is required. The
legacy FT camera report will be adapted only in its SHA-pinned proof fixture, never by product-specific runtime.
That fix now passes **29 checks** (22 new provenance/identity tests plus 7 existing camera tests) in 1.08 seconds,
and package/CLI integration **58 passed** in 7.30 seconds. Four independent camera projections and rasters preserve
the source vertices, depth, masks, face IDs, barycentric coordinates, points and normals exactly under the explicit
identity delta. Fourteen missing/tampered provenance cases refuse. Existing explicit poses remain untouched; the
runtime refuses missing geometric knuckles rather than inventing an axis. `E_NO_POSE` itself is unchanged.
Prada T5 was repeated after the UV-source helper: **47/47, 23 mock requests, completed, $0**, wrapper exit 0
(`m4b-t5-final/rehearsal.json`, 2026-10-13 12:42:43–12:45:01 UTC). No product appearance verdict has occurred.

The complete second-product proof now passes (`m4b_nested2`, copy `m4b-eecfca1e`, 87.031 seconds, wrapper
exit 0): nine mock requests share one approval/ledger; seven child requests compare four distinct heads and three
distinct alternatives, finish and apply through the trusted worker, then the parent resumes with a successful
inspection. All 23 non-temple primitives retain their geometry, all vertex attributes, materials and metadata;
the deliberate prior bridge edit is retained. Original scene, camera report, model and six source photos are
byte-identical. Actual spend is $0; this scripted control is not an appearance verdict. Final integration checks
(stage/package/rigid pose/CLI/tools/instructions): 195 passed in 10.80 seconds.

UV regression fixes reject shared target/backup mesh ownership before mutation and preserve the original partRole
on undo. The source generation helper protects live backup references. Seam intersections use the existing
EDGE_SLACK band to snap near-corner intersections onto the existing corner; directed-edge, closed-manifold and
Euler checks now reject malformed tessellation before Blender receives it. Independent review reproduced the
prior repeated corner/edge failure and confirmed the correction; no tolerance or geometric gate changed.
A real worker/journal regression passed (1 test, 4.68 seconds): after an actual complete second UV write, an
injected failed response restores exact prior UV bytes, fingerprint, checkpoint file/index and journal head.
Standalone direct-call failure restoration remains its existing pre-facet backup behavior; assembled calls use
the journal to restore the exact pre-call revision. The complete generation regression passed (1 pytest test,
19 receipt checks on both transformed/mirrored sides, 110.73 seconds): layer edits/additions/deletions and flags,
external geometry and scale/translation, independent known UV values at every output corner, exact A→B→A,
dry-run/variant nonmutation, failed-write cleanup, ownership refusals and exact original undo. Seam subdivision
legitimately changes the loop count, so this new test uses its independent authored UV function after subdivision;
unchanged-topology byte comparisons and the original 5e-6 UV tolerance remain. Final UV tests: 2 passed in
40.48 seconds (`m4b_uv_pass`), including real FT export validation. The independent concave audit's maximum surface
distance is 0.000019597 mm, corner normal difference 0.024898 degrees, interior difference 0.003921 degrees,
and authored-flat difference 0.002112 degrees. Its strict-interior lookup avoids selecting an adjacent shading fan
at a nearly collinear corner; the original 1e-4 mm and 0.5-degree limits remain. Final frozen-source Prada T5:
47/47 checks, 23 mock requests, completed, $0; prepare 35.0 seconds/run 95.8 seconds (`m4b-t5-frozen`).
M4b is complete. M4c is active; M4d and the full M4 suite/commit remain.

M4c's `photo_print` macro now uses explicit exact part names and a named frozen basis, fixed production thresholds,
memory-bounded workers and no coverage override. It preserves a fresh bake directory for every call, detaches the
requested parts' earlier print, replaces their print UVs, attaches only manifest-eligible parts, exports and checks
the manifest. Any later refusal or exception restores the pre-stage checkpoint, including its prior print. All
sub-steps share the macro's call id so checkpoint recovery produces one model-tool event. Cancellation checks
prevent a background bake from applying after cancellation. Optional calibration/registration files are confined
to the job; missing registration stays explicitly uncalibrated. Dispatcher, transaction, registry and instruction
checks: 211 passed in 7.18 seconds (`m4c_tool_second`, four workers/loadfile). The first collection exposed an
unordered set in the new dispatcher parametrization; sorting test cases fixed collection without changing checks.

The actual async SDK cancellation regression also passes: cancelling during a blocked bake sets the stop event,
the background thread retains the scene lock through cleanup, and no later mutation runs (24 transaction tests,
2.32 seconds). Print Blender checks: 13 synthetic plus 1 copied historical FT1119 passed; original bake metadata
unchanged. Five zero-area layout tests retain global face indices and finite collapsed charts; A1 still rejects
zero area, and the Blender tool returns `E_LAYOUT` without mutation. Safe detach refuses shared nonselected
material users, missing original swap materials or a tree that cannot exactly restore before changing anything.
Repeated attachment restores the authored materials. No gate changed. A final integration review found cached
exports retained an old checkpoint id after restoring identical geometry; cache hits now bind the current head
while preserving validated bytes/fingerprint/SHA. Export and transaction checks: 36 passed in 6.31 seconds.

Exact runtime object identities no longer strip `.001` or merge distinct Blender objects. The historical accepted
FT GLB uses 30 suffixed node names while its saved layout/recipe use unsuffixed names; reproduction therefore uses
an explicit test-only one-to-one migration on copies, retaining binary bytes and every other JSON field, with
rebound copied camera/registration hashes and a migration receipt. Runtime has no product/name alias fallback.
Target-role filtering validates all frozen camera/photo/matte identities before excluding non-target donors and
records those exclusions. Host integration: 60 passed in 42.95 seconds. Accepted-byte replay and full macro proof
remain pending the colour-policy source freeze.

The colour model is now geometry-bound across donor admission, bicubic footprints and the later highlight filter;
blue/green/grey candidates no longer inherit the unconditional brown B/R ceiling. Hardware/occlusion/clipping
margins and coverage/independent quality gates remain. Forty-three new tests passed in 19.40 seconds, including
exact masks and pigment-arm values on all six accepted FT photos; 71 existing focused tests passed in 71.63 seconds.
The warm compatibility branch is selected from at least 150 informative samples with at least 90% supporting warm
channel order. An initial new neutral-share veto wrongly classified glare/low-chroma warm pigment; removing that
unsound model condition (not changing a quality gate) restores exact parity and adds an independent glare control.
Warm-dominant mixed neutral pigment remains ambiguous and is explicitly reported by the policy; a broader palette
architecture was not added. Pure grey and non-warm patterns exercise the general model. Exact multi-material part
guards now aggregate forbidden lens/metal roles across every primitive, so a later acetate slot cannot erase them;
six permutation/role tests plus pin/name controls: 15 passed in 0.77 seconds.

Accepted FT1119 replay now passes: `data/assembly/m4c-accepted-ft-20261013-133134/result.json`, 252.20 seconds,
14 workers, $0. The PNG SHA is exactly `ac4280e9d0a2b85696c1d64f7c9508f56d81f2fc2857f7925af58fd844ddd5b4`;
the independent audit checks 729,621 photo texels with zero bad anchors and zero mixed-view faces. Layout,
class/source/tier arrays and gains match the accepted recipe. Registration differs only in its generic mode label
(the runtime no longer asserts a product-specific fact); numerical evidence and selections match. All input and
frozen implementation hashes stayed unchanged. The assembled macro proof is running separately on a copied job.

Final M4c integration review found that a later geometry edit could leave a stale print attached yet pass finish's
spec/seating checks. The bounded fix identifies active prints from their actual exported UV/texture binding and
checks the current export against its retained successful bake evidence, preserving restored older checkpoints and
intentional detach. This strengthens assembly integrity without changing any existing print quality threshold.

The first complete macro proof passed in 232.91 seconds: `m4c_macro1.log`, copied job under
`data/assembly/m4c-dc34956e/`. Real SDK tool invocation, production bake, trusted worker detach/UV/attach,
independent A6/UV/material checks, exact checkpoint restore and original-file hashes all passed; no paid call.
The later freshness safeguard is being verified separately and the final macro proof will include it. Freshness
reports are recomputed against successful retained application/manifests even when export bytes are cached; new
finish/delivery require a SHA-bound passing report, while historical immutable deliveries remain readable.
Complete old bake groups must be reprinted or detached together; the existing full-manifest checker is unchanged.

Independent review also found an output-parent junction escape and a cancellation/teardown race. The bake output
now resolves inside job work before creation, and cancellation waits for the print thread's actual cleanup before
the session can close (including repeated cancellation). The real Windows junction test and async cancellation
test pass: 29 transaction tests in 2.89 seconds. Initial finish/delivery/export/transaction integration: 141 passed,
1 symlink privilege skip in 9.20 seconds; the skipped case now uses a Windows junction and passes. No gate loosened.

The first attachment-marker design failed the unchanged B5 metadata-preservation check (12/14 Blender tests passed;
both failures were node extras). That design was removed. The final implementation uses existing print UV metadata
and actual image/sampler bindings, with no attach/detach metadata change: all 14 Blender tests pass in 33.50 seconds.
Nineteen host GLB tests pass in 1.57 seconds, including stale geometry/UV/image, missing/tampered evidence, restored
old and disjoint complete bakes, extra ineligible objects borrowing a valid print, stripped metadata and an
unsupported image source. The authored texture restored by detach remains valid. Combined finish/delivery/export/
transaction/freshness integration: 160 passed in 8.73 seconds (four workers/loadfile).

Final macro proof passes in **258.50 seconds**, `$0`, 14 workers: `m4c_macro_final.log`, copy under
`data/assembly/m4c-755a336f/`. The tool result independently confirms freshness status `verified` and one bound
attachment group, SHA-bound manifest, and the exact accepted `ac4280e9...` PNG. Detach reports `not_required`;
A6, UV invariance, B5, original-source hashes and pre-print checkpoint restoration all pass. Free commit before
launch was 21.25 GiB, BLAS 1, no overlapping heavy pools. Final bounded review repairs passed: malformed evidence
refuses rather than throwing (41 freshness tests, 2.41 seconds); basis-only imports exclude recorded alternate
colourways while explicit job roles remain authoritative (41 production-input tests, 45.66 seconds); legacy test
CLIs consistently use hash-rebound copies after the exact-name fixture migration (5 wrapper tests, 9.24 seconds).
These only affect malformed evidence or alternate input wrappers; the accepted replay and final macro inputs are
unchanged. M4c is complete.

M4d adds signed rear-three-quarter camera candidates, per-photo matte modes, and the existing lens-optional loss
for exactly classified rimless/semi-rimless lens faces. All optimizer levels and candidate comparisons receive the
same optional-face mask; raw IoU/contour metrics and existing thresholds remain unchanged, with optional metrics
reported separately. Each attempted photo keeps its identity, hash, failure and available evidence. Automatic
seeing operations use successful cameras and report unavailable refs; explicit failed-photo requests refuse with
the named diagnostic. An all-failed attempt remains inspectable and does not replace the previous usable default
basis. Temple stages still require a complete camera set; print requires at least two usable target cameras and
reports each excluded photo. Damaged frozen evidence refuses instead of silently selecting an older basis.

Camera fast checks: **31 passed in 1.36 seconds**. Dedicated/legacy optimizer checks: **23 passed in 178.75
seconds**, including full-prior signed rear recovery and unchanged front/top/elevated thresholds. Seeing/tool/
temple-package checks: **71 passed in 7.46 seconds**. Fit/intake/rigid checks: **100 passed in 2.25 seconds**;
partial-print/production: **47 passed in 42.30 seconds**, then 7 partial-input checks in 3.80 seconds including
direct loader matte-mode parity. Basis/legacy/temple integration: **70 passed in 11.21 seconds** (22 new partial
and integrity cases). Historical Prada tests use copied, hash-pinned camera metadata with the three actual `.001`
front names; runtime exact-name matching stays strict.

The mixed-photo proof **passed in 29.594 seconds**, `$0`, copy `data/assembly/m4d-da7887f6/`, log
`m4d_proof2.log`: front/rear cameras meet the unchanged synthetic truth gates, the glossy-floor matte matches
exactly, the blank photo keeps its empty-matte failure, automatic comparison uses the two usable cameras, and
explicit blank requests refuse. An all-failed refit preserves the previous default and all old basis/source bytes;
the live head is unchanged. Before launch: 23.15 GiB free commit, 14-worker upper limit, BLAS 1. Initial proof
intake correctly refused missing explicit `side: null`; only the synthetic view data was corrected. Review added
separate optional-lens metrics to frozen/tool reports and kept failed-reference diagnostics when an `all` render
can show only canonical views. Seven seeing-boundary tests pass in 2.69 seconds after correcting their fake bounds.

M4 regression underway: main Python suite uses eight workers/loadfile (23.13 GiB free commit); photoprint follows
at two workers. AR: **497/497**, QA: **103/103**, TypeScript check passes. Every run remains offline and `$0`.

Expanded M4 scope, explicitly approved by the owner: **"Expand M4 to fix rimless matte"**. An additional real
rimless stress case (actual closed lens-role meshes, hard-frame photo, existing untuned camera truth/priors)
failed its unchanged 98% optional-IoU check: fitted 0.857904; even the exact camera only 0.938868. Independent
matte-stage diagnosis: raw threshold matches the 5,802-pixel hard render exactly; opening removes 15 pixels,
closing adds 31, then default hole filling adds 336 background pixels. Lens-first-hit occlusion is not the main
error. Exact source model/photo/result are retained under `data/assembly/m4d-rimless-counterexample/`; diagnostic
receipts `m4d_rimless_diagnostic.json` and `m4d_rimless_matte_diagnostic.json`. No fixture, seed or threshold was
changed. Owner approval authorizes a general rimless-matte fix; it does not authorize loosening any gate.

Final UV review also found a bounded ownership bug: another object's retained mesh could be used as a live mesh
and then renamed, retargeting its string-based undo reference. A symmetric current/retained ownership preflight
now closes both directions. Its Blender regression passes in **8.82 seconds**, checks original and later UV-source
generations, refuses without changing scene/datablock state, then restores exact original mesh/UV bytes and cleans
retained generations (`m4_uv_ownership.log`).

Initial main suite completed: **2,901 passed, 16 failed in 692.89 seconds**. Nine failures came from historical
test doubles missing the new Basis usability methods (38 targeted tests pass, one unrelated real-Blender seed test
deselected because it passed in the full run). The instruction contract's literal-key checker now follows the
extracted fit-summary helper; a new fake-bounds test was corrected while the suite still had its earlier module
loaded. Combined root checks **125 passed in 7.35 seconds**. QoS regression **12 passed in 2.68 seconds** with no
external descendant watcher; timing suite **95 passed in 71.48 seconds**. Full seeing module **22 passed in
110.01 seconds** alone: two fixture failures were an adapter applied to the wrong asset (the original Blender
scene exports unsuffixed exact front names; the standalone historical GLB needs `.001` names). The original
camera metadata is retained for that live scene, with an added exact-name presence assertion. Its 60-second render
threshold is unchanged and passes without eight-worker load. No quality assertion was removed or reduced.

The authorized matte correction adds `open_frame`: the identical background/threshold/opening/closing/component
recipe, omitting unconditional hole filling. An absent photo mode selects it only with actual optional lens faces
from rimless/semi-rimless construction; explicit modes win and the resolved mode is frozen, including failed rows.
**115 focused checks pass in 21.39 seconds**, including 12 FT/Prada historical default masks byte-identical to the
independent WP3 recipe and frozen/raw-print mode parity. Exact-truth optional IoU is now **0.992969** with contour
0.063675 px. The unchanged real optimizer test still fails at **0.912881**; its evidence is preserved separately.
Independent review found missing optional-face handling inside existing camera refinement: full-lens bbox
alignment displaces the true-angle initial Y centre by 31.55 px (hard geometry: 0.19 px), and the effective
photo-dependent contour creates lens-ray correspondences without real hard geometry (86/833 samples). Bounded
alignment/ICP integration diagnosis is in progress; priors, thresholds and the failing test remain unchanged.

The final rimless correction passes the actual optimizer proof in **16.54 seconds**: optional IoU
**0.9907518411** (gate >=0.98), optional contour **0.0791593194 px** (gate <1 px), yaw **4.0320623 degrees**
versus truth 4, pitch **11.9279084 degrees** versus truth 12 (both within the unchanged 2-degree gate).
Raw IoU **0.1661735559** is retained separately; frozen matte, raw/optional metrics and source hashes are verified.
Evidence: `m4d_rimless_physical_support.log`, `data/assembly/m4d-rimless-verified/`.
Each existing angular prior now scores both the original full bounding-box alignment and an alignment to vertices
belonging to required faces; the unchanged loss chooses the better candidate, with full alignment winning ties.
Optional-lens interior points borrowed from photo-dependent contours cannot become artificial ICP ray constraints
in either correspondence direction. Actual rendered boundaries, including opaque lens edges, remain available.
There are no new angular priors, refinement iterations, loss weights or relaxed gates. Clear/opaque alignment,
both ICP directions, all-optional fallback and raw-metric checks pass **17/17 in 0.58 seconds**; the separate rear
recovery test runs in the full suite. Independent review found no blocker, and paths without optional faces retain
their original arithmetic. This completes the owner's approved rimless-matte expansion.

Initial full photoprint run: **313 passed, 1 failed in 217.39 seconds**. Its only failure expected the old `.001`
fixture name after the explicit exact-name migration. The assertion now uses the same pinned name mapping as its
copied asset; all decal-fill, see-through, 2% and 98% assertions remain unchanged. Five migration-wrapper checks
pass in 8.20 seconds and the saved bake satisfies that corrected identity assertion. Final source-frozen full
main suite started at **14:41:44 UTC**, eight workers/loadfile, **22.99 GiB free commit**, BLAS 1 and EcoQoS opt-out;
the final full photoprint rerun follows at two workers with no overlapping heavy pools.

Final source-frozen main run (`m4_final_main`): **2,948 passed, 2 failed, zero skipped in 648.55 seconds**.
Both remaining cases are the known environment-sensitive checks: the external descendant watcher changes the
initial Windows power state measured by `test_opt_out_self_reads_back_and_is_not_inherited`; the 28-image render
took 163.983 seconds under eight-worker load against its unchanged 60-second limit. Photoprint's final run started
at **14:53:50 UTC**, two workers/loadfile, **23.00 GiB free commit**; isolated checks follow before closing M4.
Final photoprint: **314/314 passed in 233.34 seconds**, exit 0 (`m4_final_print`). The unchanged Windows
power-state module passed **12/12 in 3.23 seconds** without the external descendant watcher (`m4_final_qos`).
The whole seeing module passed alone after the print pool exited: **22/22 in 111.73 seconds**, exit 0
(`m4_final_seeing`). Its unchanged 60-second render check passes; no overlapping heavy pools.

**Final M4 regression: 3,264 unique Python tests passed after the two isolated environment-sensitive reruns,
zero skipped** (2,950 main + 314 photoprint). Arview **497 unit + 103 QA tests passed**, TypeScript clean.
The final source was frozen before the main run; only this progress log changed during the final suites.
Every M4a-d proof, including Prada T5, the second-product nested temple stage, the exact accepted FT1119 print,
the mixed-photo camera proof and the approved rimless stress case, passes. All paid transports remained unused:
**actual API spend $0**. Independent integration reviews found no remaining M4 blocker, and `git diff --check`
is clean. The explicit commit list contains 120 source/test/documentation files; generated evidence, photos,
models and scenes are excluded. Work remains in the requested checkout on `glasses3d`, with no push or worktree.

Open limits remain explicit: ten historical WP4 quality failures (recommendations above), unbuilt junction captures
and photo-derived hide ranges outside M4, historical WP5 A4/A5/B3 failures, and the recorded warm-dominant mixed
neutral-pigment donor ambiguity. Offline integration proofs are not new product appearance acceptances; M5 and its
owner AR verdicts remain unrun. The original root `data/assembly/m1-proof-b8bb88d4` artifact remains untracked:
automatic approval review rejected its cleanup move with the stated reason "blocked by policy". No cleanup retry
or workaround was attempted, and the artifact is excluded from all commits.

M5 planning estimate, calculated locally from `estimate_g3d` and the frozen
`gpt-6-astra-standard-2026-09-27` tariff (no API/token-count call): 25,000 initial tokens, 60 replies and 16,384
maximum output tokens estimate **$15.699976 per run**, growth-only range **$13.942871–$21.678160**. This is not
a confidence interval; the calibration is eight historical temple runs on one product, and assumes no image-pruning
savings. Six such planning runs estimate **$94.20** total (growth-only range **$83.66–$130.07**); D5's unchanged
reserve ceilings bound six individual runs by **$138 plus the separately approved smoke cap**. The high-growth
scenario requires $29.886542 reserve and therefore cannot complete all 60 replies under the $23 guard. A minimal
one-turn smoke with 2,500 input tokens and 4,096 maximum output tokens estimates **$0.102150**, worst-case
reservation **$0.236050**; an actual smoke requires its own exact input estimate and owner approval. D5's $20
settled stop, $23 reserve ceiling, max effort and Oakley-first choice are final. Total M5 budget and smoke cap remain
unapproved; no M5 request has been sent.

## Status

| WP | State |
|---|---|
| WP0 / WP1 / WP3 / WP3b | Complete and tested |
| WP4 | Trusted seating tools integrated and proof refreshed in M4a; all 24 real-product tests pass without stale skips. Quality verdicts remain P2 71/71, P3 34/40 (+2 report), P4 109/112 (+2 pending), P5 31/32 (+1 N/A, +2 report): 10 known quality failures remain failures. Junction captures and photo-derived hide ranges remain unbuilt, outside M4 scope. D4 crystal Boolean choice is recorded in ASSEMBLY; no quality gate was loosened. |
| WP5 | Complete by owner visual acceptance 2026-10-08, FT1119 only: observed-slope join + Poisson is the default (`4304c1d`). Not an all-proofs pass: A4/A5/B3 failures stay recorded ([WP5_HANDOFF](WP5_HANDOFF.md)) |
| WP6 | **Complete by owner acceptance 2026-10-13** ("you know what. im satisfied with wp6, lets wrap this"), after paid run 8 `facet-astra-008` on the v3 tools ([WP6_V3.md](WP6_V3.md), section "WP6 v3" below). Result: run 8's best checkpoint c0015, revision 25 (`data/wp6v2/viewer/new_astra_run8.glb`). Left open at closing: the hinge-side face reads too faint (built 2.2° off the logo) and Astra's images pile up unlabelled ("Open at closing" below). (v2 stopped by the owner after run 7; v1 retired 2026-10-10, tag `wp6-v1-final`) |
| WP2 | A–F and all M4 specialist stages assembled and proven offline in M0–M4, including immutable delivery and owner review |
| WP7 | Implemented and proven in M2 ([WP7_DESIGN.md](WP7_DESIGN.md)); specialist tools and teaching registered and tested in M4 |
| WP8 | Not started (paid; owner OK per run) |

## WP6 v2 build order (WP6_FACETS §6)

1. **Archive + docs (§7)** — done 2026-10-10 (track F), committed in the archive commit after `62b25e4` (see git log).
   **Seeing (§3)** in parallel: render.py, seeing.py, compare.py, pose.py (new) and their tests.
2. Owner sheet #1 (after seeing). **Builder v2 (§2)** in parallel: g3d/temple/*, temple_bpy.py, core temple tests.
3. **Lab tools (§4)**: agent/temple_lab/*, worker method for variant renders, loop.py settings, tests, T5, T5b;
   remove the pending v1 modules below. Owner sheet #2.
4. Adversarial review of the whole diff, fixes, commit. Cost estimate. Owner OK. One paid run.

Phase 1–2 results (2026-10-11):
- Seeing is built: pose basis from fits.json, photo-matched compare, white balance and the photo_env light fit.
- **T2 rig check: no rig passes.** photo_env shows the gloss change 2.6 L8/mm against the 10 target. studio_dome_v1
  passes the size target but reverses the facet tone order. Details are in WP6_FACETS §3.2 (amended).
- The builder `g3d.temple/2` is built: 268 tests, v1 equivalence 3e-14 mm, template p95 0.30 mm.
- The v1 lab is broken until step 3 (it writes /1 specs).

Phase 3 and pre-flight results (2026-10-11):
- **Astra facet tools** (`agent/temple_lab`, rebuilt):
  - 7 tools, the strict edits union, judgements and finish preconditions;
  - the worker's non-mutating `temple_variants_render`;
  - loop settings bound in the approval: tool_choice required, finish-gated stop, context guard, timeouts and E_BUSY;
  - the six pending v1 relief modules are removed.
- **T5 rehearsal** (MockTransport, real Blender, 0 network): 22/22 checks. **T5b**: the preferred layout is reached from
  revision 0 in 23 edit ops.
- **Finish rule "distinct head"** (preconditions 1 and 4): two attempts differ only when the built temple surface
  (normal distance) or outline moves ≥ 0.2 mm. Five review rounds found ways to meet label-, layout- and
  crease-threshold rules with near-zero geometry. Crease comparison is now reported, never decisive.
- The light is the owner's choice C (`studio_dome_v1`). Sheet #2 is in `data/wp6v2/sheet2/`. The lighting sheet is in
  `data/wp6v2/light_choice/owner/`.
- **Estimate** (from T5 tool outputs): 40 replies $7.26 (cap $15); 60 replies $13.78, edit-heavy $16.56 (cap $20);
  90 replies $22.19 (cap $50). Package: `data/wp6v2/lab/package`. The request `runs/facet-astra-001` is UNAPPROVED.

**Paid run `facet-astra-002` (2026-10-11).**
- Approval, in the owner's words: "yuo can run it with 20$ max (dont use reserve spending, just cut it by 18$). dont
  have a replies limit."
- Settings: effort max, no reply cap, cap $20, no output squeezing.
- A host watcher (`data/wp6v2/lab/stop_at_18.py`) would have stopped the run at $18 settled. Until 10:29 it counted the
  guard's per-request hold as spend; it was corrected while running, and it never fired.
- Result: 10:03:29–10:30:09, **33 requests, $6.679752 settled**, Astra finished on its own via `facet_finish`
  "matched".
  - Best checkpoint c0013, revision 15. Export `head-10ab74e8a162dded.glb`; validate_spec ok (2 warnings).
  - Layout: logo triangle, crown, hinge flank and tail flank fading at 55–85 mm, roll 0.25 mm. Every crease was
    declared a ridge.
  - 3 variant sheets, 6+ builder refusals that Astra fixed itself.
- Owner sheet: `data/wp6v2/result_facet-astra-002/` (tests/proofs/wp6v2_result_sheet.py, deleted 2026-10-13). The
  owner's verdict is pending.

**Owner feedback on facet-astra-002, verbatim:**
- "yes it did miss the red line."
- "the top triangle facet is barely noticeable. every other triangle also faces the same issue which is not being
  pulled enough outwards"
- an unwanted line from the logo apex
- "the each triangle line is very soft wheres it needs to be sharp"

**Fixes:**
- `c7362c4` builder: flat cells before a fade, sharp creases, E_FADE_CLIFF.
- `b34df09` black photos B1–B3 for Astra, plus clearer instructions.
- `cc38cbe` pre-flight P1: the start-model "camera error" sentence had kept the facets shallow.
- A three-lens pre-flight review gave GO.

**Paid run `facet-astra-003` (2026-10-11).**
- Approval: "yes, make the fixes and run it again", with the same terms. 13:27:20–13:42:02, 15 requests, **$3.598669**.
- Astra finished on its own as "matched". Best checkpoint c0011, rev 12; export `head-bad259c0eb030eff.glb`; spec ok.
- Layout: logo, heel, crown, lower_aft and tail. The crease behind the logo is now present (valley, 12°). Creases run
  12–19° (run 2: 7–8°). Roll 0.15 mm.
- Crown and tail both fade 47→76 mm, so the upper-back triangle does not read as its own flat face.
- Sheet: `data/wp6v2/result_facet-astra-003/` (faces_run3.png). The owner's verdict is pending; first remark: "it still
  looks like the top triangle isnt being recognized right?"

**Owner feedback on facet-astra-003, verbatim:** "i meant 4 and the results look like it really didnt recognize it"
(face 4 = the thin band above the logo, the top of the temple's front block). Evidence: run 3's top photo (03)
outline, photo minus model, both sides: d30 +1.5, d40 +0.7, d50 −1.7, d60 −2.1 against a common −0.6. From above the
real front block is broad and widest near d 25–30; run 3 is slim there and widest at d 50–60. Astra's own summary
named "minor top-view shoulder/transition offsets (about 1–2 mm)" and still finished "matched".

**Fixes for run 4** (owner: "yes, implement changes, test them, and run a paid run with a budget of 15$ to force
stop"):
- Finish precondition 5 (`matched` only): the top photo's outline measured and within 1 mm of its common offset at
  every station; every compare that shows the top photo prints its outline line (WP6_FACETS §4.1).
- Instructions: judge top and bottom faces from the top photo (edge-on from the side); the outline line's stations
  are shape, not camera error; the top photo may be oblique (a station is one image row); a corner in the top
  outline needs separate facets meeting at a node; a thin band takes a crease + rise plane; finish before the cap.
- Builder `W_FADE_EARLY` (core wp6-core-2.2): a fade that leaves its facet flat for under 5 mm and under a quarter of
  its length (run 3's tail: 0.1 mm).
- Tool test ($0, a COPY of run 3's package, `data/wp6v2/tooltest_face4/`, never Astra input; photo-minus-model mm at
  the real top-photo compare):
  - run 3's best head: d30 +2.1, d40 +1.2, d50 −1.1, d60 −1.5 off;
  - its layout with node depths A, C, D and the crown's rise searched (45 tries): best d30 +1.3, d60 −1.2;
  - face 4 split from the upper-back face (A-B-C and B-F-C), depths and rises searched (~50 tries): best d30 +1.3
    alone; 35 tries with face 4 through its three nodes were refused E_ILL_CONDITIONED;
  - a fit on the mesh's widest point per d (ignoring the oblique top camera, pitch 60°) passed on the mesh but read
    d30 +1.8, d40 +3.4, d50 +2.8, d60 +2.2 at the camera.
  No tested head met the rule. Whole models at the same compare: the original and the owner's earlier "preferred"
  shape fail it (preferred d30 +2.2, d40 +2.9, d50 +1.8); only Astra's rejected c0003 passes, with d40 unmeasured
  (now refused as unmeasured). Told to the owner before the run: the rule pushes Astra to work on the top view;
  passing it does not make a shape good; Astra may finish "stuck".
- Pre-flight review (5 reviewers + 5 verifiers): fixed the T5 outline check, unmeasured stations passing, 0.1 mm
  deviations, the oblique-camera wording, W_FADE_EARLY's length rule and wording, this paragraph's claims, and the
  money plan: the owner named one number, so the cap is $15 (the budget guard), which spend can never pass; the
  guard keeps room for the reply in flight, so the run ends near $13 at the latest. No separate watcher.

**Paid run `facet-astra-004` (2026-10-11).**
- Approval in the owner's words: "yes, implement changes, test them, and run a paid run with a budget of 15$ to force
  stop". Cap $15 (the budget guard), effort max, no reply cap, K 4. Code `e138736`; T5 `t5_004` 27/27.
- 15:01:06–15:30:05, 32 requests, **$8.209531** settled. Astra finished on its own as **"stuck"** (the outline rule
  was never met; no finish refusals). Best checkpoint c0019 (revision 17); export `head-38c91e136fe6ab7b.glb`
  (sha 0857568818…); validate_spec ok.
- Layout: hinge, logo, sweep, tail and two upper faces. **Face 4 is its own facet** (`upper_front`: hinge top, the
  top-outline peak, the logo corner; crease + rise −0.59, steeper than run 3's −0.35). The upper back is a separate
  face (`upper_rear`), with a ridge between them. Fades start at 77 mm, so no face fades from its own corner.
  Seven creases declared; roll 0.12 mm.
- Top photo outline of the best head: only d30 +1.45 beyond 1 mm (run 3: d30 +2.1, d40 +1.2, d50 −1.1, d60 −1.5).
  Astra's summary: about 1.4 mm short at d30; its last three distinct changes moved the error elsewhere.
- Sheets: `data/wp6v2/result_facet-astra-004/` (faces_run4.png, top_run4.png, reference-0N.png, black-side.png); the
  3D viewer's default is run 4.

**Owner feedback on facet-astra-004, verbatim:** "the only issue that i still see is the top triangle. please
investigate. (its split into two)". Found: the real top triangle (face 4) is ONE long thin face (hinge top, the
top-outline peak, a point far back; its lower edge one straight line through the logo's back corner). Run 4 added a
crease from the peak down to the logo corner that no photo shows (`upper_front` | `upper_rear`). Contributing causes:
the run-4 instruction "A corner in the top photo's outline is a corner of the faces …" (now removed), the
coordinator's own split tool test, and layout decided from one viewing direction; run 2 reached the same split
through a builder refusal (a single face 4 refused because every crease at the logo corner was declared a ridge, and
"do not redeclare" left splitting as the way out). Evidence: `result_facet-astra-004/top_triangle.png`; run 4's creases drawn on the black angled
photo with a transported camera cross the top face where the photo shows no edge (review notes).

**Review of Astra's guidance (2026-10-11).** 26 of 106 instruction sentences were not general (20 added from this
product's failures); Prada assumptions also sit in code (packaging defaults, "acetate" in briefs, gloss-black for
every colourway, fixed section stations, the top-only outline rule). Why the layout rests on one image: the prompt sent
every position reading to the side photos ("never from oblique views") and asked for no check from another angle;
the "two photos agree" rule compared one direction with itself (B1 uses 05's camera, 01 is its mirror); the black
angled photo had no camera; in run 4 the layout was fixed before Astra first saw an angled view, and the top-photo
rule kept it chasing one photo's numbers (13 of 14 judgements of 03). Not money, resolution or context.

**Owner decisions after the review, verbatim:** "yes, build 1-3 and make the rule information only" (1: general
instructions with per-job facts in the brief and a confirm-from-another-angle habit; 2: cameras for the black angled
and front photos; 3: variant sheets that show black photos with each variant's crease lines). "please max utilize
local machine for faster work" (pytest-xdist pinned; builds and reviews run in parallel). The outline rule is
information only since `2ca5be9`.

**Built for run 5 (2026-10-11; build workflow, review, fix workflow, re-review; all $0):**
- **General instructions** (`instructions.txt`, 12.8k chars): no product or photo-set facts; lay out in the square-on
  views, then confirm (keep / remove / add creases) in a calibrated view that sees the face at a moderate angle; a
  crease only where two directions show it; never split a face no photo shows divided; offsets and the outline line
  are information; the corner rule (one face straight through a corner: its creases cannot all be ridges; keep the
  face whole; decide with variants at the moderate and glossy views when no silhouette shows it).
- **Per-job brief** (`cli.brief_text`): each photo's direction, angle band to the outer face and points_px error per
  mm of depth, direction groups, glossy photos, number lines, camera-fit text, render light, revision 0, roll unknown.
- **Cameras for B2/B3** (black angled / front) by similarity transport from 02/04 (`g3d_host/camera_similarity.py`
  restored from wp6-v1-final; `tests/proofs/wp6v2_basis.py`): B2 0.82 px / IoU 0.992 (model check 0.934, temple
  0.875); B3 1.09 px / 0.992 (0.977 / 0.886) after the per-photo matte fix (white-background matte for B2/B3, B1 keeps
  its dark matte). Existing basis records byte-identical (`data/wp6v2/basis-pre-b2b3`, `basis-pre-b3fix` kept).
- **facet_variants** takes any photo with a camera (1..4, at most 3 targets), renders other colours in gloss black,
  and draws each model's crease lines (the compare overlay's rule, host-side, matches it to 0-2 px).
- **Builder:** E_CREASE_SHAPE names a node a facet runs straight through ("one is a valley or two of those faces are
  one ...; keep X whole").
- **Estimate:** output tokens per reply from runs 2-4 (1852 expected, 2064 high); up to 4 variant sheets.
- T5 rehearsal 31/31; reviews: no P1 left. Owner, verbatim: "you may do a paid run, only when your ready, max 15$".

**Paid run `facet-astra-005` (2026-10-11).** Approval in the owner's words: "you may do a paid run, only when your
ready, max 15$". Cap $15 (guard), effort max, no reply cap, K 4; code `bf0b699`; T5 `t5_005` 31/31; estimate_005
(40 replies $10.63, up to $13.78).
- 19:3x–19:54, 30 requests, **$6.766442** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0017
  (revision 14); export `head-dd42b6a10e649032.glb` (sha 7895814159…); validate_spec ok.
- Behaviour: the first compare included the angled view; Astra opened a crop of the black angled photo (B2) before
  laying out; every one of its variant sheets included B2. **The top triangle stayed one face** (`crown` A-P-R-B, no
  crease from the peak to the logo corner); one valley declared at the logo's back corner (`lower_sweep|tail_face`);
  `crown|logo` declared a valley; two thin bands along the top edge (`top_front_band`, `top_rear_band`, under 0.5 mm
  tall from the side) with a ridge at the peak. Roll 0.2 mm.
- Still off, by Astra's summary and the images: from above, the front block is about 1.9-2.5 mm too slim at d30-40
  (run 4 was closer there); a small lip at the crown in the front view.
- Sheets: `data/wp6v2/result_facet-astra-005/` (top_triangle_run5.png, faces_run5.png, top_run5.png,
  angled_run5.png, reference-0N.png); the 3D viewer's default is run 5. The owner's verdict is pending.

**Owner feedback on facet-astra-005, verbatim:** "the inward edge is the one thats really bugging me since the layout
is perfect. what caused it?" Found ($0; `data/wp6v2/tooltest_crown_ridge/`, review notes): the crown|logo crease is a
ridge in the photos (~80%; depth read from the angled views gives a ridge with probability 0.96). Run 5 built an 8°
valley because the logo's back corner (B) had t solve, so the builder took it from the crown's extrapolated plane
(5.41 against ~7.0-7.5 in the photos); a bracket chasing the top photo's outline (var013: five changes at once and an
explicit valley declaration in every arm) then flipped it. Astra never saw solved values or the flip against the head
(applying the variant said "no change"), and had no way to measure t from the angled photos (points_px gives the
model's t). Owner, verbatim: "ok, fix it and run again, max 15$".

**Built for run 6 (2026-10-11, $0; build, two review rounds, fixes):**
- facet_inspect prints solved node values (`solve -> 5.41`) and, per crease, a host margin in the builder's own
  planes (each face's corner farthest across the crease line, its offset in X = x_in(d) + t from the other face's
  plane, + = ridge; solved setting corners named). Receipts list `solved` moves and `margins` (flips, solved or
  fragile creases); `crease_vs_head` / `diff_vs_head` state shape flips against the head, also when a variant is
  applied with no edits and on facet_restore.
- photo_compare `depths`: t measured from a photo at (d, y) and a pixel (exact inverse projection; px per mm; miss);
  front/rear views pick the temple nearest at the model's t. Run 5's logo corner reads 7.0-7.4 in B2, 02 and B3.
- E_CREASE_SHAPE names the crease's solved corners; E_RELIEF_STEP names the rise_per_mm that closes it.
- Instructions (13.9k chars, cap 14,000): decide ridge/valley from evidence about that crease (measured corner t,
  read the host margin, never extrapolate t by hand; views that look along it); measured t for the corners that set a
  decided crease; one bracket answers one question; never change a crease's shape for an outline number; on
  E_CREASE_SHAPE first measure its solved corners. T5 38/38 (`t5_wip6e`).

**Paid run `facet-astra-006` (2026-10-12).** Approval in the owner's words: "ok, fix it and run again, max 15$". Cap $15,
effort max, no reply cap, K 4; code `7631a31`; T5 `t5_006` 38/38; estimate_006 (40 replies $10.77, up to $13.80).
- 00:15-00:34, 26 requests, **$5.075421** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0013
  (revision 13); export `head-8e43a634deb82aa3.glb` (sha daa5beb96b…); validate_spec ok.
- Fixed: the top triangle stays one face and the **crown|logo crease is a ridge**; Astra measured A, C, D, H and F
  with depths (C 7.49) and declared what the measured values built.
- New problems: the back faces fade from d 48 (Crown, Lower and Tail fade 48-82; Tail is flat 0.7 mm before its fade,
  W_FADE_EARLY fired 4 times and Astra kept the early fade from a bracket judged on B2 smoothness and the top
  silhouette), so the upper-back and lower-back triangles do not read as crisp faces; Front|Logo (the face beside the
  hinge against the logo) is a valley from the measured depths, a ridge in runs 3-5; the top triangle's rise -0.85
  renders it bright in the side view. Top view still 1-2.5 mm slim at d10-40 (Astra's summary).
- Sheets: `data/wp6v2/result_facet-astra-006/` (top_triangle_run6.png, faces_run6.png, angled_run6.png, top_run6.png);
  the 3D viewer's default is run 6. The owner's verdict is pending.

**Owner feedback on facet-astra-006, verbatim:** "run 6 looks closer, but as you said theres this inward issue and the
top triangle is still not one piece and being smoothen and not flat. please review evidence thoroughly and the tools
and instructions. please plan heavily for yourself (no need to stop to share the plan) and fix, after the fixes come
back when youre ready for a paid run and illl authorize it. run 6 is better than run 5 but there are still issues as
mentioned above.."

**Found ($0; corners triangulated from photo pairs with the job's own cameras; `data/wp6v2/tooltest_run7/`):**
- The smoothed, "not one piece" top triangle is the fade. Crown, Lower and Tail fade over d 48-82, so the crown is
  flat only over d 0-48 and blends into the arm behind. Astra faded to hide a wing:
  - the crown's tip E was solved from the crown's plane at t 9.8 (d 90), about 4 mm proud of the arm;
  - top view −3.2 / −4.3 mm at d 70 / 80; B2 "an over-raised wing";
  - the crown's lower edge A-C-E is one straight line, so with A 3.55 and C 7.49 the tip took C's height.
- A 3.55 was a one-photo reading at an assumed (d 0, y 10.2).
  - In B2 a d error moves the pixel along the depth line, and the inner face turns 0.78 mm per mm of d at the hinge.
  - The same corner picked in B2 and B1 sits at d −1.58, X 70.71: t 4.77 at d 0.
  - The hinge-bottom corner H reads 4.8-5.4 the same way (Astra 5.52).
- The inward hinge-side edge (Front|Logo valley, H 1.6 mm outside the logo's plane) came from the same A reading.
  With A and H read in two photos and C solved from one flat crown, it is a 1.9° crease on the ridge side
  (H +0.4 / C +1.2), within the readings' error.
- Readings of one corner differ by 1-2 mm between cameras (02's fit: mean contour 7.2 px).
  - C reads 7.5-7.9 from the angled photos and at most 6.9 from the top photo's outline.
  - One flat crown through A, T and the measured tip (t 5.25 at d 81) solves C at 5.8.
  - The top view then has no wing (d 60-110 within 0.7 mm).
- My run-6 rule "measure, never solve, the corners that set a crease" pushed C's thick reading in and the tip out.
- Every paid run (2-6) faded the faces on both sides of a crease that runs on (the crown's back crease near d 90;
  runs 3 and 6 from d 47-48). The owner's earlier remarks match: run 2 "the each triangle line is very soft", run 6
  "smoothen and not flat".

**Built for run 7 ($0):**
- photo_compare `depths` items take `also`: the same point in 1-3 more photos with a camera (`lab.corner_reading`).
  - The host finds the point by least squares over the pixels.
  - It prints first the t to enter at the reading's own (d, y) ("t 4.77 here"), then where the point is, then each
    photo's residual.
  - It refuses photo pairs that see the point along nearly one line, using the pixel Jacobian's condition number
    over 20 (B2+B1 1.9; the opposite side views 05+01 about 940).
  - A one-photo reading says where the inner face turns fast.
- Builder (core `wp6-core-2.3`):
  - A crease + rise plane may hold the ends of a straight run of its polygon's edges (`spec.crease_run`, corners
    within 0.5 mm). This is how a thin band such as the crown gets its tilt from a rise, with both ends measured.
  - A run corner off the line that another facet meets on both run edges is `E_SPEC`, checked before and after snaps.
  - `E_FADE_CREASE`: a crease stands to its ends. Two facets both faded anywhere along their shared crease are
    refused. The fix offered: start the fades where it ends, or let one stand.
  - `E_FADE_CLIFF` and `W_FADE_EARLY` no longer advise fading both facets of a crease alike.
- Refusal notes:
  - `E_FACET_NOT_PLANAR` lists each given corner solved alone and its move, smallest first ("C -> 5.84 (−1.90),
    E -> 8.45 (+3.20)"). It also names the straight run a crease plane can hold ("; or a crease plane on the run A-E
    (through C)"). For one given corner well off a crease line, it names the rise_per_mm that meets it.
  - `E_ILL_CONDITIONED` names the corners without a value and the straight runs.
- Instructions (13,974 chars, under the 14,000 cap by cutting wording):
  - read corners also in a photo from another direction, and enter the t printed for your (d, y);
  - a crease plane on a straight run's two ends;
  - when a face's measured corners fit no plane, take the run or rise named, else solve the corner that moves least,
    and keep it solved;
  - a crease stands to its ends; never fade to fix a thickness (read the end corner instead);
  - a crease left open by its readings and by the views along it: enter values within the readings' error that
    build a ridge, unless a bracket shows a valley;
  - a crease built flat stays undeclared.
- Tool test (labelled copy of package_006, local Blender): run 6's layout with the two-photo corners, the crown on
  A-E and no fades, builds. In the black side view the top triangle is one flat band to its tip and the back faces are
  crisp (`tooltest_run7/owner_top_triangle.png`).
- Reviews:
  - round 1 (4 reviewers, 4 verifiers): 3 P1 and about 10 P2, all fixed. The P1s were:
    - the guard could be passed by moving a crease end inside the outline;
    - the corner text printed the misleading t first;
    - the tie-break, flat-crease and solve rules looped;
  - round 2 (2 + 2): 1 P1 (the "end it at a node, fade from there" advice rebuilt the smooth surface through a base
    strip) and 7 P2/P3, all fixed;
  - a final check of the round-2 fixes: 1 P1 (a printed rise missed by rounding), 1 P2 (the run named with no values
    at its ends) and 2 P3, all fixed.
- Tests:
  - T5 `t5_wip7b` 39/39 (new: `corner_reading_from_two_photos`).
  - Full suite from the repo root: 2295 passed. 4 failed: the stale WP5 test and three load-sensitive tests, which
    pass alone. Builder, lab and CLI suites rerun after the final fixes: 379 passed.
  - `FX.fade`'s ridge now ends where its fade starts. The former dying ridge (`ridge_to=60`, `legacy_fade`) is kept
    for the fade-value tests with the guard off.
  - Recorded runs 2-6 revisions with both-faded creases are now `E_FADE_CREASE`.

**Prepared for run 7 ($0, code `9fb64a5`):** T5 `t5_007` 39/39; `estimate_007` (40 replies at effort max: expected $10.82,
up to $13.84; runs 5 and 6 cost $6.77 and $5.08); `package_007` and the request `facet-astra-007` (cap $15 by the budget
guard, effort max, no reply cap, K 4), unapproved until the owner's words are recorded.

**Paid run `facet-astra-007` (2026-10-12).** Approval in the owner's words: "yes, run it with the $15 cap". Cap $15
(budget guard), effort max, no reply cap, K 4; code `9fb64a5`; T5 `t5_007` 39/39; estimate_007 (40 replies $10.82,
up to $13.84).
- 08:11-08:27, 20 requests, **$3.846039** settled. Astra finished on its own as **"stuck"**. Best checkpoint c0007
  (revision 7); export `head-0a4b0e841a2bfdb2.glb` (sha f5895ca520…); validate_spec ok.
- Astra read every corner in B2 + B1 with `also` (the hinge corner: t 4.42 here, against run 6's 3.55). On
  `E_FACET_NOT_PLANAR` it solved the logo back corner for the crown's plane (6.15; its readings 7.6-7.8) and kept
  the crown whole, with the peak node at the triangulated position.
- **No fades.** The top triangle (crown A-K-E-H) is one flat face to its tip. Every crease on the front block is a
  ridge, including the hinge-side edge (logo|nose ridge 3.3°). As in runs 3-6, the edge between the two back
  triangles is a valley (7.6°).
- Still off, by Astra's summary: the logo's diagonals are too shallow; the top view is short near d 30-40 and wide
  near d 80; the rear arm is broad around d 90-110.
- Sheets: `data/wp6v2/result_facet-astra-007/` (top_triangle_run7.png, faces_run7.png, angled_run7.png, top_run7.png);
  the 3D viewer's default is run 7. The owner's verdict is pending.

No further paid call is authorized. Each one needs a cost estimate and the owner's explicit OK.

## WP6 v3 (2026-10-12/13)

**Investigation (owner, 2026-10-12: "please work on wp6 ... review all code related to wp6 very very thoroughly ...
review all the evidence of the runs ... what tools are actually good, how to write the prompt ... what astra did good
but our tools werent good for it. come back with findings").** Two review rounds, 117 agents, $0. Findings:
`data/wp6v2/review_20261012/FINDINGS.md` (reports beside it). In short: the real top triangle is not one plane (K
+1.5..+2.0 mm off the plane of its other corners, a 4.1-4.4 deg kink in the angled photos), the exact-planar builder
had to put that misfit in one place, and each run's newest rule chose the place (fold, proud tip + fades, K pushed
in); every v2 run put the crown's tip 8-20 mm too far back; photo 03's front-block outline cannot be matched with the
other photos under the camera model (up to +2.2 mm) and was printed as shape; no render light reproduces the photos'
facet tones; Astra measured crisp corners well (0.2-0.5 mm) and executed the newest rule literally.

**Corrections to this file (evidence in FINDINGS.md §5 and `review_20261012/w2/root-cause.md` §4, with
`w2/critic-skeptic.md`'s fixes):** the stop entry's "underlying limit was camera disagreement of 1-2 mm per corner"
is wrong for the crown (its readings agree within 0.3-0.45 mm; the run's 1-2 mm pair differences were mostly picks on
different features); "face 4's lower edge runs straight through the logo's back corner" holds only in the side views;
"front block slim from above at d30-40" (runs 3-7) is mostly the 03 camera-model conflict; the "true A 4.77 at d 0" is
the logo's front corner, not the crown's vertex, and the "measured tip t 5.25 at d 81" is 4-5 mm too far back; the
run-7 tool test log also showed Logo|Lower flat 1.7 deg; Astra did see B's solved value in run 5's charts; "fading
diagonals" (v1 entry, Decisions) were Astra's own reading of the cream photos, and the diagonals end where two edges
cross.

**Rebuild (owner, 2026-10-12: "please fix wp6. do very very hard work!!! come back to me when you think we are good
to go with a paid run. please do your work with the conclusions from before. +max utilize this machine for faster
runs").** Design [WP6_V3.md](WP6_V3.md). Built in three phases of parallel tracks with adversarial review after each
(phase 1: 17 agents; phase 2: 21; phase 3: 29; plus a consistency pass):
- builder: `{"coef"}` and `"warp"` (mean value coordinates) facets, soft facet|base borders, one planarity tolerance,
  facet|base shape against the base, `model_edges`; every existing spec builds bit-identically;
- `g3d/temple/fit.py`: joint weighted least squares over read / typed / solved corners, one camera-theta turn per
  camera fit, declared shapes as constraints accepted within chi2 +4, refusals and warnings as facts;
- `agent/temple_lab/readings.py`: corner and crossing readings (traced edges intersected), pick and camera error,
  slide-along-edge facts, pick sheets;
- seeing: native-scale zooms with a light-independent geometry view, the model's creases drawn by identity with
  their angles, side-view outline offsets;
- lab/tools: `photo_read`, object note, fit receipts, finish rules; removed fades, crease+rise planes, solve notes,
  one-photo depths, the 03/06 offset tables, the outline line and facet_restore; VARIANTS_MAX 4;
- prompt `agent/temple_lab/instructions.txt` rewritten (8,972 characters, was 13,974): the object, the evidence
  ranking, an order of reasoning, few rules;
- run safety: the context guard stubs only outputs present when it fires, a request payload limit, a machine-load
  preflight before a paid run.
Engineering choices (coordinator): ACCEPT_DCHI2 4, TYPED_SD 0.5 mm, PRIOR_SD 5 mm, MIN_CREASE 2.5 deg, THETA_SD 0.3 deg,
CAMERA_PX_SD 1 px, warp = mean value coordinates (a d-ruled surface gives the same turn between H and K), prompt cap
9,000 characters.
Proofs ($0): T5 v3 47/47 (`data/wp6v2/lab/t5_v3b`); full suite from the repo root 2642 passed, 7 Blender/timing
failures that pass alone, and the stale WP5 test_a4 (untouched, WP5 closed); estimate `data/wp6v2/lab/estimate_v3.json`
at effort max: 40 replies $15.02 (up to $21.67), 60 replies $32.22 (up to $46.11). Commits 8378bc4, 4c24124.

**Blind stand-in rehearsal (2026-10-13, $0, TOOL TEST).** A Claude agent played Astra with only instructions.txt, the
brief, the photos and the real registry (harness `review_20261012/v3/standin/drive.py`, report STANDIN_REPORT.md). It
found the photos' layout itself (logo, four faces around it, the top triangle as one warp face because its lower edge
bends at the logo corner, the faceting ending at d 74) and finished "matched" in 31 calls (c0013); its one typed depth
(the top triangle's peak P) made the top ridges 44.5/55.3 deg and the band render white. Prompt additions it showed
were missing: faces clipped by corners beyond a curved outline; the top strip's boundary next to the outer side is
that side's edge; an open edge sign is declared a ridge (the convex prior fills open signs only). With them it read P
(t 5.45; the review's independent 5.29-5.50) and finished again (c0015): top triangle|logo 22.6, top|upper-back 32.8,
logo back diagonal 6.5, hinge-side edge a 2.5 ridge (readings -3.5 +- 3.0: open). Owner-complaint checklist
(WP6_V3 §8, `data/wp6v2/result_v3_rehearsal2/checklist.md`): c0015 holds 9 of 10 (the tip at d 74.0, 1.5 mm short:
F unread), run 7 7 of 10. Options sheet: a fold adds a 10.6 deg line inside the top triangle; flat brings back the
logo corner 1.75 mm in and a 3.2 deg back diagonal. Sheets `data/wp6v2/result_v3_rehearsal2/`; 3D viewer default
`viewer/v3_rehearsal.glb`.
Final checks: full suite (10 workers) no WP6 failure (load-only failures pass alone; stale WP5 test_a4 untouched); T5
`t5_v3c` 47/47; estimate `estimate_v3.json` 40 replies $15.06 (up to $21.71), 60 replies $32.27 (up to $46.18).
Package `data/wp6v2/lab/package_008` and request `facet-astra-008` (cap $30, effort max, no reply cap, K 4) prepared,
UNAPPROVED (request sha 6d205b95…, registry 5613828a…).

**Paid run `facet-astra-008` (2026-10-13, the first v3 run).** Approval in the owner's words: "you may run it with max
18$ (20$ hard cap). im waiting here for the results, please let me see them in a 3d viewer". Cap $20 (budget guard), a
watcher stopping at $18 settled (`data/wp6v2/lab/stop_at_18_008.py`, never fired), effort max, no reply cap, K 4;
package_008 (code 7a3f295, request 6d205b95…; the earlier $30 request set aside unused under `runs-unused/`).
- 07:26-07:57 local, 40 requests, **$8.993582** settled. Astra finished on its own as **"stuck"**; best checkpoint
  c0015 (revision 25); export `head-681a8190c2cd3fd0.glb` (sha d8e6b88a…); validate_spec ok. In the 3D viewer as
  "Astra run 8 (v3 tools)", the default (`data/wp6v2/viewer/new_astra_run8.glb`).
- Behaviour: its object note named the photos' five faces (fore, logo, upper = the top triangle "one face even if it
  needs warp", lower, tail with a soft border into the arm), the top strip as the top surface, and the 03/06 conflicts
  as "not depth targets". It read all 7 corners as edge crossings in B1+B2 (0 typed values), built the top triangle as
  one warp face (1.48 mm off one plane), declared the open back-triangle line a ridge (2.5°), tried a ridge on the
  hinge-side edge and left it undeclared when the fit refused it (valley 2.2°, open), then spent its last third on the
  arm's end ("the oblique hook/end-cap shape in 02/B2" is its named remaining blocker).
- Final creases: top triangle|logo ridge 28.1° (26.6-38.8), logo back diagonal 7.9°, back-triangle line ridge 2.5°
  (declared, open), hinge-side valley 2.2° (open), tail|arm soft and flat 1.5°.
- Owner's review: the triangle closest to the lenses (the hinge-side face) does not show, its plane being barely
  different from the logo's (Decisions). Measured 2-4° from the readings; the photos' light turns that into a large
  tone step, our renders cannot.

**Closed (2026-10-13).** Owner: "you know what. im satisfied with wp6, lets wrap this". WP6 is complete on run 8 (c0015, revision 25). This
is the owner's acceptance, not an all-runs pass.

Open at closing (proposed after run 8, not built; the owner closed WP6 without them):
- **Image pile.** Astra's input is the whole conversation on every request. In run 8 it grew from 9 to 136 images
  (26k to 219k tokens) by request 40; images carry no revision or compare id and no current/superseded mark, and
  the context guard (240k tokens) never fired. Proposal: label every image, and replace superseded ones with short
  text notes every few replies (keep the photos, the latest compare per view, current pick sheets, the best
  checkpoint). This matters for any long Astra loop (WP7/WP8), not only WP6.
- **Visible faces.** An edge the photos show crisply can be measured at only a few degrees and is then built too
  faint to see (run 8's hinge-side face; the fit refused Astra's ridge declaration). Proposal: a declaration with a
  visible minimum angle, chosen from renders and honoured over the readings, plus one prompt rule.
- Astra's named blocker at its finish: the arm's end hook in photos 02/B2.
- Owner's later plan: a natural-language "more edits" box at the end of the automation, where Astra edits the
  model by the user's request (instead of parameter sliders).
- Leftovers, git-ignored: the $0 hinge-face tool test the owner stopped (`data/wp6v2/result_run8_hinge_face/`,
  with its never-committed script `wp6v3_hinge_face.py`) and its `run8_hinge_face_*.glb` entries in the viewer.

## Decisions (owner's words verbatim)

- **2026-10-13, decisions taken up front for the assembly (`ASSEMBLY.md` §4):** description vs photos: "Photos for the
  look" (photos win on shape and colour, the description on facts photos cannot show). Print tools: "astra can use it
  anytime it see fits. but we need the instructions to be good enough so astra will be able to decide to use the tool
  when it needs to (i.e. a tortoiseshell print and others means the tool needs to be used, while other frames dont need
  a print because their visuals arent complex, editing their material and color and finish is good)". Crystal frames:
  "Apply the check to all frames". Known-good product: "Oakley OO9208 v2". Per-run money: "20$ hard stop 23$ max
  reserve". Effort: max. Lens reflections: one fixed default. Total WP8 budget: "theres no reason to talk about this at
  the moment. this will be a thought when we'll have a full operational automation".
- **2026-10-13, process:** Claude plans, Codex executes (`ASSEMBLY.md`).
- **2026-10-13, Oakley run 1 job corrected by Claude before approval:** the M5-prep job
  `oakley-oo9208-v2-cd068969` (smoke run done there) put two engineering lines into "Owner's specifications
  (verbatim)" (`assembly_run_1_scope: "frame + lenses + seating only."`, `assembly_scope_source`), which Astra would
  read as the owner's words and could take as "build no temples", and repeated the owner's note as both description
  and specs. Replaced by `oakley-oo9208-v2-3e057bab`: the same six photos (sha256 identical to the Studio job 3dff8674),
  the same labels (checked by eye: 01 front, 02 angled wearer-left, 03 side right, 04 rear, 05 side left, 06 top), the
  owner's original note verbatim as the description, specs `{}`. Brief fix `cdb52a2`: side labels are the wearer's
  side. Run request `20261013T163235-772629`: expected $14.96, range $13.20-$20.12, $20 settled stop, $23 reserve.
- **2026-10-13, smoke turn pre-approved:** "Yes, cap $1" — one real request on the Oakley job after ASSEMBLY M5-prep
  items 1-4 pass, $1 settled stop and $1 reserve cap. Oakley run 1 still needs its own OK and estimate.

- **2026-10-13, WP6 closed:** "you know what. im satisfied with wp6, lets wrap this".
- **2026-10-13, after run 8:** "the closest triangle to the lenses is not visible since its plane is barely
  different than the logo. you can also see in the evidence that our algorithm didnt catch that triangle in any of
  the layout images. correct me if im wrong." Then, on edits: "lets focus on astra, what if we just gave it more
  images? at the end of the entire automation i plan to add "more edits" iput box where the user can say "this
  facets meeting point should be further out, so the left facet would have more degrees " and then should edit buy
  the user request. i like this combo better, what do you think?"; and, stopping the $0 hinge-face variants:
  "focus. stop all youre doing i dont need a fixed temple i need wp6 to work!!!!!".
- **2026-10-13, run 8:** "you may run it with max 18$ (20$ hard cap). im waiting here for the results, please let me
  see them in a 3d viewer".
- **2026-10-12, after the investigation:** "please fix wp6. do very very hard work!!! come back to me when you think
  we are good to go with a paid run. please do your work with the conclusions from before. +max utilize this machine
  for faster runs". WP6 resumed as v3 (above).

- **2026-10-12, after run 7: STOP.** Owner, verbatim: "its not fucking good. i dont now what to tell you anymore, i dont
  think this is fucking possible anymore and youve given me zero reasons to believe that you can make it work. every
  model has an other defect this is fucking exhausting", then, on the offer of one $0 feasibility check: "it sounds
  stupid, because how can we improve on astras understanding?????? we cant so stop fucking easting my money and
  time?!!!!!!!!!!!!!!!!". WP6 Astra facet runs are stopped: no further paid runs, no further work on this approach,
  no new proposals unless the owner asks. Runs 2-7 each fixed the defect named last and showed another; the
  underlying limit was camera disagreement of 1-2 mm per corner against facets whose shape hinges on that much.

- **2026-10-11, light for photo-matched renders:** "c is definitely the best". The owner was shown A/B/C on the
  preferred and original models at all 7 photos, with neutral letters and identical lenses. C = `studio_dome_v1`
  (A = photo_env, B = catalog_band_v1). Record: `data/wp6v2/light_choice/owner_choice.json`. `studio_dome_v1` is
  the job rig for Astra and owner sheets. Its facet tone order is still not the photos' (WP6_FACETS §3.2), so
  shading stays a qualitative aid.

- **2026-10-10, WP6 rebuild:** "please rebuild wp6" (reply to the review that proposed judging WP6 by eye on
  photo-matched renders); "i just want astra to be able to handle facets and such, thats it. you can do whatever,
  even rebuild from scratch". Recorded design: [WP6_FACETS.md](WP6_FACETS.md) (62b25e4).
- **2026-10-10, Astra temple lab trial rejected:** "ok what did you learn? the results are still not good".
  Run `real-astra-001` (35 replies, 34 tool calls, $5.599202 settled of a $10 cap) exported c0003; one layout at
  two depths, hard full-length construction edges where the photos show fading diagonals. Evidence:
  `data/wp6/astra_temple_lab_20261010/` (owner-review-20261010.json, real-astra-report.md).
- **2026-10-08, WP5 closed:** "Observed-slope join + Poisson is the best, set it and mark wp5 as done".
- **Owner's edit to the v1 session prompt** (kept in the archived copy): "Use heavy CPU resources for faster runs."
  Process pools within free-commit memory (glasses3d/AGENTS.md).
- **Process (coding agent, 2026-10-10):** Codex sessions work sequentially; Claude sessions with ultracode on may run
  workflows with disjoint file ownership; the coordinator commits with explicit paths.

## Retired WP6 v1 gates (2026-10-10, owner decision)

S1, R1–R4, G1–G5, P2, the P3 decal limit, the STOP-I/C/R/S/T/P rules, `relief_rules.py` with its frozen thresholds
and the frozen S1 dependency table, and the delegated "KEEP STOP-I/R1/R3" decision. The review showed the true or
owner-preferred temple fails R1, R3 and P3, and that facet depth is not measurable from these photos through
silhouettes (WP6_FACETS §0). The v1 code is preserved under the local tag **`wp6-v1-final`** (3ad4459, never
pushed): `git show wp6-v1-final:glasses3d/<path>`.

The general rule stays: never loosen a gate to get a pass; if a gate is wrong, prove it (the true or owner-accepted
answer fails it) and ask the owner.

## Known-good references

FT1123 crystal, Oakley OO9208 v2 and INVU IA22403B v2, re-accepted by the owner on 2026-10-06 (asset-bound verdicts
in `data/ledger/owner_verdicts.jsonl`). **Prada PR 17WS is an open failure**: its older "looks great" verdict
predates noticing the facets and the shortened-temple rendering defect.
The owner's 2026-10-13 WP6 acceptance (run 8's temples) closes WP6; it is not a whole-asset Prada verdict.

## Archive step (track F, 2026-10-10)

Removed (`git rm`): `g3d_host/relief_{anchors,crop,cue,edges,fit,hinge,junction,photo_mask,ridge,transition}.py`,
`g3d_host/camera_similarity.py` (restored 2026-10-11 in `bf0b699` for the B2/B3 camera transport of
`tests/proofs/wp6v2_basis.py`), every `tests/proofs/wp6_*` except the Blender driver, the camera/occlusion evidence
fixtures, and 33 test files that only exercised v1 code. Renamed `tests/proofs/wp6_bpy_run.py` →
`tests/proofs/temple_bpy_run.py` (the Blender driver of `test_temple_blender.py`; it imports no v1 module). Kept and
stripped: `test_temple_blender.py` (P0 instrument grade dropped), `test_temple_real.py` (glossy WP3b table test only),
`test_temple_design_checks.py` (builder warnings + the decal-standoff test moved in from `test_temple_relief_cue.py`).

The six relief modules then pending (`relief_trace`, `relief_check`, `relief_views`, `relief_rules`,
`relief_registration`, `relief_normal_residual`) were removed in build step 3. The stale doc links listed here were
fixed in the 2026-10-13 cleanup ([REVIEW_20261013.md](REVIEW_20261013.md) §7).

Checks (basetemp `data/pytest_tmp/f_archive`): `pytest --collect-only glasses3d/tests` collects 1904 tests with 0
errors; temple/spec/glb/lens_id suites 290 passed; `tests/agent` 420 passed; `test_wp2_*` 363 passed;
`test_temple_blender.py` 7 passed (renamed driver); all 105 host-importable modules of g3d, g3d_host and agent
import, and the 7 Blender-only modules import in Blender 5.2.

## Ledger (2026-10-10)

- `3e8e247` camera uncertainty + apex-gap diagnostics; `c9ad82f` owner accepted both apex comparison variants.
- `b46bda8` opt-in camera transfer + arm review checkpoint; `e846e0f` owner preference for the distal-arm revision.
- `dbebc7c` opt-in contour-normal refinement; `c97df92` retained replay failures + full diagnostic run;
  `db95572` fresh S1 floor + preferred export coverage audit; `8d6e006` full-proof progress; `e6b31a7` completed
  proof saved for the owner's review pause.
- `c99da5c` owner rejection + fresh-chat handoff; `70c9e90` opt-in smooth facet-to-arm comparison tool;
  `6c72c9f` owner decision to retain the preferred reference.
- `f0205a3` facet normals + complete P3 export checks; `b5add8f` preferred recovery + live photo diagnostic;
  `8a2113c` native recovery + R1 preservation checks; `b1c8651` all exported decals graded + preferred comparison.
- `166de93` isolated Astra temple lab; `008fc73` beige references + flat-temple views; `ddd6658` owner-authorized
  test without a reply cap; `62662f0` observed Astra test documented; `3ad4459` owner rejection and lessons
  (= `wp6-v1-final`).
- `62b25e4` WP6 v2 facets design (owner-approved rebuild).
- `4d84cf1` archive WP6 v1 + start docs; `fa857b0` seeing (pose basis, matched compare, white balance, photo_env);
  `04f8ceb` builder g3d.temple/2 + template + drawings + Blender layer; `c102efb` builder review fixes;
  `cf9cf1a` Astra facet tools, loop finish settings, rehearsals, light choice; `831a8a2` pre-flight (owner light C,
  gate/timeout fixes); next: surface-only distinct-head rule, rebuilt package and estimate.
- `8378bc4`, `4c24124` WP6 v3 build (fit, warp faces, crossing readings, seeing, lab, prompt); `be8d56d`,
  `7a3f295` blind stand-in rehearsal fixes + owner sheets; `7fcf037` paid run 8; then WP6 closed by the owner.
