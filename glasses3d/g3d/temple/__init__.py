"""WP6 v2 faceted temple core, g3d.temple/2 (plan/WP6_FACETS.md §2). numpy only, so the same code runs inside
Blender 5.2 and on the host; nothing here imports bpy, open3d, scipy or cv2.

    spec      schema "g3d.temple/2", defaults, refusal / warning codes, canonical JSON + sha256, knot
              canonicalisation (snapping), validation into a ParsedSpec
    arrange   2D geometry in (d, y): clipping, the planar arrangement (half-edge faces), ear clipping
    planes    exact surface propagation in world X (3 known values, crease + rise, a given coef plane, or a warp:
              mean value coordinates over the corners); no base seeding
    build     border band (soft: wider), stations, warp refinement, values (facets, warps, ramps, base),
              chamfers, core roll, cells, sharp sets, design corner normals, crease check, self-checks, mirror, opening,
              model_edges (the creases per run with their signed angle per point, for the compares)
    measure   chart maps, sections, decal / hinge-part rules
    report    the compact receipt (crease lines first; the 10 mm station table on request)
    convert   g3d.temple/1 -> /2 (tests only; the only place a /1 spec is read)
    template  revision 0: a /2 spec measured from an existing temple mesh (template_from_mesh) and its report
    draw      the (d, y) chart image (host only: imports PIL, never imported in Blender)
    fixtures  the P0 fixture specs and refusal cases, the Prada-layout helper
    sections  g3d.polygon_section (closed 2D sections with sharp / chamfer / round corners), check_round_corners
    loft      g3d.loft_sections (per-station lofts with sharp longitudinal edges)

Frame: scene mm, +Y up, +Z front. The spec describes the +X temple; the -X temple is its exact mirror (x negated, faces
reversed). Parts are chosen by the sign of x, never by name. Station d = z_h - z at opening 0; X = |x|.
"""
SCHEMA = "g3d.temple/2"
CORE_VERSION = "wp6-core-3.0"      # 2.1 (2026-10-11): cells by fade weight, mixed fades (E_FADE_CLIFF, W_FADE_HOLD,
#                                    W_FADE_INERT), standing faded creases sharp; 2.2 (2026-10-11): W_FADE_EARLY;
#                                    2.3 (2026-10-12): E_FADE_CREASE, crease planes on a straight run of edges;
#                                    3.0 (2026-10-12, WP6_V3 §2): plane modes coef and warp, soft borders, one
#                                    tolerance (planar_tol_mm), facet|base creases against the base surface (the
#                                    fold across the border), warp refinement clear of slivers, facts-only refusals;
#                                    3.0 (phase 2, 2026-10-12): crease means, shape votes and shares over the
#                                    face-to-face edges as range_deg (roll and chamfer bands out), model_edges (soft
#                                    only where the export shades it smooth), angles never rounded across the flat
#                                    line (build.deg_round, flat_cut); geometry unchanged;
#                                    2026-10-13: fades removed (a spec with one is E_SPEC; E_FADE_* and W_FADE_* gone),
#                                    every other spec builds bit for bit as before

from .spec import TempleError, canonical, canonical_json, spec_sha256  # noqa: E402,F401
from .build import build_temple, model_edges, TempleBuild  # noqa: E402,F401
