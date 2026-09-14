# Modeling Auto

Independent local pipeline at **http://127.0.0.1:8060**. All application code,
settings, dependencies, jobs, receipts, renders and test fixtures live here.
There is no runtime or data dependency on the previous pipeline.

**Accepted checkpoint: `automation_pipeline_first_perfecto` — September 14, 2026.**
The owner completed a fresh Test pipeline run from blank generation through both
finish passes, accepted r005, and reported that the pipeline works well. This
checkpoint retains the tested v7 implementation and the current/legacy mode.

## Run

```powershell
Set-Location 'C:\Users\Shay\PycharmProjects\lenses\ar_v4\modeling_auto'
.\start.ps1
```

Open **API setup** and enter the two provider keys in this new app. Saving them
does not make a provider request. Keys are stored in ignored `.env`; they are
never returned by the HTTP API or passed to Blender. No previous settings are
copied. `MODELING_AUTO_BLENDER` can select another installed Blender executable;
the verified default is Blender 5.2. The app is bound to loopback only.

For a fresh dependency installation run `.\setup.ps1` first. The Python and npm
lockfiles record the tested versions. To stop the new app while idle run
`.\stop.ps1`. If a job is active, cancel it in the app and wait for the stopped
state first. This does not operate any other pipeline or port.

Copy the complete OpenAI key, including its `sk-` prefix. API setup rejects a
missing prefix before saving. Keys saved in this app's `.env` take precedence
over inherited environment keys on restart; environment keys are fallback only
when the corresponding local setting is absent. Saving keys makes no request.

## Current pipeline

Supply a name, exactly five labeled photos (front, back, left, right, angled),
at least three known dimensions in millimeters, and optional notes. Originals
are retained; normalized copies are sent to providers.

1. Meshy generates an untextured model with Meshy 7 Ultra and no remeshing.
2. Astra receives the five original references and one scoped smoothing/lens
   task. Its single Python tool response edits a disposable Blender revision.
3. Astra receives five current renders and an oblique lens/rim close-up. Its
   single script repairs lens seating while non-lens geometry stays locked.
4. Meshy textures the edited GLB using Meshy 7, 8K and PBR. The entire returned
   textured asset becomes a new saved revision through native Blender import.
5. Astra receives the five original references and one complete material/finish
   task. Blender executes it with geometry, UVs, normals and transforms locked.
6. The final preview pauses. **Another edit** repeats only step 5. **Accept**
   binds the exact packed `.blend` hash and exposes its download.

**Start authorizes the entire five-call sequence: two Meshy and three Astra
requests, with no intermediate approval gates.** Another edit authorizes one
Astra finish request. Every Astra request is standalone; no progress history or
earlier images are sent. There is exactly one Python custom-tool response.

## Separate test pipeline

Choose **Test pipeline**, or open **http://127.0.0.1:8060/?pipeline=test**, before
creating a new model. It keeps the same uploads, dimensions, quality settings,
Blender editing restrictions and saved revisions. Existing jobs retain their
original pipeline; opening the test page does not convert or resume them.

The automatic sequence is **Meshy blank → Astra/Blender lenses → Astra/Blender
lens seating with fresh views and close-up → Meshy texture → Astra/Blender finish
pass 1 → Astra/Blender finish pass 2 → final preview**. Start authorizes **two
Meshy and four Astra requests**, with no intermediate approval gates.

Both finish passes receive eleven images: the five original references, five
fresh renders of the immediately preceding model and its lens/frame close-up.
They inherit the existing material-only scope and complete geometry lock. Each
request contains only its current task and scene; earlier scripts and response
history are excluded.

At final review, **Download & finish run** accepts and downloads the exact packed
Blender master. **Send another edit** requires specific written instructions and
runs just one more material/finish Astra request against the current result, then
returns to review. Original product notes remain intact. Failures stop the
sequence and retain the last good revision; retries and recovery stay explicit.

Meshy accepts four references, so it receives front, back, left and right.
All five owner references are retained and used by Astra. Input dimensions are
context for the editor; no automatic resize or measurement-fitting algorithm
changes the baseline.

## Revisions, stopping and recovery

Every provider response, partial response, reserved request and completed
revision stays under `data/jobs/<id>`. A failed edit never replaces the last good
revision. Call counters count **reservations**, including failed or uncertain
requests; provider billing is authoritative for charges.

There are no POST retries or startup resumes. Cancellation stops local work and
the owned Blender child; a remote request already dispatched may still run or
be charged. **Recover saved work** reuses a recorded Meshy task, complete Astra
response, download or native result and then continues the remaining sequence.
Recovery also handles a stop between two stages. An uncertain submission with
no complete saved result cannot be automatically submitted again.

