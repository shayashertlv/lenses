# modeler.agentic: one durable author conversation per product

`python -m modeler.agentic` turns product photographs (and optional stated dimensions) into a glasses GLB for the AR
try-on through **one continuing hosted Astra conversation** (gpt-6-astra, the Responses API): the author inspects the
photos and the host's measurements, writes and revises a Blender construction program through strict tools, receives
the images and errors its builds produce, and delivers one compatible asset with an honest status. The legacy loop
(`python -m modeler.job`) keeps working unchanged; this route consolidates its construction, export, observation and
evaluation code under a durable, budgeted, recoverable session. Built 2026-09-27 from the working-tree audit in
`reviews/agentic/20260927-working-tree-review/` (REVIEW.md, AGENTIC_DESIGN.md, CLAUDE_IMPLEMENTATION_PROMPT.md; kept
locally, not in Git).

What is real and what is not, in one line each:

* **Real:** the state machine, budget gateway, transport capture, tool registry, sealed evidence boundary, the fake and
  native-fixture workers, the Docker adapter and its doctor, the offline demo and the acceptance tests.
* **Verified:** two paid runs (test-pilot-001 and test-pilot-002, 2026-09-28) delivered compatible assets; the Linux
  Docker worker image was built on this machine and its doctor self-test passed on 2026-09-28 (see Worker setup).
* **Not verified:** live compaction cost/behaviour (no run has reached the compaction threshold) and any claim about
  unseen-product fidelity.

## Layout

