# Review of the fresh Oakley OO9208 v2 run — 2026-10-13

**Verdict: the repaired automation completed reliably, but the delivered model does not yet satisfy the job's
main appearance requirement.** It is a recognizable, technically valid reconstruction with a real delivery,
traceable spend and useful visual evidence. The broad green/cyan lens appearance remains missing, the central
vents became substantially smaller during seating repairs, and the top view never obtained a usable registered
comparison. Several expensive difficulties were tool/interface limitations, not failures of Astra to notice
the reference features. Owner visual acceptance is still pending; this review does not record acceptance.

Job: `oakley-oo9208-v2-3e057bab`. Reviewed fresh run: **`20261013T173404-2accf4`**, final checkpoint **c0021**,
implementation **123dcca**, final GLB SHA
`a2814e098ce88aafc17c746e05af288d54f73eb28d82cb1d9f61044912279c23`.
The earlier failed run `20261013T163235-772629` is used only as historical context. This review is $0: no model
request, no product edit and no source-code repair. The only new Blender execution was a four-case material
export experiment on a factory-created diagnostic cube, never the product.

**Evidence coverage and limits.** Reviewed all 65 calls, their arguments, issued code, full results and journals;
provider request/response records, usage, events, public reasoning summaries, approvals/configuration, ledgers,
checkpoint index, exports, final GLB contents, delivery/report, monitoring record and review records. All 22
fresh checkpoint files c0000–c0021 match their recorded SHA-256 and byte counts. A fresh receipt audit verified
130 transport records, all 65 provider-output/tool-argument bindings, all 23 issued-code hashes and 65 journals
(21 mutating); its 222 source files remained unchanged. This is a byte-integrity check;
the review did not separately reopen and rerender every historical checkpoint. Visual review covered all six
original photos, both camera bases and saved mattes, five successful native/clay/normal render groups, both
photo-comparison batches (six earlier panels and five final panels), and all three seven-pose AR captures.
The retained files allow inspection beyond the images originally sent to Astra. The earlier receipt audit also
verified 27 unique blobs across 195 receipt documents and the immutable delivery binding.

The saved AR captures use a featureless skin-coloured test background. They establish rendered appearance at
the scripted poses, not actual fit on the owner's nose/ears, eye visibility on a live face or tracker stability.
Starting `job review` is not evidence that the owner has inspected or accepted the model. There is no new
asset-bound owner verdict. No measured dimensions were supplied, so millimetre residuals and scale rely on the
model's estimated size, not independent physical measurements. No comparison against the previously accepted
Oakley GLB was performed in this run; known-good visual parity is therefore unestablished.

All local paths below are relative to `glasses3d/` unless given in full. For compact references,
`thorough-review/` abbreviates `data/assembly/m5oakley/thorough-review/`; `work/`, `cameras/`, `runs/` and
`deliveries/` abbreviate those folders under `data/jobs/oakley-oo9208-v2-3e057bab/`.
Original run evidence remains unchanged.

**1. Highest product priority: the coating mismatch is real, and its reported cause was too broad.**

The front, angled, rear and side photo comparisons show a mainly violet rendered shield where the photos have
broad green/cyan areas blending into violet. AR does show rounded green/cyan highlights that move with yaw
and pitch. That meets the narrower requirement for a moving region rather than a painted stationary patch;
it does not establish the photographed colour balance, area coverage or transmission. These observations agree
with the appearance limitation in Astra's finish summary.

The GLB carries clearcoat, transmission, specular and IOR extensions, **no `KHR_materials_iridescence`**, no volume
extension and no textures. Its lens has base colour approximately `(0.55, 0.13, 0.72)`, roughness `0.44`,
transmission `0.90`, IOR `2.05`, metallic `0.03`, and clearcoat `0.10`. The green specular colour survives export.
The absence of textures supports the record that Astra did not paint a fixed colour patch. A transmission
factor of 0.90 alone is not a measurement of perceived transparency or a proof of photographic likeness.

