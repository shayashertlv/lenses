# Segmented photo-to-AR job

This is the active reconstruction entry point. Its optional [canonical Astra editing stage](SEGMENTED_ASTRA.md) now runs after base delivery when explicitly enabled. Image interpretation and reversed-order material review remain separate semantic-client stages; Astra receives its own typed geometry/material tools, authorization, checkpoint state and actual-AR observations. The [earlier readiness audit](ASTRA_READINESS_2026_09_23.md) is historical; see the current Astra runbook for validation status and recovery rules.

`segmented_ar_v1` is the explicit Tripo route through the normal job entry point.
It retains provider generation and segmentation, infers optical part groups from
known-camera rendered masks, reduces geometry before preparing optics, and
proposes photo-grounded materials. Its output is a reviewable AR candidate.
Provider fidelity and material identity are not certified by successful export.

## Request and command

```json
{
  "schema_version": 1,
  "pipeline": "segmented_ar_v1",
  "product_id": "example",
  "photos": [
    {"id": "front", "view": "front", "path": "C:/photos/front.jpg"},
    {"id": "left", "view": "left", "path": "C:/photos/left.jpg"},
    {"id": "angled", "view": "angled", "path": "C:/photos/angled.jpg", "provider_input": false}
  ],
  "source": {"kind": "tripo"},
  "settings": {"display_width_mm": 145, "yaw_degrees": -90}
}
```

The provider currently requires a front photograph and at least one actual axial
view (`left`, `right`, `back`). An angled image is not relabeled as an axial view.
Additional existing photographs can inform material interpretation. No new
photography is required, but unsupported or missing evidence can require review.
The 145 mm default is a display convention, not a recovered product dimension.

```powershell
python -m reconstruction.job --request request.json --output data/jobs/example --env .env --maximum-new-calls 6 --wait-seconds 600 --aperture-weights data/models/glasses-detector-v1 --semantic-api-key-env GEMINI_API_KEY --semantic-model gemini-3.8-flash --semantic-cache data/jobs/example/semantic-cache --semantic-maximum-calls 4
```

Use `python -m reconstruction.segmented_job --help` for this route's complete
options. The generic job entry point selects its parser after reading the
request's `pipeline` field.

Credential sources are explicit:

| Service | Current source |
| --- | --- |
| Tripo and fal/SAM3 | `TRIPO_API_KEY` and `FAL_KEY` in the file passed to `--env` |
| Gemini interpretation/review | The process environment variable named by `--semantic-api-key-env`; `--env` does not load this value into the process |
| Optional canonical Astra stage | Variable named by `--astra-api-key-env` (default `OPENAI_API_KEY`), read from the explicitly named `--astra-env` file or the process environment; base `--env` does not supply it |
| Historical Blender Astra editor | Separate legacy application and credential handling in `ar/modeling`; not invoked by the segmented route |

The existing host has these credentials. A fresh environment must provide the
explicit credential sources for the stages it enables. No key belongs in a
request, report or Git. Astra model access and current execution evidence are
recorded separately from the base provider/material results.

Six new provider calls cover generation, segmentation, and four SAM3 render-mask
requests. The explicit semantic cache has a lifetime maximum of four calls:
photo interpretation, region proposals, and two blinded material comparisons
with reversed candidate order. Completed responses are reused by content.
Unknown/failed submissions are not automatically repeated. `--wait-seconds`
polls accepted provider tasks; repeating the command resumes completed stages.
Use `--maximum-new-calls 0` to prohibit new Tripo/SAM requests during recovery.
Completed appearance experiments keep their recorded implementation on normal
resume. Use `--refresh-appearance` explicitly to adopt changed material/review
code in a new attempt; supply the prior source-bound interpretation and sampling
reports to avoid repeating those calls. Changed evidence still invalidates the
dependent stages. The previous candidates remain in immutable attempt folders.
Each API may charge according to its own account and model settings; receipts
retain observed usage. Keys are never stored in job artifacts.

Saved source-bound interpretation can be supplied with `--semantic-report` and
`--sampling-report`. Both are rebound only by exact image pixels; ordinal image
numbers are remapped. A semantic client is still needed for new render reviews.
Cached Tripo tasks can be supplied as `source.kind: cached_tripo`, with
`generation_directory` and `segmentation_directory`; the parent task and original
photograph hashes are checked before reuse.

## Optional canonical Astra editing

Append `--with-astra` and the Astra options to `reconstruction.segmented_job`
to edit after the base job completes. For an already completed job, the
standalone entry point avoids rerunning base orchestration:

```powershell
python -m reconstruction.segmented_astra_job --base-job data/jobs/example --output data/astra/example-scripted --astra-script plans.json --astra-max-turns 2
```

The [Astra runbook](SEGMENTED_ASTRA.md) supplies a valid example plan and the live
command. Scripted mode uses no paid inference. Live mode requires
`--authorize-paid-astra`, an explicit credential source, and one shared
`--astra-budget` ledger with a ceiling of 1–10 reserved calls across all sessions
in that authorization. This is separate from the Tripo/SAM and semantic budgets.
Failed or uncertain requests count; there is no automatic paid retry or
alternate-model fallback.

The editor preserves the exact base asset and authors new revisions from the
retained pre-optics source, optical groups, normal policy and descriptors.
Supported edits must pass strict re-export, receipt-aware compaction, delivery
budgets and fresh actual-AR observation before promotion. The integrated report
retains `base_candidate` and the base selection/rendering/delivery records while
its current `candidate` points to the separately compiled session result.
Neither model `finish` nor runtime compatibility establishes visual acceptance.

