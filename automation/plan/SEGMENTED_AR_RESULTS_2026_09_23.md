# Four-step implementation and five-product results

The Tripo segmentation route is implemented through `python -m reconstruction.job`.
It produces compact models with editable lens appearance, automatic part-role
evidence and actual AR render comparisons. Product fidelity still requires visual
review. The remaining visible problems are in generated shape/frame details,
ambiguous boundary identity and exact mirror response.

See [the job command and function contract](SEGMENTED_AR_JOB.md),
[machine-readable results and call ledger](../data/segmented-pipeline-v1/summary.json),
and [completed-job resume checks](../data/segmented-pipeline-v1/resume-verification.json).

![Five-product comparison](../data/segmented-pipeline-v1/final-review/five-products-overview.png)

## 1. Clear Miu appearance and inspection

Miu now retains transparent lenses with ordinary reflection and visible background
detail. Front, angled, rolled and rear inspection runs use the actual AR renderer
under room, broad studio and side studio lighting. The frame and hardware remain
opaque. The two-order material review ties between plausible clear hypotheses;
the selected low-reflection prior is recorded as an unresolved fallback.

The fresh front detector omitted one complete lens. Three other independent views
supported it. `part_role_inference.infer_part_roles` now recognizes this particular
omission pattern while preserving the contradictory view and review flag.
Moderate contamination still vetoes promotion, and duplicated cameras do not
count as independent evidence.

The generated hardware remains coarse and the rimless contours are faint.
[Pinned Miu comparison](../data/segmented-pipeline-v1/final-review/miu-selected-comparison.png).

## 2. Colored coatings and lighting

`segmented_appearance.run_segmented_appearance` obtains qualitative material
interpretation and small sampling regions from an explicitly configured vision
client. Independent local lens masks and edge filtering constrain the regions;
numeric colors come from actual image pixels. White highlights, transmitted
background and colored coating evidence have separate roles.

Oakley's first candidates were too pink. The implementation now uses source hue
coverage and generated-surface incidence distributions to propose a green plateau
and purple oblique response. Orthographic and finite-distance camera assumptions
remain explicit hypotheses. Guarded smooth-normal alternatives preserve positions,
triangle indices, UVs and frame data. The model shows green/purple behavior when
viewed through the actual optical renderer.

Room lighting still produces uneven reflections. The reverse view differs from
the broadly pink source photograph. Source camera distance and studio lighting
are unknown; the nearby AR perspective changes incidence substantially. This is
an unresolved visible mismatch. The two review orders choose different candidates,
so the pipeline records uncertainty.
[Pinned Oakley comparison](../data/segmented-pipeline-v1/final-review/oakley-selected-comparison.png).

The additional INVU mirror exposed another bug: missing clean transmission samples
had assigned zero absorption to every mirror hypothesis. Blue reflection then
competed with strong warm background transmission and the model looked amber.
Unchanged-geometry causal controls ruled out flipped normals. The generic fix
tests explicitly unmeasured neutral absorption and coating-strength alternatives
when transmission evidence is absent, alongside transparent controls. The blue
front response returns; exact rear appearance remains unresolved.
[Causal controls and measured diagnostic](../data/segmented-pipeline-v1/invu-material-causal/causal-report.json).

`segmented_appearance_review` compares anonymous candidate cards twice with order
reversed. A unique choice needs agreement and plausible/good assessments. A tie
retains an explicit fallback. If every candidate is poor, the job reports
`needs_review` with a diagnostic model instead of treating it as a plausible match.
These model judgments remain conditional evidence, not independent ground truth.

## 3. Automatic lens identity and geometric edge cases

Four known-camera views connect SAM3 render masks to visible mesh parts.
Independent-view support, contamination and adjacency identify candidate lens
groups without product-specific part numbers. The earlier four auto/guided
segmentation regressions remain stable, including the Miu thin-edge alternative.

VB introduces an ambiguous boundary strip. The job prepares both
assignments with the same reduction policy and preserves a role-selection flag.
Making the strip optical removes a bright inner ring in the control, but smooth
adjacency and appearance alone do not establish lens identity. The primary export
is diagnostic while that distinction remains unresolved.
[Boundary controls](../data/role-alternatives-v1/vb/standard-inspection/part19-front-closeup.png).