| Module | Owns |
|---|---|
| `pricing.py` | The frozen Astra tariff (standard tier, 2026-09-27) and integer micro-USD arithmetic that always rounds up. |
| `state.py` | `job.sqlite3`: WAL, `synchronous=FULL`, foreign keys, `BEGIN IMMEDIATE` transactions, legal state transitions, a single-runner lease with a fencing token, every record type (artifacts, revisions, epochs and conversation items, inference requests, tool operations, observation queue, reservations, verdicts, events, settings). |
| `artifacts.py` | Immutable content-addressed files under `artifacts/` (author-visible), `sealed/` (held-out evidence, host only), `host/`, `worker/`, `synthetic/`; host-enforced access roles; reparse-point and containment checks. |
| `budget.py` | One transactional gateway for author, intake, critic, final evaluator and compaction: reserve the worst case before a request, settle once from usage, keep unknown outcomes as liability, release only what was provably never sent. |
| `responses.py` | Exact payload construction (`store:false`, full window replay, strict tools, `parallel_tool_calls:false`, explicit `max_output_tokens`, explicit prompt caching: `CACHE_MODES` none / explicit_one_breakpoint (every job before 2026-09-28) / explicit_rolling (the default of new jobs, a marker on every input carrier)), the transports (HTTPS without retries, scripted, refusing), strict parsing of every output item, input-token counting and compaction bodies. The count mirrors every field of the inference payload the counting endpoint accepts (model, input, tools, tool_choice, parallel_tool_calls, reasoning); store, include, max_output_tokens, service_tier and the prompt-cache options are not part of the count. |
| `tools.py` | The strict versioned tool registry (`agentic_tools_v1`) and the adapters to `modeler.export`, `bsa.contract`, `modeler.observe`, `bsa.archeck`; revisions, builds, renders, measurements, selection, critic, delivery; the observation queue. A build reply (and inspect_scene) carries the exporter receipt's audit flags and notes under `export.audit` whenever the receipt has one. Every id and path a worker result names is validated against the operation's own request and the ingested files before the host touches it (renders are `<id>.png` beside result.json, the harness layout). |
| `executor.py` | The worker protocol: immutable operation bundles, ingestion of bounded regular files only (directory or tar stream), `FakeWorker`, `NativeFixtureWorker` (allow-listed fixtures only), `DockerWorker`, the doctor. Containers start with an explicit `--entrypoint` (python3 for jobs, blender for the doctor's version probe); entry.py refuses any argument list other than `<bundle>/operation.json /work/out`. The output lives on the container's tmpfs, which `docker cp` cannot read (verified 2026-09-28 on Docker Desktop: "Could not find the file" for a file that exists), so READY.json and the output folder are streamed by `tar` inside the container over `docker exec`. |
| `evaluation.py` | Sealed reservation by decoded pixels, the author-facing critic, the sealed final evaluator, verdict axes, byte-bound delivery, append-only owner verdicts. `accepted` additionally requires the wearer renders of the exact candidate (every pose on disk, no harness error, a runtime-compatible pass and a valid AR report in the observation) and calibration coverage of every tag of the delivered asset (`modeler.tags`); without wearer evidence no final request is bought and the visual axis is `unmeasured`. The axis records tags, uncovered_tags, calibration_coverage and wearer_evidence. |
| `config.py` | Request translation (legacy request files become provenance; paid switches and credentials are refused) and the validated effective policy; exit codes. |
| `runner.py` | `Session`: create, run, reconcile, request_once, handle_response, execute_pending_operations, maybe_prune_images, maybe_compact, finalize, finalize_fallback, cancel_now; the owner review: await_owner, owner_accept, owner_request_changes, owner_stop, owner_continue_in_new_job. Recovery contract: prepared requests released, sent-without-response unknown (never re-posted), completed responses of the current epoch replayed once, running operations interrupted with one output (a critic verdict recorded before the crash is reused), fallback and delivery intents recorded before their transitions, images acknowledged only by the completed request that carried them. |
| `cli.py` | `doctor`, `start`, `resume`, `status`, `cancel`, `demo`, `owner-review`, `owner-verdict`, `reconcile-unknown`, `rebuild`. |
| `review.py` | The owner review loop's pieces outside the state machine: the related measurements, the runtime-limited flags stated plainly, the owner's change message, the try-on link data, the continuation summary (see Owner review loop). |
| `rebuild.py` | The no-inference rebuild of one revision's sealed program with the current library, harness and exporter into a preview folder (see below). |
| `AUTHOR_PROMPT.md` | The stable developer instructions (sent as the first input message on every request). |
| `worker/Dockerfile`, `worker/entry.py` | The pinned Linux Blender image and its trusted PID-1 supervisor. |
| `demo.py`, `selftest.py` | The offline scripted scenario on synthetic inputs; the mutating worker self-test. |
| `examples/` | `make_owner_review_example.py` and the two scripted rounds it writes from test-pilot-002 r0006 (`owner_review_round1.json`, `owner_review_round2.json`). |

## Run

```powershell
cd automation                                                             # from the repository root
python -m modeler.agentic doctor                                         # read-only environment report; never reads a credential
python -m modeler.agentic demo --output data\modeler\agentic\demo-001 --worker fake
python -m modeler.agentic status --job data\modeler\agentic\demo-001 --json
```

The demo generates four synthetic "photos" (one held out, magenta so a leak would show), runs a scripted author on the
synthetic worker (a deliberately failing first build, a repair, image batching, a critic call, selection, delivery)
and ends with state `unresolved`, stop reason `synthetic_demo_complete`, `deliverable_status: synthetic_only` and no
`model.glb`: it proves the orchestration, never a model.

`start` runs a real request:

```powershell
python -m modeler.agentic start --request data\modeler\requests\tomford_ft1123d.json --output data\modeler\agentic\tf-scripted-001 `
    --driver scripted --script answers.json --worker native-fixture --fixture-program-sha256 <hash> --intake code --critic scripted --final-evaluator scripted
```

* `--driver scripted --script PATH`: `{"steps": [...], "fake_scenario": {...}}`; each step is a complete Responses body
  (see `demo.demo_script`) with optional expectations on what the runner must have sent. No network.
* `--driver responses`: paid. Requires `--allow-paid`, `--budget-usd`, `--max-inference-requests`, `--max-output-tokens`,
  `--max-revisions`, `--max-worker-seconds`, a credential source (`--env .env`, variable `OPENAI_API_KEY`, read only after
  every other validation passed) and a worker that is not synthetic. `--region global|us|eu` (eu: +10 %) and
  `--api-key-env NAME` (default `OPENAI_API_KEY`) select the endpoint pricing and the credential variable.
* `--intake code|scripted|synthetic|none` (default `code`): `code` measures the photos; `scripted` (`--intake-script
  FILE`) replays saved vision answers offline; `synthetic` is for the fake worker only. The paid Astra intake stage is not
  wired into this route: `--intake astra` is refused up front (exit 2) with that reason.
* `--critic none|scripted|responses` and `--final-evaluator none|scripted|responses` (default `none`): `responses` is
  paid and needs `--driver responses --allow-paid`.
* `--cache-mode none|explicit_one_breakpoint|explicit_rolling` (default `explicit_rolling`); `--image-prune-tokens N`
  (default: the compaction threshold minus 40,000, never below 20,000; `0` never prunes) opens an image prune epoch when an
  author request counted that many input tokens.
* Owner revision: `--seed-revision JOB_DIR:rNNNN` (that revision's sealed program, verified against its build bundle, built
  before the first author turn) or `--seed-program DIR`, `--owner-instruction TEXT` (quoted verbatim to the author, at most
  8,000 characters) and `--editable-modules a,b` (the only modules edit_program may change; needs a seed).
* `--no-owner-review`: deliver on the author's request_delivery as before 2026-09-28; by default a new job waits for the
  owner's live review (see Owner review loop). `--final-on-accept` runs the configured `--final-evaluator` on the accepted
  candidate (off by default under the owner review); `--review-resume-token-limit` (default 150,000).
* `--worker fake|docker|native-fixture`: synthetic; the isolated Linux image (needs `--worker-config` with the real
  digest and a passing doctor record); or the host Blender restricted to allow-listed program hashes (tests only).
* Limits: `--wall-minutes` (default 60), `--images-per-request` (default 14), `--compact-threshold-tokens` (default
  200,000), `--compact-output-bound-tokens` (paid compaction is refused until the compact endpoint's output bound is
  verified and given here), `--effort`, `--no-ar`, `--seed-program DIR` (an explicit import recorded as a seed).
* `resume --job PATH [--acknowledge-attention]`: reconciles first (released / unknown / replayed / interrupted), never
  resets caps, never re-posts an unknown request; a new `--worker-config` whose doctor passes re-pins the job (recorded).
  On a terminal or waiting job (`awaiting_owner`, `needs_attention` without `--acknowledge-attention`) it only writes the
  owner's pending calibration rows, if any, and changes nothing else.
* `cancel --job PATH`: persists the intent; a live runner stops at its next step; without one, owned containers are
  stopped and the job is marked cancelled.
* `owner-verdict --job PATH --verdict accept|borderline|reject --medium "..." [--sha256 ...]`: rehashes the delivered
  bytes; append-only; a later reject or borderline revokes an acceptance.
* `reconcile-unknown --job PATH --request qNNNN --authorized-by "..." (--no-charge | --usage-json FILE)`: after a request
  with an unknown outcome (timeout, crash after send, 5xx), the owner checks the provider dashboard and settles the
  liability with the usage it shows or an explicit no-charge statement; the request row is closed in the same
  transaction and never re-posted. `resume --acknowledge-attention` then continues with a new request. `status` lists such
  requests under `unknown_requests` whatever their HTTP outcome (a timeout, a 5xx, a 200 without a usage block): the
  liability is the reservation's.
* Lease: one runner per job, a 15-minute lease renewed between steps and while a worker runs. A `resume` within the
  lease of a runner that crashed is refused until the lease expires; `cancel` without a live runner takes its own fence
  so a runner whose lease lapsed mid-tool can commit nothing more. Every tool output and every inference (author,
  compaction, critic, final) is committed or sent only under the runner's current fence, re-checked inside the
  transaction. No paid send happens while a request with an unknown liability is unreconciled: the job stops in
  `needs_attention` (`inference_unknown`) after committing the tool output that reported it.

Exit codes (this route only): `0` normal completion (delivered or unresolved alike), `2` invalid CLI/config, `3`
execution/setup/unknown-outcome failure (`failed`, `needs_attention`), `4` budget exhausted, `130` cancelled. The stop
reason wins over an attached fallback asset.

## The job folder

```
job.sqlite3                  the authoritative state (see state.py)
artifacts/{photo,render,crop,sheet,log}/   author-visible immutable files  <id>__<sha12>.<ext>
sealed/photo/, sealed/render/               held-out photos and their renders: host only
host/requests/<qid>/{payload,response}.json exact request and raw response bytes (credential never present)
host/{program,glb,sheet,render,...}/       host-only artifacts by kind: programs, exported GLBs, final sheets, single renders
evidence/                                   the intake's measurements and the author-visible reduction (author_visible.json)
synthetic/                                  everything a fake worker produced
worker/<op>/{bundle,work,staging}/          operation bundles and ingested outputs
revisions/<rid>/{program,build,observe,heldout}/ model.glb export.json   the working copies observe.py expects
review/round-NN/{model.glb,manifest.json}, review/candidate.json   the owner review's candidates (see Owner review loop)
deliverable/{model.glb,manifest.json,report.md,receipts.json,images/}
```

`report.md` is rewritten with the manifest once the job reaches its final state (and again by `owner-verdict`); until
2026-09-28 it was written during `evaluating` and never updated, so test-pilot-002's report still says `evaluating`.

`status --json` and `deliverable/manifest.json` are exports of the database, never a second store.

Storage (measured on test-pilot-002, 217.7 MB): `host/requests` 97.2 MB (every payload re-embeds every image as base64),
`job.sqlite3` 26.5 MB (base64 images in `conversation_items`), `revisions/*/observe` 25.4 MB, `artifacts` 14.9 MB,
`worker/*/staging` 9.9 MB, `worker/*/bundle` 7.7 MB. 50.8 MB of the files are byte-identical copies of other files in the job
(19.0 MB observe renders also stored as artifacts, host renders or staging files; 9.8 MB staging copies of ingested outputs;
7.4 MB `revisions/*/model.glb` copies of `host/glb`; 7.4 MB in bundles, of which 5.5 MB are render-only copies of the revision's
`.blend` and 1.1 MB the library and harness). The large items are the request archive and the conversation items (runner / state: store
images by artifact id and sha and rebuild the exact bytes on demand); staging can be dropped once its output is committed.

## Rebuild a revision with the current library (no inference)

A library, harness, exporter or AR runtime fix reaches a delivered program only through a rebuild of that exact program.
`rebuild` does it without an author, a request or a budget:

```powershell
python -m modeler.agentic rebuild --job data/modeler/agentic/test-pilot-002 --revision r0006 --output data/modeler/agentic/test-pilot-002-r0006-rebuild-001 `
    --worker-config data/modeler/agentic/selftest-002/worker-config.passed.json
```

* The source job is only read: its `job.sqlite3` (and WAL) are copied into a scratch folder and opened there (a read-only
  SQLite open still writes the source's `-shm`), everything else is read in place. Nothing in the source job changes.
* The program is the revision's exact sealed set: the modules of its build operation's bundle (`worker/<op>/bundle`), each
  verified against the revision row's sha256, the bundle's `operation.json` `program_set_sha256` and the revision's working
  copy. A tampered or missing module, a revision that was never built (no bundle) or a bundle bound to another program set is
  refused, as are a non-empty `--output` and an output inside the source job.
* The build runs through the same path as a normal build (`tools.build_revision`: bundle, worker, ingestion, export, contract,
  observation with the AR harness) inside a shadow job under `<output>/work` (its own database with zero caps: no request, no
  reservation), then the wearer poses in the actual AR runtime. `--worker docker` (the default) needs a config whose doctor
  self-test passed for the CURRENT library and harness: the doctor record is bound to their bytes, so after a library change run
  `doctor --self-test` into a fresh folder first; the rebuild refuses a blocking doctor. `--worker fake` proves the command only
  (synthetic, no model).
* The preview folder: `model.glb` (byte-bound), `manifest.json` (kind `preview_rebuild`: source job / revision / program sha /
  bundle, library and harness hashes against the source bundle's, fingerprints, worker, build result, exporter and contract
  result with the export audit, compatibility, observation summary, wearer binding, width), `sheets/` (the observation sheets
  and the wearer sheets) and `work/` (the shadow job). The GLB's own extras name the shadow revision (`r0001` of job `work`); the
  manifest binds it to the source.
* `changes` in the manifest names every component that differs from the source build, so an owner verdict on the preview is
  attributed to the right code (test-pilot-002's preview said only `glasses_lib.py` although 13 host files, lens_colour's basis
  and the AR runtime had changed too): `library` (bundle lib files), `host_python` (per file against the source job's
  `fingerprints.python_sources`: changed / added / removed), `ar_runtime` (the ar/src digest and file count), `measurement` (per
  file against the source bundle's measurement fingerprint, see Provenance; `null` with a note for a bundle written before
  2026-09-28) and `components` (the ones that moved). The first note lists the same files in prose. `library.changed` keeps its
  shape. `measurement_fingerprint` is the rebuild's own record.
* Exit 0 with a preview GLB, 3 when the build produced none (the manifest says why), 2 on a refusal.
* `python -m modeler.tryon` lists a preview folder under `data/modeler/agentic/` beside the deliveries as "rebuild of <job>
  <rid> with the current library, not a delivery" (dashed link). It is never a delivery: the source's manifest, verdicts and
  deliverable stay as they were, and an owner verdict on the preview is a note, not `owner-verdict` on the job.

## Provenance: what built a revision and what measured it

Four records, each bound to different bytes:

* `lib_sha256` in every bundle's `operation.json`: the helper library and Blender harness the worker ran (what BUILDS).
* The doctor record (`worker_config_fingerprint`): the worker config, image ID, harness and library. A change to any of them
  demands a new self-test; host-only code never does.
* `fingerprints.python_sources` on the job (`cli.source_fingerprints`): per-file hashes of this package, the modeler modules,
  the library and `bsa/{contract,export,archeck}.py`, plus the aggregate ar/src digest.
* `measurement_fingerprint` in every bundle's `operation.json` (since 2026-09-28; `executor.measurement_fingerprint`): what
  MEASURES the output. It holds per-file hashes of the module-level import closure (inside this repository) of observe, evaluate,
  see_through, lens_colour, the modeler exporter, `bsa.cameras`, `bsa.archeck`, `bsa.tryon`, `reconstruction.mesh` and
  `qa.provider_comparison` (24 files today, among them `bsa/raster.py`, `front.py`, `lens.py`, `intake.py` and `core.py`,
  none of which test-pilot-002 recorded), plus the AR harness page (`ar/qa/provider-comparison*.{mjs,html}`, `ar/package.json`,
  `ar/package-lock.json`) and every `ar/src/**/*.ts`. Every build and render bundle carries it, so every revision's build records
  it. A rebuild diffs it per file (`changes.measurement`). It is not part of the doctor fingerprint, because it runs on the host.

The worker's receipt (`READY.json`, untrusted, kept in `worker/<op>/staging/`) and the operation's recorded outcome also carry
`gl` (which GL renderer drew the renders: renderer, vendor, version, backend, device, and how many EGL_BAD_MATCH lines were
removed, see Worker setup) and `fonts` (the image's font inventory, path to sha256).

## Money

Micro-USD integers, rounded up. A request reserves `input_tokens × cache-write rate + max_output_tokens × output rate`
(×2 / ×1.5 above 272,000 input tokens, +10 % for an EU endpoint) after counting the exact input with
`POST /v1/responses/input_tokens` on the same model, input, tools, tool_choice, parallel_tool_calls and reasoning the request will carry; without an exact count nothing is sent. `settled + unknown liability + held + new ≤
cap` and the inference-operation cap are enforced in one SQLite transaction shared by every role and every process.
A response settles from its validated usage partitions (uncached / cached / cache-write / output); a 4xx settles at
zero; a timeout, a crash after send, a 5xx or a malformed usage block leaves the whole reservation counted until the
owner reconciles it against the provider dashboard (`Budget.reconcile_unknown`). This is a conservative execution
bound under the frozen tariff, not an invoice. Paid compaction reserves the configured output bound; without one it
is refused and the job checkpoints.

## Evidence boundary

Held-out photos are sealed by decoded pixels before anything else runs: a re-encoded copy or a declared crop of a
sealed photo is sealed too, a sealed photo duplicated among the author photos fails the request. The intake derives
held-out from that sealed reservation, so a sealed crop or copy is never measured and no derived number of it reaches
the author. Worker bundles carry
the author-visible reduction of the evidence (no held-out entries, no paths). Author and critic payloads are checked
against the sealed pixel hashes before they leave. The final evaluator gets a fresh context, the frozen protocol, all
photos and the wearer renders of the exact candidate, and no author rationale; its verdict is recorded and never fed
back to the author. Runtime compatibility, automatic visual verdict and owner acceptance are separate axes.

Limits of the sealing: it recognises pixel-identical copies (any lossless re-encode) and declared crops; a lossy
re-encode, an undeclared crop or a resize of a sealed photograph is not recognised. Photos that are not held out are
sent to OpenAI when their inference runs; `store:false` is a storage choice, not a zero-retention guarantee.

## Worker setup (Docker; verified 2026-09-28 on this machine: image built, self-test passed)

1. Start Docker Desktop with the Linux engine (WSL 2 backend; the C: drive is shared by default). `docker version` must show a
   server version.
2. Get the official checksum of the Blender 5.2.0 Linux build from the release folder
   (`https://download.blender.org/release/Blender5.2/`, file `blender-5.2.0.sha256`, the line for `blender-5.2.0-linux-x64.tar.xz`)
   and build the image with it; the build verifies the tarball before extracting (10 to 20 minutes, about 1.5 GB):
   `docker build -t lenses-agentic-worker:blender-5.2 --build-arg BLENDER_SHA256=<that hex> -f modeler/agentic/worker/Dockerfile modeler/agentic/worker`
3. Write the worker config with the image's content-addressed ID. A locally built image has no registry digest (docker resolves
   `<name>@sha256:...` only for pulled or pushed images), so the ID from `docker image inspect --format '{{.Id}}'` is the reference.
   `worker/worker-config.example.json` is the template (copy it and replace its `image_digest` placeholder with that ID), or:
   `python -c "import json,subprocess;i=subprocess.check_output(['docker','image','inspect','lenses-agentic-worker:blender-5.2','--format','{{.Id}}'],text=True).strip();json.dump({'image_digest':i,'image_ref':'lenses-agentic-worker:blender-5.2','blender_version':'5.2.0','entry':'/opt/lenses/worker_entry.py','resources':{'cpus':'8','memory':'6g','pids_limit':512,'tmpfs_mb':2048,'uid':10001,'gid':10001}},open('data/modeler/agentic/worker-config.json','w'),indent=1)"`
   (`cpus` is also the Mesa rasterizer's thread count: the observation renders scale with it; the pilot's 2 rendered for 9 of
   every 12 minutes)
4. `python -m modeler.agentic doctor --worker-config data/modeler/agentic/worker-config.json --self-test --output data/modeler/agentic/selftest-001`
   runs the fixed fixture and four negative controls (network, host read, write outside, sentinel) inside the worker, exports the
   fixture through the contract, loads it in the actual AR renderer, and writes `selftest-001/worker-config.passed.json` with the
   doctor record bound to the config/image/harness fingerprint. Exit 0 means passed. Use THAT file as `--worker-config` for jobs; any
   change of image, harness or library invalidates it (rerun the self-test into a fresh folder). If the fixture's render step fails with
   a GL/EGL error, the image lacks a working software rasterizer: the Dockerfile installs Mesa's llvmpipe for that (test-pilot-002
   rendered every view with it).

What the image holds besides Blender (2026-09-28):

* Fonts: `fonts-dejavu-core` 2.37-6 and `fonts-liberation` 1:1.07.4-11 (pinned), under `/usr/share/fonts/truetype/{dejavu,liberation}/`.
  glasses_lib maps `sans`, `serif` and `mono` to the first existing file of an ordered candidate list, these worker paths first
  (DejaVuSans / DejaVuSerif / DejaVuSansMono, LiberationSans-Regular / LiberationSerif-Regular), and notes the file used. Before
  this, every text mesh of test-pilot-002 fell back to Blender's built-in font in the worker (three `font 'sans' not found` notes
  per build). The build fails when one of those files is missing, and `/opt/lenses/fonts.sha256` records every font file's
  sha256 and the package versions; the supervisor copies it into each receipt (`fonts`). There is no script or handwritten face
  in these packages: those styles still fall back.
