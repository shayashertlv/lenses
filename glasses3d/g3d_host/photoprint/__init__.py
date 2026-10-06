"""WP5 photo-projected print, host side (WP5_DESIGN.md). Built so far: build steps (1) and (2) of §9.

- ``inputs``: the job (model, photos, mattes, WP3b cameras, Astra's texture), hash-checked.
- ``signals``: warmth (R - B), irange (max - min), luminance; masked DoG band images.
- ``regions``: rigid groups (front, temple per x sign), regions (part x facing class), 4 mm stripes / folds, samples.
- ``pose``: cameras, hinges and per-region parallax -> S' per view.
- ``edges``: the edge gate per (view, part) (D2).
- ``register``: the plan (anchors, partners, free cameras) and the joint photometric fit with silhouette guards (§5).
- ``gate``: the §5.4 print-registration gate (decoys outside the window).
- ``check_print``: the held-out pixel proof (§7), graded by tests/instruments/print_metrics.py.
- ``report``: statuses, B1 / B2, STOP A and deterministic JSON. ``step0``: the end-to-end driver and CLI.
- ``procs``: the EcoQoS-opted-out process pool and progress lines.

Not built yet (build step 3): ``project_photo_texture`` and steps 2-7 (UV layout in Blender, texels, tiers, gains,
highlights, labels, fill, flatten, compose, bake). Sides are always the x sign, never part names (q001 names temples
by the viewer's side).
"""
