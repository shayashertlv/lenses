# API comparison: documentation audit and initial test contract

The six-generation experiment has now completed. See [measured outputs and next steps](API_COMPARISON_RESULTS_2026_09_23.md). The text below preserves the original pre-execution plan.

Reviewed 2026-09-23. This is a proposed experiment, not a report of generated results. No accounts, uploads, paid requests or provider adapters were created in this review. Fittingbox is excluded at the user's request. Earlier custom geometry/material experiments remain paused.

## Accounts and budget

Only two new credentials are needed:

| Credential | Create it | Purpose | Planned initial usage |
| --- | --- | --- | --- |
| `FAL_KEY`, API scope | https://fal.ai/dashboard/keys | Rodin Gen-2.5 and TRELLIS.2 | Two Rodin jobs at $0.40, two TRELLIS jobs at $0.35: $1.50 |
| `TRIPO_API_KEY` | https://developers.tripo3d.ai/en/keys | H3.1 geometry with explicitly selected v3.5 textures | Two jobs at 60 credits/$0.60: 120 credits/$1.20 |

Total estimated consumption is **$2.70 for six successful generations**, excluding reruns, add-ons, taxes or minimum account funding. Check live rates/balances before submission. Minimum deposits and current signup credits were not established from public documentation; do not promise either. Tripo's old trial-credit pages conflict with newer documentation. A direct Hyper3D subscription/key is unnecessary for this initial comparison.