* The base image is pinned by digest (`debian:bookworm-slim@sha256:3783cc01…`), and the Blender tarball is still checked against
  `BLENDER_SHA256` before extraction. The font layer comes after the Blender layer, so adding fonts reused the cached Blender layers.
* The GL renderer: the supervisor starts Blender with a probe (`--python-expr`, `worker/entry.py` `GL_PROBE`) that prints one
  `LENSES_WORKER_GL|` line with `gpu.platform` renderer / vendor / version / backend / device at the first render. It goes into
  `blender.stdout.log` and into the receipt's `gl`. Blender's headless EGL context creation prints `EGL Error (0x3009): EGL_BAD_MATCH`
  once per configuration Mesa refuses (three lines in every render op of test-pilot-002, all of which completed). When the probe
  shows that a context was made, the supervisor removes those lines from `blender.stderr.log` and appends one
  `[worker supervisor]` line with the count and the renderer. Without a context they stay verbatim, because then they are the
  diagnosis. The harness's two `use_nodes` DeprecationWarnings (Blender 6.0) are the harness's to fix and still appear.
* Image ID built 2026-09-28 with the fonts, the pinned base and the GL-probe supervisor:
  `sha256:4606bf7f8e284477d12a8cbe63deed50967a7bf64265119d4e263f3732901e45` (it replaces `sha256:5878915a…`, the image of
  test-pilot-002). A new worker config and a new doctor self-test are needed before a job can use it (the doctor record binds
  the image ID). The self-test renders nothing and writes no text, so it proves neither the GL record nor the fonts: the first
  build with a render and lettering does.