## Runtime and asset boundaries

Run from `lenses/automation`; `lenses/README.md` and `lenses/AGENTS.md` govern the live application, and `ar/README.md` governs the local AR runtime. Keep generated models, photographs, receipts and credentials out of Git. This job neither publishes `ar/site` nor changes the catalog.

The canonical GLB uses metres, +Z forward, +Y up, a bridge-underside origin and baked identity mesh transforms. Grouped optics carry the verified `LENSES_lens_appearance` descriptor and the `effective_optical_group_v1_experiment` profile. The canonical Astra stage edits its retained pre-optics source directly and regenerates these bindings. The historical `ar/modeling` editor works in millimetres, -Y forward and +Z up, builds parametric replacement lenses, and exports a different material representation. Its `prepare`, `build_lens`, `set_lens_look` and `export` operations remain an unsupported round trip for a segmented optical candidate; the canonical stage does not invoke them.

The current host runs the segmented route with system Python 3.12.10 and the historical editor with its separate `ar/modeling/.venv` Python 3.12.10. Their dependency manifests are separate. For a fresh segmented environment, use Python 3.12 and run from `automation/`:

```powershell
python -m pip install -r requirements-segmented.txt
```

That manifest includes the original numerical requirements plus `requests==2.32.5`, `python-dotenv==1.1.1`, `open3d==0.19.0`, `torch==2.7.1` and `torchvision==0.22.1`. The legacy editor uses its own `ar/modeling/requirements.txt` and Blender 5.2; the present segmented job does not require a Blender worker.

From `ar/`, a fresh browser-rendering setup uses:

```powershell
npm ci
npx playwright install chromium
```

The committed lockfile supplies `meshoptimizer==1.1.1` transitively through `@types/three`, as well as Playwright. Chromium is a separate Playwright browser installation. The current host has Node 26.5.1 and the required browser already installed.

Supply the local lens detector explicitly with `--aperture-weights data/models/glasses-detector-v1`. The retained [download receipt](../data/models/glasses-detector-v1/download-receipt.json) pins the source revision, license, lens checkpoint and SHA-256; that existing directory is an offline input, not a runtime download. These prerequisites are already present on this host, so no installation is required here. See the [Astra runbook](SEGMENTED_ASTRA.md) for the optional editor's current requirements and limits.

## Implementation and recovery

| Stage | Implementation | Evidence retained |
| --- | --- | --- |
| Snapshot and resume | `segmented_job.Journal`, `capture_photos` | Immutable inputs, dependency recipes, stage inventories, separate failed attempts |
| Generation and segmentation | `segmented_providers.advance_tripo` | Request, reservation, task lineage, response, downloaded model hashes |
| Source preservation | `part_probe_audit.bounded_correspondence` | Unique whole-model face correspondence, all three corners, winding and explicit tolerance; does not assert texture/UV equality |
| Rendered lens evidence | `render_evidence`, `advance_mask`, `view_evidence` | Known cameras, transform, pinned images, mask provenance and dimensions |
| Part identity | `part_role_inference.infer_part_roles` | Independent-view support, omissions, contamination, boundary alternatives |
| Ambiguous boundaries | `segmented_role_alternatives.process_role_alternatives` | Independently reduced/prepared competing assignments; no silent semantic promotion |
| Reduction | `part_lod_probe.run`, `compact_glb.run_compact_asset` | Optical/frame budgets, sampled source deviation, topology and attribute lineage |
| Optical preparation | `part_optics_probe.run` | Canonical placement, group declarations, strict export receipt |
| Specific normal conflict recovery | `segmented_optics.recover_smooth_failed_preparation` | Reproduced failure, unchanged positions/indices/UV/frame, guarded fit and strict re-export |
| Material proposals | `segmented_appearance.run_segmented_appearance` | Source-pixel regions constrained by independent lens masks; tint, gradient, clear and angular coating alternatives |
| Actual rendering | `segmented_job.render_appearance` | Current AR loader; front, angled, rolled and rear asset inspection; three lighting environments |
| Material review | `segmented_appearance_review.run_segmented_appearance_review` | Anonymous cards, two candidate orders, explicit unresolved fallback |
| Delivery | `segmented_job.deliver_candidate` | Strict GLB read, triangle/byte limits and resealed optical receipt |
| Optional canonical editing | `segmented_astra_job.run_astra_job`, `SegmentedAstraSession` | Copied baseline/source, typed plans, shared inference reservations, preservation proofs, strict recompilation, checkpoint restoration and current-AR observations |

The default delivery limits are 150,000 triangles and 15 MB. The optical triangle
target is 20,000 and the frame target 100,000. Simplification is measured against
the generated source; this does not measure correctness against the real product.

Job reports distinguish pending providers, missing appearance configuration,
pending review, review-needed evidence, and available candidates. Every artifact
is experimental (`accepted: false`). A vision-model tie retains a clearly marked
prior candidate; it does not become a claimed model-reviewed winner. Unanimous
material rejection returns `needs_review` with a diagnostic artifact, rather
than describing the fallback as a plausible material. Small edge
alternatives remain reviewable rather than being hidden in a single identity.

No live app deployment or catalog modification occurs. The existing legacy job
route is unchanged unless the request explicitly selects `segmented_ar_v1`.
