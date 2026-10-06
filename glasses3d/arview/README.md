# arview: the glasses3d development AR runtime

arview shows Astra and the owner what a GLB looks like worn in the browser try-on, and says plainly what violates the
owner's model spec (PLAN.md §2). It is a fork of `ar/` at 75ae133 plus the chosen stash@{0} edits (WP1 stage A,
`plan/PORT_REVIEW.md` §7), with much broader acceptance than the live site: only a §2 refusal (WP1_DESIGN.md §1) ever
blocks a render; everything else renders with a warning and every fallback is reported. It is never deployed and never
publishes anything; the live try-on stays `ar/`, served by the root application.

arview never modifies `ar/`. It reads, read-only, the public binaries in `../../ar/public` (MediaPipe WASM, the face and
hair models, the canonical face; Vite `publicDir`) and the synthetic portrait face `../../ar/qa/fixtures/face-a.jpg`
(capture only, pinned by sha256). Vite's dependency caches stay inside `arview/node_modules`. Outputs, builds, captures,
state and logs go under `glasses3d/data/` (git-ignored). Camera images, face tracking, hair segmentation and rendering
stay in the browser.

Two tools:

- **`arview capture`** (`node qa/capture.mjs`): posed AR images of one GLB plus a report for Astra (below).
- **`arview serve`** (`node qa/serve.mjs`): the owner's webcam page for one GLB (below).

## Run and verify

Run these inside `glasses3d/arview/` with Node.js 22.18 or later (this machine: v26):

```bash
npm ci
npx tsc --noEmit            # 0 errors
node --test tests/*.test.ts # unit and renderer tests
node --test qa/*.test.mjs   # capture and serve tests (headless Chromium on the GPU, spec.py from glasses3d/.venv)
npx vite build              # the frozen page into dist/ (git-ignored)
npm run assets              # checks the pinned public binaries in ../../ar read-only (writes nothing)
```

Ports (`ar/` keeps 8240/8241):

| Port | What | Command |
|---|---|---|
| 8250 | development server, hot reload; for development only (a reload mid-session kills it) | `npm run dev` |
| 8251 | the frozen webcam page for one model | `node qa/serve.mjs <model.glb>` (or `npm run build && npm run preview`, without the model report) |

`vite.config.ts`: `publicDir` `../../ar/public` (read-only), `cacheDir` `node_modules/.vite`, the Draco and Basis (KTX2)
decoders served from three's own `examples/jsm/libs` at `/decoders/` and copied into the build, `worker.format` `es`.
Playwright's Chromium is installed once with `npx playwright install chromium`.

## Webcam page (`arview serve`, WP1_DESIGN.md §6)

```bash
node qa/serve.mjs <model.glb>                 # or: npx arview serve <model.glb>; Ctrl+C stops it
node qa/serve.mjs <model.glb> --detach        # runs in the background (survives closing the terminal), prints the address
node qa/serve.mjs --stop                      # stops the server on 8251 (add --port N for another)
node qa/serve.mjs --status                    # is it running (or still checking the model), which model, which address
```