The missing extension must **not be interpreted as a general exporter limitation**. Astra's final summary
accurately says the parameters did not carry into the GLB in this run. Its call-52 material annotation describes
spectral thin-film export as unavailable; that broader description, and our earlier interpretation of it,
overstate the limitation. The experiment identifies the specific authoring prerequisites.
Call 11 made a `glTF Material Output` group with an input called `Iridescence`, not `Iridescence Factor`, and
without an `Occlusion` input. The installed Blender exporter requires the exact factor socket name. Its
material-inlining code also only preserves this unconnected glTF group when the `Occlusion` socket exists in
this export configuration. The numerous alternative custom-property spellings tried in calls 11, 14 and 35
cannot solve that: our exporter intentionally removes material custom properties except the role on temporary
export copies.
Call 52 ultimately deletes the experimental group and sets thin-film thickness to zero.

The $0 four-case experiment used the actual `g3d.export_glb` path and the run's material-group structure:

| Material group in diagnostic cube | Iridescence in exported GLB |
| --- | --- |
| Astra's original socket names, no Occlusion | Absent |
| Correct `Iridescence Factor` only | Absent |
| Occlusion only, original `Iridescence` name | Absent |
| Correct factor name **and** Occlusion | Present: factor 1, IOR 1.38, thickness maximum 460 |

All four exports left their source scene unchanged. Renaming the factor alone was the first hypothesis and
failed; the final experiment records both independent prerequisites. This proves capability and identifies
the setup error. It does **not** prove that enabling iridescence will by itself match the product photos.

Responsibility is shared: Astra guessed an unsupported authoring interface repeatedly; the tools supplied no
usable material-capability guidance and silently omitted an authored optical effect. The exporter already
warns about a dropped volume, but has no analogous thin-film-loss diagnostic.
Evidence: tool calls 11/14/35/39/52; `thorough-review/material-probe/proof.json`, `material_probe.py`,
`glb-content.json`; `g3d/export_glb.py:683` and `:715`; installed Blender
`io_scene_gltf2/blender/exp/material/materials.py:71` and `extensions/iridescence.py:40`.

**2. Seating improved its own measurements while reducing a photographed feature.**

The two central upper-brow ventilation gaps are clear in the original front photo and the c0008 comparison.
In c0018 and the final c0021 image, they read as very narrow slits. Raw render-alpha analysis found approximately
354–356 transparent pixels per central gap at 1200-pixel width earlier, versus 54–56 at 1024-pixel width finally.
After normalizing for render resolution, the opening area is roughly **79% smaller**. The gaps are not literally
sealed; the final central openings are about three pixels high. This is an image-space observation at the same
frozen front camera, not a physical opening-area measurement. Outer openings remain present.

The code history supports a mechanism. Call 35 built five separated capture lips and explicitly intended to
retain four vents. After further seating refusals, call 37 deleted those lips and the older vent/retention lips,
then built a continuous upper rail where the lens edge has y > 5. Call 39 extended it to y > 4; call 43 added
the separate side locking sockets. The later detail pass did not restore the gaps. This is strongly consistent
with the visible regression; the review did not perform a per-pixel occluder attribution.

The seating checks correctly exposed bad initial construction: call 34 refused 63/303 required stations
blocked (58 FOLD, 5 CONCAVE), then calls 36 and 38 refused remaining concavities. Astra repaired support geometry
and retained the gates. However, a mechanically plausible capture rail is not automatically the photographed
rail. There is no evidence that the numerical gate itself is wrong; weakening it would not resolve the likeness
problem. The missing control is a photo-feature regression review alongside seating.
Evidence: calls 34–44; `work/compare/call_clAB28VZPM1rzGcx4mxBhWSx/reference-01.png` versus
`work/compare/call_zYpL3YF8PKbFoSTWhVIuu6HJ/reference-01.png`; review visual evidence and issued code.

**3. The top-camera retry could not try the approximately 90-degree rolled orientation.**

The authoritative top photo is correctly oriented in the saved matte, but the fitted render is rotated roughly
90 degrees in the image plane. Astra recognized this, requested a targeted refit at call 49, and then inspected
custom rolled top renders rather than distorting the model toward the wrong camera. That was the right
diagnostic choice. The new fit nevertheless returned `status:ok`, IoU **0.3233**, mean contour distance
**7.318 mm**; it was still unusable as a registered comparison.

