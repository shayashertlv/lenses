# Agentic eyewear studio

A local interface for the persistent Astra–Blender agent. Upload references, write
a brief, fill in known product details, and start a budgeted build from an empty
Blender scene. The agent chooses its modeling and inspection actions through MCP.
Opening the app, importing an existing result and adjusting materials make no paid
model requests.

## Start

From `automation/`, double-click `agentic-studio.cmd`, or run:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.studio_server --start-ar --open
```

The studio is at `http://127.0.0.1:8767/`. Its previews load the current `ar/` source
through Vite on port 8240. Keep the local server running while using the studio.
This does not publish `ar/site/` or change the live Lenses/Railway application.

Prerequisites: the [Blender agent environment](README.md#local-setup), Blender 5.2
and the [AR dependencies and local assets](../../ar/README.md). A dedicated hidden
Blender process is launched for each run; it needs a desktop/GPU context. This
implementation is a local workstation application, not a headless cloud service.
`--blender`, `--port`, `--data` and `--env` override the defaults; `--help` lists them.

Put credentials in `automation/.env` (never commit it):

```dotenv
OPENAI_API_KEY=your-openai-api-key
GEMINI_API_KEY=your-gemini-api-key
```

Keys stay on the Python server. The UI receives availability flags only. Explicit
nonempty values in the selected `.env` take precedence over process environment
values. `GOOGLE_API_KEY` is also accepted for Gemini. Description generation uses
`gemini-3.1-pro-preview` by default; use `--gemini-model` to select another supported
Gemini model. No image or inference request is sent until the corresponding button
is clicked.

## Workflow and cost

1. Add product images and label their view and provenance. Generated auxiliary
   views are weaker evidence than original product photographs.
2. Enter a description and known facts about frame material, finish, colour,
   lenses, dimensions and small details. Unknown values can remain unknown.
3. Optionally ask Gemini to draft the description. Review its uncertainty notes
   and inferred details; known user-supplied specifications remain authoritative.
4. Choose the Astra budget and start. The default is **$20**, `max` reasoning effort
   and an 8192-token response ceiling. This is a ceiling, not a spending target.
5. Inspect progress, accounted usage and held reservations. Stop requests finish
   the current operation before saving a recoverable checkpoint. Resume reuses the
   original conversation, checkpoint and remaining trial allowance.
6. Inspect the result in 3D or AR, adjust appearance, then **Save revision**.

The optional Gemini description request is billed separately from the Astra
modeling cap. Failed/unknown Astra requests retain their reserved maximum cost;
refreshing or resuming does not erase this accounting. There are no silent paid
retries or automatic budget increases. Provider charges should be reconciled with
the provider dashboard; local receipts are the guard's evidence.

## Appearance controls

Both previews share the material panel. Controls are available only for features
actually authored in a native GLB:

| Control | Changes |
| --- | --- |
| Colour / tint | Base material colour, multiplied with any existing texture |
| Mirror / metallic strength | Metallic reflection response |
| Surface roughness | Sharp versus blurred highlights |
| See-through strength | Transmission for existing transmissive materials |
| Glass refraction | Index of refraction |
| Clearcoat gloss / roughness | Existing clear surface coating |
| Colour-shift strength, refraction, thickness | Existing iridescent coating |
| Absorption tint / distance | Existing volume tint; 0 distance means unlimited |
| Lens reflection | Shared viewer lighting multiplier, saved with the project |

The material picker identifies which authored parts share a material. Editing it
affects all those parts. These controls do not reshape lenses, change frame
thickness or separate fused geometry; those tasks require further modeling.
They do not add missing material extensions or change transparent render classes.
Canonical optical assets are explicitly excluded from native material editing.

Slider changes are local previews until saved. Reset restores the loaded revision,
including exact original linear colour values. Saving patches only allowlisted GLB
material factors and preserves geometry, textures and the original file. A receipt
records both hashes and changed fields. Old revisions stay on disk.

Lens reflection is a viewer setting, so it is reapplied in this studio's 3D/AR URLs;
an exported GLB alone does not carry it to unrelated viewers. Material changes are
embedded in the revised GLB. The downloadable Blender file remains the original
authoring scene; it is labelled accordingly after a material revision.

The AR camera opens only on request. Switching away stops its camera pipeline.
The studio bridge is opt-in and validates parent origin, random channel and model
SHA before accepting changes. It does not alter normal AR pages.

## Evidence and recovery

Project data lives under ignored `data/blender_agent/studio/jobs/`. References,
briefs, provider receipts, response history, checkpoints, review notes, exports and
material revisions are retained. The app never marks a model photographically
accurate merely because the run finished or a GLB loaded successfully.

Import a completed existing trial for preview and appearance editing:

```powershell
data/blender_agent/venv/Scripts/python.exe -m blender_agent.studio_server --start-ar --import-trial data/blender_agent/oakley-scratch-001
```

Import copies its delivery and keeps source files untouched. It does not create a
new paid allowance or make that imported trial resumable through the studio.
Existing CLI trials retain their original resume workflow.

The server binds only to loopback, validates Host and Origin, requires a random
CSRF token for mutations, and serves allowlisted project artifacts. It is not an
authenticated multi-user internet service; deployment needs a separate worker,
authentication, storage and process-ownership design.

## Verification

```powershell
$studioTests = Get-ChildItem tests/test_blender_agent_*.py, tests/test_blender_studio_*.py | ForEach-Object FullName
data/blender_agent/venv/Scripts/python.exe -m pytest @studioTests -q
node blender_agent/studio_web/app.test.mjs
cd ../ar
npm run check
npm test
npm run test:qa
node qa/studio-preview-smoke.mjs
```

Provider transports are mocked in unit tests. Running tests does not authorize
paid modeling or Gemini description calls. Rendering tests and interactive review
cover the actual exported model and current viewer independently of inference.
