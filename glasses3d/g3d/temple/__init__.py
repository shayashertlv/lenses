"""WP6 v2 faceted temple core, g3d.temple/2 (plan/WP6_FACETS.md §2). numpy only, so the same code runs inside
Blender 5.2 and on the host; nothing here imports bpy, open3d, scipy or cv2.

    spec      schema "g3d.temple/2", defaults, refusal / warning codes, canonical JSON + sha256, knot
              canonicalisation (snapping), validation into a ParsedSpec
    arrange   2D geometry in (d, y): clipping, the planar arrangement (half-edge faces), ear clipping
    planes    exact plane propagation in world X (3 known values or crease + rise); no base seeding
    build     border band, stations, values (facets, fades, ramps, base), chamfers, core roll, cells, sharp sets,
              crease check, self-checks, mirror, opening
    measure   sections, bulges, single-plane share, facet table (information), decal / hinge-part rules
    report    the compact receipt (crease lines first) and the full tables
    convert   g3d.temple/1 -> /2 (tests and owner sheets only; the only place a /1 spec is read)
    fixtures  the P0 fixture specs and refusal cases, the Prada-layout helper
    sections  g3d.polygon_section (closed 2D sections with sharp / chamfer / round corners), check_round_corners
    loft      g3d.loft_sections (per-station lofts with sharp longitudinal edges)

Frame: scene mm, +Y up, +Z front. The spec describes the +X temple; the -X temple is its exact mirror (x negated, faces
reversed). Parts are chosen by the sign of x, never by name. Station d = z_h - z at opening 0; X = |x|.
"""
SCHEMA = "g3d.temple/2"
CORE_VERSION = "wp6-core-2.0"

from .spec import TempleError, canonical, canonical_json, spec_sha256  # noqa: E402,F401
from .build import build_temple, TempleBuild  # noqa: E402,F401
