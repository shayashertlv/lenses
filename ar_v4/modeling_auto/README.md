# Modeling Auto

Independent local pipeline at **http://127.0.0.1:8060**. All application code,
settings, dependencies, jobs, receipts, renders and test fixtures live here.
There is no runtime or data dependency on the previous pipeline.

**Accepted checkpoint: `automation_pipeline_first_perfecto` — September 14, 2026.**
The owner completed a fresh six-stage run from blank generation through both
finish passes, accepted r005, and reported that the pipeline works well. The
review pass recorded at the top of `docs/REVIEWS.md` builds on that checkpoint:
it renamed the pipelines, preserved product notes across edits, widened the
script sandbox, made response parsing tolerant and added lens-surface metrics.
`HANDOFF.md` is the current state; `docs/HISTORY.md` is the chronological record.

## Run

```powershell
Set-Location 'C:\Users\Shay\PycharmProjects\lenses\ar_v4\modeling_auto'
.\start.ps1
```

Open **API setup** and enter the two provider keys in this new app. Saving them
does not make a provider request. Keys are stored in ignored `.env`; they are
never returned by the HTTP API or passed to Blender. `.env.example` must only
ever hold placeholders. `MODELING_AUTO_BLENDER` can select another installed
Blender executable; the verified default is Blender 5.2. The app is bound to
loopback only.

For a fresh dependency installation run `.\setup.ps1` first. The Python and npm
lockfiles record the tested versions. To stop the app while idle run
`.\stop.ps1`. If a job is active, cancel it in the app and wait for the stopped
state first. This does not operate any other pipeline or port.

Copy the complete OpenAI key, including its `sk-` prefix. API setup rejects a
missing prefix before saving. Keys saved in this app's `.env` take precedence
over inherited environment keys on restart; environment keys are fallback only
when the corresponding local setting is absent. Saving keys makes no request.

## Pipelines

Supply a name, exactly five labeled photos (front, back, left, right, angled),
at least three known dimensions in millimeters, and optional notes. Originals
are retained; normalized copies are sent to providers.

**Standard pipeline** (the default, also at `/?pipeline=standard`):

1. Meshy generates an untextured model with Meshy 7 Ultra and no remeshing.
2. Astra receives the five original references and one scoped smoothing/lens
   task. Its single Python tool response edits a disposable Blender revision.
3. Astra receives five current renders and an oblique lens/rim close-up, plus
   the lens-surface metrics of the current revision. Its single script repairs
   lens seating and surface ripple while non-lens geometry stays locked.
4. Meshy textures the edited GLB using Meshy 7, 8K and PBR. The entire returned
   textured asset becomes a new saved revision through native Blender import.
5. Astra receives the five original references, five fresh renders of the
   textured model and its close-up, and one complete material/finish task.
   Blender executes it with geometry, UVs, normals and transforms locked.
6. A second material/finish pass with the same eleven-image input.
7. The final preview pauses. **Download & finish run** accepts and downloads the
   exact packed `.blend`. **Send another edit** requires specific written
   instructions and runs one more finish pass, then returns to review.

**Start authorizes the entire six-call sequence: two Meshy and four Astra
requests, with no intermediate approval gates.**

**Legacy pipeline** (`/?pipeline=legacy`) is the original five-call plan: the
same first four stages, one finish pass with the five original references only,
then review. Start authorizes two Meshy and three Astra requests. **Another
material edit** runs one finish request; instructions are optional.

Saved jobs keep the pipeline they were created with. Jobs, receipts and URLs
from before the rename use the names `test` (now standard) and `current` (now
legacy); both are still accepted and normalized.

Every Astra request is standalone; no progress history or earlier images are
sent. There is exactly one Python custom-tool response. Meshy accepts four
references, so it receives front, back, left and right; Astra receives all
five. Input dimensions are context for the editor; no automatic resize or
measurement-fitting algorithm changes the baseline.

### Notes and edit instructions

