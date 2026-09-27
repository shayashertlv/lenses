# API comparison results — 23 September 2026

## Decision

**Tripo is the strongest new initializer candidate on these two products. It is not a finished AR asset generator.** It preserves the Oakley's green/purple front and pink rear appearance more closely than the other new providers, with a cleaner-looking outline than the cached Meshy result. Its Miu shape and metal/temple details are also promising. This is a qualitative judgment from inspected renders, not a calibrated accuracy score or proof across a catalog.

Rodin and TRELLIS.2 did not justify replacing Meshy on this initial test. Rodin loses major lens appearance cues. TRELLIS.2 adds outline/surface errors and misses unseen rear appearance. Do not generalize this single-seed result into a universal provider ranking.

## What actually ran

Six new paid generation requests completed: two products each through Rodin Gen-2.5 on fal, Tripo H3.1 with explicit v3.5 texture/de-lighting, and TRELLIS.2 on fal. Existing original Meshy GLBs were reused without new Meshy calls. The [settings contract](API_COMPARISON_CONTRACT_2026_09_23.md), [photo manifest](../data/provider-comparison-v1/inputs.json), and individual `runs/*/request.json`, `submission.json`, `result.json`, and `artifacts.json` preserve inputs, task IDs, outputs and hashes. No duplicate generation submissions were made.

- Oakley: continuous sports shield with colored mirror appearance.
- Miu: thin gold metal, rimless, nearly clear lenses. This is a difficult non-sport case, not representative of ordinary thick full-rim frames.
- Rodin received five existing photos, angled first. Tripo and original Meshy each received the same four named front/back/side source views. TRELLIS.2 received the angled photo only.
- Exact photographed hinge angles were not established. No fully folded/open contradiction was visibly apparent. Input bytes were preserved; no synthetic views or inferred alpha masks were used.

The budget was six successful generations. Tripo reports **60 credits per task, 120 total ($1.20 at listed rates)**. fal usage is **estimated $1.50** from the verified model prices/resolution; actual per-request billing was not retrieved because the provided API-scoped key does not grant the documented Admin billing access. Estimated total: **$2.70**, not an assertion of a verified combined invoice.

## Inspected outputs

| Product | Provider | Triangles | File size (MiB) | Visible result |
| --- | --- | ---: | ---: | --- |
| Oakley | Meshy, cached raw | 641,558 | 28.01 | Recognizable colored front/rear; noisy highlights and surface/outline defects remain |
| Oakley | Rodin | 500,000 | 20.18 | Lavender front; lost green center and pink rear distinction |
| Oakley | Tripo | 1,940,651 | 58.69 | Strongest new source resemblance; front/rear color cues retained, still painted opaque |
| Oakley | TRELLIS.2 | 496,648 | 19.19 | Distorted outer shield regions, pale multicolor front, dark rear |
| Miu | Meshy, cached raw | 252,920 | 16.78 | Recognizable construction, solid white lens plates |
| Miu | Rodin | 500,000 | 22.47 | Dark brown fronts and colored lens perimeter unlike clear rimless source |
| Miu | Tripo | 1,944,565 | 57.18 | Promising shape/metal/temple detail, but clear-looking regions are opaque white |
| Miu | TRELLIS.2 | 495,897 | 16.76 | Weaker bridge/temple detail and solid white lenses |

Full views: [Oakley comparison](../data/provider-comparison-v1/render-final/oakley__raw__light__comparison.png), [Miu comparison](../data/provider-comparison-v1/render-final/miu__raw__light__comparison.png), [Miu on a checker background](../data/provider-comparison-v1/render-final/miu__raw__checker__comparison.png), [Oakley normal diagnostic](../data/provider-comparison-v1/render-final/oakley__normals__light__comparison.png).

Raw rendering uses the same Three version as AR, a common studio environment, orthographic views, ACES tone mapping and native GLTFLoader materials. It applies only orientation, centering and uniform scale. Tripo needed a -90-degree Y rotation; other providers needed none. All orientations were visually checked. Photos retain their original lighting/perspective, so this is not an aligned pixel-error experiment. The normal diagnostic removes normal-map texture detail and is not a measured surface-accuracy metric. [Rendering report](../data/provider-comparison-v1/render-final/report.json), [orientation verification](../data/provider-comparison-v1/orientation-verification.json).

## What the APIs did not solve

### Lenses are still rendered as opaque surfaces

