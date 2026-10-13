# Facet lab (WP6 v3)

The facet lab gives `gpt-6-astra` seven tools to turn a model's plain temples into faceted temples that look like the
product photos ([design](../../plan/WP6_V3.md) §6; the plumbing v3 keeps is [WP6_FACETS](../../plan/WP6_FACETS.md) §4;
[the instructions](instructions.txt)). It works on an isolated copy of a product scene: nothing here touches a
production job, and no production registry imports it. Prada PR 17WS is the test case. Why v3 exists:
[FINDINGS](../../data/wp6v2/review_20261012/FINDINGS.md) (git-ignored data): the real top face is not one plane, an
exact-planar builder put that misfit wherever the newest rule said, and several tools printed numbers that were read as
shape.

## Tools

| Tool | Does (host side: `lab.py`) |
|---|---|
| `facet_inspect` | the head: design and fitted value per node, the readings, the object note, the fit report, the creases, the chart |
| `photo_image` | a photo or a native crop, `enhance` (contrast stretch), `scale` up to 3 for a small crop |
| `photo_read` | named readings (`readings.py`): up to 8 points a call, each a `corner` (a pixel per photo) or a `crossing` (two traced edges per photo, intersected by the host), in 2-4 photos from at least two directions; each stored by name with its error (pick and camera parts), warnings and a pick sheet |
| `photo_compare` | the head rendered at the photos' cameras with its own creases drawn by identity, angle and sign (rows photo, render, overlay for every colour; images titled with the lab's photo ids); up to 3 zooms at native scale (photo, render, edges, a light-free geometry panel); side views print their outline offsets, and a view from above the top face's far edge minus its median instead of the top outline |
| `facet_edit` | an edit list on a revision's design, then the host's joint fit (`g3d/temple/fit.py`), the build and, with `apply`, a compare; `object` = the object note (required on the first edit and whenever the layout changes); judgements name up to 3 differences per photo |
| `facet_variants` | 2-4 edit lists fitted, built and rendered beside the head without changing the scene, one sheet per photo |
| `facet_finish` | ends the run when its preconditions hold (`state.finish_missing`) |

The flow of one edit: `geometry.apply_edits` (a node's t is a stored reading, a typed mm, the base, or solved by the
fit; a facet is flat or `warp`, optionally `soft` against the arm) -> the design (`revisions/rNNNN.json`) ->
`fit.fit_spec` (weighted least squares over every reading, shape declarations as constraints, misfits in mm and sd;
`revisions/rNNNN.fit.json`) -> `build_temple` of the fitted spec -> the worker builds it on both temples. Receipts
print the fit's lines, then the creases, then the builder's warnings, all as facts with every way out or none.

Files: `cli.py` (package, brief, estimate, request, run), `tools.py` (the strict-JSON registry), `lab.py` (the tools'
host side, revisions, compares, variants, finish), `geometry.py` (edits, head distinctness), `readings.py`,
`state.py` (judgements, object notes, finish preconditions), `sheets.py` (variant pages), `instructions.txt`.

## Removed in v3, and why (FINDINGS §3)

| Removed | Why |
|---|---|
| fades | built on a misreading ("fading diagonals"); they gave run 2's "very soft" and run 6's "smoothened" |
| crease + rise planes, the "solve" option that moves least | a corner solved from another face's extrapolated plane made a key defect in 5 of 7 runs |
| refusal recipes ("do not redeclare", one offered remedy) | Astra took a refusal's listed remedy in 12 of 14 refusals |
| photo_compare `depths` (one-photo readings) | an assumed (d, y) became a t error; run 6's hinge corner |
| top / rear outer-edge offsets, the outline line, the "start" column | photo 03's front-block outline conflicts with the other photos under the camera model (up to about 2.2 mm); printed as shape it drove runs 3-5 |
| crease margins, the section strip, lean, inherited warnings | replaced by the fit's sd and certain / open, or noise |
| `facet_restore` | a restore is a `facet_edit` of that revision with no edits |

## Commands

Run from the repository root with `glasses3d/.venv/Scripts/python.exe`:

```text
-m glasses3d.agent.temple_lab.cli prepare --job <new-dir> [--rig studio_dome_v1|photo_env|catalog_band_v1]
-m glasses3d.tests.proofs.astra_temple_lab rehearse --out <new-dir> [--rig <rig>]
-m glasses3d.agent.temple_lab.cli estimate --rehearsal <rehearsal-dir>/rehearsal.json [--out <estimate.json>]
-m glasses3d.agent.temple_lab.cli request --job <dir> --run-id <id> --effort high|max --turns <n> --cap <usd> [--k 4]
-m glasses3d.agent.temple_lab.cli run --job <dir> --run-id <id> <the request's other arguments> [--env-file <dotenv>]
```

