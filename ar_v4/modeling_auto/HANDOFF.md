# Modeling Auto handoff — current state

This file describes the application as it is now. The chronological record of
every earlier repair is in `docs/HISTORY.md`; verification evidence for each
change, newest first, is in `docs/REVIEWS.md`. Nothing below is an instruction
to retry or resume a saved job.

## Runtime

- Runtime version `modeling-auto-20260914-review-v8`, served at
  **http://127.0.0.1:8060** by `start.ps1`; Blender 5.2 native worker.
- The accepted checkpoint `automation_pipeline_first_perfecto` (v7) is the last
  owner-accepted six-stage run: Oakley job `6eda65cd-…`, r005, Astra 4 / Meshy 2.
  The review pass that produced v8 changed behaviour on top of it; see below.
- Any change under `app/` or `blender/` changes the runtime manifest and blocks
  further dispatch until the service is restarted.

## Pipelines

- **Standard** (default): generate → lenses → connections → texture → finish →
  finish_refine → review. Two Meshy and four Astra requests per Start.
- **Legacy**: the original five-call plan without the second finish pass.
- Saved jobs keep the plan they were created with. Jobs and receipts written
  before the rename carry the names `test` (standard) and `current` (legacy);
  both names are accepted everywhere and normalized to the canonical ones.
- Both plans keep the product notes intact. An edit's text is stored separately
  as `edit_instructions` and sent as "Original product notes / Specific
  instructions for this edit". The standard plan requires instructions; the
  legacy plan accepts an edit without them, which clears earlier instructions.

## Editing scripts

- The prompt lists the exact builtin names available to generated code, taken
  from the runtime's own allowlist, so the two cannot drift. `type(value)` and
  the Blender-relevant exception classes (`ReferenceError`, `NameError`, …) are
  available; three-argument `type()`, `id`, `object`, `super`, `memoryview`,
  `open`, `eval` and `exec` are not.
- A narrative message beside the one `run_blender_python` tool call is tolerated;
  the paid script is still executed. A second tool call of any kind is rejected.
- Backticks inside strings or comments no longer reject a script; a script that
  does not parse and contains a fence is reported as fenced.

## Lens surface metrics

Every revision's inspection carries `lenses`, one entry per tagged lens, with
dihedral-angle quantiles, the rim-crease fraction and, per optical side, the
90th-percentile ripple angle, the sign mix of adjacent-face angles, the spread
of angle-per-edge-length and a sphere-fit residual. The seating prompt receives
them and the UI shows the worst ripple and sign mix per lens. They are advisory
only and never gate adoption. `scripts/lens_surface_report.py --job <id>` prints
them for every lens-bearing revision of a saved job.

## Operations

Use `start.ps1` and `stop.ps1` only from this directory. Stop refuses while a job
is active. Recovery is always explicit: complete saved tasks, responses,
downloads and native results are reused; ambiguous submissions are never
reissued. Reserved calls stay counted after failure or cancellation. Provider
billing is authoritative. Meshy polling keeps the first, the latest pending and
the terminal reply per operation and drops the pending replies between them.

Keep fixtures and receipts in ignored `data`, dependencies in `.venv` and
`node_modules`, caches inside `data/cache`, and all edits under this directory.
Do not introduce a paid automated smoke test or reset/retry/resume behaviour.

## Verification

Run from this directory after `setup.ps1`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=data/t-final -o cache_dir=data/tests/pytest-cache
npm run build
npm test
.\.venv\Scripts\python.exe scripts/offline_pipeline.py
.\.venv\Scripts\python.exe scripts/offline_pipeline.py --pipeline legacy
.\.venv\Scripts\python.exe scripts/lens_surface_report.py --job <job id>
```

The latest results are recorded at the top of `docs/REVIEWS.md`.

## Known limits

Meshy 7 retexture rollout, real Astra script behaviour and real product quality
are not established by offline checks. Prompts and structural checks do not
prove shape preservation, curvature correctness or seating quality; the new lens
metrics measure smoothness, not fit or aesthetics. The `.env` file holds live
keys and is ignored; `.env.example` must only ever contain placeholders.
