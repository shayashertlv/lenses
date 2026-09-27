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

Views: `front|back|left|right|angled|top|unknown`; `held_out` views are never shown to the author. Without
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

## Tests

```powershell
python -m unittest discover -s tests -p "test_modeler_*.py"
```

Unit tests (protocol validation, candidate store, status rule, Astra driver over a mocked session) plus Blender
end-to-end fixtures (generic frame -> parts -> GLB -> contract; Blender camera vs host rasterizer). Unit tests and a
loading GLB establish software properties, not visual fidelity.
