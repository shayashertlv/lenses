# Lens lab

This is a **lab**, not the product pipeline. It makes private copies of accepted
Oakley run 4's verified final checkpoint and frozen camera basis, resets its
transmitting shield material to clear neutral glass, and exposes the real Astra
tools. The laser marks, other materials, geometry, normals, UVs, object
transforms, lighting and cameras stay fixed. Nothing here publishes a delivery
or writes a verdict.

The task is [L1](../data/lens_lab/briefs/l1_lens_lab.md). Research, measurements,
process logs, trial receipts and reports live under ignored `../data/lens_lab/`.
No provider API call or paid-run approval is made. Codex runs through the user's
existing ChatGPT login; its usage is separate from the pipeline's API ledger.

Run commands from the repository root with `glasses3d/.venv/Scripts/python.exe`.
All Blender, capture and pool entry points refuse until task 17's status file
contains an `exit` line. The coordinator must also serialize the ceiling pool,
native checks and Codex trials. Worker capacity reserves 2 GiB of free commit,
then uses 1.3 GiB per worker, capped at 14; BLAS is one thread and processes opt
out of EcoQoS. A ceiling run refuses if no worker fits. Trial and scoring output
paths must resolve to private subdirectories of `glasses3d/data/lens_lab/`.

## Material tools

```powershell
$python = '.\glasses3d\.venv\Scripts\python.exe'
$trial = '.\glasses3d\data\lens_lab\trials\example'
& $python -m glasses3d.lens_lab init $trial
& $python -m glasses3d.lens_lab tools $trial
& $python -m glasses3d.lens_lab tool $trial scene_summary '{}'
& $python -m glasses3d.lens_lab tool $trial execute_blender_code --args-file "$trial\arguments.json"
Set-Content -LiteralPath "$trial\look.json" -Encoding UTF8 -Value '{"refs":["reference-01","reference-02"]}'
& $python -m glasses3d.lens_lab tool $trial compare_look_to_photos --args-file "$trial\look.json"
Set-Content -LiteralPath "$trial\finish.json.args" -Encoding UTF8 -Value '{"summary":"LAB material experiment"}'
& $python -m glasses3d.lens_lab tool $trial finish --args-file "$trial\finish.json.args"
```

`arguments.json` contains the real tool arguments, for example `{"code":"...",
"label":"LAB experiment"}`. The generated `tool-schemas.json` contains the actual
registry descriptions and schemas. Available tools include `g3d.lens_coating`
inside `execute_blender_code`, photo-look comparisons, native `render_views`,
image/report reading, checkpoint save/restore, GLB export and AR capture.

The CLI prints the pipeline text unchanged and saves exact `ToolOutputImage`
bytes. Paths following that text identify images to open with an image-view
tool. These raw images are lab evidence; adding watermarks would change the
pipeline's image transport. Diagnostic scoring sheets have explicit lab labels.
The per-trial image-deduplication state survives separate CLI invocations.

`calls.jsonl` and per-call receipt directories bind arguments, exact text, image
SHA-256, timing and scope checks. The mesh fingerprint includes mesh attributes,
so edits to UVs or normals also refuse. Refused edits restore the prior
checkpoint; restoring a rejected checkpoint refuses too. Only graph assets
exclusively used by the permitted lens material may vary. `finish.json` records
native and exported materials, the validated GLB and original-input hash audit.
Finish is terminal for material mutations and restores.

## Codex-as-Astra trials

```powershell
& $python -m glasses3d.lens_lab.trial run --variant baseline --count 10
```

`--variant` is a grouping label. Each trial takes the real main instruction
builder and brief builder from the current immutable Git HEAD; it records HEAD
and all relevant working implementation hashes. To compare instruction
variants, the coordinator first prepares the intended HEAD, then runs a
separately named batch. The runner does not inject material parameters.
Keep implementation files unchanged while each trial runs; the before/after
source audit marks a trial incomplete if its implementation changes.

The prompt is the verbatim main instructions, owner brief and one lab paragraph.
All six owner photos are attached with `-i`. The subprocess uses
`codex exec -s danger-full-access -C <repo>` and inherited model/effort settings.
Repository-document loading is disabled for this isolated experiment so extra
project instructions do not replace the supplied Astra text.

Successful completion requires an actual image-view event for a SHA-bound
render returned by a lab tool **before** the lab finish, as well as a matching
finish receipt. Codex 0.163.0 omits image-view items from `exec --json`, so the
runner uses the native session created by that exact stdout thread UUID. It
verifies session identity, directory and launch time, then pairs the image-view
call, completed event and actual image payload delivery before this trial's
finish. Direct calls are matched by call ID. Wrapped code-mode `exec` calls
require an unambiguous outer call interval and a decoded delivered image whose
SHA-256 equals the receipted render's bytes. Resized or transcoded payloads do
not count as byte-exact delivery proof. Codex
keeps that one session in its normal state directory; the trial retains its
hash and selected proof records, without copying image payloads or transcript
prose. An agent's claim that it looked is insufficient. See each trial's
`codex/receipt.json`, `native-image-proof.json`, `events.jsonl`, exact prompt and source hashes.
An infrastructure-incomplete trial stops its batch and remains recorded.
Scoring runs after Codex exits, producing a comparison image and JSON without
feeding diagnostic measurements or ceiling settings back to the trial.

This is not an exact paid-Astra replica: Codex's system instructions, model,
reasoning configuration and tool transport differ; viewing is explicit, the
geometry/cameras are fixed, and there is no simulated $20 budget or automatic
production renewal. `danger-full-access` is required by L1. The harness guards
its own tools and audits events; it is not an OS sandbox against arbitrary
shell writes.

## Diagnostic scoring and ceiling

```powershell
& $python -m glasses3d.lens_lab.score score-trial $trial
& $python -m glasses3d.lens_lab.ceiling .\glasses3d\data\lens_lab\ceiling\run-name
```

The scoring basis freezes the task-13 same-camera front capture and run-4
reference-02 angled camera. Photo lens outlines are annotated once, eroded by
three pixels, and split into image-left/centre/image-right horizontal thirds.
The exact same pixels measure the photo, accepted run 4 and every candidate.
Mask overlays and hashes make that definition reviewable.

Scores contain whole-lens L*, regional median sRGB and L*, and diagnostic Lab
distances. A labelled sheet shows photo/run 4/trial at equal crop and scale in
both views. These are measurements, not product acceptance gates.

The ceiling stores its complete design before rendering. It evaluates the real
exporter and fixed AR room, retains every candidate and failed case, verifies
exported material values, and verifies embedded thickness-map data and UV
bindings. The best measured sample is a finite-search result, not a mathematical
ceiling or proof that other materials cannot match. Its values are never copied
into Astra's instructions or tools.

## Tests

```powershell
& $python -m pytest -c glasses3d/lens_lab/pytest.ini glasses3d/lens_lab/tests
```

The main suite explicitly collects `glasses3d/tests`; these tests live outside
that tree and have a separate config. Native tests are opt-in and serial:

```powershell
$env:LENS_LAB_NATIVE_TRIAL = 'C:\Users\Shay\PycharmProjects\lenses\glasses3d\data\lens_lab\native-new'
& $python -m pytest -c glasses3d/lens_lab/pytest.ini glasses3d/lens_lab/tests
```

Use a fresh native-trial directory because the final test seals its lab finish.
Do not run these commands alongside another heavy job.
