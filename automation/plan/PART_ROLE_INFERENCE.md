# Calibrated part-role inference

`reconstruction.part_role_inference.infer_part_roles(model, view_evidence, output, policy=None)`
is a reusable, local stage. It reads one segmented opaque GLB and hash-bound
render/mask evidence. It never submits provider requests or edits geometry.
The output directory is immutable for a given request; completed runs verify
their input and artifact hashes before reuse. Interrupted identical runs can
finish their remaining deterministic outputs.

## View schema

Each view supplies:

```json
{
  "id": "front",
  "model_sha256": "SHA256 of the GLB used to render the image",
  "width": 640,
  "height": 480,
  "camera": {
    "yaw": 0, "pitch": 0, "roll": 0, "perspective": 0,
    "scale": 234.1463414634, "center_x": 319.5, "center_y": 239.5
  },
  "world_to_render": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "cull_mode": "front",
  "image": {"path": "absolute image path", "sha256": "SHA256"},
  "masks": [{"id": "mask-0", "path": "absolute mask path", "sha256": "SHA256"}],
  "mask_status": "success",
  "silhouette": {"path": "optional renderer foreground mask", "sha256": "SHA256"}
}
```

`world_to_render` is a row-major 4×4 matrix applied to decoded GLB world
positions. A flat THREE column-major matrix must be reshaped with
`np.asarray(value).reshape((4,4), order='F').tolist()`.
Camera coordinates follow `reconstruction.camera.Camera`: integer pixel centers,
positive Y upward in world space, image Y downward. For an orthographic camera,
`scale = image_height / vertical_span` and center is `((width-1)/2,(height-1)/2)`.
`cull_mode` is `front` or `double` and must describe the actual render pass.

An empty SAM result is `mask_status="no_detection", masks=[]`; failed acquisition
is `"failed"`. Neither is interpreted as negative evidence. A successful mask
set must be nonempty. Optional renderer silhouettes are checked against CPU
projection (default IoU ≥0.97). Missing silhouettes are recorded as unverified
alignment, not fabricated agreement.

Historical masks rendered from an original GLB can be reused only with
`geometry_correspondence` containing `source_model` and
`candidate_to_source_faces` (each `{path,sha256}`), and
`max_relative_corner_error <= 1e-6`. The stage independently checks a complete
unique face bijection, all three corners and cyclic winding. It does not accept
the audit report's conclusion without checking the actual geometry. This is for
tiny serialization changes; it is not a transfer after retopology.

## Decisions and limits

The stage creates per-view face-ID rasters itself. The primary optical seed rule
requires at least 100 in-mask pixels across successful views and ≥80% in-mask
purity in every view where the part occupies at least 50 pixels. Version 2 also
retains a seed when a detector completely omits it in one view (≤2% purity), but
at least two geometrically distinct views each have ≥80% purity and ≥100 mask
hits. Supporting view directions must differ by at least 12 degrees; repeated
renders or image rolls do not create independent support. Intermediate purity
between 2% and 80% remains partial-contamination evidence and blocks this
exception. Retained omissions receive explicit detector-dropout/review flags.
Confidence labels describe evidence coverage, not calibrated probabilities.

Adjacent seeds are grouped only when their shared cut boundary has consistent
surface normals. For non-seed fragments, the defaults require all of:

- Some visible mask support (≥5 pixels), with at least one view occupying ≥15 pixels.
- At most 10% of visible pixels outside a contour band of 2 pixels or 2.5% of the mask extent, whichever is larger.
- Surface area ≤20% of the adjacent seed's area.
- At least 5% of the fragment's boundary length coincident with the seed boundary, within `1e-5` of model extent.
- Mean contact normal cosine ≥0.85 and no more than 20% of contact length below that cosine.
- Area-weighted sampled p95 distance from the seed's fitted plane ≤3.5% of model extent.

These gates create explicit **alternative groups**. They do not automatically
turn the fragments into accepted lens geometry. Rear hardware cannot qualify
merely by overlapping a lens mask: it also needs visible evidence, a shared
boundary, smooth continuation, small area and shallow geometry. Curved or heavily
fragmented designs can fail these conservative conditions and require additional
views or another hypothesis. Sampling and finite views do not prove hidden roles.

`report.json` contains `primary_groups`, `hypotheses`, part bindings, all visible
votes, every evaluated fragment's gate results, source hashes, review reasons
and correspondence receipts. `accepted` remains false. No part IDs, brands,
material names or template-specific shape assumptions are hardcoded.

`make_group_declarations(report, coordinate_frame, hypothesis_id="primary")`
builds the existing optical preparer's declaration schema for the **exact
analyzed source hash**. The caller must establish a real canonical +Y-up/+Z-front
coordinate frame. After canonicalization, simplification or compaction, rebind
the selected groups against the new artifact with that stage's verified mapping;
do not reuse the old source hash or silently change its bindings.

## Saved regression

`python -m qa.part_role_regression --output <fresh-directory>` runs four saved
segmentation cases without network access. The measured run is in
`data/part-role-inference-v2/saved-regression/`. The actual four-view generic Miu
request is also replayed in `data/part-role-inference-v2/miu-generic-dropout/`:
its front detector returned only one lens, but three other views supported the
missing lens, so v2 retains both groups and records the front omission.

| Saved input | Automatically proposed primary groups | Additional edge alternative |
| --- | --- | --- |
| Oakley automatic segmentation | `[[0,1]]` | None |
| Oakley guided segmentation | `[[1]]` | None |
| Miu automatic segmentation | `[[0],[1]]` | None |
| Miu guided segmentation | `[[1],[2]]` | `[[1],[2,3]]` |

The previously missed Miu arc is detected from geometry continuity: 9.62% of the
seed's area, 94.72% shared boundary, mean contact normal cosine 0.929, and plane
p95 distance 1.77% of model extent. No other hardware fragment is proposed on
these four saved inputs. That is a bounded regression result, not catalog-wide
semantic acceptance. The regression inputs are opaque render evidence, and the
guidance masks were used to generate two of the candidates; this is not an
independent product-quality evaluation.

Run `python -m unittest discover -s tests -p test_part_role_inference.py -v` for
local adversarial tests covering small rims, hidden hardware, missing masks,
camera mismatch, hash mutation, resume integrity, declaration provenance,
folded boundaries, and false geometry transfers.
