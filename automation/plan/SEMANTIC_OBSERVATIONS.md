# Product-photo observations

2026-09-21. Automatic region hypotheses and conditional color observations are implemented. Verified semantic identification and uncalibrated optical inference remain unfinished.

The existing photo heuristics cannot establish which pixels belong to lenses, frames or temples. They threshold a mostly white background, fill holes and select contrasting interior regions. The old `analyse_photo` even returns a full-image silhouette for an empty white image because its empty connected-component result selects background label zero. Native preparation labels generated geometry, and the old Astra operation returns editing scripts; neither supplies independent component observations from the photos. Preserve their outputs as hypotheses rather than importing their acceptance rules.

## Available local mask engine

An existing SAM2.1 Hiera base-plus checkpoint loads offline with all 615 state keys matching through the installed Ultralytics architecture builder. The probe uses local `torch.load(weights_only=True)`, strict state loading and blocked network access. No package, checkpoint or original image was changed. The checkpoint SHA-256 is `a2345aede8715ab1d5d31b4a509fb160c5a4af1970f199d9054ccfb746c004c5`.

On one archived Ray-Ban front photo, a whole-image box produced background masks with scores up to 0.995. A box from the conservative contrast observer produced a plausible whole-product mask with score 0.976 and IoU 0.979 against that observer's foreground. That agreement is between two automatic hypotheses, not against a verified reference. Other candidates omitted interior regions. CPU inference was about 1.8 seconds per prompt; the complete process took about 8 seconds. One image establishes local runtime compatibility, not general accuracy or performance.

Detailed source references, probe commands and pinned results are in the private `data/semantic-probe-v1/AUDIT.md`. The installed implementation's AGPL dependency must be considered when selecting the deployment backend; this local experiment does not select a production dependency.

## Executable region stage

`reconstruction/region_engine.py` provides an explicitly configured offline SAM2.1 Base Plus adapter: pinned local weights, strict tensor-only state loading, CPU float32 and one embedding per photograph. Every prompt returns three alternatives, including duplicate or empty masks. Stable provenance pins the checkpoint, package versions, adapter and installed Ultralytics Python/configuration sources. Mutable load details live in a separate receipt, permitting verified resume after a fresh model load. Native binaries are version-identified. The temporary offline guard is process-global during calls; unrelated network work must not run concurrently in that process.

`reconstruction/region_proposals.py` uses one bounded policy for all inputs. Conservative contrast supplies an **unknown-object** box. A photograph-bound camera and exact candidate hash can additionally supply frontmost optical component priors. Declared roles, canonical extension presence, transmission materials and optical/lens name tokens are recorded as distinct **unverified** bases. Remaining geometry is not silently promoted to a verified frame partition.

Every region receives five boxes: original, ±6% scale and ±4% horizontal shift. All 15 masks survive. Each original-box alternative is matched to the best IoU alternatives in the other four runs. All co-best matches within absolute 1e-12 contribute to intersection/union, and distinct-mask association ambiguity is recorded. Decoder ordering therefore cannot silently change measured color. The 0.85 prompt-stability diagnostic is not calibrated semantic confidence. Background masks can be stable; border contact, area and prior agreement remain separate diagnostics. No final mask is selected.

`reconstruction/photo_appearance.py` measures the intersection interior after fixed 5×5 erosion. It records raw RGB, inverse-sRGB image signal, robust spread, potential 0/255 endpoint clipping, alpha coverage and at most 64 diagnostic samples. Statistics use every eligible pixel, not just the samples. Only alpha=255 contributes. Region inference currently declines nonopaque inputs instead of inventing a background.

Candidate optical regions provide normalized authored-Y height **proxies** and triangle-normal incidence under the fitted camera. Native pixels map to nearest working-grid pixels using the recorded center transform; fields outside the projection stay unknown. Four height bins describe conditional photographed signal, not verified intrinsic UVs or recovered absorption gradients. Inverse-sRGB is not calibrated scene radiance. Illumination, reflected environments, transmitted backgrounds, camera error and surface-normal error remain unresolved.

Direct calls require normalized, single-frame RGB/RGBA; remaining ICC profiles, non-RGB modes and unapplied EXIF are rejected. Jobs supply pinned PNGs from `input_bundle`. Untagged photos retain an uncalibrated sRGB assumption. Image, model, camera-report and engine bindings are checked before and after inference. Configure the job's `--region-weights` and optional `--region-weights-sha256`; no checkpoint is downloaded. Missing configuration reports `engine_unavailable`. Interrupted attempts are preserved, and completed artifacts are verified on resume. Regions and colors do not satisfy the semantic/material quality gates.

The adapter passed an actual offline 10-prompt test: 30 native masks, one embedding, all 615 state keys and stable pre/post-load identity. The full suite ran 284 tests successfully, with the optional native test skipped in ordinary discovery and exercised separately. Controls include empty images, unrelated objects, alpha, camera/hash mismatch, rolled-camera height, exported geometry using original normalization, decoder-order ambiguity, interrupted stages and artifact tampering.