Options: `--port N` (default 8251; a port in use is an error, never a silent move), `--name <label>` (default the file
name), `--clip <m>` (temple clip handed to the page, -0.2 to -0.045; default derived from the model's temples),
`--lensenv N` (lens reflection multiplier, 0.3-4). Exit codes: 0 serving / stopped, 1 failure (build, port, detached
start), 2 usage error.

It prints the address to open in Chrome or Edge on this computer:

```
http://127.0.0.1:8251/?model=<loopback model URL>&name=<label>&sha256=<model sha256>&report=<report URL>
```

What it does (`qa/serve.mjs`):

1. **Builds the frozen page if its sources changed** (`qa/serve/build.mjs`): the build is content-addressed under
   `glasses3d/data/arview/serve/builds/<source sha256>/` (index.html, src/, vite.config.ts, package.json, package-lock.json)
   and stamped with every file's sha256; a build whose files match its stamp is reused (no rebuild), one that does not
   is set aside as `*.corrupt-<time>` and rebuilt. Builds take about 2 s; a build past 100 s is reported as a stall and
   the command exits 1. Old builds are not removed automatically (about 3 MB each); delete them by hand when no server
   uses them. The public binaries are not copied into the build: the server serves them from `../../ar/public`, each
   pinned by sha256 at start (the catalog GLBs there are not served), and checks the pinned face/hair models and that
   the MediaPipe runtime equals arview's `node_modules` copy (`scripts/prepare-assets.mjs`).
2. **Checks the model** on the very bytes it serves, in parallel, each with a 60 s deadline: `python -m
   glasses3d.g3d_host.spec --full` (the single source of truth for the §2 rows) and arview's own load front-end and
   normalisation pass in Node (`src/model/check.ts`, the renderer's load minus drawing; textures are not decoded there:
   every texture path of three's parser, the built-in `EXT_texture_webp`, `EXT_texture_avif` and `KHR_texture_basisu`
   handlers included, gets an empty texture (`geometryOnly` in `qa/serve.mjs`); external buffers are handed to the parser
   from the bytes already read, never through three's FileLoader) plus the
   temple clip the renderer derives (`qa/temple-cutoff.mjs`). A check that throws or passes its deadline is an error in
   the report; an asynchronous failure nobody handled is logged and becomes a report error, never a process exit.
3. **Serves on 127.0.0.1 only**: the build and the public binaries (each re-checked against its pin on every request: a
   file changed since the start is answered 500, restart the server), the model at
   `/__arview/models/<sha256>/<name>` with `X-Arview-Sha256`, `Repr-Digest` and `ETag`, the external resources the GLB
   names, and the report at `/__arview/report.json`. External resources are resolved by one function shared with
   `arview capture` (`qa/capture/external-resources.ts`): each file is read where spec.py reads it (the GLB's folder
   joined with Python's `unquote(uri)`, so `100%.png` is the file `100%.png` and `../shared/a.bin` is read where it
   points) and served at the raw path arview's loader requests (the server looks resources up before decoding the
   path). A file outside the GLB's folder (`../`, a root-relative path, a link) is served with a warning
   (`model.outside_resources`); only an absolute URI (a scheme, a drive letter `C:/...` or `//host`: glTF resolves it as
   a URL, never next to the file, so spec.py fails it too, even when such a path exists) or a file that cannot be read
   is a 404, so arview refuses the model (§1 Format) exactly where spec.py fails the Format row, and `serve_warnings` say
   why. The URL of a file that cannot be read is an explicit 404 wherever it resolves, looked up before the frozen page
   and the public files: a URI that climbs out of the model's folder (`../../../index.html`) never gets one of the
   server's own files (an error says when such a URL is one of them). A resource whose URL would be
   one of the server's own paths (the page, a public binary, the report, the model) is not served over it: that is an
   error in the report (`model.unservable_resources`; a limit of the tool, not a §2 verdict). Like spec.py, serve reads
   any file a GLB names by a relative path, wherever it points, and serves it to loopback clients of this port: serve
   GLBs you trust (every file outside the GLB's folder is named in a warning). Every response carries
   `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`,
   `Cross-Origin-Resource-Policy: same-origin`, `Permissions-Policy: camera=(self)`, `Cache-Control: no-store` and
   `nosniff`. A request with `Host: localhost:<port>` is redirected (307) to the same path and query on 127.0.0.1, so
   typing `localhost` works: the page always runs on the origin of the handover's model and report URLs (under COEP and
   CORP a localhost page could not fetch them). Any other `Host` but `127.0.0.1:<port>` is a 421 (a DNS-rebinding page
   cannot read the report or the model). Anything else is a 404 (no SPA fallback); only GET and HEAD, plus the
   token-guarded `POST /__arview/stop` that `--stop` uses. While the model is still being checked every GET is a 503;
   the report path's 503 body (`arview.serve-pending/1`) names the server.
4. **Records itself** in `glasses3d/data/arview/serve/state/port-<port>.json` (pid, address, model, sha256, stop token)
   as soon as the port listens (`ready: false` while it checks the model, `true` once the report exists), so `--status`
   ("still checking the model", exit 0), `--stop` (works during the check) and a second `serve` on that port (refused)
   recognise it from the first second.
5. **Watches the file on disk**: every fetch of the page, the model or the report re-hashes the GLB when its size or
   mtime changed. A file re-exported to the same path (the Studio always writes `model.glb`) is a loud error in the
   report and on the page after a reload ("the file on disk changed ...; restart arview serve"); the server keeps
   serving, and the sha256 keeps naming, the bytes it read at start.

The report (`qa/serve/report.ts`, schema `arview.serve/1`) has `rows` (each §2 row: spec.py's verdict and detail, arview's
own row beside it, and whether they agree; arview's when spec.py could not run, with a loud `VALIDATOR UNAVAILABLE`),
`failed_rows` (each row's failures in `detail`, its notes such as `GLB 2.0, N bytes` or spec.py's `unverified: ...` in
`notes`), arview's `status` (`rendered`, `refused`, `unchecked`, or `not_checked` when its check failed), `refusal`,
`serve_warnings` (the server's own), `node_check_warnings` (the Node-side check's cross-check against spec.py and its
temple clip receipt), `spec_warnings` (all of spec.py's warnings), `warnings` (all of those plus every model warning,
spec.py's as `spec.py: ...`, each once), `fallbacks` (subsystem and detail), `errors` (problems of the server and of the
checks: a check that crashed or timed out, digests that disagree, a missing public binary, the file changing on disk;
`node_check_errors` names those only the Node-side check produced), `served_sha256` (the bytes served; not a rendered
digest: nothing is drawn when this report is made), `lens_identification`, `measurements`, `temple_clip` and the static
`live_site_would_fail`. The Node-side check's `fallbacks` include those the renderer reports from the temples alone (a
missing temple's fixed cap, the default clip, continuity off without any temple: `templeLoadFallbacks` in
`src/render/temple-geometry.ts`, the very sentences `renderer.ts` writes, with a handed-over `--clip` applied as the
renderer applies it), so the start line's and the report's fallback count match the capture report's load-stage
fallbacks (`tests/renderer-api.test.ts` asserts the renderer emits exactly that list); what only drawing finds is added by
the renderer's report after Open camera.

**The page** lists, under the model name, before the camera opens: the server's errors, the §2 refusal reasons, every
failed §2 row (spec.py's failures; a row's notes are never listed as errors), the warnings and each fallback labelled
with its subsystem (`src/model/served-report.ts`), and the served sha256. Once the renderer has loaded the model (Open
camera) it lists the renderer's own report instead, which adds what only drawing finds (texture decoding, shadows, hair,
continuity), with spec.py's verdict kept beside it, row by row: where spec.py fails a row its failures are listed (and
the renderer's own only where they say something else, as "(arview's renderer)"); a row the renderer could not check
after a refusal shows spec.py's failure; a row only the renderer fails is listed with a disagreement warning, which says
when spec.py's pass is only "unverified" (compressed geometry spec.py does not decode) and is left out when the renderer
refused the model. spec.py's own warnings stay listed; what only the server's Node-side check said is dropped (the
renderer has now done that check). Then the **rendered sha256**: the SHA-256 of the bytes the renderer parsed, the value
the WP2 ledger binds verdicts to (a difference from the served file is shown as an error). A report that cannot be read
or does not describe the page's model is an error line, never shown as clean. `window.__ar.servedReport()` and
`window.__ar.modelReport()` return both.

