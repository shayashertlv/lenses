# Facet lab

The facet lab gives `gpt-6-astra` seven tools to turn a model's flat temples into faceted temples that look like the
product photos ([design](../../plan/WP6_FACETS.md) §4, [the exact instructions](instructions.txt)). It works on an
isolated copy of a product scene: nothing here touches a production job, and no production registry imports it.
Prada PR 17WS is the test case.

The tools are `facet_inspect`, `photo_image`, `photo_compare`, `facet_edit`, `facet_variants`, `facet_restore` and
`facet_finish` (`tools.py`; host side `lab.py`, `geometry.py`, `state.py`, `sheets.py`).

## Commands

Run from the repository root with `glasses3d/.venv/Scripts/python.exe`:

```text
-m glasses3d.agent.temple_lab.cli prepare --job <new-dir> [--rig photo_env]
-m glasses3d.tests.proofs.astra_temple_lab rehearse --out <new-dir> [--rig photo_env]
-m glasses3d.agent.temple_lab.cli estimate --rehearsal <rehearsal-dir>/rehearsal.json [--out <estimate.json>]
-m glasses3d.agent.temple_lab.cli request --job <dir> --run-id <id> --effort high|max --turns <n> --cap <usd> [--k 4]
-m glasses3d.agent.temple_lab.cli run --job <dir> --run-id <id> <the request's other arguments> [--env-file <dotenv>]
```

- **prepare** ($0; about 4 min with `photo_env`, 1 min with `studio_dome_v1`) writes a new package:
  - `inputs/`: `scene.blend` (a byte copy of the original, sha checked), the target photos (checked against the
    reference manifest and the pose basis), `brief.txt`, `instructions.txt` and `lab.json`
    (`g3d.temple_lab/2`: the rig, every input's sha256, the photo list, the pose basis's file hashes);
  - `basis/cameras.json`: the pose basis restricted to the target photos, imported as `cameras/basis-NN`. It sits
    outside `inputs/` because every image under `inputs/` is sent in the first message;
  - then the original is compared at every photo (with `photo_env` this fits and freezes the job's light), revision 0
    (`template_from_mesh` of the original's +X temple) is applied, checkpointed and compared;
  - `job.json` binds the implementation (every `.py` of g3d, g3d_host and agent, and `instructions.txt`), the rig and
    the frozen light; `prepared.json` and `tools.json` describe the package.
- **rehearse** (T5, $0, about 6 min) prepares its own package and runs a scripted conversation through the pinned
  Agents SDK, the real Blender worker and every tool, with api.openai.com replaced by a MockTransport and the network
  blocked. The edits are arbitrary controls. It writes `rehearsal.json` with its checks and the size of every tool
  output.
- **estimate** prices 40, 60 and 90-reply runs at effort high and max from the rehearsal's tool outputs
  (`data/wp6v2/lab/estimate.json`).
- **request** writes `runs/<id>/approval-request.json` and `runs/<id>/instructions-sent.txt` for a clean package. It
  binds the inputs, the instructions (with K), the tool schemas and the run settings, and records nothing as approved.
  After the owner approves that request in chat, `approvals.write_approval` records the owner's exact words.
- **run** is the paid run. The budget guard refuses it before any request unless the owner approved exactly that
  request. `--no-turn-cap` replaces `--turns`, and `--fit-output-to-budget` lets the last replies use the rest of the
  cap; both are bound into the approval.

Run settings (`tools.run_settings`): every reply must call a tool (`tool_choice` required, kept after the first call);
the run ends only when `facet_finish` returns accepted (K distinct compared heads, `--k`, default 4); a text reply
gets one host note, at most twice, then the run ends `ended_without_finish`. At 240k counted input tokens the
context guard replaces superseded compare and variant images by text stubs once.

Finish preconditions (`state.finish_missing`, procedure only):
- K unique heads: a compared head counts only when it differs from every head already counted (going back to an
  earlier head counts nothing). "Differs" is >= 0.2 mm max |dX| or outline shift, or a facet / crease-shape change
  judged by geometry (each facet as the sorted (d, y) of its nodes to 0.01 mm), never by label: a rename is no change.
- One `facet_variants` call with 3 variants pairwise >= 0.3 mm apart, counted only over variants that rendered.

Timeouts: each tool has its SDK timeout (`loop.HARD_TIMEOUT_S` + 10 s). A timed-out call answers `E_TIMEOUT` with the
money / context footer; its thread may still run and holds the lab's scene lock (`Lab.lock`), so other tools answer
`E_BUSY`, and the loop's exit checkpoint and close wait for it. A compare such a call still makes is recorded unseen
and demands no judgement. Compare renders inside a tool run under that tool's hard timeout.

Receipts stay within the §4.5 budget (facet_edit <= 1,500 characters on the T5 outputs): border ramps are one line,
outer-edge offsets are printed to 0.1 mm, and warnings revision 0 already has (e.g. W_HINGE_LEAF,
W_ARM_NORMAL_SPREAD) are listed by `facet_inspect` as inherited from revision 0 and left out of later receipts
while their numbers stay within 0.1 (or 10 %).

A paid run starts only from a clean package: the implementation, inputs, pose basis and frozen light must be
unchanged and no facet tool may have run. Any code change after prepare (a review fix, another track's commit) makes
the package stale; prepare a fresh one, then request again. Never run from a rehearsal directory.

## Owner sheets and tool tests

- `tests/proofs/wp6v2_sheet2.py`: owner sheet #2 (`data/wp6v2/sheet2/`), one question per page: `index.html`
  ("Does revision 0 look like the original?") and `depth.html` ("Can you see the depth change, and does the preferred
  look like the photos?": the depth strip, then the preferred shape with rounded edges beside each photo).
- `tests/proofs/wp6v2_expressivity.py` (T5b, a tool test never given to Astra): the coding agent reaches the
  preferred layout from revision 0 with `facet_edit` ops only (`data/wp6v2/lab/t5b/`).

## Limits

- Facet tones depend on the light, which is estimated, not known: shading is a qualitative aid. Layout comes from the
  crease overlays, thickness from the outer-edge offsets in the top and rear photos, ridge or valley from silhouettes
  and the section strip.
- The lighting rig is a package choice (`--rig`); the owner chooses it from the lighting sheet.
- One spec drives both temples (mirrored). The front, lenses, hinges and materials never change. The edits have no op
  for the hinge, chamfers or build options.
- Revision 0 keeps the original's outline and inner face; reaching another layout can need outline splices (T5b).
- The estimate is a forecast from the rehearsal's tool outputs and a fitted token formula; effort high is priced as
  max (no high-effort ledger exists). The cap is enforced request by request, independently.
- This CLI does not resume an interrupted paid run: `request` and `run` need a clean package. The loop's resume and
  rollback planning are tested with the lab journal (prepare's apply is never rolled back) but not wired here.
