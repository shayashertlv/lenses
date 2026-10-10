# Isolated temple editing lab

This opt-in experiment starts with the original flat-temple Prada scene and eight
real photos. It exposes thirteen bounded, image-bearing tools to `gpt-6-astra`.
See [the detailed plan](../../plan/ASTRA_TEMPLE_LAB.md) and
[the exact agent instructions](instructions.txt).

There is no paid call during preparation or rehearsal. Production tools and WP6
acceptance remain gated. The implementation does not import a preferred facet
layout, photo guides or fitted depths. The scripted proof's four-facet pattern is
an arbitrary control, never a product result or an Astra-generated answer.

From the repository root, using `glasses3d/.venv/Scripts/python.exe`:

```text
-m glasses3d.agent.temple_lab.cli prepare --job <new-ignored-job-directory>
-m glasses3d.tests.proofs.astra_temple_lab rehearse --out <different-new-directory>
-m pytest glasses3d/tests/agent/test_temple_lab.py -q
```

The prepared directory contains `inputs/scene.blend` (original bytes), all eight
photos, the prompt, source hashes, strict schemas, an untouched initial checkpoint
and fixed native/clay renders. `prepared.json` describes these assets. No template
is applied to the starting scene. Use a clean package for the actual test, not the
rehearsal directory; the launch preflight rejects marked/edited rehearsals and
implementation drift. Each invocation is fresh; resuming an interrupted paid lab
conversation is not supported by this small experimental CLI.

After the owner selects effort/turns/cap, create a reviewable request:

```text
-m glasses3d.agent.temple_lab.cli request --job <clean-job> --run-id isolated-test-001 --effort <high-or-max> --turns <owner-choice> --cap <owner-choice>
```

This writes an estimate and binds inputs, implementation, instructions, tool
schemas and settings. It does **not** approve or call Astra. After explicit owner
approval, the existing `approvals.write_approval` records the exact owner words
for that request. Then launch with the same arguments and action `run`. The
existing budget guard refuses missing/stale approval before constructing a
network transport. Credentials are supplied through the existing authorized
runner configuration, never embedded in this package. No credential is needed
for preparation or offline rehearsal.

```text
-m glasses3d.agent.temple_lab.cli run --job <clean-job> --run-id isolated-test-001 --effort <same> --turns <same> --cap <same>
```

The offline proof uses MockTransport and a synthetic approval labelled **OFFLINE
ONLY**, plus socket and production-transport tripwires. Its request/response
archive exercises the actual pinned SDK, image outputs, native Blender edits,
GLB export, refusals and checkpoint restoration. It proves wiring, not Astra's
ability to infer the photographed geometry.

Known limits: template sampling and front-plane linearization change the base;
profile edits are explicit. Photo-to-chart mapping requires Astra-supplied
correspondences and does not recover depth or remove parallax. Facets use a shared
planar graph on a single-valued side outline; front/hinge mechanisms and material
editing are outside this experiment. Both temples are mirrored. Frozen WP6
quality failures remain; builder success is structural validity only. Tool
receipts include warnings and decal movements. A final decision needs actual
Astra output and owner review.