`qa/region_corpus.py` runs all five saved designs and white/unrelated-object controls with pinned original models and camera reports. It preserves every alternative and makes a sheet of all three intersection hypotheses. These development inputs are not holdouts. Execution, reproducibility and prompt stability must remain separate from semantic accuracy.

## Candidate-conditioned identity and composition diagnostics

`optical_identity.py` associates observations through supplied visible source
triangles rather than region order or a fixed left/right arrangement. Callers
provide pinned source/photo/camera identities, exact entity face membership,
visibility rasters and every mask alternative. The graph retains per-alternative
face/entity support, cross-view associations, split/merge cues, prior conflicts
and missing visibility. No shared visible face means unmeasured association, not
proof of distinct objects. A shared triangle is also coarser than a shared surface
point, so small incidental overlaps remain weak evidence rather than accepted IDs.

The five-design audit kept 255 masks over ten views. Major region numbers swap
between front and angled views for RayBan, Miu and VB. Miu's large source primitive
contains multiple substantial regions; its silicone prior has no observed
support. VB's graph preserves sixty extra links supported by only one to four
triangles alongside its strong associations. Graphs and exact mask/visibility
arrays are under `data/optical-identity-audit-v1/graphs-v2/`. Eleven tests cover
number swaps, split/merge ambiguity, missing visibility, hierarchical priors,
duplicate alternatives and capacity.

`composition_diagnostic.py` separately projects optical and opaque source
geometry and compares its boundaries with photographic edge structure. It records
front occlusion, rear content under lenses, unexplained interior edges and all
mask alternatives with source/camera pins. Six of ten saved views exceeded the
fixed unexplained-structure diagnostic; this is not a semantic failure rate.
Unexplained edges may arise from reflections, texture, frame geometry or camera
error. The diagnostic neither labels those causes nor invents a hinge pivot.

These modules expose contradictions before material interpretation. They do not
convert generated primitive names, image region ordinals or camera-dependent
overlap into verified physical lens groups. Automatic grouping and articulated
geometry inference remain separate implementation requirements.

## Remaining inference work

The first corpus color review quantifies why a single region-average tint is inadequate. Victoria Beckham front-photo low-height bins have raw RGB medians around `[211–212, 199–200, 177–178]`, versus `[132, 117, 94]` at high height. The angled near lens preserves the high bin, while the far lens drops to about `[65–66, 60–61, 58]`, consistent with the visible rear temple/hinge darkening that portion. Coordinate coverage is 96.4–98.9%, so good coordinate coverage alone cannot separate transmitted objects from intrinsic absorption. The next fitter must model the signal behind the lens as well as its material.

Ray-Ban's 12 optical hypotheses have the same `[83,82,80]` median in every populated height bin, yet angled low-decile colors differ substantially between hypotheses. Equal medians therefore do not prove equal contamination or appearance. Clear Miu regions have many 0/255 endpoint pixels, and a small gold-region hypothesis remains prompt-stable despite only 33.7–48.8% coordinate coverage and substantial cross-alternative disagreement. These are conditional image-signal observations, not identified tint, reflectance, transmission or absorption. Full input-pinned diagnostic evidence is under private `data/region-corpus/fixed-policy-v1/photometry-review/`.

Mirrored designs also resist a single color or height-only explanation. Oakley median codes change from roughly `[151–152,147–149,162–163]` in front to `[130–134,173–181,178–183]` angled, with visible lateral purple/green structure. Invu changes from `[163,207,235]` to about `[53–59,150–151,176–177]`, while its front reference is only 194×259. These differences do not identify their cause: coating response, reflected scene, transmitted content and photographic processing remain competing explanations. The existing calibrated angular-coating mismatch experiment must inform the next material-family design; a fitted vertical tint must not silently absorb all of these effects.

1. Extend the current box-only policy with independent component identity, observed normal-edge support and bounded camera/seed hypotheses, without product-specific rules.
2. Resolve component/occlusion and articulation alternatives while retaining unknown or inconsistent regions. Transparent interiors must not become missing lenses, and visible temples, reflections, shadows or printed borders must not become intrinsic lens color by default.
3. Replace candidate height/normal proxies with checked optical coordinates and propagate residual camera/surface uncertainty. Current conditional samples are a starting point; agreement with candidate projections cannot independently validate geometry or semantics.
4. Fit competing material/lighting explanations, including strong reflection, absorption gradients, spatial coating variation and image processing. Product-photo RGB alone does not separate transmitted background from reflected illumination. Compare uncertainty in predicted AR appearance across target lighting and poses; do not force a unique hidden material when the images do not constrain one.

Separately checked component evidence and unseen products remain necessary before this stage can supply acceptance gates. The initial audit retains a whole-image prompt as a background-selection failure example; it is not an unqualified production fallback. Returning a mask never changes `accepted=false`.
