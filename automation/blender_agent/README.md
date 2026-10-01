# Live Astra Blender agent

The [local eyewear studio](STUDIO.md) provides image intake, guided specifications,
optional Gemini web research for sourced specifics, budgeted runs, and shared appearance
controls in current 3D/AR previews. Start it with `agentic-studio.cmd` from
`automation/`. It uses the persistent agent described below.

This experimental route connects one OpenAI Agents SDK `Agent` / `Runner` session
to a persistent Blender process through the existing community
[`mcp-for-blender`](https://github.com/ahujasid/mcp-for-blender) server. Astra uses
the Responses API as `gpt-6-astra`. The SDK manages the conversation and tool loop;
this package does not rebuild construction programs or impose modeling phases.

The seed-editing and photos-only Tom Ford trials completed. [Results and artifacts](RESULTS.md) show
the working agent workflow and its remaining visual limitations; the model is not
yet a photographic match.

Blender MCP executes the agent's Blender commands against the open scene. Meshes,
materials, modifiers and viewport changes persist between interactions. Python
variables themselves do not persist. MCP viewport image blocks are forwarded to
Astra as images. `read_image` accepts an optional normalized crop of a saved render
or original reference, preserving native pixels and recording source identity.
`record_review` saves concise observations, interpretations, alternatives and next
checks against image/checkpoint hashes. These notes are agent judgments, not a
quality certification or a prescribed construction sequence.

For matched before/after experiments, `record_review` also accepts a short progress
assessment. Two consecutive comparisons on the same focus reporting neither an
improvement nor new information return a reconsideration advisory. The artist must
choose a different discriminating test, or preserve/revert the best reviewed state
and report the limitation. This never automatically accepts or stops a model; an
unchanged mesh can still need material work, rollback, export or validation.

The revised instructions require multi-view physical interpretation of ambiguous
photo lines, matched before/after checks, surface-section/normal inspection for
suspected ripples, and major component shape before decorative details. In the
scratch trial, subdivision plus a selective crown made the frame surface worse
despite watertight topology. A later forensic audit corrected the initial review.

## Local setup

Keep dependencies and private run files under ignored `data/blender_agent/`:

```powershell
python -m venv data/blender_agent/venv
data/blender_agent/venv/Scripts/python.exe -m pip install -r blender_agent/requirements.txt
```

Launch a dedicated Blender GUI process with an existing `.blend` copied into a new
session directory. The launcher registers the packaged upstream addon only in that
process; it does not install or change global Blender preferences. Screenshots need
a GUI/GPU context even when its window is hidden. Add `--show` for a visible window.

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.session --seed C:/path/source.blend --output data/blender_agent/session-001
data/blender_agent/venv/Scripts/python.exe -m blender_agent doctor
```

For a build from photos, use `--empty` instead of `--seed`. This factory-resets
objects and geometry datablocks, sets numeric millimetres with +Y up/+Z front and
bridge origin `(0,0,0)`, and saves `working.blend` before starting the addon:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.session --empty --output data/blender_agent/scratch-001/blender --port 9877
data/blender_agent/venv/Scripts/python.exe -m blender_agent doctor --mcp-port 9877
```

The initialization receipt records zero object/geometry counts. An empty scene
does not contain a default cube, camera, light, material or copied donor data.

The doctor inspects MCP connectivity without model inference or credentials. Only
one client should operate on a given Blender session at a time. Use matching
`--port` / `--mcp-port` options for another instance.

## Autonomous run

Put the modeling brief in a text file and attach the original references explicitly:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent run --output data/blender_agent/run-001 --prompt-file C:/path/brief.txt --photo C:/path/front.jpg --photo C:/path/angle.jpg --env .env --reasoning-effort max --max-turns 40 --max-output-tokens 8192 --max-usd 20 --run-paid
```

`--run-paid` enables metered OpenAI API inference. Before each inference request,
a small HTTP hook counts the exact input and durably reserves its maximum token
cost under the standard Astra tariff. `--max-usd` bounds completed response costs
plus outstanding reservations across the whole trial, including resumes. The cap
is frozen in `trial-budget.json`; a process lock prevents concurrent runs against
the same output directory. Valid provider usage releases
the unused part of a completed response's reservation. Failed or unknown outcomes
retain their full reservation. Actual usage and reservations are reported
separately. Automatic inference retries are disabled. A resume retains every prior
ledger and receives only the remaining allowance. Missing or malformed accounting
fails closed. Older sessions created before trial-wide accounting cannot silently
resume under a newly invented cap. `budget_status` exposes the balance to Astra.
Its current-invocation `planning` section shows the next worst-case reservation at
the last counted context size, plus a suggested finish balance (that reservation
plus three recent-high settled response costs). This is an advisory estimate,
not a reserved closeout allowance or a guarantee: context and response costs can
grow. The exact request guard remains authoritative; effort and output limits are
never silently reduced. The budget is a ceiling, not a spending target.
Preview and review tool outputs include the current balance and planning threshold,
so checking progress need not require a separate budget-only model interaction.
No API request is made by the launcher, doctor, or help commands. External
asset-generation tools are not exposed. OpenAI tracing and MCP telemetry are disabled.

New runs default to `--reasoning-effort max`; `high` remains available for an
explicit cost/quality comparison. The first two Tom Ford trials used `high`.
The default output-token ceiling stays at 8192. The first `max` trial explicitly
uses 25000, allowing headroom because reasoning and visible output share that
ceiling. This increases the worst-case reservation and can stop a run farther
below its total cap. More reasoning or output space does not guarantee quality.

The host now archives each actual API request/response locally, including provider
status, incomplete details, request/response IDs and usage. Image data is stored
as exact local bytes with hashes; authorization headers are not logged. These
files explain what the model received even when the SDK rejects a response before
its normal completion hook. Unknown/failed outcomes still retain conservative
budget holds.

Every arbitrary Blender script and direct export receives automatic compressed
before/after `.blend` copies, exact issued arguments/code, timings and bounded
scene fingerprints. A failed pre-save blocks the edit; a failed post-save is
recorded without replaying an uncertain mutation. Checkpoints are authoritative;
fingerprints identify changes, not quality. This evidence stays on disk and adds
no modeling instructions or inventory text to Astra's conversation.

The agent decides its edits and inspection sequence. It can use individual vertices,
`bmesh`, modifiers, material nodes, view rotations and close-ups through MCP. Local
review artifacts include events, model usage, images and final output. The SDK's
SQLite session preserves the conversation; `--resume` continues it. Keep the matching
Blender scene open, or restore its saved checkpoint before resuming. Checkpoints are
saved by Blender, not reconstructed from a construction script. On completion,
budget exhaustion or a turn limit, the host also requests `stop-checkpoint.blend`
while MCP remains connected, without another paid response. The result records
whether this save succeeded; a disconnected Blender process can prevent it.

Resume inputs are compared against the actual user items persisted in that SQLite
session. An unchanged latest brief and byte-identical images are reused from history;
changed instructions, changed images and inputs without proof are sent. Reference
paths are refreshed for cropping. `input-receipt.json` records hashes and decisions,
without image bytes. A failed request can already have persisted its inputs: neither
a database's existence nor a previous run receipt alone is treated as proof. Existing
conversation history and all prior budget holds remain intact.

## Native export and AR inspection

`preview_ar` exports the current live scene through Blender's native glTF exporter
and runs that exact file through the repository's `TryOnRenderer` QA harness.
Its results include the GLB hash, compatibility report and actual image blocks
seen by Astra. The agent selects view angles and a checker or solid background.
Full evidence is saved on disk; the tool returns a compact identity/validation
summary and images. `inspect_export` lets Astra request the saved GLB's actual
material properties, extensions and bounds when needed.
The compact preview includes continuity and fitting diagnostics independently from
capture validation: render success cannot conceal a structural failure. Available
diagnostic images remain visible when compatibility fails. `read_evidence_json`
reads bounded sections of registered preview, review and inspection receipts with
their exact hashes; it cannot browse arbitrary output files or raw API transport
logs. Registrations persist across resumes; unregistered legacy JSON is not exposed.
This uses synthetic face poses and current runtime lighting, not a real camera
recording. Arbitrary orbit, close-up, isolation and Blender rendering remain
available through MCP.

`preview_portrait(label, glb_path)` reads an existing export inside the agent output
directory and places those exact bytes on the checked-in synthetic `face-a` portrait
through the actual face detector, fitter, hair mask, shadows and `TryOnRenderer`.
It preserves the portrait's native aspect and inferred pose. The agent receives a
clean eye crop, the normal AR portrait/eye detail, and a second eye detail under a
fixed QA illumination condition. Numeric lighting settings remain in host evidence;
the agent evaluates images. No photograph-based head turn is invented.

The report binds model/image hashes, fitted pose, camera, crop and runtime-source
snapshot. Repeat it for before/after exports; use synthetic poses or Blender orbit
for other angles, and inspect full temples outside face/hair occlusion. The portrait
is supplementary runtime evidence, not a new photograph of the target product.
This tool neither edits Blender nor calls a paid model.

To inspect a saved export without starting Astra or Blender:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.portrait --glb C:/path/model.glb --output data/blender_agent/portrait-review-001
```

Use a fresh output directory. The local QA harness needs prepared AR assets,
Node dependencies and Playwright Chromium. It does not publish the production site.

Temporary evaluated mesh copies receive baked world transforms, scale and bridge
origin. The live scene is checked for invariance and never rebuilt. Full lens solids,
UVs and exportable native material features are retained. There is no canonical
optical descriptor or old material-record override. Unsupported Blender shader
nodes still require exportable authoring; inspect the exported result.

Blender 5.2 has a relevant exporter trap: a custom `glTF Material Output` group
with only a Thickness input can disappear during shader inlining. Use the standard
group structure, including Occlusion and Thickness inputs and Group Input/Output
nodes, then verify `KHR_materials_volume` in the exported file. Our wrapper does not
invent volume settings or patch the finished GLB to conceal missing authoring.

The first seed uses numeric millimetres, +Y up and +Z front. Set `--meters-per-unit`
for another unit, and `--bridge-origin X Y Z` when the scene does not declare
`mdl_bridge_underside`. Other axes need deliberate orientation before export.
Delivery objects use `partRole` (`frame`, `temple`, `lens`) or the seed's `part`
property. Viewport isolation does not remove objects from export; `hide_render`
does. Realize collection/Geometry Nodes instances before export.

Run the AR setup in `../ar/` first (`npm install`; prepared assets must be present).
The harness captures local source, starts its own browser/server, and verifies
that the source and rendered GLB bytes remain stable. It does not publish `ar/site`.

## Starting geometry

For the independent from-zero trial, use `tom_ford_scratch_brief.txt`, a new empty
Blender session and a new agent output directory. Attach all six original photos;
do not pass the old mesh or construction program. The brief requires complete
product geometry, portable material roles, checkpoints and final AR image review.
Use `--max-usd 17 --max-turns 120 --max-output-tokens 8192 --mcp-port 9877` for the
authorized $17 trial. This is separate from the earlier seed-editing experiment.

The earlier research recommendation was to start the first Tom Ford editing trial from its
latest editable `.blend`, with all six product photos. The supplied
`tom_ford_brief.txt` asks Astra to inspect an unchanged native-export baseline,
choose its own edits, and save a reviewed result. This tests editing ability;
it is not a photos-to-model benchmark.

Both trials have now run. The photos-only trial produced a complete AR-compatible
candidate for $14.418763 of its $17 cap; crystal/lens appearance still falls short
of the photographs. See [RESULTS.md](RESULTS.md) for exact artifacts, validation,
runtime recovery and quality limitations.

The broader decision remains component-specific: generated donors can save work
on complex opaque structures; smooth native lenses and crystal parts can avoid
the work of separating and repairing fused painted geometry. The initial scene
may contain either. See [the research](RESEARCH.md) for the proposed comparison.

## Runtime and current scope

The live connection, SDK tool discovery and image path can be tested independently
of model quality. Neither a successful connection nor a completed agent conversation
establishes accurate glasses.

The native-role AR compatibility fix recognizes tagged frame/temple parts without
requiring canonical lens descriptors. Native closed lenses are supported. The
original `modeler` exporter is not used by this agent runner: it can discard direct
Blender node edits. Do not assume a native GLB passed AR until its exact bytes render.

Currently the Python agent runner, MCP server, Blender and browser previews run on
this machine; Astra inference runs on the API. Server deployment would place these
same processes in an isolated persistent worker with Blender, a GPU/display context,
browser dependencies and durable checkpoints. It does not require the desktop app
to operate Blender. This package does not provision that worker or a job queue.

This route does not publish assets or change the live application.

To compare selected exports interactively in the already-running local AR app:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.review --glb C:/path/final.glb --baseline C:/path/baseline.glb
```

Open `http://127.0.0.1:8796/`. The review server exposes only the selected file snapshots
on loopback, with hash-pinned AR links. It does not start the camera automatically.

Focused automation tests use the environment above:

```powershell
$agentTests = Get-ChildItem tests/test_blender_agent_*.py | ForEach-Object FullName
data/blender_agent/venv/Scripts/python.exe -m pytest @agentTests -q -p no:cacheprovider
```

Run AR regression checks separately inside `../ar/` with `npm test` and `npm run check`.
The portrait QA contract has focused checks with
`node --test qa/portrait-preview-contract.test.mjs` from that directory.