**Detached**: `--detach` builds in the foreground, then starts the server as a process of its own (Windows: through WMI,
`Win32_Process.Create`, so neither closing the terminal nor a job object such as an IDE run console or the Claude app's
preview takes it down; elsewhere a detached session), waits until it listens (a line every 10 s, at most 100 s; a server
that dies or never listens is reported with its log tail and killed), prints the address and returns. Its log is
`glasses3d/data/arview/serve/logs/serve-<port>-<time>.log`. Windows may run a detached process with power throttling
(EcoQoS); serving files is unaffected, the start-up check may take a little longer. `--stop` asks the server to stop with
the token from its state file, waits for its process to end, and kills it only if it answered as that very server (a pid
in a stale state file is never killed on the file's word).

`qa/serve.test.mjs` starts the real server on synthetic GLBs (port 0, a private state directory) and checks the headers,
the page, a built script, a public binary, the model with its sha256, the 404/405/403/421 answers and the localhost
redirect, the report endpoint's expected warnings, a second server on the same port, the page itself in headless
Chromium (opened as localhost; served report and sha256 before the camera, the renderer's report and rendered sha256
after, with a fake camera), a file re-exported while serving, an external buffer alone (rendered), present
`100%.png` and `../outside.png` resources (served, rendered, spec.py passes) and missing ones (404, refused, as spec.py),
the still-checking window (`ARVIEW_SERVE_TEST_CHECK_DELAY_MS`, test plumbing that holds the check back and says so in
the log and the report), `--detach`, `--status` and `--stop`. `qa/serve-report.test.mjs` checks the report and the
page's lines on the real Node-side check.

## Model loading and the report

Opening the page does not start the camera. arview has no frame catalog and no Studio bridge (WP1 stage B): the page
renders the one model handed over as `?model=<GLB address on this computer>&name=&clip=&width=&sha256=&lensenv=&report=`
(`src/eyewear/external.ts`, a `ModelDefinition` in `src/eyewear/catalog.ts`). Without a model the camera stays closed
and the page says how to hand one over. Every fallback the load takes (an unusable handover value, a missing temple,
a stripped retired descriptor, an arm without temple continuity, ...) is a warning in the renderer's model report
(`TryOnRenderer.report()`, `window.__ar.modelReport()`). The page lists, under the model name, the refusal reasons,
then every failed §2 row (`report().errors`, worded as spec.py's `errors`; most row failures write no warning of their
own), then the warnings and the fallbacks (`renderedReportLines` in `src/model/served-report.ts`, built on `reportLines`
in `src/model/report.ts`); a fallback taken while rendering joins the list when it happens.

Loading (WP1_DESIGN.md §1, §5): the WebGLRenderer exists before the GLB is parsed; `src/model/load.ts` reads the
container and JSON, resolves external resources against the model URL's directory (fetched once, parsed from those
bytes), and parses with Draco, meshopt and KTX2 (decoders from three's `examples/jsm/libs`, served at `/decoders/` by
`vite.config.ts`). `src/model/normalize.ts` is the one normalisation pass before any subsystem: transforms (one 1e-6
check; noise within it is baked), geometry, units (60-250 units = mm, 6-25 = cm: rescaled with a loud warning),
lens identification and the measured origin/orientation rows, ported from `glasses3d/g3d_host/spec.py` and tested to
agree with it (origin x is judged on the lenses' bounding box, the front region only when no lens spans x = 0; an
origin off by more than 1 mm is a loud `ORIGIN:` warning as well as a failed row). Orientation's up axis needs the
temples' depth: with both temples Y must be the model's thinnest axis; with a temple missing (a front alone is thinnest
along Z) it is judged on the lenses' own box instead, where Y must be the middle axis (wider than the lens depth, narrower
than the lens span; measured on 41 real GLBs: z 4.8-33.1 < y 35.1-65.1 < x 115.1-148.9 mm), so a temple-less +Y-up front
reports only its missing temples, while a Z-up model still fails ("Y is not the lenses' middle axis"). The lenses' place
in the front 30 % of the depth is not judged on a model under 60 mm deep (a warning), the +Y-up sign still comes from the
temple roots (hinge hardware counts there) and "+Y up not measured" is said whenever they are absent. A degenerate
(flat) triangle is one whose height over its longest edge is within 8 float32 steps of its own largest coordinate
(h <= 8 x 2^-23 x max|coordinate|, spec.py `DEGENERATE_FLOAT32_ULPS`): flat at the precision the file stores it in, never
an area in file units, so it scales with the model. r9's right screw head has 838 (506 of them exact zeros), the left
62, the crystal front 95; q001's lenses have 207 / 206 collinear slivers (edges 1.5-2.2 mm; the old absolute rule's
189 / 191 were thin triangles, h/L 0.0004-0.064, not well-shaped ones). The millimetre copy counts within 2 of its metre
original (it is rounded to float32 again: a triangle exactly at the threshold can flip). `tests/spec-agreement.test.ts` runs
both validators on the same bytes: about 140 GLBs built by the Python fabricator (`tests/spec_agreement_cases.py`
drives `glasses3d/tests/glbfab.py`) and the four real proof GLBs (`python -m glasses3d.g3d_host.spec --full`); every
row verdict and measured number must match, arview's status must be the expected one (rendered unless the case is a
listed §1 refusal), and each intended difference (refusals before parsing, millimetre files that arview rescales and
renders, whose post-rescale measurements must equal spec.py's on the same build in metres, Draco that only arview
decodes, three spec.py gaps) is listed there and asserted. An index accessor that is not
unsigned SCALAR (signed, VEC3, normalized, FLOAT) fails the Geometry row as in spec.py and is left out of the
measurements; its stored values (read raw, never denormalized) are rewritten as unsigned 32-bit SCALAR indices so WebGL
draws it, as is a valid index view with a byteStride, and a TRIANGLE_STRIP/FAN whose index is signed, FLOAT, normalized
or non-SCALAR is rebuilt from its stored values (three's loader would triangulate it as declared and wrap or truncate
the values into 16 bits), each a reported fallback (verified drawing in headless Chromium). A negative, fractional or
non-finite index, in any mode, names no vertex: out of range, a §1 Geometry refusal quoting the stored value. The parse
is raced against the abort signal and a 60 s deadline (`PARSE_TIMEOUT_MS`): a stalled decoder ends the load with a §2
Format refusal that says so; a decoder's rejection reason is kept whatever its shape (`describeError` in
`src/model/glb.ts`: three's DRACOLoader rejects with a plain message object). A load with no Web Worker (renderer-less
Node: `checkModel`, `qa/temple-cutoff.mjs`, the tests) cannot run the Draco decoder, so a file with Draco geometry ends
there up front with status `unchecked` (`ModelUnchecked`): not a §2 verdict, no refusal, the Format row as really
checked, the other rows "not checked", and a loud `NOT CHECKED HERE:` warning; check such files in the browser.
three is handed the default scene
alone (`defaultSceneDocument` in `src/model/glb.ts`): other scenes, nodes outside it, animations, cameras, lights and
skins are never parsed, so a broken reference there refuses nothing (each omission is a warning, the lights a
fallback; animations and declared skins fail the Geometry row from the original JSON, as spec.py). Only node, mesh,
accessor, buffer view and buffer references on that path are refused when broken; a broken material reference draws
with the glTF default material and a texture that cannot be loaded (its index or its image source broken) is left out
of its material, each a reported fallback with the Format row failed (spec.py fails it for the material and the texture
index; it does not check image sources: the one listed difference). A missing external resource is refused (used or
not, WP1_DESIGN.md §1); a primitive without POSITION fails the Format row and is not drawn. A §2 violation of the §1 table is refused (`ModelRefusal`, carrying the whole report); everything
else renders, each subsystem in its own try block, every fallback in `report().fallbacks` and `report().warnings`. The
report also has the seven §2 rows, measurements, `rendered_sha256` (the exact bytes parsed; a `&sha256=` handover is
only compared with it) and `live_site_would_fail`. Capture accessors: `cameraState()`, `assetMatrixWorld()`,
`parts()`, `projectAssetPoints()`, `templeCentrelines()`, `setLighting()`, and `render(mask, {override})` with `part-id`, `lens-mask`,
`lens-hidden`, `lens-only` or `normals`. `src/model/check.ts` runs the same load and pass without drawing (without a
renderer, KTX2 textures are a reported `textures` fallback); the QA temple-cutoff loader uses it.

