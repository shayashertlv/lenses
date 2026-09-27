# Image semantics and physical optical groups

2026-09-22. The target is automatic lens/frame identity from product photos, followed by source-preserving optical groups. This work is experimental. A model prediction, stable mask, connected mesh component or successful export does not establish physical lens identity.

## Corrected geometry diagnosis

The earlier description of Miu's main optical part as a broadly fused frame/lens mesh was too strong. Direct component renders now show that its bridge and temples belong to other source parts. The optical part contains two oval lens regions, pad-shaped geometry and fragments. The nearly invisible frame in the neutral AR sheet did not, by itself, establish that the frame belonged to the transparent group. The cause of that rendered frame appearance remains unverified.

The 57,069-face optical part has 500 shared-index components, 75 components under exact world-position equality, and 329 exact shared-edge components. Index connectivity is dominated by seams. Two major components occupy each lens footprint: their front silhouette IoUs are 0.9873 and 0.9884. Nearest depth order interleaves across the surfaces, so these are not merely a front and a back sheet. Giving every connected component its own optical group would apply multiple optical events to one lens. Giving the entire source part one group would combine both lenses and pads.

The same problem appears in other designs: RayBan lenses contain multiple colocated major surfaces, and the Oakley shield overlaps two half-shield components. Group proposals therefore need spatial overlap and photographic instance support as well as source topology. Exact-position connectivity provides candidate pieces; it does not provide physical identities.

PCA thinness is also not a reliable optical classifier. The measured smallest-axis/middle-axis span ratio is roughly 0.19–0.23 for Miu's broad lens components, 0.61 for Invu's curved shield and 0.74 for Oakley's main shield. Some frame components have smaller ratios around 0.11–0.12. Those ratios combine curvature and extent; they are not optical thickness measurements. Closing boundaries, choosing the biggest component or imposing one global thinness cutoff would hide real cases.

Private evidence: `data/semantic-geometry-audit-v1/{report.json,pairs.json,miumiu-components.png,FINDINGS.md}`. The audit retains component memberships, material/source metadata and 27 overlap/depth comparisons. Original source models were not edited.

## Why the existing masks cannot decide this alone

Current SAM prompts originate from projected source parts selected by names, roles or transmission. Their masks can refine a region, but they cannot independently verify the source's optical label. Composition diagnostics use the same proposed optical/opaque division and therefore cannot serve as its semantic judge.

On Miu, intersecting all fifteen alternatives and eroding by four working pixels still retains substantial support on both overlapping components of each lens. That is expected for redundant shell representations, not evidence that one must be frame geometry. Only 1,725 source faces are geometrically visible in both photographs. Unseen surfaces and pads cannot inherit a verified label from those sparse samples.

Photo labels must distinguish the projected lens aperture from a visible opaque object behind that lens. A filled lens mask may legitimately cover a rear temple. Directly labeling every projected mesh face inside it as optical would corrupt those temples. A future lift must retain source face/barycentric support, visibility assumptions and competing transmitting/opaque explanations; shared triangle IDs alone are not point correspondences.

## Independent photo-only baseline

