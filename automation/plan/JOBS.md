# Repeatable reconstruction jobs

2026-09-22. The automation now connects photo intake, initial-model resolution, shared geometric refinement, automatic region hypotheses, optional optical candidate preparation/fitting and an explicit quality report. It does not yet reconstruct accepted glasses from arbitrary product photos. A supplied initial model remains an external input, and a generated provider asset remains a hypothesis.

## Run a local job

Save a request such as this next to its input files:

```json
{
  "schema_version": 1,
  "photos": [
    {"id": "catalog-front", "view": "front", "path": "front.jpg"},
    {"id": "detail-angle", "path": "angle.jpg"}
  ],
  "dimensions_mm": {"frame_width": 140, "temple_length": 145},
  "initializer": {"kind": "existing_glb", "path": "initial.glb"}
}
```

`dimensions_mm.frame_width` is applied, the rest is recorded. An optional `lens_facts` object carries declared product facts about the lens, caller-supplied like the dimensions and part of the input digest; `mirror_coating` (true/false) keeps only the mirror families or only the tints in the appearance fit (`lens_policy_for_facts`, recorded in the fit stage's journal entry as `lens_facts` and in the report). It exists because a photograph cannot tell a mirror coating from a bright studio reflected by a plain lens: the Victoria Beckham fit shipped a 59% mirror with a dim environment, numerically within policy, that the AR runtime renders as chrome on a brown gradient lens. A `scale` stage between the initializer and the refinement (`reconstruction/scale.py`) multiplies every vertex position, node translation and matrix translation of the initial model by one factor so its X extent equals the stated width in metres, writes `stages/scale/attempt_N/scaled.glb` beside its receipt, and every later stage works on that model; the provider bytes stay untouched in the initializer stage. The same stage then places the origin the way the AR runtime expects its assets (`ar/src/eyewear/external.ts`: bridge underside at the origin, front toward +Z; `bridge_underside_origin_v1`): the bridge is the lowest thin front run of material in a narrow central column of the front, its lowest point becomes y = 0 and its median depth z = 0, and a continuous column (a shield with its nose piece) falls back to the front slab's vertical centre 2.5 mm behind the front-most point; the translation is baked into the positions (the runtime refuses lens meshes with node transforms) and the receipt records the origin found, the column runs and the rule used. A provider generation arrives centred on its bounding box, which put the frame front seven centimetres in front of the origin and made the widest product fail the runtime's raw-pose width check before any fitting. Without a frame width the stage applies nothing and the report says so (`physical_scale.status: not_applied`). The lateral axis is assumed to be X in the source frame and the width is caller-supplied product data; the receipt records the factor, the source extents and both assumptions. The AR runtime fits eyewear to the wearer from the model's own metres, so a raw provider generation (about 1.9 units across) cannot be placed without this; the archived modeling_auto models were already in metres, and a stated width equal to their width scales them by one.

From `automation/`:

```powershell
python -m reconstruction.job --request C:/path/request.json --output data/jobs/example
```

At least two distinct photos are required. Photo IDs and angle labels are separate: multiple photos can have the same label. Labels are optional; supported priors are `front`, `back`, `left`, `right`, `angled` and `unknown`. Missing labels remain unknown in the provenance. The geometric camera fitter searches a fixed set of yaw/pitch seeds for unknown views, then optimizes the best seed basin. This is candidate-dependent initialization, not reliable semantic pose identification or an exhaustive camera search.

The complete `semantic_ar_v1` path also accepts `reserved_photo_ids` or `evaluation_photos` to withhold existing photographs from every reconstruction/model input before initialization. At least two distinct reconstruction photos must remain. See [the reservation, usage and single-candidate evaluation contract](EVALUATION_PHOTOS.md). Imported initializer history remains unverified even when current-run photo isolation succeeds.

Dimensions are optional positive finite values named `frame_width`, `lens_width`, `lens_height`, `bridge_width` and `temple_length`. They are stored in millimeters. Current stages report them as **supplied but unapplied**: neither scale nor dimensional accuracy is silently claimed. Reliable dimension constraints remain required work.

The standalone refinement command also accepts independent identity/prior syntax, for example `--photo detail-1:angled=angle.jpg --photo detail-2:angled=other-angle.jpg`. Its original `--photo front=front.jpg` syntax still works.

## Input evidence

`reconstruction/input_bundle.py` validates all inputs before creating its stage artifacts. It copies original bytes exactly, records SHA-256 hashes, and writes normalized PNGs without resizing. All eight EXIF orientations have recorded pixel-coordinate transforms. Embedded ICC profiles are validated and converted explicitly to sRGB where supported; absent profiles retain an uncalibrated color-space assumption. Original ICC hashes, dimensions, transparency and low-resolution limitations remain in the manifest.

Normalization does not establish illumination, exposure, camera or physical color calibration. Alpha remains intact. The current contrast fitter cannot use nonopaque alpha correctly, so the job explicitly excludes those photos from refinement and reports the reason; it does not discard alpha into a false contour. An alpha-aware observation stage is still needed.

Encoded-byte and normalized-pixel duplicates are rejected, including different file formats with the same decoded pixels. Corrupt images, animations, unsupported high-depth modes, unsafe/repeated IDs and invalid dimensions fail validation. Additional view counts are not automatically treated as independent geometric constraints.

## Initializer boundary

`reconstruction/initializer.py` supports:

- `existing_glb`: copies exact source bytes after bounded embedded static-triangle validation. It does not certify topology, optical semantics, axes, units or AR appearance.
- `meshy` with `cached_glb: {path, sha256?}`: imports a saved artifact, explicitly retaining unverified cache origin.
- `meshy` with an explicitly injected backend: advances a provider request by one submit/retrieve/download cycle. `MeshyBackend` accepts a caller-owned transport; the opt-in CLI can construct `BoundedMeshyTransport` with an explicitly named credential source and submission allowance.

Omitting `initializer` prepares the Meshy path. Without provider configuration, the CLI returns `awaiting_initializer` / `awaiting_backend` with no model and performs no network call. The bounded authenticated transport is implemented and tested with mocked HTTP. **No real provider call has been performed for this integration yet.** A completed provider request would supply an initial geometry hypothesis; it would not establish semantic preparation, optical correctness or accepted photos-only reconstruction.

The adapter selects at most four images, recording selected and unused inputs while retaining every photo for fitting. Its declared-direction heuristic chooses a primary near the front and then favors angular diversity. It does not verify labels from pixels. An `angled` label has no known signed direction, so it is not assigned a made-up yaw. Current Meshy 7.1 behavior and payload fields were checked against the [official multi-image API documentation](https://docs.meshy.ai/en/api/multi-image-to-3d).

An exclusive durable submission reservation is written before the POST. A request with an uncertain submission outcome never automatically submits again. A known task resumes retrieval/download by task ID. `recover_initializer_task` can associate an externally verified task after uncertainty and records that operator intervention; a local hash cannot prove the external association. Invalid artifacts and failed providers are reported as `initializer_failed`, not as successful or indefinitely running models.

Native `parts_prepare` is not invisibly applied: it may classify/join parts, remove walls, rebuild lenses and change temples. Its useful operations still need candidate lineage, preserved fallbacks and general semantic/optical coordinate checks.

### Provider split into named parts, and provider-only views

A fused provider generation cannot be grouped: the lens and the frame are one shell, and the physical-group stage reads geometry only. `"initializer": {"kind": "meshy", "split": {}}` therefore adds a **second paid task**: Meshy's `print/split` of the finished generation into named closed parts (`mode: by_parts`, `layout: assembled`, GLB). The textured generation is retained as `generated.glb` (the appearance source, receipt `generation_receipt.json`) and the split parts become `initial.glb`; the split's own reservation, task receipt, observations and terminal task live under `split/`. `split.parts` overrides the default eyewear vocabulary (`frame front, left lens, right lens, left temple, right temple, nose pads`); the names are hints to the provider and are never read as semantic identity. A cached artifact has no generation task and cannot be split.

The split is estimated at 10 credits from the observed `consumed_credits` of the 2026-09-21 split tasks (the pricing page lists no separate figure), so a generation with its split needs `--meshy-max-new-tasks 2` and a credit allowance covering both estimates (40 with the pinned defaults). Every transport instance carries at most one POST; the CLI builds a second instance for the split. Uncertain split submissions are reconciled with `recover_initializer_task(..., phase="split")`. The report's `phase` names the pending step (`generation`, `split`, or `complete`).

`"provider_views": [{"path": ..., "view": "back"}, ...]` offers extra photographs to the provider's four-image selection only. They are pinned in the request and listed as `provider_only_photos`; they never become refinement or fitting inputs, because the joint fit's mask-branch product grows with every fitted photo.

What the split GLB is: separate meshes named `model_partN` with positions and per-vertex colours, no normals, no UVs, no materials. A `surface_transfer` stage (`reconstruction/surface_transfer.py`) runs right after the initializer, in the provider's own frame: for every split vertex it finds the closest point on the retained generation's surface (Open3D float32 raycasting scene) and the texture-atlas island it lies on (`island_consistent_uv_transfer_v2`). The generation's atlas is hundreds of islands (Miu 653, Victoria Beckham 586, Oakley 997), and texture coordinates only mean something within one island, so every split face samples exactly one: a face whose corners agree keeps the exact interpolated coordinates, a face that straddles islands (13 to 14% of faces on all three products) takes, among the islands under its corners and centroid, the one whose nearest triangles need the least extrapolation for the corners off it, and those corners are extrapolated through that island's nearest triangle a little past its edge into the atlas padding (95th percentile 0.5 to 0.7 triangle widths). Corners of one source vertex that end up with different coordinates become separate vertices at the same position (about 13 to 15% more vertices), which position-based connectivity downstream does not see. The stage writes `TEXCOORD_0`, re-emits every other attribute, drops the vertex colours (glTF would multiply them into the base colour) and attaches the generation's material, textures and images to every primitive; `stages/surface_transfer/attempt_N/transferred.glb` then feeds the scale stage. The receipt records the distance statistics, how many vertices lie farther than a stated fraction of the extent, the island count, the straddling faces, the extrapolated corners and their barycentric excursion. The first version (`nearest_surface_uv_transfer_v1`, per-vertex closest-point UV with no island rule) smeared the atlas across every straddling face and put a cream marbling on the Victoria Beckham frame that the provider's own texture does not have; its receipt (median distance zero) could not see that, which is why the receipt now counts straddling faces. Declared optical groups receive their fitted material later regardless; the transferred texture is the provider's photographic bake on the frame, not a measured material. A cached split can be rerun with its generation through `"cached_generation": {"path": ...}` beside `cached_glb`; without a retained generation the stage reports `not_applicable`.

### Explicit provider execution

For a photo-only request, omit `initializer` or use `"initializer": {"kind": "meshy"}`. The caller must already have placed its API key in an environment variable of its choosing. The CLI reads **only the variable explicitly named by `--meshy-api-key-env`**; it does not read `.env`, search credentials, or assume a default Meshy variable. Do not put the credential itself in the command or request JSON.

This command authorizes at most one new task in the job directory:

```powershell
python -m reconstruction.job --request C:/path/request.json --output data/jobs/photos-only-v1 --meshy-api-key-env LENSES_MESHY_KEY --allow-meshy-submit --meshy-max-new-tasks 1 --meshy-max-estimated-credits 30 --meshy-wait-seconds 3600
```

All three submission controls are required: `--allow-meshy-submit`, `--meshy-max-new-tasks 1` and an explicit estimated-credit allowance. Without submission permission, an injected backend can retrieve an already saved task but cannot create one. The default task allowance is zero. Budget rejection occurs before the initializer reserves a POST and reports `submission_not_authorized`, so that rejection does not create a false uncertain-submission state.

The pinned defaults are Meshy 7.1, textured PBR, 4k textures, no remesh and GLB output. Their **documented estimate is 30 credits**, checked on 2026-09-21. The transport estimates 20 without texture, 30 with 2k/4k texture, 35 with 8k texture, and an additional 5 for 2k geometry. This is a local admission check against the [published pricing](https://docs.meshy.ai/en/api/pricing), **not a provider-enforced spending ceiling or a guarantee of the eventual charge**. Returned `consumed_credits`, progress and provider responses are retained separately from the estimate.

Resume the same request and output without authorizing another task:

```powershell
python -m reconstruction.job --request C:/path/request.json --output data/jobs/photos-only-v1 --meshy-api-key-env LENSES_MESHY_KEY --meshy-wait-seconds 3600
```

The CLI otherwise performs one advance and exits. `--meshy-wait-seconds` optionally keeps polling a known pending task for up to the supplied interval, between 0 and 3600 seconds; `--meshy-poll-interval` defaults to 5 seconds and accepts 1–60 seconds. It does not retry failed or uncertain submissions. Each API operation has a 60-second deadline and each model download a 900-second deadline by default; the programmatic transport can configure positive deadlines up to 3600 seconds. These operation bounds are separate from the CLI polling interval and downstream local reconstruction time.

`BoundedMeshyTransport` restricts authenticated requests to the Meshy task API. It follows no API redirects and retries no POST. Model downloads carry no API Authorization header, allow at most five redirects, and validate each HTTPS destination and every resolved address before connecting directly to a validated numeric address with the original TLS hostname. Private addresses, credentials in URLs and non-443 ports are refused. JSON responses are bounded at 4 MiB, each selected image at 20 MiB, the encoded request at 128 MiB, and a downloaded model at 2 GiB; streamed bodies and absolute deadlines are checked as well. Proxy environment settings are not used.

The initializer and transport each retain an exclusive submission reservation. Immutable HTTP attempt directories preserve sanitized request metadata, response bytes, hashes, statuses and failures; request Authorization is never stored. API credential echoes are redacted, including JSON-escaped echoes, with the original response hash retained. Pending observations, the original terminal task and subsequent refresh observations remain available. The final artifact receipt includes the provider observation that supplied its bytes. Completed job inventories pin these receipts together with the model.

An expired signed download link does not cause regeneration. Only an HTTP 401/403 with explicit expiry text triggers one fresh GET of the same known task and one new download attempt. The original terminal receipt remains unchanged and the refresh records its hash. A generic access denial does not trigger this refresh. If the POST outcome is uncertain, stop and reconcile the existing task using `recover_initializer_task`; never create a replacement merely because the local response is missing.

Cancellation is **local only**. Ctrl+C stops the CLI and leaves existing provider tasks intact; the transport never sends DELETE or claims a refund. It may have submitted a task before the interruption, so retain the directory and use its recovery state.

The bounded transport checks comprise **49 focused passing tests**: 16 transport tests, 27 initializer tests (including five real adapter/transport state-machine tests), and six CLI/job tests. They cover DNS/address pinning, authentication scope, redirects, byte/deadline limits, a socket-publication cancellation race, budget rejection before reservation, uncertain POST recovery, expired URL refresh, raw receipts and explicit environment lookup. One test runs the actual photo-only job pipeline against mocked HTTP responses, verifies exact artifact/receipt hashes and offline reuse, and requires `quality_verdict: unmeasured` / `accepted: false`. These are software and recovery checks, not measured provider reconstruction quality or a real paid execution.

## Automatic region observations

After candidate selection, the job records contrast-object and candidate-projected optical priors. An explicitly configured local SAM2.1 Base Plus engine returns all three masks for five fixed box perturbations. The stage preserves competing interpretations, co-best matching ambiguity, conditional interior RGB and candidate height/incidence hypotheses. None becomes verified semantic identity or recovered optical material. See [the observation contract](SEMANTIC_OBSERVATIONS.md).

```powershell
python -m reconstruction.job --request C:/path/request.json --output data/jobs/with-regions --region-weights C:/path/sam2.1_hiera_base_plus.pt --region-weights-sha256 a2345aede8715ab1d5d31b4a509fb160c5a4af1970f199d9054ccfb746c004c5
```

Weights must already exist; there is no download or credential lookup. Missing configuration reports `engine_unavailable`. Nonopaque photos remain preserved but unsupported by region inference. When no usable hypotheses were produced, the stage reports that condition. The optional backend was tested with torch 2.7.1+cpu, torchvision 0.22.1+cpu and Ultralytics 8.4.14. Dependency/checkpoint identity is part of immutable job settings.

## Optional optical candidate stages

Rear content: a lens ray that hits opaque or undeclared geometry behind the lens (a folded temple, a pad, the frame) carries an unknown rear radiance. The fit policy's `rear_content` field is `excluded` by default: those samples stay out of the training residual and out of the photo policy, and the coverage ledger counts them (`rear_content_samples`, `excluded_rear_content`) with the rear mode recorded as `rear_content_excluded`. `explained` restores the earlier behaviour (one unknown rear colour per photo and group, or a geometry-conditioned rear when its colour is known). Saved fits that predate the field replay as `explained`.

Add `--lens-candidates` to a job with an explicit `--region-weights` checkpoint to run optical surface preparation and conditional photo-material fitting. `--lens-fit-mode independent` retains the baseline; `--lens-fit-mode joint` shares photographic lighting/exposure across independently parameterized material groups. The omitted optimization budget is 540 for independent fitting or 2430 for joint fitting. `--lens-maximum-optimization-runs` overrides that budget; `--lens-maximum-samples` defaults to 512 per hypothesis (256 until 2026-09-23; the policy now judges a lens row only on at least 24 held-out samples, and a small far lens reaches that at 512, at about twice the fit time). Mode, expanded policy and sample capacity are immutable settings, also pinned in the fit-stage journal. The stages retain all mask combinations within capacity; exceeding the budget reports an unsupported fit instead of dropping alternatives.

The default `--optical-profile front_sheet_v1` retains the existing front-envelope preparation path and its limitations. Existing default settings gain no required profile/grouping fields. Source-preserving effective groups are a separate explicit option; the job does not switch profiles after a failed preparation.

Complete preparation binds the actual exported float32 UV/normals to the saved photos and cameras. Fitting explores five tint/gradient/mirror families and writes full ensemble reports plus diagnostic representative GLBs. Unsupported preparation or missing material groups remain explicit. See [the optical candidate contract](PHOTO_LENS_PIPELINE.md) for equations, coverage, limitations and standalone commands.

`report.json.optical_candidates` links preparation/fit reports and job-relative preview/export paths. `candidate.glb` retains the geometric candidate; diagnostic optical previews do not replace it or become accepted materials. Both optical stages preserve interrupted attempts and verify completed inventories on resume. Their current result remains `selected_material: null`, `quality_verdict: unmeasured`, `accepted: false`.

An interrupted job-level fit receives a new stage attempt; it currently does not reuse optimizer checkpoints across those attempts. The standalone joint stage supports explicit `--resume` within its own unchanged directory. A completed joint stage is reused by the job after integrity checks, including when later job finalization was interrupted. Neither recovery mode changes the fit's quality verdict.

### Source-preserving effective optical groups

The implemented `effective_optical_group_v1_experiment` profile retains complete source primitive instances, including closed or multipart geometry, under an explicit group hypothesis. It requires `--lens-candidates`, `--lens-fit-mode joint`, and one of the two grouping modes below. Unsupported combinations are rejected before job writes. The programmatic equivalents are `run_job(..., optical_profile='effective_optical_group_v1_experiment', optical_grouping=..., optical_group_declarations=...)`.

| Grouping mode | Declaration option | Meaning |
| --- | --- | --- |
| `source_part_hypotheses` | Must be omitted | Propose one group per source part selected by the existing role, extension, transmission or name evidence; preserve the complete selection ledger. |
| `explicit_declarations` | Required JSON path | Supply source-bound group membership for complete primitive instances, with checked node/mesh/primitive bindings. |

For automatic source-part hypotheses:

```powershell
python -m reconstruction.job --request C:/path/request.json --output data/jobs/group-hypotheses-v1 --region-weights C:/path/sam2.1_hiera_base_plus.pt --lens-candidates --lens-fit-mode joint --optical-profile effective_optical_group_v1_experiment --optical-grouping source_part_hypotheses
```

Automatic group/member IDs derive from node, mesh and primitive indices. Equal materials do not merge groups, and disconnected objects inside one source primitive are not automatically separated. A fused frame/lens part can remain a wrongly grouped hypothesis; source metadata may miss a lens or select a non-optical object. The full selected/unselected ledger remains available, and no selected parts yields `no_candidate_optical_parts`. This mode is usable without product-specific declarations but does not establish optical identity or complete semantic coverage. Its coordinate frame is explicitly unitless with unverified units/front direction; it does not apply optional dimensions.

For explicit grouping, add `--optical-grouping explicit_declarations --optical-group-declarations C:/path/groups.json` instead. The declaration must be UTF-8 JSON without a byte-order mark. This example describes two complete source primitive instances in one group; replace the illustrative SHA and indices with those of the actual retained candidate:

```json
{
  "schema_version": 1,
  "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "coordinate_frame": {
    "id": "source-world",
    "units": "meters",
    "up_axis": "+Y",
    "forward_axis": "+Z",
    "provenance": {"method": "caller declaration of retained source coordinates"}
  },
  "provenance": {"method": "supplied whole-part membership hypothesis"},
  "groups": [
    {
      "group_id": "optical-group-0",
      "members": [
        {
          "id": "member-0",
          "source_part_index": 0,
          "source_binding": {"node_index": 0, "mesh_index": 0, "primitive_index": 0}
        },
        {
          "id": "member-1",
          "source_part_index": 1,
          "source_binding": {"node_index": 1, "mesh_index": 1, "primitive_index": 0}
        }
      ]
    }
  ]
}
```

These structural fields are required and additional structural fields are rejected. `source_sha256` is a lowercase 64-character SHA-256. Frame, group and member IDs match `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`. Group IDs and member IDs must each be unique across the declaration; source bindings and source-part indices cannot appear twice. Groups and member lists are nonempty. All indices are nonnegative integers. Supported declared units are `meters`, `millimeters`, `centimeters` and `unitless`; the required axes are `+Y` up and `+Z` forward. Both provenance objects are nonempty finite JSON objects. Duplicate JSON keys and nonfinite values are rejected. Coordinate declarations record assumptions; they neither reorient/rescale the geometry nor verify physical units.

Membership is bound to the **actual retained geometry after refinement**. `source_part_index` is only a checked convenience index: its node/mesh/primitive tuple and the complete GLB SHA must also match. If a declaration names the initializer but refinement retains different bytes, preparation reports `unsupported_optical_group_preparation`, records the mismatch, and does not fit or export optical previews. The current geometric candidate is preserved. The job never transfers membership by name/order, drops an unsupported member, or requests a replacement provider model to resolve this mismatch. Any supplied assertion of verified identity remains unverified in the preparation result.

The preparer snapshots the source, stores prepared group reports and primitive arrays, and exports a neutral diagnostic GLB only when the entire requested group set is supported. Success is `prepared_optical_group_candidate`. Joint fitting uses the actual exported coordinates and emits complete group descriptor sets through the grouped exporter; missing group observations do not receive guessed clear materials. `optical_candidates` separately records `optical_profile`, `grouping_mode`, `preparation_status`, `fit_status` and `preview_export_status`. A neutral preparation, diagnostic fitted preview, or software compatibility check never sets `selected_material`, accepts the reconstruction, or replaces `candidate.glb`. Experimental AR transport and its remaining numerical qualification are documented separately in [effective-group runtime evidence](EFFECTIVE_GROUP_RUNTIME.md).

### Inferred physical groups (photos-only grouping)

`--optical-grouping physical_group_inference` replaces the whole-part and hand-declared modes with the inference chain described in [the physical-group document](PHYSICAL_GROUPS.md). It requires the effective group profile, lens candidates, joint fitting, a region engine and an explicit local aperture engine:

```powershell
python -m reconstruction.job --request C:/path/request.json --output data/jobs/example `
  --region-weights C:/local/sam2.1_hiera_base_plus.pt --region-weights-sha256 <sha256> `
  --lens-candidates --lens-fit-mode joint --optical-profile effective_optical_group_v1_experiment `
  --optical-grouping physical_group_inference --aperture-weights data/models/glasses-detector-v1
```

`--aperture-weights` names the local Glasses Detector v1 folder whose download receipt pins the lens weights; nothing is downloaded and no photo leaves the machine. `--lens-jacobian-mode analytic` selects the faster joint-fit derivatives; `--appearance-tolerance-codes` sets the family-selection tolerance (default 1.0 code). The aperture engine identity, the physical-group policy and the selection policy are part of the pinned job settings.

The pieces of a declared optical group carry `declared_role: optical` in the partition declaration, and the partition writer stamps `partRole: optical` (plus `pieceId`) on that piece's primitive extras; the loader reads a primitive role before the node role. The region stage therefore proposes optical priors for the hypothesis's own groups regardless of source materials or names, which is what makes a raw provider split (no materials, no roles) fittable.

Before projecting, the stage marks interior contact faces: faces whose sorted world corners equal those of a face in another primitive (`reconstruction/interior_contact.py`, exact equality only; the receipt lists the counts per part pair under `interior_contact`). A provider split leaves such faces wherever two parts touch (a wall behind a lens, the cut between two halves of a shield). They rasterize nothing, belong to no optical group, form one `interior_contact` piece per primitive in the partition declaration, and the exporter omits them from the runtime candidate (`interior_contact_parts` in the export receipt); the partitioned source keeps their geometry.

The selected hypothesis also carries `view_registration`: for every declared group and photograph, the best interpretation's inside share and whether that (group, photograph) is eligible for the fit (share at or above `optical_inside_fraction`). Ineligible pairs are passed to the joint stage as `group_photo_exclusions` (pinned in its request and listed in the observation report as `registration_excluded_observations`), so a part that projects onto the wrong pixels in a foreshortened photograph is fitted only from the photographs that measure it.

After refinement the job runs these stages, each resumable and inventoried like the others:

1. `physical_groups`: exact component inventory of the retained candidate; image-only lens apertures per photo at two crop policies; complete component projection under the refinement cameras; group hypotheses at the fixed tolerance ladder; per-hypothesis ray composition; an explicit composition rank; the partition/preparation bridge and a verified camera transfer for every distinct consensus declaration. The report records every hypothesis, its score and its bridge outcome; the selected hypothesis is the highest-ranked one with an executed bridge.
2. `hypothesis_regions`: the unchanged region stage on the selected partitioned candidate with the transferred cameras. The generic `regions` stage on the unpartitioned candidate is not run in this mode.
3. `photo_lens_fit`: the unchanged grouped joint fit against the bridged preparation.
4. `appearance_selection`: among the exported family previews, keep every preview whose worst group error is within the tolerance of the best and choose the least complex family (`least_complex_family_within_tolerance_v3`); the photo policy never narrows that pool. The selection report lists every alternative, the identifiability outcome, `photo_policy_pass` for the shipped preview, `policy_verdict` (`shipped_within_policy`, `indistinguishable_from_within_policy`, `within_policy_candidate_beyond_tolerance`, `validation_unmeasured` when a row had fewer than `minimum_validation_points_per_photo` held-out samples, `no_candidate_within_policy`), `any_candidate_within_policy` and the AR probe spread.

The selected preview is copied to `candidate-appearance.glb` beside the geometry `candidate.glb`, and both hashes enter the terminal artifact inventory. The report's `optical_candidates.appearance_candidate` names the family, the score basis and the selection rule. It remains `accepted: false` with `quality_verdict: unmeasured` and `selected_material: null`: the choice is a stated rule applied to diagnostic candidates, not a verified material. Exported candidates convert undeclared legacy optical materials (pads, fragments, remainders of a lens primitive) to opaque copies so the AR runtime's single optical interface accepts them; the export receipt lists every demotion.

### Group declaration recovery

An explicit declaration is read once for parsing/hash capture and copied byte-for-byte to job-root `optical-group-declarations.json`. `settings.optical_groups` pins its original path, SHA, declared source SHA and snapshot path together with the profile/grouping mode. The preparer saves its own exact `declarations.json` and `source.glb` snapshots in the preparation attempt; completed stage inventories pin those and every generated artifact. Preparation and fit journal entries also bind grouped settings/hash and the retained source artifact/hash.

Resume requires the same settings and implementation. A still-present original declaration must be unchanged; a missing original may use the verified job snapshot when the caller supplies the same original declaration path. Altering the original, snapshot, profile, grouping, stage metadata or saved artifacts refuses reuse. Grouped output, lock/journal, declaration and snapshot paths are checked for symlinks/reparse points before path resolution can hide an alias. These checks do not confer trust in the declared semantics.

An interrupted grouped preparation or job-level fit uses a new attempt, preserving old evidence. Completed grouped stages are reused after their inventories and settings are verified. No optimizer checkpoints are copied between job attempts. The standalone joint stage's separate explicit-resume contract remains unchanged.

## Job recovery and acceptance

`reconstruction/job.py` owns an OS lock, immutable request snapshot, version/package hashes, stage attempts and artifact inventories. A leftover lock file alone does not mean a process is alive. Only one active caller can advance a job.

Repeat the same command to resume. Completed stages must match their pinned files exactly. Request, settings, region-engine identity or implementation changes require a new output directory. Existing original files must still match their captured hashes; missing originals can be tolerated once complete snapshots exist. Interrupted local refinement or region inference receives a new attempt directory, preserving partial artifacts. The initializer retains its existing task/receipts across resume instead of creating a second generation request.

Retained refinement proposals must match the export hash and have at least two nonregressing comparisons against the actual exported geometry. A lingering or rejected `proposal.glb` cannot win merely because it exists. When refinement cannot retain a proposal, `candidate.glb` preserves the initializer and the report identifies that fallback. An interrupted or uncertain stage is never promoted by file existence alone.

Current successful stage execution returns **`candidate_available`**, **`quality_verdict: unmeasured`**, **`accepted: false`**. Required quality gates explicitly include semantic component coverage, camera/articulation identification, contact/intersections, dimensions, photo lens inference, optical layers, actual candidate AR appearance and independent unseen-product validation. Those missing capabilities cannot be replaced by passing integrity or software checks.

Output files include `request.json`, `job.json`, `report.json`, immutable stage artifacts and, when available, `candidate.glb`. Generated jobs stay private under ignored `data/`. Do not commit original product photos, provider results or old modeling archives.

## Validation and remaining work

The earlier region/job baseline ran **284 tests successfully** (one optional real SAM compatibility test skipped in ordinary discovery and passed separately). Subsequent provider checks are described above; see [current measured evidence](PROGRESS.md) for the latest frozen full-suite result. Tests cover EXIF/color/alpha handling, exact GLB copying, view priors, provider uncertainty, locks, interrupted refinement/regions, engine identity, changed sources and tampering. Actual synthetic jobs exercise local stages; injected failures test recovery. These software checks do not establish product fidelity.

The grouped job integration has **10 focused passing tests** in `tests/test_group_job.py`. They cover both grouping dispatch modes, invalid options/schema/BOM rejection before writes, original/snapshot/settings tampering, missing-original recovery, actual Windows junction rejection, changed declarations during preparation, retained-candidate SHA mismatch, and interrupted-fit attempt preservation. One small job uses real photo intake, GLB initialization, region processing, multipart group preparation, photo sampling, three bounded joint optimizer starts, preview export/reload and byte-immutable terminal reuse. Its cameras and segmentation engine are controlled test fixtures; its source has two closed group members plus opaque geometry. It verifies orchestration and preservation, not inferred camera accuracy, neural segmentation quality, real-product appearance or accepted reconstruction.

`scripts/job_corpus.py` builds pinned requests from the saved refinement corpus. It supports `--case`, `--prepare-only`, `--resume` and explicit `--archive-dimensions`. Archived dimension provenance is recorded separately; importing it never applies or verifies the values. Existing saved models were influenced by their old photo sets, so those photos are not independent holdouts.

```powershell
python scripts/job_corpus.py --manifest data/refinement-corpus.json --output data/job-corpus/integration-v1 --case rayban --archive-dimensions
python scripts/job_corpus.py --manifest data/refinement-corpus.json --output data/job-corpus/integration-v1 --case rayban --archive-dimensions --resume
```

See [current measured evidence](PROGRESS.md) for completed real-case results. Automatic semantic observations, dimension/articulation constraints, photo-conditioned optical material/lighting inference, generic surface preparation, actual-candidate validation through the bounded optical-layer renderer and frozen unseen-product evaluation remain necessary before this meets the full objective. Renderer fixture success does not automatically satisfy a reconstruction job's optical-quality gate.
