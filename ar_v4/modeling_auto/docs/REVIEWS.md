# Review pass — runtime v8, 2026-09-14

Implements the review notes recorded after the accepted checkpoint
`automation_pipeline_first_perfecto`. Runtime is `modeling-auto-20260914-review-v8`.
The running v7 service on port 8060 was idle and already reports "Restart
Modeling Auto to load changed files"; it was not restarted by this pass.

## Behaviour changes

- **Pipelines renamed and default swapped.** The six-stage plan is `standard`
  and the default; the five-stage plan is `legacy`. `test` and `current` remain
  accepted aliases in creation requests, URLs, saved jobs and saved Astra
  contexts (`app/workflows.py`). Public JSON always reports the canonical name.
- **Product notes are never overwritten.** Both plans store an edit's text as
  `edit_instructions` and send "Original product notes / Specific instructions
  for this edit". The legacy plan previously replaced the notes with the edit
  text (asserted as intended in the old `test_controller.py:116`); that test now
  asserts preservation. Instructions with any non-edit action are rejected.
- **Sandbox widened and listed.** `type(value)` (one argument only) and the
  Blender-relevant exceptions (`ReferenceError`, `NameError`, `LookupError`,
  `ArithmeticError`, `OverflowError`, `RecursionError`, `MemoryError`,
  `UnicodeError`, `FloatingPointError`) are available. The prompt lists the exact
  builtin set from `builtin_names()`; a test pins prompt and runtime together.
- **Tolerant parsing.** A `message` item beside the one custom tool call no
  longer discards the paid script; any other tool call still does. Backticks
  matter only when the script does not parse.
- **Lens surface metrics.** `inspection["lenses"]` carries per-lens dihedral
  quantiles, crease fraction and per-side ripple p90, sign mix, curvature spread
  and sphere-fit residual. The seating prompt receives them, the viewer shows the
  worst ripple and sign mix, and `scripts/lens_surface_report.py` prints them.
- **Controller structure.** `_execute` is split into `_run_meshy`, `_run_astra`,
  `_saved_native_result` and `_adopt_revision`; the duplicate result validation
  is gone; master hashing, input verification and result validation run in a
  worker thread instead of on the event loop; `Store.artifact` accepts a known
  checksum. Semicolon-joined statements were split in the controller, server
  and worker.
- **Worker.** Finite-coordinate checks use numpy `foreach_get` instead of a
  Python loop over every vertex. Meshy polling keeps the first, latest pending
  and terminal receipts per task.
- **Docs.** `HANDOFF.md` is current state only; the chronological record moved
  verbatim to `docs/HISTORY.md`. README, AGENTS and CONTRACT updated.

## Not done, on purpose

Memoizing the auth-rejection receipt check was planned and dropped: the test
`test_corrupt_or_conflicting_receipts_never_enable_auth_retry` corrupts receipt
files without a version bump and expects the next read to notice, which is a
tamper-evidence guarantee worth more than the poll cost.

## Verification (zero paid requests)

- Backend: **376 passed** (362 existing + 14 new), `--basetemp=data/rev2`; the
  validation module re-run with `-W error::SyntaxWarning` passes.
- Frontend: strict TypeScript/Vite build; **32 browser cases pass** (31 + 1 new
  for the lens-metrics line), evidence `data/browser/review-20260914/`.
- Native: `data/e2s-141305/report.json` (standard: 2 fake Meshy + 5 fake Astra
  including one instructed edit) and `data/e2l-141342/report.json` (legacy)
  both pass through the real HTTP app, real adapters on intercepted transport
  and actual Blender, with the new worker.
- Paid scripts: all 13 retained Astra scripts still pass the widened validator
  with no name outside the allowlist.

## Lens metric on the three real jobs

`data/selftest/lens-metrics-20260914-141300/report.json`, worst side per lens:

| job | r001 lenses ripple p90 / sign mix | r002 connections ripple p90 / sign mix |
|---|---|---|
| Ray-Ban RB4455 | 0.36° / 1% | 0.60° / 5% |
| Oakley OO9208 | 0.46° / 1% | 1.91° / 9% |
| Miu Miu 0MU 53 | 0.53° / 2% | 0.86° / 6% |