Generated programs never run on the host: `--worker docker` is refused without a passing doctor record, and the native-fixture worker
refuses any program hash that is not allow-listed.

## The paid pilot, end to end (owner authorization required)

test-pilot-001 and test-pilot-002 ran this way on 2026-09-28 and delivered compatible assets. Prerequisites: credits on the
OpenAI account with access to `gpt-6-astra`, `OPENAI_API_KEY` in `automation/.env` (read last, never printed), the self-test
above passed, a fresh output folder. The Tom Ford request `data/modeler/requests/tomford_ft1123d.json` holds five photos with `angled` held out; it is a
known product, so the pilot is a regression on continuity, intake, repair and budget behaviour, not unseen validation.

```powershell
python -m modeler.agentic start --request data/modeler/requests/tomford_ft1123d.json --output data/modeler/agentic/tomford-pilot-001 --driver responses --worker docker --worker-config data/modeler/agentic/selftest-001/worker-config.passed.json --allow-paid --budget-usd 15 --max-inference-requests 10 --max-output-tokens 24000 --max-revisions 6 --max-worker-seconds 600 --wall-minutes 180 --intake code --critic responses --final-evaluator responses --env .env
```

What it does: translates and validates everything, reads the key last, seals the held-out photo, measures the photos, freezes the
protocol, then loops: count the exact input, reserve the worst case, one request, one tool (a Docker build of up to ten minutes,
export, contract, renders, the AR harness), append the result, ask again. Expect 30 to 90 minutes and a few dollars of the $15 cap
(all roles share the cap and the ten operations; the critic and the final evaluator are one operation each). Every request's exact
input count, reservation and settlement is in `status --json` and under `host/requests/<qid>/`.

