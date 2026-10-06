"""WP6 faceted temple core (plan/WP6_DESIGN.md §3-§5, §10). numpy only, so the same code runs inside Blender 5.2 and
on the host; nothing here imports bpy, open3d, scipy or cv2.

    spec      schema "g3d.temple/1", defaults, refusal / warning codes, canonical JSON + sha256, validation
    arrange   2D geometry in (d, y): clipping, the planar arrangement (half-edge faces), ear clipping
    planes    exact plane propagation (3 nodes or crease + rise), base edges at r = 0, conditioning
    build     lift, strips, caps, chamfers, sharp sets, custom-normal corners, self-checks, mirror, opening
    measure   stations, vbulge / pbulge, single-plane share, facet table, flags (shared by the tool and check_relief)
    report    the tool's receipt
    sections  g3d.polygon_section (closed 2D sections with sharp / chamfer / round corners), check_round_corners
    loft      g3d.loft_sections (per-station lofts with sharp longitudinal edges and a strip planarity report)
    fixtures  the P0 fixture specs (§8) and the traced-Prada starting spec helpers

Frame: scene mm, +Y up, +Z front. The spec describes the +X temple; the -X temple is its exact mirror (x negated, faces
reversed). Parts are chosen by the sign of x, never by name. Station d = z_h - z at opening 0; X = |x|.
"""
SCHEMA = "g3d.temple/1"
CORE_VERSION = "wp6-core-1.0"

from .spec import TempleError, canonical, canonical_json, spec_sha256  # noqa: E402,F401
from .build import build_temple, TempleBuild  # noqa: E402,F401
