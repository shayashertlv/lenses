# Modeling Auto history

This file is the chronological record that used to live in HANDOFF.md. It is
kept verbatim for provenance: each section describes the state at the time it
was written, so later sections supersede earlier ones. Current state lives in
HANDOFF.md; verification evidence for each change lives in docs/REVIEWS.md.

---

## Handoff as of 2026-09-14 (checkpoint automation_pipeline_first_perfecto)

## Accepted checkpoint — automation_pipeline_first_perfecto

The owner completed a fresh Test pipeline run from the beginning and confirmed
that the pipeline works well. Save this implementation as the named Git branch
and checkpoint commit `automation_pipeline_first_perfecto`.

Fresh Oakley job `6eda65cd-d4d2-4e65-9452-7ac734b1807a` completed all six stages
without a failed operation, with Astra 4/Meshy 2 reservations. The owner accepted
r005; its packed master SHA256 is
`9c221465b726c221357f748378682661f06daa6ffe3a50a05a8ecafc1a0d2eef`.
The earlier Oakley recovery job was cancelled by the owner before this fresh run.
All older in-progress descriptions below are historical, not current actions.

Runtime remains `modeling-auto-20260914-bytearray-v7` and matches the source from
the successful 362-test backend run and actual saved-script Blender checks.
No runtime change is needed for publication. Keep generated jobs, recordings,
API keys, dependencies and local evidence excluded from Git. The checkpoint adds
only this standalone directory on top of the fetched main commit; it does not
change main, the active checkout, AR behavior or Railway configuration.
Publication receipts stay under `data/maintenance/publish-first-perfecto-20260914/`.

## Earlier correction — compact lens masks

Oakley test job `405e31ee-d1e1-49c1-8a52-dcef81f000b7` stopped during its first
lens edit with `name 'bytearray' is not defined`. The validated saved script
uses nine compact occupancy/flood-fill masks; bytearray was missing from the
execution builtins. It is now available in runtime
`modeling-auto-20260914-bytearray-v7`, with standard constructors/index updates.
No other builtin or import is missing from this script. Saved response SHA256:
`6da12c6265279a095215eed83c27782c8865f69a1b0f18e531544b8af8b8363a`.
Raw response and r000 stay intact. Existing source protections, runtime deadline,
pixel adapter and no-automatic-retry behavior are unchanged. Verification and
activation evidence is under `data/maintenance/bytearray-20260914/`; see the
current docs/REVIEWS.md entry.

Verification: all 362 backend tests pass. The exact saved lens script passed
actual Blender in 76.30 seconds with two closed tagged lenses, normal previews,
source protections and packed export/reopen checks. The suite also exposed
intermittent Windows atomic-rename failures; only the same local rename now
retries at most six times over 400 ms for codes 5/32/33. No provider call is
retried. Permanent failures preserve the previous destination and propagate.

Production recovery succeeded: **r001 is adopted and connections is running**.
The exact saved lens response was reused; the second Astra reservation is the
next authorized lens-seating stage. All 416 original files matched before
recovery, and all 415 files except the updated Oakley job.json still match.
The app is running on 8060 as v7; startup resumed nothing. Do not trigger another
recovery while connections is active. Current receipts are activated-runtime.json,
recovery-result.json and production-native.json in the maintenance folder above.

The earlier Miu Miu run has now completed and the owner accepted r005, with
Astra 4/Meshy 2. Accepted master SHA256:
`9b3364ba539d44a910c353ea1ff4cca3facae8bfd5e665b948ddabb0c48f755b`.
Its historical in-progress status below is superseded. Both accepted jobs and
the new Oakley response/input are included in the 416-file preservation snapshot.

## Earlier correction — texture sampling deadline

The Miu Miu test run reached texture r003, then first finish stopped at 1200s.
Native stacks and benchmarks identified repeated whole 8K image copies for tiny
RNA pixel samples. Runtime `modeling-auto-20260914-pixels-v6` buffers exact
float32 image reads with a 2 GiB retained-cache bound and mutation invalidation.
The paid response is unchanged; scope checks, textures, resolution, samples and
deadline remain unchanged. New native phase and execution receipts diagnose
future failures. See the current docs/REVIEWS.md entry for validation/activation.

Original paid finish response SHA256:
`50bc4e8388e32f6a5a5a8c723b8ca677d79f415db76791230c28c1b4fc8d77f6`.
Original texture r003 master SHA256:
`e799855a41f31b1aba6ca1df796b5ed3d2e74bd0427d0675b9b185191260617e`.
The existing explicit saved-script recovery reuses this finish response and
then continues the authorized finish_refine request. It never repeats the
failed finish Astra request. Earlier pointer-recovery status below is historical.

