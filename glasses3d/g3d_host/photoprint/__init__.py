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
  margins, per-pixel acetate, clipped pixels), anchors and tiers;
- ``labels``: face labels by an MRF with crease-discounted Potts (boundaries moved within 3 mm bands);
- ``gains``: per-view linear-light median gains to the front view, then gain matching across region boundaries;
- ``highlights``: detect and make holes (never subtract);
- ``fill``: the text mask (D6), mirror fill of surfaces no photo sees, PatchMatch per (part, facing class) with the
  radius chosen by knockout;
- ``seams``: label-seam band repair, uv-seam harmonisation (A5), gutter padding;
- ``compose``: the byte-identity compose (A3) and rendering a bake at a camera.
Not built here: the Blender side (``g3d.print_uv``, ``attach_print``, the TEXCOORD export; A6, B5) and flatten (off on
FT1119, §6.6). Sides are always the x sign, never part names (q001 names temples by the viewer's side).
"""
from .project import RefusedError, project_photo_texture

__all__ = ["project_photo_texture", "RefusedError"]