Two facts the instrument surfaced:

1. **The seating pass makes every lens rougher than the lens-creation pass.**
   Ripple and sign mix rise in all three jobs; the Oakley seating output is the
   worst and matches its wavy close-up. Ripple p90 and sign mix discriminate;
   `curvature_cv` (3–5 everywhere) does not and is kept only as a per-side
   detail.
2. **Lens tags do not survive Meshy retexture.** From r003 on, every job is one
   fused `Mesh_0` (frame + both lenses + UV-seam duplicates) with no
   `auto_role`, so the metric exists only for r001 and r002. The ripple visible
   in the accepted renders is the r002 geometry seen through glass materials.
   Recovering lens identity after retexture (for example by tagging faces that
   coincide with the previous revision's lens vertices) is the next item; it
   changes how the returned Meshy asset is handled and needs its own fixture.

By eye the Miu Miu final looked the worst; by the numbers the Oakley seating
output is. The Miu Miu lens is the only clear, untinted one, so refraction shows
its geometry more than the Oakley mirror does. The metric measures geometry, not
appearance, and stays advisory.

---

# Accepted checkpoint — automation_pipeline_first_perfecto, 2026-09-14

The owner tested the full Test pipeline from the beginning, accepted the result,
and explicitly reported that it works well. Fresh Oakley job
`6eda65cd-d4d2-4e65-9452-7ac734b1807a` completed generate, lenses, connections,
texture, finish and finish_refine with six successful operations and exactly
Astra 4/Meshy 2 reservations. Its accepted r005 master is
`9c221465b726c221357f748378682661f06daa6ffe3a50a05a8ecafc1a0d2eef`.
This establishes owner acceptance of the observed run, not universal model
quality. Runtime source is byte-exact to the v7 implementation verified by
362 backend tests and native Blender checks. The earlier repair statuses below
are historical; the earlier Oakley run is cancelled and the fresh run is complete.

The requested publication name is `automation_pipeline_first_perfecto`, used
for the checkpoint branch and commit subject. Only modeling_auto source, tests,
lockfiles and documentation are included. Credentials, all generated jobs/assets,
local receipts, dependencies and caches remain excluded. An isolated Git index
preserves the active checkout and unrelated edits while basing the checkpoint
on fetched main `8c56478eef5554320357033225d98a6af774676c`. Main and deployment
configuration are unchanged. Local preservation/publication receipts are in
`data/maintenance/publish-first-perfecto-20260914/`.

## Earlier review — missing bytearray runtime builtin, 2026-09-14

Oakley test job `405e31ee-d1e1-49c1-8a52-dcef81f000b7` retained blank r000 after
the saved lens script failed at line 240 with `name 'bytearray' is not defined`.
The script's nine uses allocate small binary masks for occupancy, connected
components and flood filling. Each grid mask is bounded to roughly 95 KB by
its grid construction. A recursive symbol-table audit found no other missing
builtin. Raw validation already allowed bytearray; the runtime now exposes the
standard Python container, and the prompt/native guide describe it consistently.
File, native-address and private-attribute restrictions remain unchanged.

The exact paid response remains SHA256
`6da12c6265279a095215eed83c27782c8865f69a1b0f18e531544b8af8b8363a`; blank master
remains `6bc4e2fb9ce27a55cc08f65414815bdc1d25a55d6721eeacaf573944c85c22fb`.
The server was gracefully stopped while idle after a 416-file hash snapshot.
Development verification sends no provider requests. Native saved-edit and
activation evidence is retained under `data/maintenance/bytearray-20260914/`.
The earlier Miu Miu run is now owner-accepted at r005, Astra 4/Meshy 2; its
earlier in-progress status below is historical. Both accepted jobs are preserved.

Actual Blender verification passed in **76.30 seconds** at normal 768 px/24
samples. The saved script executed in 20.52 seconds, creating exactly two closed
tagged lenses (36,002 vertices each). The original 1,519,049-vertex frame passed
the existing source protections. Five views, tagged lens close-up, packed master,
GLB export and reopen/hash checks passed. Original r000, paid response, their
copies and the production job state stayed byte-exact. Green mirrored lenses
and the retained black frame are visible in the inspected renders; their final
appearance remains for owner review. Outputs: `data/selftest/ba-0914/revision/`.

Two full backend runs exposed intermittent Windows access-denied errors during
atomic test job.json replacement, in different existing tests. Evidence is kept
in backend.xml/backend-final.xml and data/baf1/data/baf2. The local atomic rename
now allows at most six attempts over 400 ms for Windows permission/sharing-lock
codes 5/32/33 only. It reuses the same already-fsynced temporary bytes; it never
deletes/truncates the destination, repeats the write or retries a provider call.
Permanent failures still propagate and preserve the previous destination.
After the correction, **all 362 backend tests pass**, including actual missing-
builtin execution/recovery without another lens request and 12 local atomic-save
fault-injection cases. Earlier failed runs remain retained. Frontend source and
build are unchanged; this repair is confined to runtime capabilities and local
state persistence. Native verification above exercises the real saved script.
Activation and recovery evidence follows below.

**Activation and recovery succeeded.** Runtime v7 matches the tested manifest;
all 416 original files were byte-exact before explicit recovery. The served
failure page and reload made no POST or state change. The normal controller then
reused the saved lens response and adopted **r001** in 152 seconds. The run is now
executing the authorized **connections** stage, with Astra 2/Meshy 1 reservations
and still only one lens Astra operation. All 415 preexisting files except the
updated Oakley job.json remain exact, including both earlier accepted jobs. No
new lens provider request was sent. See activated-runtime.json, live-ui.json,
recovery-result.json and production-native.json in the maintenance folder above.

## Earlier review — finish texture sampling timeout, 2026-09-14

Miu Miu test job `7d87c7a4-7d07-4820-b355-b9211d17050c` completed texture r003,
then its saved first finish script exceeded the existing 1200-second Blender
deadline. Three native stack samples identify line 163, a four-channel texture
slice. Input checks took about 7 seconds; rendering had not started.
[Blender's RNA implementation](https://raw.githubusercontent.com/blender/blender/main/source/blender/python/intern/bpy_rna.cc)
copies the entire float array for an indexed/sliced read. Locally each 8K sample
took 0.39–0.40 seconds. One compact full buffer took 0.46 seconds to allocate/fill,
then 100,000 slices took 0.023 seconds with exact matching sampled values.

The worker now adapts validated Python attribute access in memory. Image pixels
use native foreach_get into exact float32 buffers, with a 2 GiB retained-cache
limit and LRU eviction. Writes and relevant image changes invalidate snapshots;
ordinary non-image access remains native. Raw source and response bytes remain
unchanged. Dynamic names still pass the existing allowlist, and private helper
binding through imports, exceptions or match patterns is rejected. Buffers are
released before rendering. No image resampling, geometry-check relaxation,
deadline increase, failed-request retry or pipeline change was introduced.

Each new native attempt retains phase timings and a script-hash/pixel-cache
receipt. Timeouts name a recognized phase when available. A 331-file production
snapshot was taken before graceful idle shutdown. Development verification uses
local saved inputs and synthetic fixtures only; no provider requests are sent.
Evidence is under `data/maintenance/finish-timeout-20260914/` and
`data/selftest/ft-0914/`.

Verification: **340 backend tests, 31 browser cases, strict frontend build and
15 actual Blender pixel semantics checks pass**. The saved finish script then
passed the full normal 768 px/24-sample worker in **164.14 seconds**: editing
94.3 seconds, two bulk copies totaling 1.25 GiB and 2,883,920 cache hits. Exact
geometry lock, all original packed 8K/4K image hashes, five proofs, close-up,
packed master, GLB export and reopen checks pass. Native outputs are retained
separately at `data/selftest/ff-0914/`. Front, angled and close-up inspection show
transparent lenses, metallic hardware and amber tips; geometric imperfections
remain for owner review. This verifies execution, not visual acceptance.

The execution receipt hashes the UTF-8 script string supplied in request.json
(`7ad5d3…`); normal read_text converted the saved file's CRLF newlines to LF.
The original paid file remains byte-exact (`50bc4e…`). No script instruction was
changed. Runtime `modeling-auto-20260914-pixels-v6` is restarted and ready; all
331 preexisting production files match the snapshot and the runtime source
matches the tested manifest. Startup resumed no job. Explicit recovery evidence
is recorded below after applying the saved response for the owner's repair.

**Production recovery succeeded:** the saved finish response was applied once
through the normal controller and adopted as **r004**. The run automatically
advanced to **finish_refine**, with Astra 4/Meshy 2 reservations. There remains
exactly one finish Astra operation; only the next authorized refinement request
was added. All 330 preexisting files except the legitimately updated target
job.json remain byte-exact, including the earlier accepted Ray-Ban master. The
served r003 failure/recovery page also passed read-only inspection and reload
without any POST or state change. Evidence: `recovery-result.json`,
`production-native.json` and `live-ui.json` in the maintenance folder above.
The final refinement is still running; r004 is available meanwhile.

## Earlier correction — saved pointer identity query, 2026-09-14

The Miu Miu test job's first Astra response was complete, but the local validator
rejected `as_pointer` before any Blender edit. Its only two uses (lines628/630)
deduplicate shared mesh datablocks. The read-only integer identity call is now
allowed only when invoked directly with zero arguments. Method capture,
assignment/deletion, reflective access, raw-address conversion and arbitrary
native imports remain blocked. [Blender's API documentation](https://docs.blender.org/api/dev/bpy.types.bpy_struct.html#bpy.types.bpy_struct.as_pointer)
describes the returned integer; no memory dereference is needed by this script.
The prompt, AST contract and native guide now agree. No geometry safeguard,
workflow step, quality setting or provider retry behavior was relaxed.

The exact saved 746-line Python response (SHA256
`c44831ec3186ff0cbc2a443e3c8417e11fc08550c1e35b83e075ef9bd7fdbed4`)
passed the complete unchanged Blender lens-stage checks on an isolated r000
copy in **86.7 seconds**, with normal 768 px/24 sample previews. It created two
closed tagged lens solids (18,690 vertices each), exported GLB, packed/reopened
the master and produced all five views plus the lens/contact close-up. Source
topology/bounds were preserved; the reported 0.003 displacement fraction is the
existing permitted threshold, not a measured displacement. The code changes no
source vertex coordinates or transforms. All 42 target production files, input
and paid response remain byte-identical.

Actual renders show cleared optical centers and retained frame/attachment
structure. Uneven original optical edges remain for the subsequent seating
pass to inspect. This proves saved-script execution and local constraints,
not final lens fit or visual acceptance. The native output remains separate
from the production job. After successful checks and activation, the saved-edit
recovery action was submitted once for the owner's reported failure. It reuses
the completed lens response locally and then continues the remaining originally
authorized sequence. No new paid request was used to diagnose or test this
correction, and the failed lens request is not resubmitted. Normal remaining
provider stages retain the original run authorization.

**Verification and activation:** all **293 backend tests pass**, including the
new direct identity-query contract and recovery of a saved rejected lens script
exactly once before completing the six-stage test plan. The production app is
restarted and ready as `modeling-auto-20260914-pointer-v5`; all **232 original
production files were verified byte-exact before explicit recovery**, including
the previous accepted Ray-Ban master and all Miu Miu inputs/receipts. Runtime
source matches the tested manifest. Startup resumed no job; the later recovery
is an explicit action following the owner's repair request.

Browser regression testing exposed a repeatable cold fixture-server delay:
the first navigation took 27.3 seconds, exhausting a preexisting 30-second case.
The test-only Vite server now limits dependency discovery to index.html and
excludes generated data and Python dependencies from watching. With the same
two workers and deadlines, the affected case then takes 2.8 seconds. Production
frontend/render behavior is unchanged; earlier timeout receipts remain under
`data/browser/pointer-20260914/` and its serial subfolder.

All **31 browser cases pass** with unchanged two-worker/30-second settings,
including the exact as_pointer/r000/test-step2 failure, saved-script recovery,
no action on reload and no duplicate recovery POST. Final evidence is in
`data/browser/pointer-20260914/final/`. The actual served job also passes
read-only inspection/reload with no mutations, external requests or page
errors before explicit recovery (`live-ui.json`).

**Production recovery succeeded:** the exact saved lens script completed in
the real job, and r001 was adopted. The automatic sequence continued to
connections with Astra2/Meshy1 reservations: one original lenses request plus
the new authorized seating request. There was no replacement lenses request.
All231 preexisting files except the intentionally updated job-state JSON remain
byte-exact, including the blank, paid response and previous accepted job.
`recovery-result.json` records the single completed lens operation and continued
stage. Subsequent modeling quality and remaining provider responses are still
part of the normal live run, not outcomes claimed by this repair.

Evidence is retained under `data/maintenance/pointer-20260914/` and
`data/selftest/ptr-0914/`. The previous accepted Ray-Ban job, private references,
all earlier receipts, other applications, AR and deployment remain unchanged.

## Previous separate automatic test pipeline, 2026-09-13

The owner requested a separate automatic sequence with two Astra/Blender edits
before Meshy texturing and two afterwards. **Test pipeline** is now selectable
for new models at `/?pipeline=test`. Start authorizes generate → lenses →
connections → texture → finish → finish_refine, with two Meshy/four Astra
requests and no intermediate approvals. Existing and legacy jobs retain their
current five-call sequence and earlier review behavior.

Both test finish passes receive original5 plus fresh current5 and lens/rim
close-up. Request receipts bind each input image, current master and scene
inspection to the preceding adopted revision. The extra pass maps to the
existing native material-only finish scope; geometry, UVs, normals, transforms,
scene and visibility protections remain. No quality setting or API fallback was
introduced. Final review offers **Download & finish run** or **Send another
edit**, requiring specific written instructions for one further Astra request.
Original product notes and every preceding revision remain intact.

The final review corrected two integration issues before activation: the UI now
adds review/complete after the backend's remote-stage list, and a failed second
finish pass never claims both passes succeeded. Its saved first-pass model and
failed second step remain clearly identified.

Browser testing also exposed synchronous GPU initialization on the empty upload
page before its event handlers and polling started. The viewer now initializes
only when an actual model URL is opened. Empty forms need no WebGL context;
permanent disposal and generation checks prevent delayed loads from reviving a
closed viewer. Rendering settings and the model's materials remain unchanged.
Earlier browser startup-timeout receipts were retained in the distinct initial,
final and serial test folders rather than hidden by automatic test retries.
Cold development-server/browser startup remained variable; the evidence does
not establish GPU initialization as its sole cause or measure a speed increase.
Retained traces show the development server taking 13.258 seconds to return CSS
and about 7.1 seconds for other startup modules during a reload.

**Backend verification:** all **274 Python tests pass**, including exact request
image order/hashes, immutable mode selection, the extra stage's cancellation,
adoption/restart boundaries, authentication retry, saved-result recovery,
required feedback, single-request repeat, exact acceptance and unchanged legacy
behavior. Test receipts are under `data/maintenance/test-pipeline-20260913/`.

**Browser verification:** all **30 Chromium cases pass** with the original
two-worker/30-second settings and no automatic retries; strict TypeScript/Vite
build passes. Cases cover both pipelines, exact download, required feedback,
saved-mode precedence, six-stage progress and mobile layout, correct failed-step
copy, missing-GPU fallback and delayed-load/disposal lifecycle. Evidence is
`data/browser/test-pipeline-20260913-lazy/results.json`. Earlier failure receipts
remain available in the initial/final/serial folders. The served HTML/JS/CSS
match the final build byte-for-byte.
After the final fallback-message correction, three focused creation/GPU-fallback/
lifecycle cases also pass in `data/browser/test-pipeline-20260913-viewer-final/`.
Read-only inspection of the actual served test page, reload, mode switching and
the original completed job passes with no mutations, external requests or page
errors. Desktop/mobile screenshots and `live-ui.json` are in the maintenance
folder. A first inspection locator matched both the timeline and navigation;
the inspection selector was narrowed, with no application change required.

**Actual Blender verification:** `data/e2t-194705/report.json` passes the complete
test HTTP application/provider-adapter/native sequence plus one instructed
extra edit. Astra image counts are **5,6,11,11,11**. Each request consumes the
preceding result; both material passes and the repeat preserve tested geometry
and packed texture hashes. All original artifacts remain byte-exact. The exact
accepted download survives store reopen without dispatch. Final synthetic views
and the actual lens/contact close-up were visually inspected. The retained
current pipeline also passes: `data/e2e-194839/report.json`, images **5,6,5,5**,
including its one finish repeat. All provider traffic was intercepted by fake
transport; **zero paid requests** were made.

**Preservation and activation:** the owner had completed the Ray-Ban job before
this change. After restarting only the idle Modeling Auto service, all **190
production job files are byte-exact**, r005 remains accepted, and reservations
remain Astra5/Meshy2. The accepted master is
`492a2eb8384069415aaf4d912bb373507d0c2403b7cf67c3587e301286ca8dcb`.
Runtime `modeling-auto-20260913-test-v4` is ready and idle on port8060, with its
source matching the tested manifest. Prior state descriptions below are
historical evidence, not directions to retry the completed job.

These checks establish local workflow behavior on synthetic fixtures. Live
Meshy/Astra response quality, product fidelity and paid completion time for the
new test sequence were not measured. Existing settings, private assets, the
other application, AR and deployment remain outside this change.

## Previous saved-script recovery and stage clarity, 2026-09-13

The owner saw a successful Astra model before a later `__len__` error. The job
record confirms two different stages: r001 was adopted by **Smooth & create
lenses** at 15:12:45 UTC; **Lens seating** started next at 15:12:46 and failed
local Python validation at 15:16:26, about 3 minutes 40 seconds later. Its Blender
edit had not started. The successful displayed r001 was never rejected or altered.

The saved 355-line connection script used `hasattr(value, '__len__')` while reading
shader socket values. The blanket private-name validator rejected that Boolean
query. Validation and guarded execution now permit only this length-protocol
presence check; direct method access, getattr/setattr and all other private
attributes remain unavailable. The AST exception is limited to the second
argument of an exact two-argument hasattr call. Original paid response/script
bytes remain unchanged. Prompt and native capability guidance agree.

The UI now distinguishes `current.stage` (the successful displayed revision)
from `stage` (active or failed work), names the failed step and puts its technical
details behind an expandable label. Saved Python recovery has the explicit
**Apply saved Astra edit & continue** action. It runs that paid script locally,
then continues the remaining original sequence; it does not repeat the Astra
request. No intermediate approval gates or different modeling workflow were added.

**Verification:** 234 Python regressions and 21 Chromium scenarios pass; strict
TypeScript/Vite build passes. New tests cover scalar/array socket inspection,
private-access rejection, exact saved-response recovery with unchanged paid
accounting, distinct displayed/running/failed stages, and one explicit recovery
POST. Desktop/mobile failure layouts and the actual served job were inspected.

The exact saved script (`c7bb97bddbf89d2b0f6058cbd0bb8b7dc491ee4e235ce642fa2fa37b46249bcf`)
executed on r001 in a separate native Blender output directory. The existing
connection-stage checks passed: protected source topology retained, source
displacement zero, closed-lens/static checks passed, five renders and close-up
produced, GLB exported and packed Blender master reopened with matching geometry.
The script reads material data and writes only the tagged lens geometry/normals.
Before/after angled renders were visually inspected. This confirms this script's
local execution, not universal automatic lens fit or owner acceptance.

The original job remains at connections with current r001 and **Astra 3 / Meshy
1** reservations. All **62 production job files are byte-exact** after restart,
including original inputs, both revisions and all paid response receipts. Runtime
`modeling-auto-20260913-length-v3` is running and idle at port 8060 with a matching
tested manifest. The actual UI/reload made no mutations or external requests and
had no page errors. No new paid calls were made. The original selected model,
other application, AR, deployment and private recovery/recordings remain untouched.

Evidence: `data/maintenance/len-contract-20260913/` contains test XML, native-report,
tested-runtime, activated-runtime, live-ui, and live UI screenshots. The isolated
native outputs are in `data/selftest/len-0913/`. Browser evidence is in
`data/browser/results.json` and `lens-seating-failure-{desktop,mobile}.png`.

## Previous authentication correction, 2026-09-13

**Resolved cause:** the newly entered OpenAI key was missing its first `s`.
The failed lens-stage response is `401 / invalid_api_key`. One model-list GET
using the corrected key returned HTTP 200 and listed `gpt-6-astra`; the correction
was saved through this app's local settings API. This verifies authentication
and model visibility, not successful inference, billing or output quality.
[OpenAI's error guide](https://developers.openai.com/api/docs/guides/error-codes)
identifies an incorrect key as a cause of 401 responses.

**Review findings corrected:** authentication failure previously stranded a job
after its Meshy blank was complete. The new explicit **Retry Astra & continue**
action requires a definite saved rejection and starts a fresh operation at that
Astra stage, preserving all previous revisions, receipts and counters. It then
continues the remaining sequence. Structured HTTP-error receipts bind the raw
response hash and rejected-key hash; unknown, corrupted, conflicting or partial
results cannot unlock this action. The original v1 rejection is supported only
with its exact saved 401 message plus complete `invalid_api_key` response. New
receipts prohibit retry with the unchanged rejected key. Nothing retries on
settings save, refresh or startup.

The review also found cancellation before reservation/dispatch could strand
unused inputs. Explicit continuation now retains the authorized pending stage
and reuses an undispatched reservation without counting it twice. Retry/edit
cancellation and restart preserve the intended stage. Local saved credentials
now take precedence over inherited environment values after restart, and API
setup rejects OpenAI keys missing the complete `sk-` prefix.

**Validation:** 221 Python tests pass, including all three Astra retry stages,
legacy/current receipts, unchanged-key blocking, stale/duplicate requests,
HTTP routing, ambiguity and corruption rejection, cancellation and restart.
Strict TypeScript/Vite build and all 19 Chromium scenarios pass; three focused
auth cases also pass with desktop/mobile screenshots inspected. The initial
full-suite output path exceeded Windows' path limit; failed fixtures were kept
and the same code tested under a short local path. A separate existing polling
test used a 30 ms sleep and raced task persistence; it now waits for the durable
task boundary before testing cancellation. The subsequent full suite is green.

The idle Modeling Auto server was gracefully stopped and restarted on 8060 as
`modeling-auto-20260913-auth-v2`. Its tested source manifest matches. All **41
saved production job files remain byte-exact**, including the failed response,
references and revision r000. The Ray-Ban job remains paused at lenses with
**Astra 1 / Meshy 1** reservations and the explicit retry available. The actual
served page passes read-only inspection/reload with no mutation, external browser
request or page error. The corrected key is saved; no owner re-entry is needed.

Receipts: `data/maintenance/auth-401-20260913/` contains `auth-check.json`,
`all-tests.xml`, `tested-runtime.json`, `activated-runtime.json`, `live-ui.json`,
`live-auth-actions.png`, and the retained test-failure explanation/receipt.
Browser evidence: `data/browser/results.json`, `results-auth-final.json`, and
`auth-failure-desktop.png` / `auth-failure-mobile.png`.

No paid inference requests were made. Native Blender geometry/rendering and
the AR application were not changed, so no new rendering-quality claim is made.
The next live step is the owner's explicit retry, followed by visual inspection
when the sequence reaches its final preview. The previous pipeline, parent app,
deployment and private recordings/recovery material were left alone.

## Initial readiness review — 2026-09-13

Scope: independent `modeling_auto` implementation. Previous pipeline untouched;
no paid calls, old fixtures, selected-model replacement or experiment resumption.

## Verdict

**Green light for supervised testing of the new software**, after entering its
provider keys. This is supported by complete offline flow and native Blender
evidence, not by a claim that generated scripts or model aesthetics cannot fail.

## Concrete corrections made during review

- Aligned prompts, AST validation and execution imports. Tested native
  `mathutils.bvhtree`, KDTree and geometry operations instead of broad imports or
  disabling validation. Removed obsolete Blender socket/API guidance.
- Repaired the controller/prompt context field mismatch that would have silently
  omitted scene inspection, supplied dimensions and owner feedback.
- Removed the previous map-transfer dependency entirely. The new requested
  texture stage imports Meshy's complete returned GLB and keeps earlier revisions.
  A GLB without tangent attributes imports correctly; texture files remain packed.
- Tested fused optical caps through precise per-face transparent material
  assignments and new curved solid lenses, without changing source topology.
  No guessed contour-sector or thin-to-rim preflight rejects the first Astra call.
- Added exact scene/world/camera/light/collection guards after finding that mesh
  snapshots alone missed these changes. Bounds for smoothing are in world space.
- Recovered complete saved provider responses across the provider/job commit gap.
  Late cancellation can retain a completed Astra response; explicit failed or
  conflicting response streams cannot execute. Partial responses stay diagnostic.
- Added continuation after every completed-stage adoption boundary, including
  finish-to-review, so a stop there does not require another paid request.
- Kept malformed receipts confined to their job instead of preventing startup.
  Uncertain dispatches stay counted and cannot automatically repeat.
- Verified all retained artifact hashes again during cached native recovery.
  Source/reference tampering blocks the next request or acceptance.
- Fixed the Windows process/store lock error path. Native cancellation terminates
  only the owned child tree; timeout/cancel leave the input untouched.
- Allowed empty-key startup for the new setup UI. Settings replacement is atomic,
  idle-only and redacted; injected disk failure preserves previous settings.
- Replaced the arbitrary 256 MB texture upload threshold with bounded streaming
  data-URI JSON. Upload and download share a 2 GB local envelope. This is a local
  resource limit; Meshy's retexture docs do not publish an exact server byte cap.
- Kept user interaction accurate: one automatic run, finish-only repeat, exact
  accepted download, cancellation pending until stopped, preserved viewer after
  navigation/failure, reservation labels, and material judgment using native proofs.

## Evidence

Full Python suite: **178 passed**, `data/tests/all-results.xml`.
Browser: **16 passed**, plus **4 affected cases** after final wording changes;
actual live page also passed its read-only check. Strict TypeScript/build passed.
Native: **25 actual Blender capability/scope checks**, cancellation/timeout and
fresh open-rim/fused-source sequences passed. Final native fixture was visually
checked for uncropped model views and an actual lens/rim close-up.

`data/e2e-125624/report.json` records the integrated HTTP/provider/Blender run:
generate → lenses → connections → texture → finish → one finish repeat → accept.
Astra image counts were **5, 6, 5, 5**. All original revisions survived, packed
texture hashes and geometry were unchanged in the tested finish, and the accepted
download matched its master hash. Reopening that store dispatched nothing.
All provider traffic in this test was intercepted; real paid calls: **zero**.

The new service was stopped only while idle and restarted on port 8060.
`data/restart-report.json` confirms the tested source manifest still matches,
Blender is available, no run resumed, the new production store remains empty,
and all 127 retained integration-fixture files stayed byte-identical. The live
browser check also verified served HTML/JS/CSS against the build byte-for-byte.

## Remaining empirical limits

Both API keys must be configured in this new app. Meshy 7 retexture access is
subject to account rollout; real provider responses and product appearance have
not been measured. Synthetic optical selection does not prove segmentation on
arbitrary Meshy meshes. Numeric bounds and prompts do not establish visual shape
preservation or lens fit. Material transparency can change perceived shape even
when mesh vertices are preserved, so the owner must inspect the final renders.

No earlier rebuilt model was repaired by these prompt changes. A real preservation
test must compare against the saved original blank for that same new job.