While it runs, from another shell: `python -m modeler.agentic status --job data/modeler/agentic/tomford-pilot-001` (`--json` for the
requests, reservations, observations and the lease). `cancel --job ...` stops it at the next step (exit 130).

How it ends (the exit code first, then the manifest):

* `0` with state `delivered`: `deliverable/model.glb` (byte-bound to the revision), `manifest.json` (`axes.visual.status` is the
  automatic verdict: `accepted` only with a calibrated bar, every tag covered and complete wearer evidence; otherwise `best_effort` or
  `quality_unverified`), `report.md`, `receipts.json`, `images/`.
* `0` with state `unresolved`: nothing compatible was delivered; the manifest's `problems` and `limitations` say why.
* `4` budget exhausted (a fallback asset may still be attached; the stop reason wins), `130` cancelled, `3` `failed` or `needs_attention`.
* `needs_attention` with stop reason `inference_unknown`: one request's outcome is unknown (timeout, 5xx, a 200 without usage). Check
  the OpenAI usage dashboard for it, then `reconcile-unknown --job ... --request <qid from status> --authorized-by "<your name>"
  --no-charge` (or `--usage-json FILE` holding the usage block the dashboard shows), then `resume --job ... --acknowledge-attention
  --env .env`. Any other `needs_attention` reason: read `status --json`, fix the cause, resume the same way. After a crash just
  `resume`: it reconciles first; within 15 minutes of the crash the old lease is still held and resume is refused until it expires.