Verification passed: 340 backend tests, 31 browser cases, strict build and 15
native pixel semantics checks. Full saved-edit execution took 164.14 seconds
at normal 768 px/24 samples with exact geometry and original packed-image hashes.
Runtime v6 is restarted and ready; all 331 prior production files and the tested
source manifest were verified before recovery. Evidence and retained full native
artifacts are linked in the current review.

Production recovery succeeded: **r004 is adopted; finish_refine is running**.
The saved finish response was reused, with no repeat finish Astra call. Current
reservations are Astra 4/Meshy 2, including the remaining authorized final pass.
All 330 preexisting files other than the updated Miu Miu job.json remain exact.
Do not trigger recovery while this final pass is active. The service continues
independently on port 8060. `recovery-result.json` and `production-native.json`
under the maintenance folder capture the verified adoption and current state.

## Earlier correction — saved mesh identity query

The Miu Miu test job `7d87c7a4-7d07-4820-b355-b9211d17050c` stopped at lenses
because Astra used `me.as_pointer()` twice to deduplicate shared mesh data. The
validator now allows only that direct no-argument call. Method capture/rebinding,
reflective retrieval and address conversion remain blocked. Prompts and native
capability guidance agree. Runtime is `modeling-auto-20260914-pointer-v5`.

The exact saved 746-line script was executed on a separate r000 copy in actual
Blender 5.2: two closed tagged lenses, five previews and lens close-up, packed
master/export/reopen and existing source protections all pass in 86.7 seconds.
All 42 target-job files and the paid script/input remain byte-exact. Native output
is only a repair check; it is not a new adopted production revision. The script
can be reused with **Apply saved Astra edit & continue**, without another Astra
request for lenses; successful recovery continues the remaining authorized stages.

Evidence: `data/maintenance/pointer-20260914/native-report.json` and
`data/selftest/ptr-0914/output/`. Exact script SHA256:
`c44831ec3186ff0cbc2a443e3c8417e11fc08550c1e35b83e075ef9bd7fdbed4`.
See the latest review for full regressions and activation evidence. Test-pipeline
behavior below remains unchanged; its earlier runtime version is historical.

Activation/recovery complete: **293 backend and31 browser tests pass**. The
actual served recovery UI passes read-only checks. All232 original files were
verified unchanged before explicit recovery. The owner's saved lens response
was then applied once through the normal controller: **r001 is adopted and the
run is now at connections**, with Astra2/Meshy1 reservations. There is still
only one lens Astra operation; its original response was reused. The second
Astra request is the next authorized lens-seating stage. All231 preexisting
files other than the legitimately updated Miu Miu job state remain byte-exact.
Evidence: `data/maintenance/pointer-20260914/recovery-result.json`.

## Current implementation — separate test pipeline

Open **http://127.0.0.1:8060/?pipeline=test** to create a test run. The saved job
owns its pipeline choice; existing jobs without a choice remain current. The
current five-call flow remains available through Current pipeline.

The test sequence is generate → lenses → connections → texture → finish →
finish_refine → review, automatically after one Start. This reserves two Meshy
and four Astra requests. Lenses receives original5; connections current5 plus
lens/rim close-up; both finish passes original5 plus fresh current5 and close-up.
Every request binds its inspection/model hash and images to the immediately
preceding adopted revision. Both finish stages run the existing native finish
scope with complete geometry/UV/normal/transform locks.

At review, Download & finish run accepts and downloads the exact packed .blend.
Send another edit requires specific written instructions and reserves only one
finish_refine Astra request, then returns to review. Original notes are retained
separately. Cancellation, restart, saved-script recovery and authentication retry
follow the selected plan. Failure never auto-retries or replaces the last good
revision. Runtime version: `modeling-auto-20260913-test-v4`.

Before this change, the owner had already completed the original Ray-Ban job:
r005 accepted, Astra5/Meshy2 reservations, packed master hash
`492a2eb8384069415aaf4d912bb373507d0c2403b7cf67c3587e301286ca8dcb`.
The earlier paused-state descriptions below are historical repair evidence,
not the current state or an instruction to retry the completed job.