INVU also exposed conflicting effective normals on coincident surfaces.
`segmented_optics.recover_smooth_failed_preparation` reproduces that exact failure,
applies the existing guarded smooth fit, preserves geometry/UV/frame data and
passes the unchanged strict exporter and reader. It refuses unrelated failures
and unsupported fits. No face deletion or relaxed optical contract is used.

## 4. Job integration and additional products

The job snapshots photographs, retains paid provider receipts, verifies parent-task
lineage, checks whole-model geometry correspondence, builds lens evidence, reduces
and compacts before optical preparation, generates appearance alternatives,
renders them, reviews them and writes `candidate.glb` plus a detailed report.

All five segmentation results have a complete bounded face bijection with their
generated source, with all three corners checked and no reversed winding. This
does not assert bit-exact coordinates, UVs or texture equality: provider atlases
can be repacked. Reduction measurements are against generated geometry, not a
physical product measurement.

| Product | Appearance exercised | Triangles | Model |
| --- | --- | ---: | --- |
| Miu | Clear rimless | 126,123 | [GLB](../data/segmented-pipeline-v1/jobs/miu/candidate.glb) |
| Oakley | Green/purple mirror shield | 119,983 | [GLB](../data/segmented-pipeline-v1/jobs/oakley/candidate.glb) |
| RayBan | Dark tint, full-rim tortoiseshell | 120,500 | [GLB](../data/segmented-pipeline-v1/jobs/rayban/candidate.glb) |
| VB | Brown gradient, full-rim frame | 124,185 | [GLB](../data/segmented-pipeline-v1/jobs/vb/candidate.glb) |
| INVU | Blue/purple mirror | 119,988 | [GLB](../data/segmented-pipeline-v1/jobs/invu/candidate.glb) |

The models are about 5–7 MB each and pass the current AR loader. Their lens
appearance uses this application's optical profile; a generic glTF viewer is not
an equivalent material test. Width is a display convention because the AR engine
handles user fit. Back inspection rotates the asset after fitting; it is not a
real wearer test or a complete undeformed temple inspection.

Provider recovery now validates cached request/result lineage, task IDs, image
dimensions and binary masks. Terminal failures do not poll forever. Unknown
submissions are not repeated automatically. Failed local attempts are retained,
and top-level job status records failures rather than leaving a stale success.
Completed jobs resume without credentials, new calls or changed candidate bytes.

The first fresh Gemini interpretation requests returned `400 INVALID_ARGUMENT`.
The transport now preserves output shape and enums while moving decoder range/array
bounds into descriptions; full local domain validation remains in place. A live
corrected interpretation succeeded; the server did not identify the exact rejected
constraint. One intervening 503 and the two original 400
receipts remain recorded; no automatic retry concealed them.

## Validation, spending and limits

The 85 focused tests pass and cover role evidence, provider recovery, optical-normal repair,
material proposals, schema adaptation, comparison order, rejection handling and
job recovery. Actual browser runs cover candidate export/loading and multiple
views/environments. The machine-readable summary records exact selected hashes,
triangle/byte counts, review outcomes and original API receipts.

This pass made six new Tripo tasks: generation and segmentation for three additional
products, consuming 300 recorded Tripo credits. Oakley/Miu generation and
segmentation were reused. Twenty SAM3 render-mask requests were made. Semantic
attempts total 25, with 22 completed and three failed responses. Outcomes and
returned token usage are listed individually in the ledger;
failed attempts are not represented as confirmed billed usage. Local material
and normal controls made no generation or segmentation calls.

The next quality work is concrete:

1. Correct coarse frame geometry, lens outlines, small hardware, logos and baked
   frame highlights. This pass deliberately retains those generated frame assets.
2. Resolve optical boundary alternatives such as VB's strip with stronger evidence.
3. Improve mirror/front-rear appearance using better camera/lighting correspondence.
   Available product photos do not uniquely identify physical coating parameters.
4. Expand beyond this five-design development corpus, with held-out views where
   available, and measure real-camera appearance and phone rendering performance.

Every output is marked `accepted: false` and `production_ready: false`. The four
implementation steps are delivered; automatic approval of product fidelity is
still an open problem.
