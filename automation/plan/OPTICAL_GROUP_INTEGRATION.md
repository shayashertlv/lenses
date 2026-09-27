# Effective optical groups: integration contract

The experimental export and photo-sampling path now retains every selected source
triangle. It does not relax `front_sheet_v1` or infer lens identity. The local AR
renderer now has a separate explicit group path for closed/multipart geometry;
see [the runtime contract and evidence](EFFECTIVE_GROUP_RUNTIME.md).
The [grouped reconstruction job](GROUP_JOB_ADOPTION.md) now prepares explicit
declarations or automatic source-part hypotheses and feeds the existing joint
fitter. Its previews remain diagnostic, with no accepted material.
All five saved designs can be represented by the proposed group arrays; that is
distinct from proving that the supplied groups identify their actual lenses.

## Identity and geometry

An optical group contributes **one nearest interaction per ray**, across all of
its member primitives. A closed group's rear face is not a second interaction.
Two different groups sharing one material remain two interactions. The first
integration accepts whole source parts only, identified by the pinned source
GLB plus selected-scene node, mesh and primitive indices. Each part belongs to
exactly one group. Face partitions and automatic split/merge decisions remain
future work; neither primitive nor material identity establishes physical identity.

`optical_groups.prepare_optical_group` supplies common-frame coordinates,
normal hypotheses and one group-wide bottom-to-top V coordinate. Its source
bindings must be verified against the original GLB by the exporter. Retain all
vertices and original index order, including back surfaces and separate members.
Use per-vertex normalized normals, interpolated and normalized again, with
`abs(dot(N, view))`. This symmetric effective response does not model separate
front/rear coatings, refraction, thickness or a physically oriented volume.

For the minimum CPU slice, bake the verified source world arrays into identity
root nodes, as in `optical_asset.write_optical_candidate`, but measure float32
conversion explicitly. Reject nonfinite attributes, collapsed or inverted
triangles, and unsupported coincident patches on the **actual exported arrays**.
Report position/normal/UV error and retained indices; do not say float64 geometry
is unchanged. This matches the demonstrated GPU world-array experiment. The five
current sources have no node transforms, but transformed sources need the same
checks and a source-to-common transform receipt.

Preserving original local POSITION/index/NORMAL accessors and hierarchy would
avoid this additional position quantization and is a useful later alternative.
It requires a transform-aware authoritative reader and equivalent Three loader
tests. For multiple primitives, dedicated identity child nodes under the original
transformed node give reliable node metadata without applying the transform
twice. Primitive extras currently land on geometry userdata in GLTFLoader, which
can be cached; node extras on a multi-primitive Group do not automatically become
child Mesh extras. Do not mix these representations without a versioned contract.

## CPU export and photo bridge

1. Add `optical_group_asset.write_optical_group_candidate` and its strict reader.
   Clone the selected scene; preserve the source BIN prefix, original node/mesh/
   material records, unrelated selected instances and other scenes. Reject
   external texture relocation. Append one canonical material per group and
   one identity root node per member, with stable group/member/source bindings,
   descriptor hash and `effective_optical_group_v1_experiment` profile. The exact
   existing `LENSES_lens_appearance` material extension remains unchanged. A
   receipt binds source, output, all attributes, group membership and validation.
2. Re-read the exported GLB. Return mesh, UV, common normals, face group IDs and
   fitter surface bindings. Group IDs are asset-scoped; per-group descriptors
   are independent even when numerically identical. Verify the receipt and
   metadata, not only the presence of a canonical material. Re-run
   `optical_group_runtime.validate_effective_optical_runtime` after float32 export;
   incomplete checks cannot grant support. Exact coplanar proofs do not cover
   near-coincidence, noncoplanar intersection lines or finite GPU depth ties.
3. Add `optical_group_observations` using `optical_group_raster` and the shared
   sampling policy in `photo_lens_observations`. Map region source-part memberships
   explicitly to groups; do not use mask ordinals or material names. Use actual
   exported attributes with the original pinned camera normalization. Preserve
   every mask alternative and fixed spatial split. Only one distinct optical
   group before an opaque stop is supported by the current photo fitter. Stacked
   groups, ambiguous depths, invalid normals and capacity overflow remain
   explicit unknown observations, including their coverage.
4. Feed these bindings through the profile-aware adapter into the existing joint fitter. Joint per-photo
   lighting remains conditional on supplied cameras, masks, geometry and rear
   composition. Reuse the diagnostic residual/support audit. No new descriptor
   is automatically selected or accepted; unobserved groups remain unmeasured.

## Production group path