Every raw model has one active mesh primitive, one opaque material, and no material extension providing optical transmission. Checker-background renders expose the Miu white plates clearly. Apparent clearness on a white background is not transmission. Material assignment remains necessary for both sport and non-sport products.

### Tripo's parts cannot be recovered by disconnected-component splitting

Both Tripo models have one connected component after matching exactly equal positions across UV/normal seams. Under shared-edge connectivity, Miu is one component; Oakley has one 1,940,649-face component and two isolated single-face components. Hundreds of apparent index islands are seams, not usable separately labeled lenses and temples. Surface classification/cutting or replacement lens surfaces remain necessary. [Exact connectivity audit](../data/provider-comparison-v1/geometry-audit.json).

### The high-detail assets exceed current delivery limits

The current production policy is 150,000 triangles and 15,000,000 bytes. Tripo is approximately 13 times the triangle limit and four times the byte limit. Its sharper result was obtained at much higher geometry density; the test does not establish equal-budget superiority. Mobile simplification must be evaluated separately, preserving thin temples, shield outline and material boundaries. Actual mobile frame rate was not measured here.

### Raw handover is incompatible with the current AR asset contract

The unchanged `TryOnRenderer` was exercised on all eight original GLBs. All rejected raw handover with `Rear drop requires physical lens geometry to protect the optical front.` This is an expected integration requirement for optical preparation, not an independent verdict that all eight shapes are unusable. No synthetic lens labels or geometry edits were introduced to make the check pass. [Actual AR loader report](../data/provider-comparison-v1/ar-final/report.json).

## Concrete next experiment

The evidence favors improving the Tripo-to-AR handoff next. It does not favor a broad generator rewrite or expanding Rodin/TRELLIS usage yet.

1. **Test guided surface separation on the two saved Tripo assets.** Implement a standalone `prepare_tripo_segmentation_request(asset, reference_mask)` using the documented `/v3/mesh/segment` endpoint, then `verify_segmented_surface(original, result)` to measure surface/outline retention and projected part membership. A color-coded mask must be tied to the reference view expected by that API; do not assume an arbitrary catalog mask is aligned. Recover/re-render all parts before trusting names. This is an unproven API experiment, not promised correct lens extraction.
2. **Build optical candidates on the separated lens surfaces.** Reuse `reconstruction.initializer.resolve_initial_model(kind='existing_glb')` and existing group/preparation/export paths with new source hashes and face bindings. For fused/noisy surfaces that separation does not fix, resume the previously paused clean-shield constructor experiment instead of painting transparency over frame triangles.
3. **Compare materials while holding geometry fixed.** Tripo's texture endpoint offers a later de-lighting on/off test for frame regions. Preserve logos and photographed pattern evidence. This benchmark cannot attribute appearance differences to the de-lighting flag, because it did not run an ablation. Optical tint/coating remains a separate stage.
4. **Simplify and verify in AR.** Evaluate a <=150k-triangle candidate against the detailed source, then test front, angled, side and rear views with prepared optics in the actual renderer. Passing a file/triangle budget is not sufficient evidence of acceptable appearance.

These follow-up paid operations were **not run**. The current deliverable is the completed six-generation comparison and reusable local tools. More products, seeds and a full-rim example remain needed before choosing a catalog-wide default.

## Implementation and verification

- `qa/provider_benchmark.py`: pinned requests, explicit dotenv credentials, upload/submit/resume/download, exclusive submission reservation, output hashes and GLB container validation.
- `qa/provider_comparison_manifest.py` and `qa/provider_comparison.py`: raw asset manifests and source-photo comparison sheets.
- New `ar/qa/provider-comparison*` harnesses: native raw rendering and separate actual-AR handover checks. No live AR source/deployment change.
- 13 mocked provider tests pass, including no duplicate submission after ambiguous timeout, exact task resumption, upload errors, source hash changes and corrupt GLB rejection.
- Eight raw assets rendered successfully into 160 individual images and eight full comparison sheets, without browser/resource errors. All eight actual-AR handover failures were recorded, not converted into acceptance.

Reproduce locally from `automation` (these commands make no paid requests):

```powershell
python -m unittest discover -s tests -p test_provider_benchmark.py -q
python -m qa.provider_comparison --manifest data/provider-comparison-v1/render-manifest-final.json --output data/provider-comparison-v1/render-final
python -m qa.provider_comparison --manifest data/provider-comparison-v1/render-manifest-final.json --output data/provider-comparison-v1/ar-final --ar-check
```
