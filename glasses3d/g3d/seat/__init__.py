"""WP4 lens seating core (WP4_DESIGN.md §3, §9). numpy only, so the same code runs inside Blender 5.2 and on the host;
nothing here imports bpy, open3d or scipy.

    geom      exact weld, topology, volume, normals, cap/wall split, rim loops, rungs, quadric fit
    intervals LineIntervals backends (NumpyBackend), parity, merge, re-cast
    stations  LensBody, measurement points, station frames, measure()
    rules     FRAME_TYPES (§7 / PLAN §3), rule values, verdicts, the check verdict
    report    report schema and summaries
"""