The source explains the failure: `ROLL_BOUNDS=(-25,25)` and top/topb seeds have roll zero, with constrained yaw
priors. The retry repeats the same restricted search; a reason string describing the 90-degree rotation does
not change those bounds. An expanded-search counterfactual was not run, so this establishes a search-coverage
defect, not the sole possible cause of every residual. The last comparison explicitly includes only photos
01–05. The top photo was used for visual reasoning and custom renders, but it never received a valid final
registered comparison. The tool
also labelled the original basis's top reference usable despite the visible mismatch. A numerical completion
status must not be read as a good camera fit.

Some matte error also contaminates the other residuals: the saved right-side matte treats bright portions of
the solid silver emblem as background holes. Consequently a contour residual mixes camera, segmentation and
model errors. Evidence: calls 8/49/50/53/59, `cameras/basis-01/`, `cameras/basis-02/`,
`g3d_host/cameras.py:97`, `:322`, `:664`.

**4. The temple-stage failures expose a narrow detector and a misleading error.**

All four `temple_stage` calls failed before starting a paid specialist loop. There is no specialist-fitted
result. Three calls (18, 20, 31) reported no geometric knuckle axis. Astra responded by building real interlocking
Y-axis hinge cylinders at call 19. The detector only accepts temple-role knuckles smaller than **3.2 mm in both
x and z**; these authored knuckles are **4.5 mm** across. The detector therefore excludes them even once the
roles are valid. This is a detector applicability limit, not proof the model has no hinge axis. Shrinking the
product hardware to satisfy that heuristic would not be an appropriate general fix.

During the retries Astra also made a genuine role error: call 23 assigned `partRole='hinge'`, while supported
roles are lens/frame/temple. Call 25 then said "parent export does not match its current checkpoint" because
`package.py` collapses an unsuccessful export into that message. The real unsupported-role cause only became
clear in call 29's direct export refusal; call 30 corrected it. The next stage attempt still failed the size
heuristic. The direct four failed invocations cost $0.351395 in parent requests; the wider hinge-repair sequence
cost $0.862270. These subsets overlap and must not be added together.

This simple assembly trial was planned as frame/lenses/seating, with no specialist temple challenge needed.
The general prompt's instruction to use the stage for faceted temples led Astra into a path whose capture
assumptions did not cover these authored hinges. The final temples are Astra's own code, with photo-visible
facets and pod details still approximate. Evidence: calls 18–31, `g3d/temple/measure.py:28`, `:66`,
`agent/temple_lab/package.py:83`; `thorough-review/geometry/geometry-evidence.json`.

**5. Context handling made the last part of the run disproportionately expensive.**

All 65 provider usage records independently re-price to **$19.161346**. The budget was respected: no request
began at or above $20 settled, committed spend stayed within $23, and final held/unknown amounts are both zero.
The run ended through an accepted `finish`, not a budget cutoff. Including the old failed run ($0.828903), the
job total is **$19.990249**; the earlier smoke belongs to a different job. Actual fresh cost was about **28.1%**
above the $14.957821 estimate, within the quoted $13.200719–$20.117690 range.

| Billed component | Cost | Share |
| --- | ---: | ---: |
| Cache writes | $10.304676 | 53.8% |
| Cached input reads | $5.902870 | 30.8% |
| Other input | $0.081900 | 0.4% |
| Output, including reasoning | $2.871900 | 15.0% |
| Total | **$19.161346** | **100%** |

Input processing accounted for **85.0%** of cost, including newly added content as well as repeated history.
Cumulative input was 6,735,433 tokens; output was 57,438,
including 36,742 reasoning tokens. Per-request input grew from **18,702 to 230,515** tokens. All six photos
were present on every request. Image pruning worked in the narrow sense of removing old images (peak 25,
final 19), but did not prevent context growth or expensive cache rewrites.