Temples, clip and fit scale (WP1_DESIGN.md §3, §4; `src/render/temple-geometry.ts`): temples are found per side by
position, whatever their opacity (crystal and alpha-blended arms are temples like opaque ones; lens-class geometry never
is): the non-lens geometry at |x| > 45 mm behind the lens rear, or the `temple_left`/`temple_right` region of a valid
`LENSES_part_visibility` profile (the positional arm parts leave out front parts, see "Compact authored front-frame
parts" below). A side has a temple only when it reaches z <= -60 mm (the §2 Orientation rule), so
hinge hardware alone never counts. A side without one keeps a fixed cap (no arm shape, continuity, terminal return or
hair endpoint), is a `temple <side>` fallback, and the Orientation row fails with "temple <side> missing"; a model with
no temple at all still renders. Lens geometry behind -60 mm is a warning. The temple clip is derived from the arms'
tips, each side measured on its own: the one shared plane lies just inside the shorter arm, never behind it, and never
further back than -0.14 m, clamped to [-0.2, -0.045] m; a handed-over `&clip=` inside that range overrides it (and is
cross-checked against the tips), one outside it is ignored with a warning. The report records `temples`, `temple_clip`
and `fit_scale` measurements. Continuity is built from the normalised scene the renderer draws (no pin, no second
parse), per side: an arm with no cross-section somewhere before the clip loses only its own path, named with its z.
The live auto-fit's applied scale is reported, and a scale more than 3 % from 1 is a warning (with the 1.08 fit
preference that is the usual case); `TryOnRenderer.create(..., {fixedScale: true})` renders at the authored size.
The fixed arm spread (18 mm per arm at the clip, `src/render/rear-drop.ts`) pivots at the hinge: the first slice where
the arm collapses to a thin shaft (at most 15 mm tall for 20 mm). That boundary is the hinge only in front of the
proximal guard (15 mm behind the lens rear); a tall temple (a shield or wide acetate arm, such as the Prada PR17WS,
33 mm tall at z -40 and first under 15 mm at z -73) collapses far behind it and pivots at the guard plane instead
(`hingeZM` null, `shaftBoundaryZM` says where it collapsed). Before 2026-10-05 such an arm pivoted at -73 mm, its front
half stayed unspread inside the head proxy, and every turned view drew it only to the shield (as the Studio-era
runtime, ar/ 75ae133 + stash@{0}, also did: its own preview of the Prada delivery shows the same cut).

Look forward for a few seconds while automatic sizing collects consistent upper-face observations. The Fit panel
shows progress; glasses and their shadows appear only after calibration and size settling finish. Refit hides
them again until the new fit is ready. Later slider adjustments remain visible. The default includes an 8% wider visual fit; the size slider's zero
uses that fit. Size adjustment provides a small personal correction within the frame's supported size limits;
Refit starts a new calibration; closing and reopening the camera starts fresh. The page always includes fitting,
shadows and occlusion.

## Capture CLI (`arview capture`, WP1_DESIGN.md §6)

```bash
node qa/capture.mjs <model.glb> --poses default|junction|all --out <fresh dir> [options]   # or: npx arview capture ...
```