The public [Glasses Detector repository](https://github.com/mantasu/glasses-detector) offers separate learned lens and frame segmentation heads. The selected medium architecture is torchvision Lite R-ASPP with MobileNetV3. Its documented training sources include face crops and eyewear datasets; standalone-product proportions and clear/shield/mirror coverage are not established. Its published aggregate benchmark is not an accuracy measurement for this task.

Two local v1.0.0 checkpoints, approximately 13 MB each, were downloaded from the maintainer's release. Source commit `246ce6ade849f740f8f1bd4fb197ea2e89fc3d23`, MIT code license, source files and download hashes are retained in `data/models/glasses-detector-v1/`. No separate checkpoint model card/license was located; this local experiment does not establish rights for a future deployed model. No product images were uploaded, no package/environment was installed or changed, and inference ran on CPU with both pretrained initialization defaults disabled.

The fixed experiment in `qa/photo_semantic_baseline.py` uses only the photos:

- Ten original development photos, two existing negative controls and twelve previously unused catalog photos. The catalog subset is the first twelve unique SHA-256 values after excluding all seventy archived reference hashes. This selection does not claim absence from upstream training data.
- Original and normalized snapshots, pinned checkpoint bytes, the author's RGB/bicubic-256/ImageNet preprocessing, two independent binary heads, and raw float32 logits.
- Full image and one contrast-only crop, padded 15% on each axis. Failed contrast detection uses the full image. The crop choice does not use a generated model, optical role, camera or SAM mask.
- Horizontal flip of each same crop, with outputs aligned back. The fixed binary diagnostic is `logit > 0`; crop exteriors remain unknown, and overlapping heads remain overlapping.

The final 24-image run took 12.10 seconds. All 192 original/flip logit arrays reproduce bit-for-bit from the first run; the second run only corrects saved normalization paths. Original and normalized artifact hashes also verify. Independent source review checked architecture, preprocessing equality, flip alignment and grid/native mapping.

| Evidence | Result and implication |
|---|---|
| Development lens flip IoU, full image | Median 0.972, minimum 0.794. Useful coarse stability, not accuracy. |
| Catalog lens flip IoU, full image | Median 0.965, minimum 0.698. Reflections and difficult boundaries remain. |
| Cropped catalog lens flip minimum | 0.264 on one clear-lens example; more object pixels do not guarantee a better result. |
| Development frame flip IoU, cropped | Median 0.343, minimum 0.0. This head is not suitable for assigning frame material. |
| White control | Zero lens positives; 123 false frame pixels after native interpolation. |
| Mug control | About 14.3% of the image is falsely labeled lens, with cropped flip IoU 0.976. Stable positive predictions cannot establish that the object is glasses. |
| Visual inspection of all 24 | Coarse lenses often follow the product aperture. Frame predictions miss broad VB frames, hallucinate rims on rimless glasses and overlap lenses. Catalog floor reflections can become additional predicted lenses. |

Input manifest SHA: `b83d60a58e6d3f807cf7c83c52239e977bec2f014bc7d813a4e28bc4b998e74b`.
Final report: `data/photo-semantic-baseline-v1/results-v2/report.json`, SHA `a34a79fb805fd199291adaa5a6f0d32199511c0951cf305c53c328fc61fbf861`.
`replay-verification.json` binds the 192-array replay. Four contact sheets retain every case and both crop policies.

## Fixed semantic-to-SAM experiment

The fixed runner `qa/photo_semantic_refinement.py` completed all 24 photos with **91 prompts and 273 retained decoder masks in 95.21 seconds**. Both crop branches remained. On the 256 grid, all four-connected positive components with at least sixteen pixels supplied a box expanded 10% and one farthest-interior positive point. Three smaller components, totaling seventeen grid pixels, were recorded as omitted; no branch exceeded the thirty-two-component capacity. All three SAM2 decoder alternatives survived every prompt. There was one embedding per nonempty photograph (23 total); white supplied no prompt. No mesh, generated role, camera or product-specific prompt entered this experiment.

| Fixed-case observation | Consequence |
|---|---|
| Mug: all six SAM alternatives cover 14.80–15.44% of the native photo; coarse/SAM IoU 0.833–0.929, SAM score up to 0.979 | Model agreement and confidence cannot establish optical identity. |
| VB and RayBan: alternatives expand onto rims, bridges or hardware | Boundary refinement is not reliably lens-boundary refinement; using these pixels for tint estimation contaminates the fit. |
| Miu: full-image proposals include projecting temples; cropped alternatives are tighter but retain hardware ambiguity | One crop branch cannot be selected globally as the correct semantic answer. |
| Oakley/Invu: hardware inclusion and fragmented/scattered mirrored-region alternatives | High-mirror behavior is still a difficult image-domain case before material fitting begins. |
| Catalog 02/04: floor reflections survive as additional lens proposals | Number of predicted regions is not physical lens count. |
| Catalog 05: clear cropped apertures fragment; decoder alternatives disagree | Neither interpolation, prompt stability nor a successful model call supplies missing semantic boundaries. |

All six contact sheets were visually inspected. Their per-decoder unions are explicitly **display composites**, not selected masks or correspondences between decoder indices. Coarse/SAM IoU is measured only within the upstream semantic crop; SAM support outside that crop is counted separately as unknown. Native crossproposal overlaps retain every decoder combination. These are agreement measurements, not ground-truth accuracy scores.

Report: `data/photo-semantic-refinement-v1/report.json`, SHA `1d0c8edef6bfec80a31f856a8b4b6d14308a848d59e426e772faafb0d3007847`.
Verification: `data/photo-semantic-refinement-v1/verification.json`, SHA `8aa8b19390c01a81489902eeb1636d98df19f2dd88ab598ebf6e9d98028e76ff`.
The verification checks 628 artifact hashes, all 1,458 native crossproposal overlap calculations, complete decoder inventories, display unions and source/code pins. Coordinate-policy controls include diagonal connectivity, omitted-component accounting, crop cell/center mapping, distance-transform ties and explicit capacity failure. The SAM checkpoint is the existing pinned local base-plus checkpoint; all 615 state keys matched.

Independent read-only review also recomputed all 91 point/box mappings and checked native dimensions for every retained SAM mask. No coordinate or grid mismatch was found to explain the observed expansion/fragmentation. Both QA modules compile; this experiment changes no production algorithm and does not call for rerunning the unchanged full reconstruction/AR suites.

Reproduce against the saved local inputs, using fresh output directories:

```powershell
python -m qa.photo_semantic_baseline --manifest data/photo-semantic-baseline-v1/input-manifest.json --weights data/models/glasses-detector-v1 --output data/photo-semantic-baseline-replay
python -m qa.photo_semantic_refinement --baseline-report data/photo-semantic-baseline-replay/report.json --weights-receipt data/region-corpus/fixed-policy-v1/cases/white-control/attempt-001/report.json --output data/photo-semantic-refinement-replay
```

The saved region report provides only the pinned checkpoint configuration; its masks and prompts are not inputs to this experiment. The manifest, photographs and weights are local development artifacts, not repository-distributed assets. Inference blocks network access and performs no provider submission.

## Inference decision

**Do not adopt semantic-to-SAM as automatic lens-boundary correction or optical classification.** The fixed experiment falsifies that shortcut on several constructions. Preserve the learned logits, coarse component masks and SAM alternatives as image-conditioned aperture hypotheses; no new prompting variant is selected to repair individual development products. The frame head and the complement of a lens mask cannot overwrite source materials. The mug, rimless and floor-reflection failures remain part of the retained evidence.

The next implementation must distinguish three things that a flat binary mask conflates:

1. **A projected aperture:** a region through which the optical element may be present, including pixels where a rear temple is visible.
2. **A physical surface group:** all source pieces representing one lens or shield, with pads and other unsupported pieces kept separate. Overlapping shell fragments must not multiply the effective optical response.
3. **The contents of a photographic ray:** foreground hardware, transmitting lens, rear temple/background and reflected environment. A pixel inside an aperture is not necessarily a clean measurement of lens tint.

Preserve exact source-face lineage and allow face subsets before inferring groups. Existing whole-primitive declarations cannot express a lens/pad partition inside Miu's main source part. Exact-position components provide reversible candidate pieces; spatial overlap/containment and evidence from multiple views provide possible group relationships. Neither connectivity nor overlap alone verifies those relationships. Distinct lenses and clip-ons can overlap in projection; unobserved or unsupported pieces must remain unknown rather than being discarded or assigned by nearest component.

This distinction is particularly consequential for gradients and mirrors. Black rim contamination can imitate increasing absorption, and reflected softboxes or hue changes with viewing angle can imitate a spatial coating gradient. Material fitting must separate these alternatives using shared lens-local coordinates, per-photo lighting/camera hypotheses and ray visibility. Eroding a mask is insufficient when a rear temple crosses the lens interior. The existing canonical transmission/reflection descriptor remains the rendering target, not a proof that its parameters can be uniquely inferred from the available photos.

The representation portion is now implemented in the [component inventory, face-partition and optical binding stages](FACE_PARTITIONS.md). A fixed five-source control preserves all source-face occurrences and feeds explicit piece unions into the actual optical preparer. That control uses deliberately arbitrary component parity and retains existing source-part hypotheses; it does not infer lens/frame/pad identity.

The [image-aperture/component integration](COMPONENT_APERTURE_EVIDENCE.md) now supplies complete per-piece projection, directional coverage, pair overlap and depth-order evidence under the ten unchanged cameras. All 667 source components and 128 photo alternatives remain represented. The next implementation must infer competing **group unions across views** and emit source-bound partition/group hypotheses; choosing the best mask independently for each connected component would repeat the identity error. Quantitative semantic accuracy still needs independent reference labels; the five development designs and twelve single catalog views do not establish unseen multiview reconstruction quality.

The subsequent [shared support experiment](SUPPORT_UNION_INFERENCE.md) now infers component subsets across both views under twenty fixed coarse interpretation branches. It produces stable broad support on Invu/VB and explicit alternative/neutral memberships elsewhere. It also demonstrates that optimum support is not complete optical identity: a rear piece can improve Miu's limiting aperture fit, while broad overlapping Oakley surfaces need not be selected at all. Physical partition hypotheses remain a separate decision; neither selected IDs nor branch agreement may overwrite source materials.

The full automation still needs reliable scale/articulation, independently assessed semantic boundaries, photo-conditioned tint/gradient/mirror recovery and evaluation on new multiview products. The previous zero-passing-candidate VB optical result and strict renderer depth discrepancy remain unresolved.