At requests 51, 61 and 64, the guard replaced earlier images with text stubs. Comparing consecutive saved
request bodies shows these replacements were the only edits to existing history; other request fields did
not change. Provider cached-input counts then fell to **4,252** on each request, from 145,987 / 198,943 /
225,789 on the preceding requests. Large cache writes followed. Those three requests cost **$7.479712 (39.0%)**.
This is strong evidence that editing the old prompt prefix caused the cache misses, consistent with
`ContextGuard`'s documented behavior. It is not a controlled counterfactual measurement of money that could
all have been saved; retaining images also has costs.

The final eight calls (58–65) changed no geometry or materials and cost **$7.374968 (38.5%)**. They supplied
necessary inspection, comparison, AR, seating checks, checkpoint marking, export and finish, so calling all
that work waste would be wrong. The orchestration cost is the concern: call 64's cached local export took
**0.018 seconds**, while the model request selecting it cost **$2.790425**. The first finish-soon notice appeared
after call 61 at $15.31; the subsequent export request consumed most of the remaining room. Astra still finished
with about $0.84 left to the settled stop.

Evidence: `thorough-review/accounting/{summary.json,metrics.json,calls.csv,prefix-diffs.json}`; original
`runs/20261013T173404-2accf4/{budget.json,transport/,events.jsonl}`; `agent/loop.py:747`.

**6. Passing validation establishes useful properties, with important boundaries.**

The delivered GLB is **1,605,328 bytes**, **74,150 triangles**, **45 mesh objects**, nine material instances.
The final scene summary reports all meshes closed, with one lens, 38 frame objects and six temple objects.
The overall width is **155.65 mm**, estimated because dimensions were not measured.

Specification **7/7** passes: format, units, origin, orientation, transforms, geometry and components. There
are no final specification errors or warnings. This validator is not a photo-likeness test or a full
manufacturing/manifold certificate: earlier exports passed its geometry row while seating found pinched
optical-rim vertices. The final seating checker separately establishes a valid closed lens body with no
reported lens problems, open parts or refusals.

Final seating: **541 PASS, 1 TRANSITION, 366 VISIBLE_BY_DESIGN**, required 542 points;
`share_ge_pass=0.9982`, `share_ge_target=0.9945`. The one transition is below the nominal pass depth and is
handled by the existing rule, not a new exemption. There is an over-capture warning at **150 points**, maximum
**1.2977 mm** against its **0.6 mm** warning limit. The top-level warnings list is empty, but the nested
`over_capture` record explicitly calls this a warning. It must not be reported as warning-free seating.

The report also measures the continuous rail projecting below the brow at 343 stations: median exposure
0.825 mm on the front and 1.375 mm on the back, maximum 1.425 mm on either side. No hidden ranges are declared,
so this band table has `graded_stations=0`. Seating PASS therefore does not certify rail invisibility or vent
preservation. The final seating operation moved only four vertices/two rim nodes, at most 0.2346 mm; most of
the improvement came from Astra rebuilding frame supports rather than the seating tool moving the lens.

The AR capture succeeded at all seven fixed-scale poses with five warnings: two lens markings classified as
decals, two overlap measurements reaching the 3 mm observation-window floor, and the rearward-wrapped brow
being treated as arm geometry by the positional heuristic. The latter can alter its compositing despite the
front and temple being separate objects. The exporter does not preserve authored `LENSES_part_visibility`,
so the viewer's generic suggestion to author it is not an available remedy through this pipeline. Earlier
degenerate-temple-triangle warnings are absent from the final capture. Photo print was not required for this
plain frame; its status is `not_required`, not proof a print stage ran.

Evidence: immutable `deliveries/20261013T173404-2accf4-*/report.json`, final GLB, call 58/62 results;
`thorough-review/geometry/final-metrics.json`; `g3d_host/spec.py:36`; `g3d/export_glb.py:35`.

**Visual progress across the six authoritative views.** These contour numbers use the same basis-01 camera,
but the earlier and final renders have different raster sizes. They are useful reported trends, not exact
physical measurements or a new acceptance threshold. The masks and fitted cameras also have the limits above.

