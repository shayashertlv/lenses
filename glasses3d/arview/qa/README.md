# ar/qa index

Run every tool from `ar/`. None of them opens a real camera (except `camera-probe.mjs`), calls a provider or
publishes `site/`. Every output goes to the ignored `qa/output/` (or the `--output`/`--out` you pass). Where a
tool or doc below cites evidence under `qa/output/...`, that is a local, uncommitted run: regenerate it with the
command given rather than expecting it in a clone.

## 1. Page gates (the app on a synthetic camera)

These drive the normal page. The `--base` tools need a running dev (`npm run dev`, 8240) or preview (8241) server.

| Tool | What it checks | Inputs |
|---|---|---|
| `measure.mjs` (`npm run measure`) | Lifecycle, pacing and one audited frame on the checked-in face-a fixture | `--base`, default 8241 |
| `pipeline-smoke.mjs` | Normal page, both shipped models, restart, held-frame protection | `--base`, default 8241 |
| `shadow-smoke.mjs` | Same-frame shadow on/off images, controls, tracking loss | `--base`, default 8247 |
| `shadow-stability.mjs` | Frame-to-frame shadow variation under repeatable pose/depth noise | `--base`, default 8247 |
| `fit-smoke.mjs` | Calibration, locked sizing, Refit, fresh sessions | `--base`, default 8247 |
| `verify-published.py` | Served bytes and headers of the deployed `/ar/` against `site/public-manifest.json` | `--url` of the deployment |
| `camera-probe.mjs` | What the real default camera delivers under several constraint sets (opens the camera; nothing is stored) | `--base`, default 8241 |
| `camera-rate.mjs` | Camera delivery rate versus processed rate, read from a timing export | a saved timing/measurement report |

## 2. Harnesses used by automation code or plan docs

Each starts and closes its own local Vite server and headless Chromium (ANGLE D3D11) unless noted.

| Tool | Caller | Inputs |
|---|---|---|
| `provider-comparison.mjs` (+ `.html`, `-ar.html`, `-lighting.mjs`) | `automation/qa/provider_comparison.py`, `bsa/archeck.py`, `bsa/tryon.py`, `modeler/appearance.py`, `modeler/agentic/`, `reconstruction/segmented_*` | `--manifest` of local GLBs; see [provider-comparison.README.md](provider-comparison.README.md) |
| `semantic-material-cards.mjs` (+ `.html`) | `automation/reconstruction/semantic_appearance_stage.py` | `--manifest` written by that stage |
| `prepared-optical-groups.mjs` (+ `.html`) | `automation/qa/preview_manifest.py` (builds its manifest) | `--manifest` |
| `prepared-optics.mjs` (+ `.html`) | `automation/plan/PHOTO_LENS_PIPELINE.md` | `--manifest` |
| `canonical-lens-runtime.mjs` (+ `.html`, `canonical-lens-layers.mjs`) | `ar/README.md`, `automation/plan/LENS_RUNTIME.md` | fixtures from `python scripts/runtime_lens_fixtures.py` (run in `automation/`; default output `ar/qa/output/canonical-lens-fixtures`) |
| `lens-conformance.mjs` (+ `lens-appearance.html`) | `automation/plan/LENS_CONFORMANCE.md` | fixtures from `python -m reconstruction.lens_asset --output data/lens-conformance` (run in `automation/`; ignored data) |
| `production-optical-groups.mjs` (+ `.html`, `-browser.mjs`, `-oracle.mjs`, `-arithmetic.mjs`, `viewport-subpixel-probe.mjs`) | `automation/plan/EFFECTIVE_GROUP_RUNTIME.md`; see [production-optical-groups.md](production-optical-groups.md) | none (synthetic recipes); `--mode=arithmetic` for the diagnostic mode |

## 3. Optical-group evidence reproducers

Research harnesses behind `automation/plan/OPTICAL_GROUPS.md`. They reproduce recorded experiments; they are
not gates of the production renderer.

| Tool | What it reproduces | Inputs |
|---|---|---|
| `effective-optical-groups.mjs` (+ `.html`, `-browser.mjs`); `effective-optical-groups-angle.mjs` | The isolated QA-only group renderer, and its math-override runs; see [effective-optical-groups.md](effective-optical-groups.md) | none |
| `source-optical-groups.mjs` (+ `.html`, `-browser.mjs`) | Real source triangles in the QA-only transport; see [source-optical-groups.md](source-optical-groups.md) | `--bundle`, default `../automation/data/source-optical-groups-browser-v1` (built as that doc describes) |
| `acos-precision.mjs` (+ `.html`, `-browser.mjs`) | Builtin `acos` versus the fixed Taylor formula on the GPU | none |
| `taylor24-angle-override.mjs`, `raster-arithmetic-override.mjs` | The fixed math overrides applied by `effective-optical-groups-angle.mjs` and `source-optical-groups.mjs` | imported, not run directly |
| `canonical-display-regression.mjs` (+ `.html`, `-browser.mjs`) | The canonical lens display shader against composed RGB; `--old-gate=true` is the negative control | none |

`raster-arithmetic-override.test.mjs` is a `node:test` suite for the raster override. `npm test` does not run it
(it reads the prototype harness source); run it with `npm run test:qa`.

Files in `qa/` that are not listed here are local session experiments, not part of any pipeline.
