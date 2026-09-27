# Pending frame membership guard

Staged while reconstruction implementation hashes are frozen. No production
source has been changed by these files.

Run:

```powershell
python -m qa.staged_frame_membership.build_stage
python -m unittest qa.staged_frame_membership.test_frame_image_support -v
```

`build_stage.py` captures the current production file hash and creates the
guarded intrinsic-frame module in this directory. Replacements are checked for
unique matches; an unexpected production edit fails instead of patching blindly.

`frame_image_support.build_frame_image_support(photo_record, region_directory,
output=None, *, pins=None)` replays native source pixels and the saved independent
`contrast-object` region. It verifies all original mask hashes, the recomputed
source-contrast prior and all five source-only prompts, then intersects all 15
SAM masks with strong source contrast. Invalid alternatives are never dropped.
Authored alpha provides a separate geometric support path. Candidate optical
prior masks may only exclude, never establish foreground support.

Membership is native-grid uint8: 0 unknown, 1 corroborated foreground for
conditional opaque-frame sampling, 2 authored-alpha exterior. Class 1 does not
certify a semantic frame or physical opacity. Opaque JPEG white regions stay
unknown; source white texture remains unchanged. Partial alpha is excluded.

The staged frame module stores membership, geometric eligibility and source
alpha per track. It intersects eligibility before exposure estimation,
separation and complete-area coverage. Reports include per-photo exclusions
and pinned support images. No valid membership means no correction and no
observed-area credit; the original GLB remains byte-identical.

Eleven tests cover the positive path, mask/prompt mutation, contrast-only and
projected-only rejection, a dark background line, invalid/disagreeing masks,
white patterns, translucent/hidden alpha, misregistered white backdrop,
unchanged unsupported GLB, and guarded complete-area coverage. The source-only
saved Oakley five-view replay also returns nonempty support in all five views.

After the root agent lifts the freeze, install `frame_image_support.py` into
`reconstruction/` and the staged intrinsic module with production-relative
imports. Update the intrinsic-frame test photo fixture to record independent
source-only region alternatives at adequate native resolution; old fixtures
provide only candidate projection and must no longer grant observation credit.
Move the new focused tests into `tests/` with production imports. The existing
`run_intrinsic_frame_stage` public call does not change; delivery integration
needs no additional engine, model load or API call.