Your verdict is the only acceptance. A job started since 2026-09-28 waits for it in `awaiting_owner` (see Owner review loop:
`owner-review --accept | --changes | --stop`); a job started with `--no-owner-review` delivers and takes a verdict afterwards:
`python -m modeler.tryon 8793` (with the AR dev server on 8240) lists the pilot's delivered GLB
beside the legacy jobs; look at it live, then
`python -m modeler.agentic owner-verdict --job data/modeler/agentic/tomford-pilot-001 --verdict accept|borderline|reject --medium "live AR mirror" --note "..."`
(append-only, rehashes the delivered bytes; a later reject revokes an accept).

## Owner review loop (2026-09-28)

The automation ends with the owner's live AR try-on, not with the automatic final evaluator. A new job (policy
`owner_review`, on by default; `--no-owner-review` keeps the old behaviour; a job created before it reads as off) never
delivers on the author's request_delivery:

1. **awaiting_owner.** When the author calls request_delivery on a compatible revision whose images it has received (or a
   fallback picks one: budget exhausted, wall deadline, the author stopped, the context limit), the host writes the review
   candidate and the job waits in state `awaiting_owner`: `review/round-NN/model.glb` (copied from the revision's verified
   bytes), `review/round-NN/manifest.json` and `review/candidate.json` (kind `review_candidate`: the revision, the asset and
   its sha256, the author's note, the related measurements, the runtime-limited flags, the budget, and `tryon`: width, clip,
   the link and the command that serves it). A row in the `owner_rounds` table records the round. No final evaluator runs
   (`--final-on-accept` runs it on the accepted candidate). Waiting costs nothing and never expires: nothing is held open,
   `resume` on a waiting job sends nothing and needs no `--script`, and the exit code is 0.
2. **Look at it.** `python -m modeler.agentic status --job JOB` prints the candidate, the try-on link and the three commands.
   `python -m modeler.tryon 8793 --jobs <job folder>` (the AR dev server on 8240) lists it as "awaiting your review"
   (`--jobs` now takes absolute folders; without `--jobs` it lists the waiting jobs under `data/modeler/agentic/`).
