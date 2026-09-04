# Lenses AR

Browser-based eyewear try-on that scans a face once, fits a frame once, and
tracks the resulting model in real time.

## Pipeline

1. The browser captures a short guided face scan.
2. Enrollment solves a personal `FaceModel` locally from selected frames.
3. The contact solver places the selected frame against that model.
4. Per-frame tracking estimates head pose; the cached fit moves with the head.

The renderer, detector runtime, and frame assets are served locally. The app
does not send camera frames or face-model data to a service.

## Run locally

```bash
npm install
npm run build
npm run serve
```

Open the local URL printed by the server. Use `localhost` during development or
HTTPS in a deployed environment so the browser can grant camera access.

To refresh the verified browser runtime after a fresh clone:

```bash
npm run vendor
```

## Verify

```bash
npm test
npm run check:vendor
```

`npm test` compiles the project, checks browser-boundary and constant-ledger
rules, validates report metadata, and runs the Node test suite.

## Project layout

- `src/enroll/` — guided capture and face-model reconstruction
- `src/track/` — live head-pose tracking
- `src/fit/` — frame assets, contact fitting, and fit scores
- `src/render/` — Three.js scene and occlusion rendering
- `src/app/` — browser orchestration and UI
- `tests/` — deterministic unit and pipeline coverage

## Data handling

The saved face model and an optional user-entered PD live only in this browser's
local storage. “Delete my measurements” clears saved and active measurement
state immediately. See [the privacy details](docs/PRIVACY.md).

## Technical references

- [Architecture](docs/ARCHITECTURE.md)
- [Constants ledger](docs/CONSTANTS.md)
- [Open validation work](docs/OPEN-QUESTIONS.md)