`lens-material.ts` validates the explicit profile and creates mutable transport
state by group ID. `nearest-optical-groups.ts` owns per-group nearest-event maps
used by both `lens-layers.ts` and `eyewear-shadow.ts`. Closed geometry therefore
contributes its response once: nearest event for each whole group first,
then camera far-to-near composition and light near-to-far transmission. Both stop at
opaque geometry and qualify every shadow layer against receiver depth. A receiver
before the nearest group event gets none of that response; an interior receiver
after it gets the effective response once. A back view uses the nearest back
surface under the same symmetric model.

Key mutable resources by `(asset instance, group ID, profile)`, not source
material. Distinct groups must never share a nearest-depth texture or composition
state because their descriptors match. Immutable descriptor values and compiled
shader programs may share; owned targets and per-group materials need separate
lifecycle control. The helpers preserve source geometry/texture ownership and
hierarchy visibility. The existing renderer installs the validated groups before
fitting and posing. The old front-sheet and legacy paths remain intact; unsupported
mixed profiles are rejected. Legacy closed volumes do not become new groups.

Opaque stops must reproduce actual source alpha/discard behavior. The real-source
QA bundle's constant opaque controls do not establish source frame appearance.
Preserved transparent noncanonical frame materials therefore require an explicit
runtime policy. Source units, bridge anchoring and semantic grouping also need
separate validation; an external-model URL or matching hash establishes none.

## Adoption checks and remaining blockers

- Export/reload checks: shared mesh instances, multipart groups, other scenes,
  nested/nonuniform/reflected transforms, absent or derived normals, zero-alpha
  canonical fallback, source/receipt tampering, float32 collapse, exact conflicting
  group ties and bounded-check exhaustion. No unrelated primitive may disappear.
- CPU checks: front/back nearest events, closed and disconnected members, opaque
  blockers, receiver before/between/behind, stacked distinct groups, unknown masks,
  no visible group, and unchanged frozen sampling/split behavior.
- Actual production GPU checks: the same exported attributes through GLTFLoader;
  group-sharing materials; per-pixel crossings; camera/light/receiver depth and
  color; explicit layer/group overflow; render-failure restoration and disposal.
  Keep exact attribute and implementation pins. The isolated light-depth
  discrepancy was traced to the CPU oracle's double-precision snapping near a
  float32 boundary: the corrected test-only float32 oracle and Taylor24 response
  passed all 112 controls at unchanged coverage/thresholds on the tested D3D11
  device. Those earlier reports do not certify production or other GPU arithmetic;
  [new production evidence](EFFECTIVE_GROUP_RUNTIME.md) is recorded separately.
- The local production group path and shared angle helper now implement the
  representation. Mobile performance and driver memory overhead remain unmeasured.
  Miu's silicone/lens prior ambiguity, unsupported optical overlaps, photographic
  articulation and material/lighting ambiguity remain explicit evidence gaps.

Passing these checks establishes a usable conditional representation and its
implementation. Accurate automatic reconstruction from arbitrary product photos
still requires independent product/view evidence and a policy for insufficient
or contradictory inputs.

## Completed CPU bridge and reproduction

`optical_group_asset.py` implements the exporter and authoritative reader;
`optical_group_observations.py` feeds the existing fitting contracts through the
shared native-mask/color sampler. The original sampler's twelve frozen VB
observations remain exactly equal after refactoring. The new integration test
exports a multipart closed group, samples front/back masks and executes the
unchanged joint fitter. These checks do not establish photographic fit quality.

The saved corpus completed all five designs, eight declared groups, ten views and
51 mask hypotheses with one neutral control descriptor. All 1,033,521 source
triangles remained, and all previous coordinate/composition coverage counts
matched after export. Source positions and supplied normals had zero float32
conversion error on these five assets; UV error remained below `2.981e-8`.
The Miu silicone group remained unobserved and its main-part ambiguity was not
silently repaired. The corpus does not establish complete automatic grouping.

From `automation/`, using the retained private inputs:

```powershell
python -m qa.optical_group_candidates --groups data/optical-groups-v1/manifest.json --regions data/region-corpus/fixed-policy-v1 --output data/optical-group-candidates-new
```

Use a new output directory. The command verifies every archived part, source,
prepared-array and region binding, exports each case, rereads its actual attributes,
and saves exact fitter observations. It fits no color and cannot claim production
AR compatibility. The completed report is
`data/optical-group-candidates-v1/report.json`, SHA
`750378a0788505560304446b74c6b9e61f68986bad3a59a420f39c0b3091bf5e`.
Eleven exporter tests include source/receipt tampering, captured-byte geometry,
other scenes, shared/reflected instances and float32-created cross-group ties.
Six photo-adapter tests include actual export-to-fit execution and retained
unsupported rays. Production adoption remains the next representation step.
