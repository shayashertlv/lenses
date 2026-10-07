"""WP5 photo-projected print, host side (WP5_DESIGN.md). Build steps (1)-(3) of §9, host side.

Step 0 (registration):
- ``inputs``: the job (model, photos, mattes, WP3b cameras, Astra's texture), hash-checked.
- ``signals``: warmth (R - B), irange (max - min), luminance; masked DoG band images.
- ``regions``: rigid groups (front, temple per x sign), regions (part x facing class), 4 mm stripes / folds, samples.
- ``pose``: cameras, hinges and per-region parallax -> S' per view.
- ``edges``: the edge gate per (view, part) (D2).
- ``register``: the plan (anchors, partners, free cameras) and the joint photometric fit with silhouette guards (§5).
- ``gate``: the §5.4 print-registration gate; its null displaces the TEXTURE along the region's strip (build step 3,
  §5.4 option (c): the overlap is kept, so a perfect match registers), the legacy photo-displacement null is kept for
  reports.
- ``check_print``: the held-out pixel proof (§7), graded by tests/instruments/print_metrics.py (a diagnostic for Astra
  in single-source mode, never a gate).
- ``report``: statuses, B1 / B2, STOP A and deterministic JSON. ``step0``: the end-to-end driver and CLI.
- ``procs``: the EcoQoS-opted-out process pool and progress lines.

Steps 2-7 (single-source mode: each region painted from its anchor photo only, never mixing unregistered views;
registered regions, none on FT1119 today, mixed in tier 1): ``project.project_photo_texture`` runs the numpy layout
core ``glasses3d.g3d.printuv`` on host arrays, then
- ``texels``: atlas texels, the whole-scene occluders (decals included), per-view donor samples (matte, occluder
  margins, per-pixel acetate, clipped pixels, and since step 4 the holes of ``metal``), anchors and tiers;
- ``metal`` (step 4 fix 1, 4): per photo, the metal / achromatic-highlight colour model (the havana's pigment arm in
  luminance x B/R), the hardware margins, thin glint / outline lines and dark compact blobs (rivets) as donor holes;
- ``labels``: face labels by an MRF with crease-discounted Potts (boundaries moved within 3 mm bands);
- ``gains``: per-view linear-light median gains to the front view (step 4: mirror edges, same-part boundaries, clip
  [0.33, 3]), per (region, view) group gains (same-part edges, mirror equations, robust pass) and the smooth stage-3
  levelling field across region boundaries and uv seams;
- ``highlights``: detect and make holes (never subtract);
- ``fill``: the text mask (D6), mirror fill of surfaces no photo sees, then the hole filler per (part, facing class)
  with the radius chosen by knockout: block quilting (default since step 4) or PyPatchMatch (``fill='patchmatch'``);
- ``seams``: label-seam band repair, the chart-seam reflection of fill texels (step 4, A5), uv-seam harmonisation,
  gutter padding;
- ``compose``: the byte-identity compose (A3) and rendering a bake at a camera.
- ``manifest`` (step 4): print_manifest.json, what ``g3d.attach_print`` reads (print.png, atlas, density and the
  print geometry's identities from ``g3d.printuv.glbmesh``), written with every bake; ``check_glb`` is the host's
  "print stale; re-project" check of an exported GLB.
Correction round (2026-10-06, after the AR tool test of 10bf932; PORT_REVIEW section 10):
- ``shading``: each photo's own normal-space SH shading divided out in linear light (``flatten='test'``: kept only if
  the photos then agree better on the same surface) and the AR level (``level``: gain and gamma calibrated TO THE
  PHOTOS on arview captures by ``tests/photoprint/ar_calibrate.py``), applied to the composed atlas as the last step;
- ``texels`` / ``metal`` / ``highlights``: the bicubic footprint (no donor within 2.5 px of the background or a bright
  non-acetate pixel: the endpiece scratches), the photometric occluder margin, gold-only foreign-blob dilation, no
  large-hardware margin, shoulder-only glint dilation, and witness flags that must be local highlights (brow-front
  coverage);
- ``quilt_t``: strip-space donors for quilting (the -x temple's streaks) and the see-through text closed into a band
  (``fill``); a label-seam band repair that fails §6.4's test is no longer applied.
Fix round 1 of the correction round: the flatten keep test also needs the drop to survive leaving any one view pair
out (on FT1119 it rested on pair 04-05: not kept); the footprint also keeps a donor 2.5 px from the light anti-aliased
edges of bright non-acetate pixels (``texels.footprint_holes``, rule 'edges'; a glint-line footprint is built, off).
Step 6 (2026-10-07, after the step-5 AR tool test; PORT_REVIEW section 10): in crease mode the anchor keeps the faces it
sees (``labels.KEEP_SEEN_SHARE``: the photometric seam had handed photo 05's brow honey band to the top view 04);
see-through text needs light acetate (``fill.light_acetate``: photo 03 shows no lettering, its 2 mm band was filled
smooth); thin light achromatic lines are highlight holes (``highlights.thin_light_lines``: the studio's dim glint
remnants on the endpiece bevels); the AR level map has a calibrated chroma factor (``shading.level_map`` ``sat``;
fix round 1: a power of the texel's C*ab, ``chroma_power``, which lifted the low end but also greyed the saturated
tail: with fix round 1's sat 1.107 / power 0.745 the factor is 1.107 at C* 10 and falls below 1 above C* ~15
(1.107 x (C* / 10)^-0.255 = 1 at C* 14.9); fix round 2: ``chroma_hold`` keeps the factor >= 1 and ``chroma_hue`` lets
it act only inside the havana hue band, so off-hue noise is not amplified). The level a bake applied is recorded in
its report.json ``level`` (FT1119's production bake ``data/wp5/ft1119_bake``: gain 0.931, gamma 1.080, sat 0.996,
chroma_power 0.455, chroma_hold true, chroma_hue 20-100 deg).
The Blender side (``g3d.print_uv``, ``attach_print``, ``detach_print``, ``remove_print_uv``; the exporter's
``g3d_print_uv`` allowlist and TEXCOORD index; A6, B5) is ``glasses3d/g3d/print_bpy.py``. Flatten (§6.6) is built since the correction round (``shading``; it was off on
FT1119, §6.6). Sides are always the x sign, never part names (q001 names temples by the viewer's side).
"""
from .project import RefusedError, project_photo_texture

__all__ = ["project_photo_texture", "RefusedError"]
