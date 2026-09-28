# Modeler — runnable instructions

Photos of one pair of glasses -> an AI runtime author writes Blender construction programs -> the host builds,
exports the AR contract GLB, renders and measures -> the author revises -> an independent evaluator judges ->
`manifest.json` names the delivered asset and its status. Design, frames and policies: [DESIGN.md](DESIGN.md).

## Prerequisites (present on this host)

* Python 3.12 with the `automation/` requirements (numpy, scipy, opencv, PIL, shapely, scikit-image, open3d, torch CPU
  for the offline lens detector at `data/models/glasses-detector-v1`).
* Blender 5.2 at `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (or `MODELER_BLENDER`).
* Node 22+ with `ar/node_modules` (Playwright Chromium) for the actual AR renderer harness (`bsa.archeck`).

## Request

```json
{
  "product_id": "vb",
  "photos": [{"path": "C:/.../front.jpg", "view": "front"}, {"path": "C:/.../back.jpg", "view": "back"},
             {"path": "C:/.../left.jpg", "view": "left"}, {"path": "C:/.../right.jpg", "view": "right"},
             {"path": "C:/.../angled.jpg", "view": "angled", "held_out": true}],
  "dimensions": {"front_width_mm": null},
  "notes": "catalog description (material, lens type, accents); no measurements",
  "limits": {"max_turns": 14, "blender_time_limit_s": 300, "wall_time_limit_min": 240, "stagnation_turns": 3},
  "author": {"driver": "package"}
}
```

Views: `front|back|left|right|angled|top|rear_angled|other|unknown` (cameras are fitted for the first five only,
`modeler/request.py` VIEWS); `held_out` views are never shown to the author. Without
`front_width_mm` the scale is the nominal 140 mm default and every millimetre is nominal (recorded in the manifest).

## Run

```powershell
cd C:\Users\Shay\PycharmProjects\lenses\automation
python -m modeler.job --request data/modeler/requests/vb.json --output data/modeler/jobs/vb-run1 --author package --evaluator package --checklist data/modeler/protocols/vb_identity_2026-09-26.json
```

* `--author package`: the job writes `turns/turn-NNNN/request.json` + `images/` and waits for `response.json`. An
  external agent answers each turn with the prompt in [AUTHOR_AGENT_PROMPT.md](AUTHOR_AGENT_PROMPT.md); store its
  transcript beside the turn with `python -m modeler.audit_transcript --transcript <jsonl> --package <turn dir> --copy`.
* `--author astra --astra-cap N --astra-cap-usd D --astra-ledger <file> --astra-env <dotenv> [--astra-api-key-env OPENAI_API_KEY]
  [--evaluator astra] [--astra-reserve-evaluator-usd 2]`: fully automatic paid driver (gpt-6-astra, Responses API).
  Refuses without an explicit call cap (1..10 per ledger), a dollar cap and a credential. Every call is estimated
  worst-case before it is sent and charged from the response usage afterwards into `<ledger>.usd.json`; receipts,
  payloads and raw responses stay under `turns/turn-NNNN/api/`. First exercised 2026-09-26 on `vb-astra1` under a
  $16 owner cap (about $0.25-0.55 per turn observed).
* `--finalize --output <job> [--evaluator ...]`: open an existing job, evaluate the incumbent and write the manifest
  without another author turn (slow or interrupted runs).
* `--author scripted --script decisions.json`: replay (tests, dry runs).
* `--evaluator package|scripted|none`, `--checklist <json>`: the independent evaluator's identity checklist.
* `--no-ar`: skip the AR harness (development only; no candidate can become valid).
* `--seed-program <dir>`: start from an existing candidate's `program/` folder (built and observed as this job's first
  candidate, recorded in the manifest) so the author only rewrites the modules that need work.

Outputs under the job folder: `inputs/` (immutable copies + sha256), `evidence/` (masks, measurements, author photos),
`evaluation_protocol.json` (frozen before turn 0), `turns/`, `candidates/cNNNN/` (program modules, `build/`
with `candidate.blend` and parts arrays, `model.glb`, `export.json`, `observe/` sheets and metrics, `heldout/`),
`evaluation/`, `deliverable/<product>.glb`, `manifest.json`, `journal.jsonl`.

## Live try-on and the owner's verdict

```powershell
npm --prefix ..\ar run dev          # the AR app on 127.0.0.1:8240 (or the "AR app (dev)" launch entry)
python -m modeler.tryon 8793        # index of every finished job's delivered GLB + its baselines -> http://127.0.0.1:8793/
```

Each link opens the AR app through its external-model handover (`?model=&width=&clip=&sha256=`) with the manifest's
mounting values; allow the camera. The desktop app's Browser pane blocks the camera, so this needs a real browser.
Record what the owner saw against the exact asset:

```powershell
python -m modeler.owner_verdict --job data/modeler/jobs/vb-astra1 --verdict accept --medium "live AR try-on, Chrome, own camera" --note "perfect" --sha256 <asset digest>
```

An owner `accept` upgrades the job to `accepted` (`accepted_by: owner`, previous status and reasons kept), an owner
`reject` is recorded without changing the status; either appends one row (owner verdict, evaluator verdict,
provisional thresholds, metrics) to `data/modeler/calibration/owner_verdicts.jsonl` and rewrites `REPORT.md`.

Calibrate the automatic bar against those verdicts (evaluator protocol v2, see DESIGN.md):

```powershell
python -m modeler.evaluate_asset prepare-verdicts     # wearer-proxy AR renders + v2 packages for every owner-judged asset
# a fresh agent answers each <asset>/evaluation_v2/request.json with response.json (read nothing outside the folder)
python -m modeler.evaluate_asset collect-verdicts
python -m modeler.calibration                          # owner vs automatic verdict per asset; calibrated = both classes, all agree
```

`python -m modeler.evaluate_asset prepare|collect --job <dir> --candidate cNNNN | --baseline NAME` does one asset.

API evaluator (paid; validate it against the agent answers and the owner before relying on it):

```powershell
python -m modeler.evaluate_asset evaluate-verdicts --evaluator astra --astra-cap 10 --astra-cap-usd 8 --astra-ledger data/modeler/astra_ledgers/<name>.json --astra-env .env
python -m modeler.calibration --evaluation-dir evaluation_v2_astra
```

State on 2026-09-26 (late): `data/modeler/calibration/calibration.json` says `calibrated: true` (5 owner accepts, 4 rejects,
0 disagreements under protocol v2.2). A job created from now on freezes that flag and may award `accepted` on its own;
keep recording every owner verdict, and when one disagrees with the automatic verdict the rule is refit or the flag drops.

## The intake reading and the measurement review (Astra at the start of the loop)

`--intake astra` adds two vision calls before the author's first turn (`modeler/intake_astra.py`, design in
`PLAN_2026-09-27_astra_intake.md`): a **product reading** of the non-held-out photos (view of every photo, folded
temples above the front, layout, rim class, frame material and colour, rim thickness estimate, lens finish and colour,
branding, a legible size marking, symmetry, the identity features the evaluator judges against, a product description
and cautions for the author) and a **measurement review** of the code's overlay (rim class and thickness trustworthy or
not, temple tips in the silhouette, which arcs of each lens outline are clipped by a reflection). The code keeps every
pixel measurement and applies the answers with provenance: a relabelled view, an absolute scale from the size marking,
the front's height and fit mask cut to the lens band, the rim class and thickness of a crystal front, a constructed
silhouette when the measured one is a fragment, a report-only lens gate (shared with the calibration's verdict) when an
arc is untrusted; the replaced code values stay under `front.code_measured`. Both calls run on their own call ledger
(`<ledger>.intake.json`) sharing the job's dollar cap; a failure of either leaves the code-only evidence in place.
`--intake package` has a fresh agent answer the same packages (no API cost); `--intake scripted --intake-script
answers.json` replays saved answers. On a finished job, `python -m modeler.intake_astra --job <dir> --intake ...`
previews the stage beside the evidence (never over it) unless `--in-place`.

## Translucent (crystal) fronts and coverage by kind

`gl.material_translucent(name, tint_srgb, thickness_mm=4.0)` on the frame AND temple parts gives a crystal or
translucent acetate front and arms: the exported GLB carries physical transmission, IOR and volume absorption, and the
AR runtime (source tree, `ar/src`, unpublished) classifies it as frame or temple through the node's `partRole` extra, so
the wearer's face shows through the rim and the arms while hair occlusion, arm clipping, continuity and shadows treat
them as frame (pass B draws them through a camera-transmission twin, `ar/src/render/translucent-twin.ts`). Hardware
(metal) stays opaque; the exporter forces a translucent frame or temple material single-sided and refuses any lens
descriptor on a frame or temple part, and the contract enforces both. When such a material is present the observation
adds `summary.frame_see_through` (0 opaque … 1 clear, from two solid-fixture renders of the front view) and, only when
the temples are translucent, `summary.temple_see_through` beside it: the same measure in the angled view of the same
two runs, over the near arm as the runtime deforms it (the fixed 18 mm spread and the terminal return, ported from
`ar/src/render`), in front of its rear temple clip, the arm's own opaque hardware and bare-fixture pixels dropped; on
r0002's crystal-temple variant it reads 0.885 where a painted ground truth of the drawn arm reads 0.886 (the earlier
undeformed projection read 0.796). It is reported even when the front has no number; a translucent temple that could
not be measured appears as `{status}` (e.g. `no_temple_pixels`); the key is absent for opaque temples and when the
front's harness run failed before the temples were rendered (`frame_see_through` then carries that status). The
front and temple renders form the see-through sheet.

Every calibration row now carries the asset's tags (`modeler.tags`: `pair`/`single`, `rim_*`, `translucent`,
`mirrored`); `python -m modeler.calibration` prints owner verdicts per tag, and a job whose delivered asset carries a
tag without enough owner verdicts is not awarded `accepted` (the manifest and REPORT name the uncovered tag). The
translucent dry run: `python -m modeler.job --request data/modeler/requests/rayban_rb4455.json --output
data/modeler/dryruns/rayban-translucent-dry --author scripted --script data/modeler/scripts/dryrun_translucent_decisions.json --evaluator none`.

## The agentic route (`python -m modeler.agentic`)

One durable Astra conversation per product instead of a fresh package per turn: strict tools, immutable revisions,
a budget reserved before every request of every role, an image queue the author must fetch, a sealed held-out
boundary, a fresh-context critic, a sealed final evaluator and byte-bound delivery. Everything is in
[agentic/README.md](agentic/README.md): the offline demo (`python -m modeler.agentic demo --output <fresh dir> --worker fake`),
the CLI (`doctor`, `start`, `resume`, `status`, `cancel`, `owner-verdict`), exit codes, the Docker worker setup and the
owner-authorized paid pilot command. This legacy `modeler.job` route keeps working unchanged.

## Tests

```powershell
python -B -m pytest (Get-ChildItem tests\test_modeler_*.py) -q
python -B -m pytest (Get-ChildItem tests\test_agentic_legacy_*.py) -q  # the 2026-09-27 audit's regressions in the shared modules
python -B -m pytest (Get-ChildItem tests\test_agentic_*.py) -q  # the agentic route (see agentic/README.md for the fresh-basetemp form)
```

Unit tests (protocol validation, candidate store, status rule, Astra driver over a mocked session, tags and
coverage, the translucent material's export and contract rules) plus Blender end-to-end fixtures (generic frame ->
parts -> GLB -> contract; a crystal front with opaque temples, and crystal temples; Blender camera vs host
rasterizer). Unit tests and a loading GLB establish software properties, not visual fidelity. The runtime side: `cd ar && npm test`
(`tests/frame-roles.test.ts` covers the role classification).