- **prepare** ($0, about 1 min with the default rig) writes a new package: `inputs/` (the scene's byte copy, the target
  photos, the black photos B1-B3, `brief.txt`, `instructions.txt`, `lab.json`, every file sha-bound and every image
  sent in the first message), `basis/cameras.json` (outside `inputs/`, bound separately), then revision 0 (the
  starting model's +X temple measured into a spec: no faces, no edges) fitted, applied and compared. `job.json` binds
  the implementation (every `.py` of g3d, g3d_host and agent, and `instructions.txt`).
- **The brief** (`cli.brief_text`) carries the job's facts; `instructions.txt` names no photo and no product. Per
  photo: view, camera direction, the angle to the outer face, what its compares print (side views: the bottom outline
  offsets and, looking down 14 deg, the top face's far edge minus its median in place of the top outline) and its
  camera (B1's check is labelled as the all-views
  validation's, the check the target photos print, not the starting-model check B2 and B3 print). Then the direction
  groups; the cameras' lineage (01-06 fitted on their own; B1, B2, B3 moved from 05, 02, 04 with theta copied, as the
  basis records it); reading precision (crisp corners 0.2-0.5 mm; tips and edges along the line of sight about
  0.7-1.5 mm; a point far back also carries the cameras' theta; `READING_PRECISION`); the theta lever (a rigid turn of
  the temple, about 1.4 mm per degree at d 80, the same only for readings made from the same photos); the job's
  camera-model conflicts (`CAMERA_CONFLICTS`, worded as a limit of the camera model, never as doubt about a photo:
  photo 03's outer outline at d 10-40, up to about 2.2 mm further out than the other photos allow; photos 03 and 06
  near d 75, up to about 2.7 mm inside the side and angled readings, unresolved by any hinge opening of the angled
  photos, w2/crown-geometry.md §2); the render light and what it does not reproduce (tone marks where an edge is,
  not its size or sign); revision 0 and the scope (the whole temple to its tip, outline chains editable).
- **estimate** prices 40, 60 and 90-reply runs at effort high and max from the rehearsal's recorded tool outputs
  (`--out`, default `data/wp6v2/lab/estimate_v3.json`, `cli.ESTIMATE`). The typical mix (`cli.MIXES`) is the method
  the instructions teach. Each image-heavy call is priced at the most one call returns, at the largest image the tool
  makes: facet_variants at the job's sheets (one a photo) of 1280 x 900 px and 4 arms of text; photo_read at 8 pick
  sheets of 972 x 1412 px (4 photos a reading), its text and arguments scaled alike, a drop-only call left out of its
  mean; photo_compare at 3 zoom grids of 1538 x 1066 px and their text beside its compare images (a call's own grids,
  recorded as kind "zoom", are replaced; a rehearsal recorded before that is priced up to 3 grids high);
  facet_inspect at its largest recorded call. The range's top is the costliest of every reply an edit, the
  typical mix and a read-heavy mix (photo_read 3 in 10), each at the high output (`range_top`), and the approval text
  says the v3 mix is assumed (no v3 run exists). A projected run whose context would pass the model's input limit
  ends at that request, and its row says so. Output tokens per request are the v2 paid runs' means
  (`cli.OUTPUT_TOKENS`). `request` refuses a table made for another tool mix, range or image pricing.
- **request** writes `runs/<id>/approval-request.json` and `instructions-sent.txt` for a clean package, binding the
  inputs, the instructions (with K), the tool schemas and the run settings. Its tool contract
  (`cli.tool_contract`) refuses a package whose instructions, brief or tool descriptions name a retired phrase
  (`cli.RETIRED_PHRASES`: the removed tools and rules, the depths parameter by its forms such as "photo_compare's
  depths", never the plain word; `loop.REMOVED_PHRASES`). Nothing is approved here.
- **run** is the paid run, refused before any request unless the owner approved exactly that request.

Run settings (`tools.run_settings`): every reply calls a tool; the run ends only on an accepted `facet_finish` (K
distinct compared heads, `--k`); the context guard at 240k counted input tokens replaces superseded compare and
variant images (zoom grids included) and the pick sheets of readings read again or dropped by text stubs once.

Finish preconditions (`state.finish_missing`; only what reached Astra counts): (1) K distinct applied heads compared
(two heads differ when the built temple surface or outline moves at least 0.2 mm somewhere); (2) one facet_variants
call with 3 variants pairwise at least 0.3 mm apart; (3) best_checkpoint's revision compared at every photo with a
camera; (4) an object note; (5) for stuck: a variants result after the latest applied head and the last 3 judgements
on 3 distinct heads, none "better".

A paid run starts only from a clean package: any code change after prepare makes it stale; prepare a fresh one, then
request again. Never run from a rehearsal directory.

## Tests

`tests/agent/test_temple_lab_cli.py` pins only what WP6_V3 §6 asks of the prompt (the v3 tool names, `{K}`, no photo
id or product word, no retired or removed phrase, at most 9,500 characters, the finish text against
`state.finish_missing`; the prompt and the state both say "shown", pinned in `test_temple_lab_v3.py`), every brief line (on the real pose basis when the git-ignored data is present; the conflict and precision
numbers against the investigation's own tables when present) and the estimate (the largest images against a real pick
sheet and compare's grid; the image kinds against a real Lab's zoom registration and `context_stubs`). v2 pinned 96
phrases of the prompt; those pins are retired with v2.

## Limits

- No render light reproduces the photos' facet tones: renders and the geometry zoom are compared for where edges and
  outlines are, never for tone.
- One spec drives both temples (mirrored). The front, lenses, hinges and materials never change; the edits have no op
  for the hinge, chamfers or build options.
- The estimate is a forecast; effort high is priced as max (no high-effort ledger exists); the v3 tool mix is assumed
  (no v3 run exists) and image-heavy calls are priced at their maximum, so a run that reads fewer points or zooms less
  costs less; a photo_read's arguments are output tokens the v2 means do not include. The cap is enforced request by
  request, independently.
- This CLI does not resume an interrupted paid run: `request` and `run` need a clean package.