The product notes entered at creation are never overwritten. Each edit's text
is stored separately as `edit_instructions` and every later Astra request
receives both, as "Original product notes" followed by "Specific instructions
for this edit". The standard pipeline requires instructions for another edit;
the legacy pipeline accepts an edit without them, which clears any earlier
instructions. Instructions are accepted only with an edit action.

## Revisions, stopping and recovery

Every provider response, partial response, reserved request and completed
revision stays under `data/jobs/<id>`. A failed edit never replaces the last good
revision. Call counters count **reservations**, including failed or uncertain
requests; provider billing is authoritative for charges. Meshy polling keeps
the first reply, the latest pending reply and the terminal reply of each task
and drops the identical pending replies in between.

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

## Lens surface metrics

Every revision's inspection carries a `lenses` entry, one record per tagged
lens, computed by the native worker from the saved geometry:

- `dihedral_deg` quantiles of the angle between adjacent faces, and the
  fraction of edges sharper than the rim-crease threshold;
- per optical side: `surface_ripple_p90_deg` (the 90th-percentile angle away
  from the rim creases), `sign_mix` (how often adjacent faces bend the other
  way, which a smooth side never does), `curvature_cv` (the spread of angle per
  edge length) and a `sphere_fit` residual as a fraction of the lens extent;
- `summary` with the worst side's values.

Lower is smoother. The seating request receives these numbers with the scene
inspection, the viewer shows the worst ripple and sign mix per lens, and
`scripts/lens_surface_report.py --job <id>` prints them for every lens-bearing
revision of a saved job. They are advisory: they never decide whether an edit
is adopted, they measure smoothness rather than fit, and a wrap-around shield
lens is legitimately non-spherical. Use the rendered views for the final call.

## Capabilities and limits

The prompt, AST validator and Blender builtins share one import contract:
`bpy`, `bmesh`, `math`, `mathutils`, `mathutils.bvhtree`, `mathutils.kdtree`, and
`mathutils.geometry`. Approved operators are explicitly listed in the prompt, and
so is the exact set of builtin names available to generated code, generated
from the runtime's own allowlist so the two cannot drift. `type(value)` and the
Blender-relevant exception classes such as `ReferenceError` are available;
three-argument `type()`, `id`, `object`, `super`, `memoryview`, `open`, `eval`
and `exec` are not. Files, network, arbitrary imports, arbitrary Blender
operators, executable nodes and private Python access are unavailable to
editing scripts. These checks are defense in depth, not an operating-system
sandbox.

A response must contain exactly one `run_blender_python` custom tool call. A
narrative message beside it is tolerated and the script is still executed; a
second tool call of any kind is rejected. Backticks inside strings or comments
do not reject a script; a script that fails to parse and contains a Markdown
fence is reported as fenced.

The read-only `hasattr(value, '__len__')` check is permitted for distinguishing
scalar and array shader values. Direct `datablock.as_pointer()` calls with no
arguments are permitted for identifying shared mesh/material data within one
script. Capturing/rebinding the method, retrieving it through attribute helpers
and converting raw addresses remain blocked.

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
model uploads stream bounded base64 chunks. Texture sampling uses compact,
exact float32 buffers to avoid repeated copies of entire 8K images. The
1200-second limit and all geometry checks still apply.

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
.\.venv\Scripts\python.exe scripts/offline_pipeline.py --pipeline legacy
```

The last two commands exercise the actual HTTP application, real provider adapters
on a completely intercepted fake transport, and actual Blender, including a
finish-only repeat and accepted download. They create their own fused synthetic
input and make **zero paid requests**. Run native checks sequentially. The
default checks all six standard stages, one additional instructed finish edit,
current model/image hashes at each request and the exact accepted download.
See `HANDOFF.md` for the current state and `docs/REVIEWS.md` for evidence.

Official API references checked for this implementation:
[Meshy multi-image generation](https://docs.meshy.ai/en/api/multi-image-to-3d),
[Meshy retexture](https://docs.meshy.ai/en/api/retexture),
[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), and
[custom tools](https://developers.openai.com/api/docs/guides/function-calling#custom-tools).
