# Shared optical-support inference

2026-09-22. This is the next experimental inference step after [component/aperture measurement](COMPONENT_APERTURE_EVIDENCE.md). The policy below is written before the new corpus solve. It does not alter the frozen image predictions, source geometry or cameras.

## Decision variable and objective

Select one shared subset of source components across all supplied photographs. Its projected union is candidate optical support; physical group membership remains unresolved. The first supported input profile requires at least two distinct photographs and a nonempty aperture hypothesis in every view. An empty prediction is unsupported for this objective, not proof that the product lacks lenses.

For each view, evaluate the union only on the declared known domain. Let `A` be the complete number of positive aperture pixels, `TP` the positive pixels covered by the component union, and `FP` the union's known negative pixels. The score is `TP / (A + FP)`. Positive pixels that no component can explain remain in A. The shared objective maximizes the smallest view score; mean and complete per-view residuals are also reported. Source component count carries no penalty. There is no rule that the support must contain one or two pieces.

This fixed-interpretation objective can be expressed through finite binary inclusion variables. Pixels with identical source-component membership have the same union state and are compressed without changing their positive/negative counts. Each union state is the logical OR of its component inclusions. At trial ratio r, every view must satisfy `TP - r*FP >= r*A`. Bisection and bounded mixed-integer feasibility solves produce a candidate and a conditional objective interval. Actual unions are recomputed from every returned inclusion candidate before it can improve the lower bound.