Current verification and activation receipts are recorded at the top of
`docs/REVIEWS.md` and in `data/maintenance/test-pipeline-20260913/`. Native offline
checks support both `scripts/offline_pipeline.py` and `--pipeline test`, with
fully intercepted provider adapters and actual Blender. No development paid
requests are permitted. Synthetic evidence does not establish real model quality.

Final verification: **274 backend tests, 30 browser cases, three focused viewer
checks and strict frontend build pass**. Both actual Blender offline sequences
pass. The final service is running and idle with all190 original job files
byte-exact; the accepted r005 and Astra5/Meshy2 counts are unchanged. The actual
served test page and legacy job pass read-only inspection with no mutation or
page error; HTML/JS/CSS match the final build. No paid calls were made.

## Historical saved connection script and stage clarity

The owner's first successful Astra lens edit is r001. It completed at 15:12:45
UTC and was displayed while the next automatic lens-seating request ran. That
second request failed local validation at 15:16:26 UTC because its read-only
shader inspection used `hasattr(value, '__len__')`. No second Blender edit had
run, and the failure was never a rejection of the displayed r001 model.

The validator/runtime now permit only that Boolean length-protocol query while
retaining direct/private method, getattr/setattr and other dunder restrictions.
The exact saved 355-line connections script was executed unchanged on a separate
r001 copy in native Blender: all checks, five renders, close-up, export and packed
reopen pass; protected source displacement is zero. The original job remains
untouched at connections with r001 and Astra 3 / Meshy 1 reservations.

The UI now names the displayed revision's completed stage and the separate active
or failed step. **Apply saved Astra edit & continue** applies the already-paid
connection script locally, then proceeds to the remaining texturing and finish
stages. It does not repeat the Astra connection request or the earlier stages.
No extra paid calls, workflow pause gates or geometry tuning were introduced.

234 backend tests and 21 browser cases pass; strict frontend build passes.
Native evidence is in `data/maintenance/len-contract-20260913/native-report.json`
and `data/selftest/len-0913/`. The updated runtime version is
`modeling-auto-20260913-length-v3`. See the latest review for activation evidence.
The service is restarted and idle. Activation verified all 62 original job files
byte-exact, the same r001/Astra 3/Meshy 1 state, and saved-script recovery available.
The live page clearly identifies completed lenses versus failed lens seating;
reload performed no mutations or external requests. No new paid calls were made.

## Previous authentication correction

The owner's Ray-Ban RB4455 Zuri job stopped at the first Astra lens request
after successful Meshy generation. The local OpenAI key was missing its initial
`s`; the private saved response confirms `invalid_api_key`. Correcting that
character returned HTTP 200 from one OpenAI model-list GET, which listed
`gpt-6-astra`. The corrected key was saved through this app's settings endpoint.
No paid generation request was made during this repair.

The explicit **Retry Astra & continue** action now restarts only a definitively
authentication-rejected Astra stage, then the remaining original sequence.
It keeps failed requests counted and retains all earlier revisions and receipts.
Settings save/reload/startup never trigger a retry. Network uncertainty and
partial responses remain blocked. Saved local keys take priority on restart;
setup rejects an OpenAI key missing the complete `sk-` prefix. Cancellation
before dispatch also has an explicit continuation path.

The original job remains paused at `lenses`; the owner can use the new retry
button without repeating Meshy generation. See the latest `docs/REVIEWS.md`
entry for test and preservation evidence. Native geometry/render behavior was
not changed; actual Astra output quality remains unmeasured for this job.
The repaired service is running and idle at **http://127.0.0.1:8060**, version
`modeling-auto-20260913-auth-v2`. All 221 backend and 19 browser tests pass.
Restart/live-page verification preserves all 41 job files byte-for-byte and
the original Astra 1 / Meshy 1 counts. The tested manifest and activation receipt
are under `data/maintenance/auth-401-20260913/`.

The owner replaced the earlier task with a new, independent application under
`modeling_auto`. Work on the previous pipeline is forbidden. No prior code,
environment, selected model, job, paid response or fixture is required by this
application. Do not move its jobs into this one or operate its process.

## Current result

The implementation passed offline supervised-testing readiness checks. The new
service runs at **http://127.0.0.1:8060** with Blender 5.2. Its source manifest is
recorded in the latest maintenance receipt; runtime drift blocks further dispatches
until a restart. Both provider keys are configured; the OpenAI key was corrected
in the latest follow-up above. No development paid calls were
made, and no provider account capability or real-product quality was measured.

## Owner's final workflow