Options: `--backgrounds skin,dark,checker`, `--size WxH` (width <= 1280, default 1280x854), `--depth-cm N` (default
23, the prototype's), `--fixed-scale` (authored size instead of the live auto-fit), `--clip auto|<m>`, `--lensenv N`,
`--crop-upscale 1|2`, `--no-portrait`, `--live-probe`, `--step-timeout-s N` (5-90, default 60; model loads get 1.5x, at most 95 s). Exit codes: 0
captured and spec.py passes; 3 captured but the spec fails or the validator is unavailable (Astra is never blocked); 4
refused or unchecked by arview (nothing rendered); 1 infrastructure failure (stall, crash, software GPU, integrity); 2
usage error. Write outputs under `glasses3d/data/` (git-ignored); the output directory must be empty.

- **Poses** (`qa/capture/poses.ts`, Euler YXZ; yaw > 0 turns the nose toward image +x, so the asset's -X side is near
  the camera; pitch > 0 views from above): `default` = front, yaw ±30, yaw ±45, pitch ±20 on skin, beauty + part-id
  (14 images); `junction` = yaw ±30 × pitch ±20 on skin and dark, beauty + part-id, each full frame plus a crop around
  each side's lens-brow contact (36 images); `all` = both, on skin/dark/checker, plus normals, lens-hidden and lens-only
  (133 images). Every set adds the worn front portrait (5 images) unless `--no-portrait`. Files are
  `<pose>__<background>__<variant>.png`, `<pose>__<variant>.png` for the flat variants, `..__crop_<left|right>.png`,
  `portrait__{source,a,b}__{full,eyes}.png`; sides are named by geometry as spec.py names them (left = x < 0).
- **Synthetic face** (`qa/capture/synthetic-face.ts`): the canonical face (`../../ar/public/models/canonical-face.json`)
  posed at the requested angles and 23 cm, projected through the 63° MediaPipe camera; `FaceSurface.reconstruct`
  recovers it exactly (tested). A frontal settle (51 frames over 5 s) lets the live fit measure first; each pose then
  settles 30 frames. If the fit does not settle, the capture falls back to the authored size, with a warning.
- **Junction** (`qa/capture/junction.ts`): measured once on the normalised scene from triangle slices, never vertex
  bins or names: per side, 24 stations over the lens x-range trimmed 8 %, lens top vs the lowest non-lens point within
  3 mm below and 6 mm in depth (`report.geometry.lens_top_vs_brow`, overlap_mm > 0 seated, < 0 a gap; per frame part too,
  so a browline's eyewire groove and acetate brow read separately). A frame part riding on the lens surface (every
  vertex within 0.3 mm of a lens triangle and at most 2 % of the lens area: a decal such as r9's "Lens inscription",
  0.016 mm off) is not brow: it is left out of the reading and of the crop column metrics (read as the lens it sits on),
  listed in `lens_decals` and warned; q001's eyewire (up to 0.43 mm off, 9 % of the lens area) stays frame. The crop is the projected junction box
  (`projectAssetPoints`) plus 16 px; part-id crops get column metrics (contact / gap / lens-only per column, median gap,
  per-part pixels in the 3 mm band; `qa/capture/id-palette.ts`), beauty crops the old band metrics (white %, edges).
  The part-id colours (`src/render/override.ts` `partColour`) are a fixed farthest-point sequence on an sRGB grid: up to
  200 parts are at least 51 levels apart and 102 from the black background and the lens magenta. Each colour decodes
  within its OWN tolerance (below half the distance to its nearest other colour, at most 24; `id_palette[].decode_tolerance`),
  so a close pair shrinks only those two parts' tolerance and is a warning naming them.
- **Temple reach** (`qa/capture/temple-reach.ts`): every turned part-id render (|yaw| >= 20) projects the near arm's
  drawn centreline (`templeCentrelines()`: continuity stations with the fixed spread and terminal return) and records
  `images[].temple_reach`: how far back the arm is drawn (`drawn_to_z_mm`, a station counts when the arm's own part
  colour is within 1 px of its projected centre), where its fixed return behind the ear begins (`return_start_z_mm`)
  and its cap. An arm hidden by the face/head depth occluders more than 5 mm ahead of the return start is a `TEMPLE CUT`
  warning: the drawn arm lies inside the head proxy there. That is a shortfall at the end (`hidden_before_return_mm`) or
  a hidden stretch in the middle that is drawn again behind (`longest_hidden_run_mm`, from `longest_hidden_run_from_z_mm`:
  the cheek can hide the middle while the head shell lets the arm reappear). A cut ahead of the return start is never
  silent; behind it the arm is buried by design and not judged: `drawn_to_z_mm` can still lie behind the return start,
  because the curled return can project onto a nearer part of the same arm (the Prada PR17WS reads -113.7 mm at yaw
  ±30, its return folding back onto the shield, while the arm is seen to the return start at -75 mm), so such a depth is
  no evidence the arm is seen there; `behind_return_drawn_stations` counts those stations.
- **Portrait** (`qa/capture/portrait.ts`, the port of qa/portrait-preview): face-a.jpg only, read read-only from
  `../../ar/qa/fixtures` and pinned by sha256 (`qa/capture/portrait-contract.ts`); CPU face detection and hair
  segmentation, the SAME detection posed 51 times, condition a (runtime lighting) and b (`setLighting` side_studio
  0.8) with the hair mask. Model problems (continuity, fit, guard fallback) are warnings; the rendered-bytes identity
  check is a hard integrity failure. Images are unmirrored (the live page mirrors).
- **Runtime**: Vite on arview (publicDir `../../ar/public` read-only, cacheDir `node_modules/.vite-capture`, no watcher,
  dependencies pre-bundled, appType `mpa`) and headless Chromium with `--use-angle=d3d11 --enable-gpu --ignore-gpu-blocklist`.
  The model is served under `/__arview/models/` with the external resources it names, resolved as `arview serve`
  resolves them (`qa/capture/external-resources.ts`: read where spec.py reads them, `100%.png` and `../` included, a
  file outside the GLB's folder with a warning; served at the raw path arview requests); every other `/__arview/` path
  is a 404 (`routesMiddleware`), never Vite's SPA index.html, and so is the URL of every resource that cannot be read,
  wherever it resolves (a URI climbing out of the model's folder to `/package.json` got arview's own package.json with
  HTTP 200), so a missing external resource is missing to arview and the model is refused (§1 Format), as spec.py fails
  it (`qa/capture-refusal.test.mjs`). The live probe registers the same 404s. Should arview still render a model whose
  resource could not be read, the capture refuses it itself: `capture_refusal`, status `refused`, exit 4. The GPU
  renderer string is recorded and a software rasteriser (SwiftShader, WARP, llvmpipe) is refused before any model
  loads, and again for every renderer that draws (posed, portrait, and their authored-size reloads) from that
  renderer's own GPU string. The page (`qa/capture.html`, `qa/capture/page.ts`, renderer public API only) is driven
  ONE STEP PER page.evaluate, each with its own deadline, and every other await on Vite, Chromium or git has one too.
  Chromium is launched as a Playwright BrowserServer (`qa/capture/chromium.mjs`) so the capture holds its process: a
  missed deadline kills the whole tree (`taskkill /T /F`), confirms the root gone, records it in
  `report.chromium_process`, writes a partial report and exits 1 inside 2 minutes. Every stall path is listed in
  `qa/capture/cli.ts` `stallReportPathsMs` and tested below 120 s; the worst is a stalled connect: 95 s deadline, a 3 s
  kill inside `launchChromium` (whose outcome and pid the report carries), then at most 20 s of cleanup = 118 s. A
  stalled `--live-probe` step skips its graceful closes (the abort kills the tree and closes the probe's server).
  `qa/capture-stall.test.mjs` runs `main()` in-process through the test hook `ARVIEW_CAPTURE_TEST_STALL=<step>` (a page
  step, `chromium: connect`, and the probe's `live probe: load on main's ar/`) and checks the OS process table: no
  Chromium process is left whose parent is the capture or the killed root. PNGs stream to disk through the
  `arviewEmit` binding as they are made (sha256 each); every image (portrait ones included) joins report.images as soon
  as its step returns, so a partial report lists everything a completed step produced.
- **report.json** (`qa/capture/report.ts`, schema `arview.capture/1`): status, gpu, spec (`python -m
  glasses3d.g3d_host.spec --full`, the authority for the §2 rows, or a loud "VALIDATOR UNAVAILABLE"), runtime (the
  model report: warnings, fallbacks, lens identification, temples, units, clip, fit scale, rendered_sha256, its own §2
  rows and where they disagree with spec.py), live_site_would_fail (`static_would_fail` + `reasons` from spec.py's and
  arview's static rules, `runtime_verdict` / `runtime_would_fail` from the `--live-probe`, `would_fail` = either, and
  `verdicts_agree` / `contradiction`, a loud warning when main's runtime and the static rules disagree, e.g. q001: main
  loads it although "frame material 'Clear pads.001' is not fully opaque"), geometry.lens_top_vs_brow, poses (camera and asset matrices per pose), images [{file, pose,
  background, variant, sha256, ...}], id_palette, portrait, chromium_process, warnings, errors, timings,
  implementation (source hashes before/after). spec.py's `{pass, detail}` rows are compared row by row with arview's
  (`runtime.spec_disagreements`, `spec_rows_compared`, and `spec_rows_uncompared` with a warning for any row that has
  no verdict, including rows arview failed only as "not checked", e.g. after a Format refusal). Every model warning, error and fallback (the posed renderer's as `model: ...`, the portrait renderer's
  extra ones as `portrait model: ...`) is mirrored into the top-level warnings; any capture warning makes the status
  `captured_with_warnings`.
- **`--live-probe`** (`qa/capture/live-probe.mjs`): loads the GLB once on main's unmodified `ar/` (Vite root `ar/` with
  no config file, cacheDir `arview/node_modules/.vite-live`, a virtual probe page) through its own `?model=` handover
  and `LiveRenderer.create`, poses one frontal frame, and records main's exact text (e.g. r9: "The rear-drop start must
  be forward of the accepted cap."); it checks `git status` of ar/ and ar/node_modules before and after. Its server
  also runs with appType `mpa` and answers unrouted `/__arview_live/` paths 404, so main's page sees a missing resource
  as missing (main itself then loads such a model without the texture: three logs "Couldn't load texture").

## Frame pipeline

1. **Capture** (`src/pipeline/`): acquire a frame of at most 1280 pixels on its longest edge. Laptops default
   to VideoFrame capture; phones and tablets use canvas capture. Frame identity and a SHA-256 bind inference
   results to the captured pixels. The frame pump bounds ownership and overlaps inference with preparation.
2. **Inference** (`src/face/`, `src/hair/`): workers run the pinned MediaPipe face landmarker and hair segmenter
   on reduced inputs. The face detector tries CPU first. Hair uses a 640-pixel input; Apple phones and tablets
   default to the CPU hair delegate, while other devices use the capability probe.
3. **Attachment** (`src/render/`): reconstruct the observed face, retain a separate cheek-depth input, apply
   nasal shaping, smooth orientation/depth, and pin the bridge to the current nose landmarks. The default
   movement gains are 0.3 for rotation and 0.8 for depth, with a 3 Hz velocity filter and 1 Hz resting cutoffs.
   Image-plane attachment is not given another smoothing delay.
   **Sizing** (`src/render/eyewear-fit.ts`) compares upper-face proportions with the frame front in a shared virtual
   reference view, removing initial camera distance and head rotation from the observations. Each model has a
   preferred front-to-face proportion. A robust estimate requires both
   sufficient samples and stable observation time, eases into a bounded uniform scale around the bridge, then
   stays locked through head movement and tracking loss until Refit or a new camera session. Calibration stores
   only rendering proportions in memory; it does not measure physical face dimensions.
4. **Temple visibility**: project the fixed arm geometry, update each hair endpoint once per frame, and
   combine head depth, observed cheek depth and the current hair mask. Audit renders reuse that same state.
5. **Face shadows** (`src/render/eyewear-shadow.ts`): project soft frame shadows and colored lens transmission
   onto the observed face. Lens tint and gradients come from the loaded material, including its texture UV set.
   A camera-relative upper light approximates soft studio lighting; it does not estimate the room's lighting.
   The shadowed camera underlay stays on the GPU and is shared by the lenses, temple fades and hair composition.
   The Shadows controls change frame strength, lens tint and softness without restarting the camera.
   Defaults are 16% frame strength, 18% lens tint and 1.9× softness.
6. **GPU composition**: render directly to the visible canvas, with stencil protection for the optical and
   nasal regions. Hair blending stays inside the editable arm corridors. Explicitly authored front parts
   receive no hair-mask shader, while actual face depth remains active. Untagged models retain the legacy behavior.
   Camera image pixels stay on the GPU
   during live use; an explicit audit can separately read the rendered image.
7. **Profiling**: collect numeric timings and frame age. Images, detections, masks and hashes are excluded
   from timing reports.

On laptops every frame waits for its own hair mask. Phones and tablets normally segment every second frame
and reuse the latest sufficiently recent mask, transformed with head movement. Reuse supplies no new
permission to reveal a hidden temple. A stalled or unavailable mask does not release a held endpoint.

## Fixed temples and occlusion

ar/'s catalog models (not served by arview) use an authored 18 mm outward splay. An inward return begins at model-local z = -75 mm
and buries the terminal band inside the canonical head volume. Its containment is recalculated when the uniform
eyewear size changes; the front and lens proportions are preserved. After fitting, shape does not bend with
head angle or camera distance, and the system does not try to fit the tips around an ear. Drawn geometry,
optical protection, hair paths and shadow coverage all use the same fitted asset scale.

A closed posterior head shell joins the canonical face perimeter to the head proxy. Canonical lateral depth,
attached to the glasses pose, prevents asymmetric landmark shape changes from cutting one arm earlier
while nodding. The central face and nose retain observed shape. A bounded bilateral head-width calibration
adjusts the anterior occluder independently of eyewear sizing or the terminal volume.

The observed cheek has its own depth pass, independent of the stable main head surface. Posterior arm pixels
in front of that cheek remain visible; pixels behind it blend toward the paired camera image. A guarded
optical front, native fragment depth and exclusion from the physical lenses' transmission input prevent
inner temples from appearing across the eyes. The cheek transition begins 1 mm behind the surface and
widens toward two render pixels, bounded to full concealment by 6 mm. A 45 ms head-local shape filter limits
cheek depth correction to 0.75 mm and preserves the current pose and image rays.

Hair ending follows the **first resolved crossing from hinge to tip**, separately for each arm. Once that
crossing hides the shaft, every section behind it remains hidden, including gaps between later hair patches.
The fade fits inside the supported hair interval. A newly earlier crossing hides the rear immediately;
lengthening requires distinct fresh clear observations and proceeds at a bounded rate. Warped reuse can
hide more but cannot accumulate release confidence. Missing or uncertain masks hold the previous endpoint.
Brief fringe changes in a reused mask preserve recent fresh clearance evidence without extending the arm;
missing, invalid or expired evidence restarts the dwell. Light edge contacts with a clear shaft centre do not
pin an old cutoff, even when no later hair patch is detected. Centre or thick obstructions still block release.
A small two-pixel tent filter softens the rendered hair edge without changing category evidence.

Shadows use the separately observed face, before nasal/head-occluder reshaping. They disappear with tracking
loss and in the clean-camera audit view. Hair masks protect foreground hair from skin shadows when available.
Shadow lookup coordinates use a filtered local face shape attached to the same pose as the glasses, while
the receiver still rasterizes the current observed face. This stabilizes depth without delaying head motion
or filtering camera pixels. A fixed light frustum, texel-aligned sampling grid and continuous depth/edge
filtering reduce shimmer as the head turns. Tracking loss clears the local-shape history.
The light's color filtering is an appearance approximation, not a measured optical simulation or prescription
lens model. Clear lenses cast a much weaker neutral shadow than tinted lenses.

The model dimensions and learned face depth are rendering estimates, not anatomical measurements.
Regression tests protect these rules; live appearance still depends on tracking and segmentation quality.

## Lens and crystal materials

arview draws every lens through its own glTF material (native lenses). The retired canonical lens descriptor
(`LENSES_lens_appearance`, written only by the retired bsa/reconstruction routes) has no runtime here: a material that
still carries it is stripped at load, reported as a warning, and drawn as its native glTF material. Translucent (crystal)
frame materials reflect an unblurred see-through room: three's `RoomEnvironment` without its +Z panel, which sits behind
the selfie camera and clipped to a flat white rectangle. Native lenses keep the scene room (with `?lensenv`, they own that
room at 0.8 × lensenv). A total mirror remains a lens in geometry, fitting, visibility and hair consumers.

Lens identification (WP1_DESIGN.md §2, `src/eyewear/lens-identification.ts`, the same order and shared test vectors
as `glasses3d/g3d_host/lens_id.py`) decides every material's class once at load, from the glTF JSON (never three's
merged userData), per use (node x primitive):
1. extras on the material, then mesh, then node: `partRole` equal to `lens`, or legacy `part_role` starting with
   `lens`; any other explicit role (frame, temple, hardware, temple_left, ...) is not a lens;
2. a whole-word name token `lens` / `lenses` / `glass` (never `glasses`) in the material or node name, unless either
   name marks an etch, lettering, inscription or print;
3. only when nothing in the file is a lens by rules 1-2: transmission > 0 or alpha-blended alpha < 1, as a warned
   last resort.
The class is never re-derived from live transmission, so a transmissive frame or temple (crystal) stays frame. A
material used by lens and frame primitives is cloned per class (both render) and fails the §2 Components row; a
model with no identified lens is refused. Every material, its class, deciding signal and uses are in the renderer's
`lensIdentification()` block; every fallback is a model-report warning. Native lenses retain their authored geometry
and physical material.

Compact authored front-frame parts keep their inner rim and hinge hardware in the lens input. The temple-only
visibility rules do not apply to those parts, even when the frame and arms share a glTF material. Eligibility requires
an explicit `frame` role (`partRole` or legacy `part_role`) and geometry wholly inside the protected front region
(local Z at least -30 mm), with baked transforms. On the positional path (no usable `LENSES_part_visibility`) an
untagged compact part (wholly at local Z >= -30 mm) whose X extent reaches the central band |x| <= 45 mm (a front
crossing x = 0, a rim or brow half: no temple lies there) is front too (`isPositionalFrontFrame`), so a metadata-free copy
draws its front as the authored one does: the Prada PR17WS stripped of every name and extras rendered its front's
outer back as arm geometry through the acetate it shares with the temples, and the cream inner bevel at the outer lens
corners vanished from the lens see-through (portrait: 4,562 px, up to 146 levels; now byte-identical except 3 of 5 hinge
knuckles per side seen through the far lens at yaw 45, which the authored copy calls front and no geometry rule can
tell from the other two: 126-134 px, at most 13 levels; `qa/capture-positional-parity.test.mjs`). An explicit role
other than `frame` bars it. A long part that holds front AND temple (one mesh, Amber Horizon) keeps the geometric arm
rules, and the temple inventory warns that its front's outer back is drawn with the arm treatment. Ordinary face depth
still applies; this exemption does not draw a frame through the wearer's head.

New exports can provide explicit node extras `LENSES_part_visibility: {version: 1, region: ...}` with
`front`, `temple_left` (native negative X), `temple_right` (positive X), or `lens`. This opt-in contract must cover
every mesh primitive, agree with the identified class (region `lens` on lens parts only), and contain static
geometry with baked authored transforms. Only an immediate glTF primitive Group may supply a part's profile;
model-wide metadata does not substitute for ownership. The regions are optional metadata: malformed, partial, or
conflicting profiles are ignored for the whole asset, recorded as a `part visibility` fallback listing every problem (unbaked transforms
and non-static geometry are still refused, once, by the load-time §2 check in `src/model/normalize.ts`).
Unprofiled assets keep the legacy behavior above; old whole-body `frame` tags are never automatically upgraded.
Sides are judged by geometry: a `temple_left` region must lie at x < 0 and `temple_right` at x > 0 (every consumer maps
`temple_left` to the negative-X side), otherwise the profiles are ignored for the whole asset and position decides.

For profiled assets, front pieces and their hardware remain fixed and survive all arm-specific clipping,
deformation, lens-input filtering, and shadow trimming regardless of wrap depth. Explicit arms supply continuity
and fitting geometry independently of color or transmission. Shared materials and geometry are separated by
ownership policy without changing their PBR values or source buffers. Normal face depth still occludes the front.
A binary hair category cannot tell whether hair is in front of the frame or behind it, so it never erases explicit
front parts or their hardware. True bangs over the front would require additional depth/order evidence that this
pipeline does not have. This preserves temple hair behavior without claiming perfect front-hair ordering and
does not relax the canonical lens profile's existing alpha/material restrictions. Alpha-blended native arms
retain ordinary depth behavior; they are not converted into opaque depth-relief overlays.

Crystal frame materials with opaque alpha mode use a separate, sharp look-through capture of the camera and opaque
hardware, including the near-arm visibility overlays. Lenses and crystal sampling materials are excluded from that
capture. On the canvas, the crystal samples it; inside either the explicit canonical or Three's native lens input,
the crystal instead samples the paired background. The two sources prevent the lens arm-exclusion rule from erasing
an internal temple core, while keeping far-arm hardware out of the lenses. Hair occlusion, arm clipping, continuity,
rear drop and face depth remain active. This straight look-through approximates crystal transmission without refraction
offsets or roughness blur. The crystal twin loses the surface's reflected share and limits its reflection, clearcoat
included, with the crystal's knee (half the headroom). Alpha-blended temples are not covered by this look-through path;
they are still temples (shaped, clipped, followed by continuity, hair-occluded), but their near-arm overlay stays hidden
because a blended overlay is not built.
Volume absorption distances
(`KHR_materials_volume`, metres in the file) are converted once to the scene's centimetres (`src/render/eyewear-volume.ts`),
since Three scales the thickness through the model matrix but reads the distance in world units; the shadow keeps the
authored ratio. No shipped asset carries a volume extension.

## Audit and diagnostics

**Hold & audit** keeps one frame, renders it with/without hair and without eyewear, and checks that hair edits
preserve the optical region, nose, background and pixels outside the editable corridors. All four equality checks
remain strict: a geometric front/hair-mask overlap does not authorize a protected pixel change. It records endpoint,
fixed-return and head-fit metadata, plus lossless images and difference maps. The CPU binary-mask baseline
is a diagnostic reference; it does not reproduce GPU edge feathering. Reused masks skip that reference
comparison because it requires a frame's own mask. Audit images stay local unless the user downloads them.
An additional shadows-off view and difference map record the effect on that exact same held frame; hair-on
and hair-off views share the same shadow underlay, retaining all four existing protection checks.

`?diag=1` makes the page post numeric startup and timing reports (no images, landmarks, masks or hashes) to the same
site's `diagnostic` endpoint. Only the live site (`UI/ar_site.py`) has one; arview's loopback servers do not (`arview
serve` answers 405), so nothing is recorded. Unknown address options are shown on the page as ignored.

Useful diagnostic options are defined in `src/config.ts`:

| Option | Default / purpose |
|---|---|
| `capture`, `source` | 1280-pixel maximum; VideoFrame on laptops, canvas on mobile |
| `face` | CPU first; `gpu` reverses the fallback order |
| `hairinput`, `hairdelegate` | 640-pixel input; auto delegate except CPU on Apple mobile |
| `hairframes`, `hairmove`, `hairmaxage` | Device-specific mask scheduling, motion-triggered refresh and reuse age |
| `hairwait` | 120 ms worker-stall limit where a frame waits for its own mask |
| `hairModel`, `hair` | Initial hair model and local hair-occlusion toggle |
| `steady`, `steadyhz`, `steadybeta`, `steadydepthhz`, `steadydepthbeta` | Pose filter diagnostics |
| `guard`, `sync`, `hairz`, `exposure` | Render/camera diagnostics; normal defaults retain protection and GPU pacing |
| `diag` | Same-origin numeric telemetry, off by default (no endpoint under arview) |
| `model`, `name`, `clip`, `width`, `sha256`, `lensenv`, `report` | Validated model handover through `src/eyewear/external.ts`; `lensenv` (0.3-4) scales the lens environment reflection; `report` is the model report `arview serve` made |

The page's Measure panel (`window.__ar.measure()`) runs fresh camera sessions and reports their rates. The ar/ QA
gates tied to the live site's shipped catalog frames (`qa/measure.mjs`, `pipeline-smoke`, `shadow-smoke`,
`shadow-stability`, `fit-smoke`) and the retired canonical-lens and optical-group harnesses were not ported
(`plan/PORT_REVIEW.md` §7); the webcam-page proof is `qa/serve.test.mjs` (face-a streamed as the camera through the
normal page). [qa/README.md](qa/README.md) indexes the QA tools and tests.

## Repository rules

arview is source only: it is never deployed and never publishes (`ar/` keeps its own `npm run publish` and `site/`).
It never writes into `../../ar/` (`npm run assets` only verifies the pinned public binaries there). Keep its package
manifests, assets and tests inside `glasses3d/arview/`; write every output (captures, serve builds, state, logs) under
`glasses3d/data/` (git-ignored). Never commit GLBs, renders, photos, recordings or `.env` files; `node_modules/` and
`dist/` are ignored.