3. **Decide** with `python -m modeler.agentic owner-review --job JOB --authorized-by "<you>"` and exactly one of:
   * `--accept [--note ...] [--medium ...]`: the job goes `awaiting_owner -> evaluating -> delivered` (stop reason
     `accepted_by_owner`) and delivers exactly the candidate's bytes (rehashed against the round; a changed file is refused).
     This is the only way a job with the owner review delivers. A synthetic candidate (fake worker) has no bytes: accepted, unresolved.
   * `--changes "the lens colour is too light, the frame is too clear" --max-inference-requests N --budget-usd USD
     [--editable-modules materials,lenses] [--max-revisions N] [--wall-minutes M]`: the owner's words go VERBATIM, as a new
     user message, into the SAME author conversation (the author keeps its whole memory). The message has three blocks:
     a heading ("Owner review, round N ..."), the owner's text unchanged, and the host data: the latest observation's
     measurements that relate to it (`summary.lens_colour` with `lens_transmission_recommended`, the lens environment
     recommendation, `summary.appearance` per material, `summary.lens_reflection`, the frame / temple see-through, the export
     audit flags), every `*_runtime_limited` flag stated plainly (e.g. `crystal_clarity_runtime_limited`: the viewer cannot draw
     the crystal's edge contrast; match the body, do not chase the edges), the allowance, the module locks and how to go on.
     The allowance is required and refused above 50 USD or 30 operations per round (and above 200 USD / 200 operations per
     job, counted as committed + grant). The round spends its grant and nothing more: its caps become what the job has
     committed at the grant (settled + unknown liability + held USD; operations used) plus exactly the allowance, and any
     leftover of the earlier caps is dropped (`Budget.grant_allowance`, event `allowance_granted` with who authorized it, the
     caps before and after, `committed_*` and `dropped_leftover_*`). A held or unknown amount counted as committed at the
     grant that later resolves lower (a reconciled unknown outcome, a released hold) lowers the caps by the difference (event
     `allowance_pre_grant_resolved`), so the round's remaining never exceeds its grant minus its own spend. Each author
     request first holds its worst case (about 0.8-1.5 USD at 50k-100k input tokens under the frozen tariff), so grant about
     1 USD per operation: a 1 USD grant pays for about one request. The locks reuse `editable_modules` (not given: every
     module is editable this round) and bind to the reviewed revision's bytes: in a locked round every locked module of an
     edit's base, a delivered revision, a `deliver_if_compatible` build and a budget or deadline fallback must be byte-equal
     (sha256) to the reviewed revision's, and a delivery or fallback hands the owner only a revision made this round (or,
     for a fallback, the reviewed one back). The revision cap is the revisions used + `--max-revisions` (default: the granted
     operations) and the wall limit counts from the change request. The run then continues like `resume` (same `--script` / `--worker-config` / `--env` flags) unless `--no-continue`
     (then `resume` continues it). The author edits, builds and calls request_delivery again; the job returns to
     `awaiting_owner` with round N+1.
   * `--stop [--note ...]`: `unresolved` (stop reason `owner_stopped`); the candidate stays under `review/round-NN/`.
   `cancel` on a waiting job cancels it and closes the round as `cancelled`.
4. **Too large to continue cheaply.** When the last author request counted `review_resume_token_limit` input tokens or more
   (default 150,000: below the 200,000 compaction threshold and the 272,000 price cliff), `--changes` starts a NEW job
   instead (`--new-job-output DIR`, default `<job>-continued-<round>` beside the job): seeded from the candidate revision
   (the `--seed-revision` path: its sealed program verified against its build bundle, built before the first author turn),
   the owner's words as its `owner_instruction`, the round's locks as its `editable_modules`, `previous_job` (a short summary:
   the job, the revision, the earlier owner requests, the related measurements, the runtime-limited flags) in its first
   message, the allowance as its caps, owner review on. The two jobs are linked both ways (setting `lineage`:
   `continued_in` on the old job, `continued_from` on the new one; `status` prints it); the old job ends `unresolved`
   (`continued_in_new_job`), its candidate kept. The continuation's intent (setting `owner_continuation_intent`: the
   folder, the words, the allowance, the editable modules and the new revisions) is recorded before the new job exists. A
   retry after a crash finishes the link in the same folder and must repeat those values (different ones are refused; the
   new job already holds the first ones). A new job that failed to initialize (a Docker seed build error, a stop inside
   initialize) is recorded (`failed_attempts`, event `continuation_failed`), its folder kept for inspection and never
   reused; retry the same `--changes` with a new `--new-job-output` (without it the CLI picks `<job>-continued-<round>-2`, ...).
   While the new job a crash left unlinked can still run (any state but terminal or `created`), `--accept` and `--stop` on the
   old job are refused with a message naming it (a stop would otherwise leave a linked job able to spend its allowance under a
   stopped job): finish the link (the same `--changes`, `--new-job-output` that folder) or `cancel --job` the new job, then
   decide again; the decision then clears the intent (event `continuation_abandoned`).

