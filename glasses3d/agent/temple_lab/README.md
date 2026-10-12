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
-m glasses3d.agent.temple_lab.cli prepare --job <new-dir> [--rig studio_dome_v1|photo_env|catalog_band_v1]
-m glasses3d.tests.proofs.astra_temple_lab rehearse --out <new-dir> [--rig <rig>]
-m glasses3d.agent.temple_lab.cli estimate --rehearsal <rehearsal-dir>/rehearsal.json [--out <estimate.json>]
-m glasses3d.agent.temple_lab.cli request --job <dir> --run-id <id> --effort high|max --turns <n> --cap <usd> [--k 4]
-m glasses3d.agent.temple_lab.cli run --job <dir> --run-id <id> <the request's other arguments> [--env-file <dotenv>]
```

- **prepare** ($0; 30-40 s with the default rig `studio_dome_v1`, about 4 min with `photo_env`) writes a new package:
  - `inputs/`: `scene.blend` (a byte copy of the original, sha checked), the target photos (checked against the
    reference manifest and the pose basis), the black photos (`cli.COLOURWAY_PHOTOS`, `cli.REFERENCE_PHOTOS`, each
    sha-pinned; below), `brief.txt`, `instructions.txt` and `lab.json` (`g3d.temple_lab/2`: the rig, every input's
    sha256, the photo list with each photo's role, the pose basis's file hashes). `instructions.txt` is general for
    any faceted temple and photo set; `brief.txt` (`cli.brief_text`) carries the job's facts: per photo its camera
    direction, the angle to the temple's outer face (square-on, moderate, edge-on) with how finely photo_compare's
    `depths` reads t there (px per mm), its number lines and its camera's fit (a transported camera: photo to photo
    and its check against the starting model); the direction groups; the views that look along creases running front
    to back; the render light (`--rig`); which temple is charted and which
    mirrored; the finishes the photo rows give (only the black photos'); revision 0; and the real temples' edge
    rounding, "not known";
  - `basis/cameras.json`: the pose basis restricted to the target photos and B1-B3 (with their supplied mattes under
    `basis/mattes/`), imported as `cameras/basis-NN` (with its photo mattes). It sits outside `inputs/` because every
    file under `inputs/` is sha-bound as an approved input (`approvals.input_digests`) and every image under
    `inputs/` is sent in the first message (`approvals.run_input_binding`); the basis files are bound separately
    (`lab.json` `basis.files`);
  - then the original is compared at every target photo (only with `--rig photo_env` does this fit and freeze the job's
    light, about 150 s), revision 0 (`template_from_mesh` of the original's +X temple) is applied, checkpointed and
    compared;
  - `job.json` binds the implementation (every `.py` of g3d, g3d_host and agent, and `instructions.txt`), the rig and
    any frozen light; `prepared.json` and `tools.json` describe the package.
- **rehearse** (T5, $0, about 2 min: prepare 30-40 s, run about 90 s, one Blender worker) prepares its own package
  and runs a scripted conversation (20 requests) through the pinned Agents SDK, the real Blender worker and every
  tool, with api.openai.com replaced by a MockTransport and the network blocked. The edits are arbitrary controls. It
  covers the approval and network guards, required tool choice, E_JUDGEMENT, an SDK timeout then E_BUSY, a timed-out
  apply, a refused then an accepted finish, the context guard, the export, the text budgets and the top photo's
  outline line, and the photo set: every photo in the first request (6 target + 3 colourway roles), a black photo in
  a facet_edit compare, facet_variants at a target and a black photo (gloss_black panels, each model's crease lines
  counted per panel, the scene unchanged), the B2/B3 compare (photo / overlay / gloss_black) with `points_px`, the
  gloss gain still from black-side, photo_compare's `depths` (a B2 reading outside the default set, two on a black
  photo, and a corner reading of one point in B2 and B1 with `also`, its d given 2 mm off), the flips against the head
  (`crease_vs_head`, `diff_vs_head` of an applied variant) and inspect's solved values. It writes `rehearsal.json`
  with its checks (39 on `t5_wip7a`) and the size of every tool output.
- **estimate** prices 40, 60 and 90-reply runs at effort high and max from the rehearsal's tool outputs
  (`--out`, default `data/wp6v2/lab/estimate.json`). Output tokens per request are the paid facet runs' means at
  effort max (`cli.OUTPUT_TOKENS`: expected 1,852 = run 4, high 2,064 = run 3). facet_variants is priced at the most
  sheets one call of the rehearsal's job returns (`cli.variant_sheets`: 4 with a photo of another colour, else
  3). `request`
  refuses a table that priced fewer sheets than its package's calls return: estimate from a rehearsal of the current
  tree and pass `--estimate` explicitly.
- **request** writes `runs/<id>/approval-request.json` and `runs/<id>/instructions-sent.txt` for a clean package. It
  binds the inputs, the instructions (with K), the tool schemas and the run settings, and records nothing as approved.
  After the owner approves that request in chat, `approvals.write_approval` records the owner's exact words.
- **run** is the paid run. The budget guard refuses it before any request unless the owner approved exactly that
  request. `--no-turn-cap` replaces `--turns`, and `--fit-output-to-budget` lets the last replies use the rest of the
  cap; both are bound into the approval.

Photos (owner, 2026-10-11, approving the run after facet-astra-002 with the black photos included). Every image under
`inputs/` is in the first message, the target photos first:
- target `01`..`06` (role `target`): every tool; the default compare set; finish precondition 3;
- `B1` side, `B2` angled and `B3` front (role `colourway`; `black-side.png` sha 6eddabbb…, `black-angled.webp` sha
  5705c2ca…, `black-front.webp` sha e89d19f4…): the same model in glossy black, each with its pose-basis camera
  transported from the target photo of the same pose (B1 from 05, B2 from 02, B3 from 04; WP6_FACETS §3.1; owner
  2026-10-11: "yes, build 1-3"). `photo_image`, `photo_compare` and `facet_variants` take them; a compare shows photo
  / overlay / gloss_black render, no native row (the model's material is the target colourway), and a variant sheet
  at one shows gloss_black panels. Never in the default set; one may be the 4th photo of a `facet_variants` call
  (at most 3 target photos);
- a photo without a camera would be role `reference` (`photo_image` only; none for Prada).

With B1 in the package's basis, the job's gloss_black gain and stretch come from that dark photo (seeing
`gains_from_renders`: a dark colourway sets them; its side view has no usable front, so the region is its largest
acetate region, labelled so), frozen at prepare like every gain. Without a dark photo they were the fixed exposure
of the front acetate to luma 40 and the stretch 0-128.

Run settings (`tools.run_settings`): every reply must call a tool (`tool_choice` required, kept after the first call);
the run ends only when `facet_finish` returns accepted (K distinct compared heads, `--k`, default 4); a text reply
gets one host note, at most twice, then the run ends `ended_without_finish`. At 240k counted input tokens the
context guard replaces superseded compare and variant images by text stubs once.

Finish preconditions (`state.finish_missing`, procedure only; only compares and variant sheets that reached Astra
count):
- K unique heads: a compared head counts only when it differs from every head already counted (going back to an
  earlier head counts nothing).
- "Differs" is one rule (`Lab.distinct` = `geometry.distinct_heads`), also used for the stuck status. It compares the
  two BUILT temples (the g3d.temple build of each head's spec), never their specs, labels, node names or crease
  tables. Astra is told: the built temple surface or outline moves by at least 0.2 mm somewhere. Two heads differ
  when either is >= 0.2 mm:
  - surface: the largest NORMAL distance between the two outer surfaces (roll and chamfers included) on the common
    0.5 mm (d, y) grid inside both outlines, |dX| / sqrt(1 + |grad X|^2) with the mean of the two gradients, so the
    nearly vertical roll band does not magnify an outline move;
  - outline: y_top or y_bot at any d, or the end d0 / d1.

  The visible-crease comparison (ridge and valley sets by Hausdorff distance, `crease_delta`) is still computed and
  reported, but it never decides: every threshold on crease visibility had a knife edge that 0.05-0.1 mm thickness
  nudges could cross (reviews of 2026-10-11), while a real layout change or ridge <-> valley flip moves the surface.
  So flat (flush or coplanar) facets, coplanar splits, slivers, hairline gaps, a rename, a reordered polygon, a node
  or corner outside the outline and nudges under 0.2 mm are no change by construction; a 0.3 mm thickness change at
  a node or a deleted proud facet is. K = 4 therefore needs four built temples each at least 0.2 mm from the others.
  The comparison takes about 0.05 s per pair on the Prada fixture (0.15 s on the preferred layout with a 0.4 mm
  roll) and is cached per pair of revisions.
- One `facet_variants` call with 3 variants pairwise >= 0.3 mm apart (`value_mm`: the surface or outline part above),
  counted only over variants that rendered and whose result reached Astra (a call the SDK timed out counts nothing).
- best_checkpoint's revision compared at every target photo by compares Astra was shown (prepare's compares and a
  timed-out call's compare do not count; the black photos are not required).
- stuck only: the last 3 judgements are on 3 heads that differ pairwise (the same rule), and none says better.

The lab's state is mutated and saved only under one lock (`Lab.state_lock`), so `mark_unseen` (on the event loop,
when the SDK times a call out) never races the worker thread's saves.

Timeouts: each tool has its SDK timeout (`loop.HARD_TIMEOUT_S` + 10 s). A timed-out call answers `E_TIMEOUT` with the
money / context footer; its thread may still run and holds the lab's scene lock (`Lab.lock`), so other tools answer
`E_BUSY`, and the loop's exit checkpoint and close wait for it. A compare such a call still makes is recorded unseen
and demands no judgement; its variant sheets never meet precondition 2; a finish it still accepts is kept as
`finish_unseen` and never ends the run. Compare renders inside a tool run under that tool's hard timeout.
`facet_restore` (90 s) and `facet_finish` (210 s) are their worker deadlines (a checkpoint restore 60 s,
an export 120 s) plus a 30 s margin, so the SDK never gives up on them while a restore or export is still
within its own deadline.

Receipts stay within the §4.5 budget (facet_edit <= 1,500 characters on the T5 outputs, all six photos included, with
the longest footer; the layout `reset_layout` cleared is counted, not named): border ramps are one line,
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

- Facet tones depend on the light, and no rig reproduces the photos' facet tone order: shading is a qualitative aid.
  Layout comes from the crease overlays (laid out square-on, confirmed at a moderate angle), thickness from the
  outer-edge offsets in the top and rear photos, ridge or valley from silhouettes (the section strip shows what the
  model built).
- The lighting rig is a package choice (`--rig`). The default is `studio_dome_v1`, the owner's choice of 2026-10-11 on
  the lighting sheet ("c is definitely the best"); `photo_env` and `catalog_band_v1` stay available.
- One spec drives both temples (mirrored). The front, lenses, hinges and materials never change. The edits have no op
  for the hinge, chamfers or build options.
- Revision 0 keeps the original's outline and inner face; reaching another layout can need outline splices (T5b).
- The estimate is a forecast from the rehearsal's tool outputs and a fitted token formula; effort high is priced as
  max (no high-effort ledger exists). The cap is enforced request by request, independently.
- This CLI does not resume an interrupted paid run: `request` and `run` need a clean package. The loop's resume and
  rollback planning are tested with the lab journal (prepare's apply is never rolled back) but not wired here.
- Head distinctness: a crease rising from below 1 deg to 2 deg or more counts as appearing (on the 2 mm border band a
  facet proud by 0.035 mm, then 0.07 mm: a 0.04 mm surface change). A fold is measured over the border band or the
  largest of its halvings (down to 0.25 mm) that stays in each side's region, so a facet narrower than 0.25 mm is
  measured partly across its neighbour; a fold that cannot be measured inside the outline counts as no fold.