| Photo/view | Earlier c0008 mean, mm | Final c0021 mean, mm | Final silhouette IoU | Visual reading |
| --- | ---: | ---: | ---: | --- |
| 01 front | 0.656 | 0.637 | 0.9573 | Recognizable shield outline; missing green field, narrow central vents, heavier/angular nose support |
| 02 left angled | 1.192 | 1.062 | 0.9205 | Broad silhouette improves; lens colour/coverage, locking pod/emblem and lower shield shape remain different |
| 03 right side | 1.450 | 1.420 | 0.8566 | Weaker outline agreement; far-shield edge, pod/hinge integration and temple facets remain visibly approximate |
| 04 rear | 0.943 | 1.014 | 0.9084 | Residual worsens; pads and rear arm profiles remain more angular/simplified; green lens region absent |
| 05 left side | 1.227 | 1.198 | 0.8774 | Small contour improvement; lens colour and the detailed pod/arm transition still differ |
| 06 top | Misregistered | No final registered comparison | Retry 0.3233 | Used in custom rolled renders; no trustworthy registered final shape measurement |

The first complete model already captured the broad product family. Later work repaired optical topology,
added actual hardware and retention support, improved nasal depth/ribs and broadened the temple top surfaces.
Early lens/bridge artifacts and degenerate temple triangles disappeared. The final result is not a monotonic
improvement: the central vents regressed, rear residual worsened, and the priority coating remained unresolved.
Tiny OAKLEY/PRIZM marks are present, but their exact type, placement and contrast are approximations. The smaller
AR model on a plain background can conceal detail mismatches obvious in the photo panels.

**How the hour and money were spent.** Phase boundaries group consecutive calls; wall times include model,
tool and intervening runner overhead. Phase times are rounded; startup is included in the first phase, and
sub-second finalization follows the last. Unrounded grouped intervals total 3,496.349 seconds, 0.410 seconds
below the recorded run duration.

| Calls | Work | API cost | Approximate wall time |
| --- | --- | ---: | ---: |
| 1–8 | Initial model, first render, six-camera fit | $1.182509 | 11.1 min |
| 9–17 | Optical topology and material export attempts | $1.023933 | 5.5 min |
| 18–32 | Temple-stage attempts, hinge/role fixes, lens retessellation | $1.679355 | 8.5 min |
| 33–46 | Six-view comparison, seating repairs, first AR/best checkpoint | $2.923176 | 11.5 min |
| 47–57 | Coating, failed top refit, construction detail, further AR | $4.977405 | 17.0 min |
| 58–65 | Final evidence and accepted finish, unchanged model | $7.374968 | 4.7 min |

Total elapsed **58 min 16.8 s**. Model-response time was **41.7 minutes (71.6%)**; local tools **10.0 minutes
(17.1%)**; other startup/counting/transport/runner overhead accounts for the remainder. The two camera fits
used 488 seconds of the 599 seconds of local tool time. More CPU alone cannot remove the dominant model/API
time. There is no evidence of a silent stall or lost paid response. All 65 requests settled; 130 transport
receipts verify; final result and delivery agree. The repaired checkpoint fingerprint did not fail again.

There were **13 error/refusal results out of 65 calls** (10 refusals, three errors), costing $1.863532 for those
parent requests alone. They include legitimate topology/seating protection, unsupported-role rejection,
three API-discovery attempts, a wrong triangulation return-type assumption and an invalid custom view name.
Their follow-up repair costs are additional; the number is not a complete avoidable-cost estimate.

**Assessment of Astra's decisions.** Astra used all photos, retained the frozen basis for comparable evidence,
noticed the incorrect top orientation, fixed substantive topology problems, attempted the required stage order,
revisited seating after geometry changes, inspected AR repeatedly and finished within budget with a candid
limitation summary. It did not hide the missing specialist result or claim an exact lens match.