Every decision (accept, changes with the text, continued_in_new_job, stop) is appended to the owner verdicts: the job's
`verdicts` table (kind `owner`; verdict `accept`, `changes_requested` or `stop`) with the asset hash and a snapshot of the
related measurements, and, for a real (non-synthetic) candidate, one row of kind `agentic_review` in the calibration set
`data/modeler/calibration/owner_verdicts.jsonl` (`modeler.owner_verdict.review_record`; `--calibration-file` writes it
elsewhere), so owner-vs-instrument disagreements can recalibrate the instruments later. `modeler.calibration` counts only
accept and reject rows; a `changes_requested` or `stop` row is listed, never counted.

Recovery: the round row is written before the transition and reused if the runner stopped in between; an acceptance records
its intent before `evaluating`, and the delivered transition, the verdict, the round decision and the pending calibration
row are one transaction, so a crash there completes the same acceptance on the next `resume --job JOB` (without the final
evaluator it sends nothing and needs no `--script` or credential; a retried `owner-review --accept` says so). A calibration
row the file refused (a locked file) stays pending (setting `owner_calibration_pending`; `status` prints the count) and is
written once by the next decision, run or `resume` (also on a terminal or waiting job, under the lease).

The schema: `owner_rounds` is an additive table. A read-write open of a job created before it adds it in one transaction
(event `schema_migrated`), a read-only open (`status`, `tryon`) reads it as empty; nothing else changed, so test-pilot-001
and -002 open and report their states as before (the tests open copies, never the folders).

Test the loop offline (fake worker, scripted rounds):

```powershell
python -m modeler.agentic start --request req.json --output $env:TEMP\or-001 --driver scripted --script r1.json --worker fake --intake synthetic --no-ar
python -m modeler.agentic owner-review --job $env:TEMP\or-001 --changes "the lens colour is too light" --authorized-by "<you>" `
    --max-inference-requests 3 --budget-usd 3 --editable-modules materials --script r2.json --calibration-file $env:TEMP\or-cal.jsonl
python -m modeler.agentic owner-review --job $env:TEMP\or-001 --accept --authorized-by "<you>"
```

With the Docker worker, `examples/` holds the two rounds made from test-pilot-002 r0006 (`python -m
modeler.agentic.examples.make_owner_review_example` rewrites them from the job's working copy, each module checked against
the revision row of a copy of its database): round 1 is `edit_program` with r0006's exact seven modules (base null,
build_now), `fetch_pending_images`, `request_delivery r0001`; round 2 patches the lens tint in the materials module
(`transmission_top_rgb` / `transmission_bottom_rgb` of `gl.lens_optics`, 0.75/0.725/0.67) to `lens_transmission_recommended`
(0.59, 0.485, 0.38), then `fetch_pending_images`, `request_delivery r0002`:

```powershell
python -m modeler.agentic start --request data/modeler/requests/tomford_test.json --output $env:TEMP\or-docker-001 `
    --driver scripted --script modeler/agentic/examples/owner_review_round1.json --worker docker --worker-config <a config whose doctor self-test passed for this library> `
    --intake code --max-revisions 4 --max-worker-seconds 900 --wall-minutes 120
python -m modeler.agentic owner-review --job $env:TEMP\or-docker-001 --changes "the lens colour is too light, the frame is too clear" `
    --authorized-by "<you>" --max-inference-requests 3 --budget-usd 3 --editable-modules materials `
    --script modeler/agentic/examples/owner_review_round2.json --calibration-file $env:TEMP\or-docker-cal.jsonl
```

The scripted driver sends nothing anywhere (its "money" is the scripted usage block); only the Docker build and the host's
observation run for real.

## Tests

```powershell
python -B -m pytest tests/test_agentic_state.py tests/test_agentic_budget.py tests/test_agentic_protocol.py tests/test_agentic_session.py tests/test_agentic_executor.py tests/test_agentic_evaluation.py tests/test_agentic_cli.py tests/test_agentic_tools.py tests/test_agentic_recovery.py tests/test_agentic_rebuild.py tests/test_agentic_selftest.py tests/test_agentic_owner_review.py -q -p no:cacheprovider --basetemp $env:TEMP\lag-pt
```

The suites make their job folders under `LENSES_TEST_TMP` when it is set, otherwise the system temp folder
(`tests/test_agentic_support.py`: `lag-<suite>` there, created on first use, never at import, removed after each test with
any leftover reported as a `LeftoverWarning`); the per-suite overrides `LAG_OWNER_TMP`, `LAG_REBUILD_TMP`,
`LAG_RECOVERY_TMP`, `LAG_SESSION_TMP` and `LAG_TOOLS_TMP` still apply. Keep that root short (Windows path length): set
`LENSES_TEST_TMP` when TMP/TEMP point at a long folder. `--basetemp` only places pytest's own `tmp_path`. The suites are
offline; the native fixture parity test skips without Blender and never runs generated code. Docker isolation is exercised
here with a fake `docker` runner; on a real engine it was verified by the doctor self-test of 2026-09-28 (image
`sha256:4606bf7f...`, see Worker setup), which a new image or library needs again.

The six `tests/test_agentic_legacy_*.py` are not part of this command: they are the 2026-09-27 audit regressions of the
shared modules this route adapts (`bsa/`, `modeler/`, `reconstruction/`); modeler/README.md gives their command
(`python -B -m pytest (Get-ChildItem tests\test_agentic_legacy_*.py) -q`).