Start authorizes **two Meshy and three Astra requests automatically**, then
stops at the final preview. There are no intermediate approval gates in this
new application. Another edit authorizes **only one material/finish Astra
request**, then returns to preview. Do not repeat generation, lens creation,
connection editing or texturing in that loop.

Image sets are exactly five original references for lenses, six current renders
(five views plus lens/rim close-up) for connections, and five original references
for finish. Astra receives one standalone task and returns one Python custom
tool; no historical images, previous scripts or workflow history.

Meshy uses the current documented high-quality configuration: Meshy 7 Ultra,
untextured, no remesh for generation; Meshy 7, 8K, PBR for retexture. Its API uses
four references; Astra receives all five. Retexture imports the entire returned
asset into a new revision. No tangent-dependent map-transfer bridge exists.

## Preservation and evidence limits

The Meshy blank and every later revision remain immutable files. Initial edits
preserve source topology/transforms and bound gentle smoothing; connection edits
lock non-lens geometry; material edits lock all geometry/UVs/normals/transforms.
Scene, camera, light, world and collection changes are rejected. Every output is
hashed, packed, reopened and checked before adoption. Acceptance binds the exact
downloaded `.blend` hash. There is no dimension-fitting or seam-readiness gate.

Fused optical surfaces have a tested non-destructive route: precisely selected
source optical caps can be transparent while separate curved closed lenses are
created. The fixture uses known authored cap coordinates. It is **not a universal
optical segmentation algorithm**. A script could identify the wrong faces or make
poor material choices without moving source vertices. Prompts and structural
checks do not prove visual preservation, curvature correctness, seating quality
or reference fidelity. A changed prompt cannot undo an already changed revision.
Any future real preservation comparison must start from that job's original blank.

Meshy 7 retexture account rollout, real Astra script behavior, real model quality,
and the provider's acceptance of large uploads remain unmeasured. There is no
silent fallback to another quality tier. These are limits of the evidence, not
claims that every future paid run will succeed.

## Passed checks

- `data/tests/all-results.xml`: **178 Python regressions**, including provider
  contracts, script allowlists, controller, HTTP, native runner and recovery.
- `data/browser/results.json`: **16 Chromium UI scenarios**. Four affected cases
  also passed after final copy corrections in `results-final-ui-copy.json`.
- `data/browser/live-readonly/report.json`: actual port 8060 page, setup fields,
  five uploads, runtime state and unchanged empty production job list on reload;
  zero mutations, external requests or page errors.
- `data/restart-report.json`: stopped only the idle new service, restarted it,
  verified the tested runtime manifest, unchanged empty production store and
  **127 retained integration-fixture files** by hash. New service is running.
- `data/selftest/native-capabilities-v4-20260913/report.json`: **25 actual Blender
  import/operator/geometry/scene checks**, including BVH and KDTree.
- `data/selftest/native-lifecycle-20260913-124232/report.json`: owned Blender
  cancellation and timeout, preserving the source.
- `data/selftest/native-20260913-125051/report.json`: fused-source native sequence,
  three scope violations rejected, complete textured GLB import without tangents,
  packed image hashes preserved through the tested finish.
- `data/e2e-125624/report.json`: complete real HTTP application + production
  provider adapters on an intercepted fake transport + actual Blender. Passed
  all five stages, finish-only repeat, final acceptance/download and reopening
  the saved store. Requests were 2 fake Meshy + 4 fake Astra; paid requests zero.
  Exact accepted hash: `f16dd4214215fcf23f807bcdadbb63ac7b30c5929948545040c8125ea66d9d6e`.
- Strict TypeScript/Vite build passed. npm audit reported zero vulnerabilities
  for the installed frontend dependencies. Its Three.js bundle-size advisory is
  a loading-size observation, not a build failure.

## Operations

Use `start.ps1` and `stop.ps1` only from this directory. Stop checks this app's
health and refuses while a job is active. Recovery is always explicit: complete
saved tasks/responses/downloads/native results are reused; ambiguous submissions
are not reissued. Crashes at each stage-adoption boundary preserve continuation
without replaying the completed call. Reserved calls remain counted after failure
or cancellation. User billing is determined by the provider, not the counter.

Keep fixtures and receipts in ignored `data`, dependencies in `.venv` and
`node_modules`, caches inside `data/cache`, and all edits under this directory.
Do not introduce a paid automated smoke test or reset/retry/resume behavior.

Remaining work for the owner: choose **Retry Astra & continue** on the saved
Ray-Ban job, then judge the rendered result when the sequence reaches review.