The weak points were repeated guessing of the material interface, changing valid hinge roles to an unsupported
role, and allowing numerical seating success to obscure a vent-feature regression. Only two full comparison
calls were made: one at c0008 and the final one at c0021 (five views). Several intervening geometry edits were
assessed through checks or selected renders rather than a refreshed all-view comparison. A current-head "best"
marker was maintained, but no restore/alternative comparison demonstrated that c0021 was better than every
earlier candidate on every feature. The word "best" records Astra's selection, not an objective optimum.

**Recommended order before another paid run.** These are proposed general repairs and $0 proofs, not changes
made by this review. No gate needs to be relaxed to establish these facts.

1. Prove and expose the supported optical-material authoring contract. Warn when a nonzero authored thin film
   disappears during export; regression-test the installed Blender version through the actual export path.
   Then evaluate exported appearance in the fixed AR renderer before claiming a photo match.
2. Make context pruning account for cache rewrites and remaining runway. Replay saved requests to compare
   strategies without a model call; retain all six authoritative photos and enough current/best visual evidence.
   Reduce repeated large tool-result history and avoid paying a new model turn for deterministic duplicate
   finalization steps where the contract can safely perform them together. Prove retention and finish semantics.
3. Make camera search cover image-plane orientation, including the rotated top fixture, and distinguish a
   completed optimization from a usable registration. Preserve the photo; do not reshape the model to a bad fit.
4. Report the actual temple capture/export refusal and expose detector applicability. Establish a general hinge
   axis method or a clearly supported explicit geometric input, with non-Prada $0 fixtures; do not shrink
   authored hardware to satisfy the 3.2 mm heuristic.
5. Review seating and photo-visible openings together. Preserve photographed vents while repairing support;
   add an evidence comparison that catches this regression without substituting a new arbitrary likeness gate.
6. Resolve the AR front/temple classification path for wrapped fronts; warnings should propose a remedy this
   exporter can actually carry. Verify on fixed poses and then the owner's live review.

After those proofs, any further Astra editing still needs the owner's separate paid-run approval. A repeat
under the unchanged interfaces would risk spending money on the same discoverable limitations.

**Evidence index.**

- Original narrative: `data/jobs/oakley-oo9208-v2-3e057bab/runs/20261013T173404-2accf4/transcript.md`.
  The transcript intentionally truncates some tool results; the `tools/NNNN-name/result.json` files contain the
  full results used for this review. `issued-code.py` records the exact snippets.
- Complete per-call accounting and phases: `data/assembly/m5oakley/thorough-review/accounting/calls.csv`,
  `summary.json`, `metrics.json`, `prefix-diffs.json`, `receipt-bindings.json`, `ACCOUNTING_REVIEW.md`; earlier independent audit
  `data/assembly/m5oakley/fresh-audit.json`.
- Geometry/checkpoints: `data/assembly/m5oakley/thorough-review/geometry/geometry-evidence.json`,
  `final-metrics.json`, `checkpoint-hashes.json`, `timeline.txt`, `findings.json`.
- Visual inventory, per-view review and vent measurement:
  `data/assembly/m5oakley/thorough-review/visual/visual-review.txt`, `visual-evidence.json`, `visual-evidence.py`.
- Coating proof: `data/assembly/m5oakley/thorough-review/material-probe/proof.json` and
  `data/assembly/m5oakley/thorough-review/material_probe.py`; raw GLB extraction `glb-content.json`.
- Final photo panels: `data/jobs/oakley-oo9208-v2-3e057bab/work/compare/call_zYpL3YF8PKbFoSTWhVIuu6HJ/`.
  Earlier all-six comparison: `work/compare/call_clAB28VZPM1rzGcx4mxBhWSx/` in the same job.
- Final immutable report/model/contact sheet:
  `data/jobs/oakley-oo9208-v2-3e057bab/deliveries/20261013T173404-2accf4-cf35d209a42d0149c739654e5e046f58a46d405c93ea362e11ef629a7f675904/`.
  Contact sheet: `contact-sheets/01.jpg`. Review server identity/URL: the job's `review/current.json`.

Prepared from local evidence, with independent accounting, geometry and visual reviews. The $0 material
experiment and its source explanation were independently checked. Runtime code, all product assets and the
original run records were left unchanged.