The solver uses [SciPy's mixed-integer interface](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html). Numerical infeasibility, time limits and solver errors are distinct outcomes. Only a reported infeasible trial can lower the upper bound, and that interval remains conditional on the floating-point solver. It is not a formal proof, a semantic confidence interval or a physical-identity certificate. Every branch has a 30-second total solve budget, at most 20 feasibility iterations and a requested IoU interval width of 0.001. Explicit allocation/work caps reject an oversized problem; components and image evidence are never silently truncated.

## Ambiguity survives the inferred support

Record components with no known-domain projection, unselected components whose addition changes no scored union, and selected components whose individual removal changes no scored union. Individual removability does not imply that all such components can be removed together. These are conditional equivalences of one candidate's image support; they are not the complete set of optimal subsets.

Optical support and physical partition are separate variables. A bridged image aperture can contain both lenses, while several image fragments can belong to a single shield. A contained pad or duplicate shell can change no aperture pixels when added. The optimizer must therefore emit no inferred physical group, frame label, optical descriptor or accepted asset from its support score alone. It retains original component IDs and caller-supplied source/photo/camera provenance for subsequent partition hypotheses.

## Fixed first corpus experiment

Use every component from the frozen five-source, ten-view corpus. For each photograph construct two coarse interpretations: union all unflipped full-image coarse components, or union all unflipped contrast-crop coarse components. The union is known positive wherever any constituent is positive, and known negative only wherever all constituents supplied predictions. Save the constituents and domains. Do not select the highest-confidence semantic component or remove reflected/extra positives.

Cross the two choices independently between front and angled views: **four branches per design, twenty solves total**. Retain every branch and its objective interval, residuals, support membership and conditional ambiguity. There is no overall winning image interpretation. The existing SAM alternatives remain referenced, explicitly **unoptimized** in this first bounded experiment. This is not a search over every available image interpretation and does not imply that matching decoder ordinals identify the same semantics across regions or photos.

No case-specific threshold, camera refit, geometry change, source optical-role prior, paid provider call or material fit belongs to this experiment. Unexplained photo support, frozen-camera/articulation error and image-classifier mistakes remain competing explanations. Independent semantic labels and unseen multiview products are still required to assess reconstruction accuracy. A solved numerical objective does not establish the user's final AR appearance requirement.

## Validation before the corpus run

All **23 independent support/union tests pass**, including exhaustive subset enumeration for eighteen small random problems. Deterministic controls distinguish worst-view IoU from pooled IoU when aperture sizes differ; check complementary fragments, duplicated representations, unknown support, conflicting views and unexplainable positive pixels; and reject unsupported inputs/capacity overruns. Mocked successful solver statuses with invalid incumbents cannot close the interval. Timeout/failure statuses cannot become infeasibility. Conditional membership checks compare actual pixel sets, including an example where the bottleneck score stays unchanged while another view's union changes.

Independent read-only review found no material formulation, numerical-status or capacity defect. The initial core freeze is `reconstruction/support_union.py`, SHA `fd2bdca91a33b958ba74893739d8730f588714ab011cef30b8a49518b260bbe0`. The corpus driver also independently recounts every returned union metric and individual membership toggle against its cached source pixels. These checks establish the defined support objective, not semantic accuracy.

## Fixed run results

All **twenty branches** completed in **5.66 seconds**, with unchanged consumed inputs and core/driver files. Every branch reached the requested conditional interval width; the largest remaining gap was **0.0009671**. This is numerical resolution of the stated support objective under each fixed interpretation. It is not reconstruction or material acceptance.

| Development design | Worst-view IoU range across four branches | Selected component count | Individually removable selected components |
|---|---:|---:|---:|
| RayBan | 0.8826–0.8952 | 11–15 | 0–6 |
| Miu | 0.7445–0.8448 | 8–27 | 0–18 |
| Oakley | 0.8740–0.9028 | 19–41 | 10–30 |
| Invu | 0.8640–0.8642 | 1 | 0 |
| Victoria Beckham | 0.8762–0.8892 | 2 | 0 |

The reported IoUs compare generated component footprints with unverified coarse image predictions, under previously fitted cameras. They are not ground-truth lens or reconstruction accuracies. All four branches remain saved, and SAM interpretations remain unoptimized as declared.

The inferred support is stable for Invu (component 224) and VB (components 8 and 9). That stability does not establish unique membership: Invu has 223 individually addable components without a scored-union change, including thirteen with observed support; VB has the observed, addable component 1. Unknown components remain in the original source inventory.

RayBan varies broad co-projecting components between branches, not just tiny fragments. Miu retains broad components 0 and 6 throughout but changes overlapping surfaces and smaller pieces. Its full-front/cropped-angled branch also selects component 169: that piece explains 164 otherwise-uncovered front pixels while introducing 131 exclusive angled support pixels outside the aperture. The earlier depth evidence places it far behind a broad surface. The optimizer can therefore reward a geometrically separate piece when it improves the limiting view. This is a recorded failure of interpreting support membership as optical identity, not a reason to alter the fixed objective or mask to repair Miu.

Oakley retains broad component 104 in every branch, but never selects the substantial overlapping components 98/101. Their geometry remains in the source; selected support is not complete physical-group membership. Most branch variability there concerns smaller pieces or individual redundancy. The selected component count is not physical product complexity, and the intersection of branch selections is not a verified semantic label.

The combined contact sheet and representative native-size overlays were inspected. The Miu mixed-crop branch visibly retains projected temple spill; other views retain boundary/camera disagreement. The saved source-bound support hypotheses, complete conditional-toggle ledgers, union arrays, solver trial outcomes and overlays are available under `data/support-union-corpus-v1/`.

Report: `data/support-union-corpus-v1/report.json`, SHA `ee5e5bd04143e454286b12f4dc1df237d721c9afab2c2f423b7dee52e933fdf6`.
Contact sheet SHA: `130182f7774ef7bd4ec2755560c7092006cef6e4ccd05ae8e7318885bbe70319`.
Combined validation: `data/support-union-validation-v1/receipt.json`, SHA `d4022cb76788c4c2012d180d7ba7413156bfd8e0dbdcd8881a5681e9e85e621f`. It checks 190 files, all 39 consumed input/code pins, aggregate/branch agreement and the saved numerical metrics. The full Python suite ran **678 tests: 677 passed, one optional SAM test skipped**, in 75.51 seconds; compilation passed.

Reproduce using the frozen private evidence and a fresh output directory:

```powershell
python -m qa.support_union_corpus --output data/support-union-corpus-replay
```

## Inference decision after this run

Retain shared support inference as a reusable proposal mechanism. Do not translate its selected subset directly into lens materials or discard unselected geometry. The next physical partition must distinguish broad surface support, redundant/closed-shell membership, separate rear geometry and genuinely separate optical layers. It must retain competing assignments where the photographs cannot resolve them. Depth/order/containment relations already measured can contribute to that decision, but no fixed two-lens count, largest-component rule, one-way containment threshold or per-product ID list is justified.

Only an explicit physical-group hypothesis can pass through the existing reversible partition/group exporter to conditional optical fitting. Tint, gradient and mirror recovery still require the ray's transmitted background/rear geometry and reflected lighting to be modeled separately. No material fit, AR model, job default or live application was changed by this support experiment.