The viewer names the stage that produced its saved revision, separately from
the stage currently running or stopped. A new Astra step can run while you view
the previous successful result. If it fails, that displayed result remains
unchanged. **Apply saved Astra edit & continue** executes a complete saved
script locally after a compatible validator/runtime fix; it sends no new Astra
request for that step, then continues the remaining authorized sequence.

Texture sampling uses compact, exact float32 buffers to avoid repeated copies
of entire 8K images. Saved scripts and texture resolution are preserved. Native
attempts retain execution receipts and phase timings; a timeout identifies the
phase when available. The 1200-second limit and all geometry checks still apply.

For a saved OpenAI `401 / invalid_api_key` rejection, correct the key in **API
setup**, then choose **Retry Astra & continue**. This explicit action creates a
new Astra request at the failed stage and continues the remaining sequence from
the saved model. Completed Meshy generation is reused. The rejected operation,
response and reservation count are retained. New rejection receipts also prevent
retrying the same rejected key. Ambiguous network/partial-response failures do
not unlock this action. A cancellation before any dispatch can be continued
explicitly through **Recover saved work**.

The saved input and every artifact are verified against hashes. Acceptance
downloads the exact inspected master. The textured Meshy return is intentionally
a new complete asset, not a map transfer onto an earlier master; all earlier
geometry remains available as its own revision.

## Capabilities and limits

The prompt, AST validator and Blender builtins share one import contract:
`bpy`, `bmesh`, `math`, `mathutils`, `mathutils.bvhtree`, `mathutils.kdtree`, and
`mathutils.geometry`. Approved operators are explicitly listed in the prompt.
Files, network, arbitrary imports, arbitrary Blender operators, executable
nodes and private Python access are unavailable to editing scripts. These
checks are defense in depth, not an operating-system sandbox.

The read-only `hasattr(value, '__len__')` check is permitted for distinguishing
scalar and array shader values. It returns only a Boolean; other private access
is still blocked. This keeps already-paid scripts usable without rewriting them.

Direct `datablock.as_pointer()` calls with no arguments are permitted for
identifying shared mesh/material data within one script. Capturing/rebinding the
method, retrieving it through attribute helpers and converting raw addresses
remain blocked. A previously rejected complete script can be reused through
**Apply saved Astra edit & continue** after restarting the updated app.

The initial pass preserves source object identity, topology and transforms,
with source vertex movement bounded to 0.3% of overall extent. Separate curved
closed lens meshes are required. Precisely identified fused optical caps may
receive a transparent material without deleting source surfaces. A fresh fused
fixture proves this mechanism, not universal optical-face identification.
Material assignment can still change perceived shape, and numeric checks do
not prove lens fit or aesthetics. Prompt restrictions alone do not guarantee
shape preservation.

Meshy 7 retexturing is documented as a progressive rollout; account access is
not established by local tests. There is no silent lower-quality fallback.
Local resource limits include 24 MB per uploaded reference, 40 million pixels,
10 million native mesh vertices and a 2 GB self-contained GLB envelope. Large
model uploads stream bounded base64 chunks. Real-provider size acceptance and
completion times remain provider-dependent.

Use the Blender-rendered views to judge material appearance; the interactive
GLB viewer cannot reproduce every Blender shader node. No rendering fixture is
a real product-quality acceptance test.

## Offline validation

After setup on a fresh checkout, install the test browser once:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD 'data/cache/playwright'
npx playwright install chromium
```

Then run the local checks from this directory:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=data/t-final -o cache_dir=data/tests/pytest-cache
npm run build
npm test
.\.venv\Scripts\python.exe scripts/offline_pipeline.py
.\.venv\Scripts\python.exe scripts/offline_pipeline.py --pipeline test
```

The last two commands exercise the actual HTTP application, real provider adapters
on a completely intercepted fake transport, and actual Blender, including a
finish-only repeat and accepted download. It creates its own fused synthetic
input and make **zero paid requests**. Run native checks sequentially. The test
variant checks all six stages, one additional instructed finish edit, current
model/image hashes at each request and the exact accepted download.
See `HANDOFF.md` and `docs/REVIEWS.md` for verified evidence and current status.

Official API references checked for this implementation:
[Meshy multi-image generation](https://docs.meshy.ai/en/api/multi-image-to-3d),
[Meshy retexture](https://docs.meshy.ai/en/api/retexture),
[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), and
[custom tools](https://developers.openai.com/api/docs/guides/function-calling#custom-tools).