Store credentials in the git-ignored `automation/.env` or the process environment. fal API scope is sufficient; ADMIN is unnecessary. Tripo authentication uses Bearer; fal uses `Authorization: Key ...`. [fal authentication](https://fal.ai/docs/documentation/setting-up/authentication), [Tripo authentication](https://developers.tripo3d.ai/en/docs/authentication).

Prices: [Rodin on fal](https://fal.ai/models/fal-ai/hyper3d/rodin/v2.5), [TRELLIS.2 on fal](https://fal.ai/models/fal-ai/trellis-2), [Tripo pricing](https://developers.tripo3d.ai/en/pricing), [H3.1 pricing](https://developers.tripo3d.ai/en/models/v3-1). Tripo's 60-credit estimate comprises 30 basic, 10 detailed texture and 20 detailed geometry credits. fal uses prepaid credit. [fal billing](https://fal.ai/docs/documentation/model-apis/pricing), [Tripo billing](https://developers.tripo3d.ai/en/docs/billing).

## Rodin Gen-2.5 through fal

Use `fal-ai/hyper3d/rodin/v2.5`, not the older `/v2`. It accepts up to five photos and returns `model_mesh`, optional `model_meshes`, and textures. Select GLB/PBR explicitly. Starting request, with actual input URLs supplied after photo review:

```json
{
  "image_urls": ["<primary>", "<compatible-other-views>"],
  "tier": "Gen-2.5-High",
  "geometry_file_format": "glb",
  "material": "PBR",
  "quality_mesh_option": "500K Triangle",
  "texture_mode": "high",
  "enable_creative_mode": false,
  "texture_delight": true,
  "hd_texture": false,
  "use_original_alpha": false,
  "seed": 42,
  "preview_render": true
}
```

These are proposed experiment settings, not a validated eyewear preset. Keep detail initially; simplify separately. The wrapper maps creative=true to the direct creative strategy; false is its fidelity-oriented option, but wrapper source-level translation was not verified. Do not request HighPack initially. Inspect all returned files rather than assuming the first texture is a material map. [fal schema](https://fal.ai/models/fal-ai/hyper3d/rodin/v2.5/api), [OpenAPI](https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=fal-ai/hyper3d/rodin/v2.5).

Direct Hyper3D additionally exposes direction labels, symmetry, soft edges, detail, normal baking, UHD/extreme-high textures and Hybrid material, which fal's schema does not expose. The first input guides material generation. No calibrated-camera or recovered lens-transmission guarantee is documented. Direct integration is `POST /api/v2/rodin` with repeated multipart images; save UUID and jobs subscription key, poll `/status`, fetch `/download`. Direct access is listed on Business at $120/month. It is an escalation option if missing controls become consequential. [Direct Gen-2.5](https://docs.hyper3d.ai/en/api-specification/rodin-gen2-5), [quick start](https://docs.hyper3d.ai/en/get-started/quick-start), [plans](https://hyper3d.ai/pricing).

## Tripo direct v3

Use `POST https://openapi.tripo3d.ai/v3/generation/multiview-to-model`. Starting request:

```json
{
  "inputs": [
    {"front": {"file_token": "<front>"}},
    {"left": {"file_token": "<left>"}},
    {"back": {"file_token": "<back>"}},
    {"right": {"file_token": "<right>"}}
  ],
  "model": "v3.1-20260211",
  "geometry_quality": "detailed",
  "texture": true,
  "pbr": true,
  "texture_version": "v3.5-20260815",
  "texture_quality": "detailed",
  "texture_alignment": "original_image",
  "delight": true,
  "model_seed": 230923,
  "texture_seed": 230923,
  "auto_size": false,
  "quad": false,
  "smart_low_poly": false,
  "generate_parts": false
}
```

Requires 2–4 views including front; omit incompatible views. Named keys avoid positional-order mistakes. No face limit initially. Part generation conflicts with texture/PBR, and quad output changes format to FBX. Listed material channels are base color, metallic, roughness and normal, without guaranteed lens optics. [Multiview contract](https://developers.tripo3d.ai/en/docs/generation-multiview-to-model/standard).

Crucial version detail: H3.1 alone defaults to older v3.0 textures. Explicit texture v3.5 is required for delight; older texture versions ignore that flag. fal's current H3.1 wrapper lacks both controls, which is why this test needs a direct Tripo key. [Changelog](https://developers.tripo3d.ai/en/docs/changelog), [fal H3.1 schema](https://fal.ai/models/tripo3d/h3.1/multiview-to-3d/api).

Use Python HTTP against v3: official Python SDK 0.4.2 primarily uses v2 and lacks v3 generation. Upload PNGs under 20 MB with `POST /v3/files`; save tokens. Submit once, persist task ID, poll `GET /v3/tasks/{id}`, then download `output.model_url` immediately. Signed URLs are documented as expiring after five minutes; re-query the existing task for a fresh URL. [SDK](https://developers.tripo3d.ai/en/docs/sdk), [uploads](https://developers.tripo3d.ai/en/docs/files), [quick start](https://developers.tripo3d.ai/en/docs/quick-start).

## TRELLIS.2 through fal

Use `fal-ai/trellis-2` with the best existing angled photo of each product:

```json
{
  "image_url": "<angled-photo>",
  "seed": 20260923,
  "resolution": 1536,
  "decimation_target": 500000,
  "texture_size": 2048,
  "remesh": true
}
```

The decimation target counts vertices, not triangles. Save documented guidance defaults with the request. Output is `model_glb.url`. No camera, semantic part, de-lighting or physical optical controls are exposed. A shared schema mentions multiple images, but a callable public TRELLIS.2 multiview route was not verified; do not send `image_urls` to this endpoint or substitute old TRELLIS silently. This comparison arm is explicitly single-image. [Generation API](https://fal.ai/models/fal-ai/trellis-2/api).

Microsoft's native model predicts alpha as well as material channels, but native export defaults to OPAQUE. fal's exported alpha behavior and preprocessing implementation need inspecting. Background removal can erase thin or translucent parts; preserve source photos and do not invent transparency masks. [Microsoft implementation](https://github.com/microsoft/TRELLIS.2), [preprocessing](https://raw.githubusercontent.com/microsoft/TRELLIS.2/main/trellis2/pipelines/trellis2_image_to_3d.py).

## Shared execution and comparison design

Use the existing Oakley and Miu photos. Check source views for compatible temple poses before upload. Retain source bytes and hashes, normalize format only where needed, and never synthesize missing views for this experiment. Camera directions are not interchangeable between providers. Images used for development review are not a new independent validation set.

The initial six calls assess each provider's practical high-detail output, not equal input-view counts or equal triangle counts. Keep those differences visible in the report. Compare against cached Meshy assets without new Meshy calls. Inspect raw geometry, material channels and unmodified renders first, then use the same AR engine. Score recognizable shape, lens/frame separation, lens smoothness, temples, logos/colors, triangles, file size and runtime. Do not automatically conclude that an opaque GLB default means the model contains no usable lens surface.

Proposed functions for the next implementation turn (not implemented here):

1. `prepare_provider_request(product, provider, settings)` creates a pinned photo ledger and validated provider payload.
2. `submit_once(request, budget, output)` saves a submission reservation and task receipt; it never retries an uncertain paid submission.
3. `resume_provider_task(receipt)` polls the original task, preserving structured errors and terminal states.
4. `download_provider_artifacts(receipt)` promptly saves files, hashes and actual mesh/material inventory.
5. `build_raw_comparison_manifest(artifacts)` renders original assets plus diagnostic materials with documented cameras/lighting.
6. `compare_provider_candidates(manifest, source_photos)` produces photo comparisons, geometry/material observations and measured size/performance.

The current `reconstruction.initializer.resolve_initial_model()` already accepts `existing_glb`. Use that bridge for promising candidates without mislabeling their provider history as verified Meshy history. Full provider routing can follow after evidence of improvement.

For fal, use Python `fal_client.upload_file/submit`, persist request ID, poll status, retrieve result. A completed queue entry may contain an error. Disable provider queue retries for the first benchmark with `X-Fal-No-Retry: 1`; a client timeout never implies cancellation. Download temporary files immediately. [Queue](https://fal.ai/docs/documentation/model-apis/inference/queue), [retention](https://fal.ai/docs/documentation/model-apis/media-expiration).

Tripo lifecycle/error pages disagree on some terminal/error codes; handle HTTP status and structured body, preserve unknown failures, and resume by task ID. Record actual `credits_consumed`. No public submission idempotency contract was established. [Lifecycle](https://developers.tripo3d.ai/en/docs/task-lifecycle), [errors](https://developers.tripo3d.ai/en/docs/error-handling).

## Useful second experiments, outside the six-generation budget

- Tripo `/v3/models/texture`: retexture existing geometry using multiple references, v3.5 and a delight toggle. This allows a geometry-fixed lighting-removal comparison. HD retexture is listed at 20 credits. [Texture API](https://developers.tripo3d.ai/en/docs/models-texture).
- Tripo `/v3/mesh/segment`: beta `v2.0-20260430` accepts a color-coded reference mask. Test whether our observed lens/frame regions can guide actual 3D separation; correctness remains unproven. Listed at 40 credits. Retopology can separately address mobile complexity. [Segmentation](https://developers.tripo3d.ai/en/docs/mesh-segment), [retopology](https://developers.tripo3d.ai/en/docs/mesh-decimate).
- fal `fal-ai/trellis-2/retexture`: one existing mesh and one reference image, returning GLB. [Retexture API](https://fal.ai/models/fal-ai/trellis-2/retexture/api).
- Direct Rodin Bang offers instructed part splitting; its texture-only API can retexture an existing mesh. These require a separate direct-access decision, not an additional key now. [Bang](https://docs.hyper3d.ai/en/api-specification/bang), [texture-only](https://docs.hyper3d.ai/en/api-specification/generate-texture).

No reviewed provider documentation establishes accurate sunglass transmission or mirrored coating recovery from our inputs. These tests can determine whether better initial geometry, explicit texture de-lighting, or provider part separation substantially reduces our remaining custom work.
