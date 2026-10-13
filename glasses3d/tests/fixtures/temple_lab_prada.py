"""Explicit source data for the historical Prada T5 proof; never imported by runtime code."""
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
G3D = REPO / "glasses3d"
SOURCE_SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SOURCE_SCENE_SHA256 = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
REFERENCE = G3D / "data/reference/prada"
BASIS_JSON = G3D / "data/wp6v2/basis/cameras.json"
LAB_DATA = G3D / "data/wp6v2/lab"
# The current (v3) estimate table: request's --estimate and estimate's --out default. estimate.json is the v2 table,
# which request_estimate refuses (no photo_read, v2 image pricing): as the default it stopped `cli request` with a
# traceback (review 2026-10-12, P3; T5 open item 2).
ESTIMATE = LAB_DATA / "estimate_v3.json"
DEFAULT_RIG = "studio_dome_v1"          # the job rig: the owner's light choice of 2026-10-11 (plan/PROGRESS.md decisions)
K_TOKEN = "{K}"                     # instructions.txt states finish precondition 1 with the run's K
# The same model in black acetate (owner, 2026-10-11, approving the run after facet-astra-002 with the black photos
# included: "yes, make the fixes and run it again"; and "yes, build 1-3": calibrated cameras for B2 and B3). Each is a
# pose basis image (colourway variant, sha256-pinned bytes) whose camera was transported from the target photo of the
# same pose by an image similarity (tests/proofs/wp6v2_basis.py, g3d_host/camera_similarity.py): B1 from 05, B2 from
# 02, B3 from 04. photo_compare shows each beside the gloss_black render. No Prada photo is reference only now; a
# job whose extra photo has no camera passes it as a reference photo ({id, view, colour, source, path, sha256}:
# photo_image only). colour and the optional finish are job data: the brief and the photo labels print them (the
# instructions name no product's colour or material). The three black photos are glossy (their facets show as
# mirrored studio lights; review images 2026-10-11).
BLACK, GLOSSY = "black", "glossy"
COLOURWAY_PHOTOS = ({"id": "B1", "image": "black-side.png", "colour": BLACK, "finish": GLOSSY,
                     "sha256": "6eddabbb7bd40888801eaa40964795a278d1daaa4f10986f6b5f86a6e26c19be"},
                    {"id": "B2", "image": "black-angled.webp", "colour": BLACK, "finish": GLOSSY,
                     "sha256": "5705c2cab03f5e457d2b1dfc58b5155dec5ac03c3450e7672511a10aeb4ac8f1"},
                    {"id": "B3", "image": "black-front.webp", "colour": BLACK, "finish": GLOSSY,
                     "sha256": "e89d19f4101d6457a32274dc4a1b8b51a0de525ef09d2179b8657eb6c34621c3"})
REFERENCE_PHOTOS = ()
# The job's known camera-model conflicts (WP6_V3 P5: job data in the brief, worded as a limit of the camera model, never
# as doubt about a photo: owner rule, every supplied image is a real photo). Each row (conflict_text): ``photos`` (whose
# outline), ``what`` of them, ``d_mm`` (a range, or one d: "near d"), ``against`` (what cannot be matched with it),
# ``up_to_mm`` (how far apart at most, about), ``temples`` and ``further_out`` (whose lies further out). Rows:
# 1. photo 03's outer outline at the front block: under the pipeline's camera and rigid-temple model it passes outside
#    the crown|logo ridge that every other photo fixes by +0.84 / +0.86 (d 10-15), +1.57 / +1.47 (15-20), +2.18 /
#    +2.07 (20-25), +2.07 / +1.98 (25-30), +1.27 / +1.19 (30-35) and +0.44 / +0.40 mm (35-40) on the two temples (row
#    spread about +-0.35 mm): up to about 2.2 mm (FINDINGS §0.3 and WP6_V3 §0.4 say +2.2); free focal length, lens
#    distortion, a free centre, off-axis cameras, the temple pose, the station rule and the matte were each ruled out
#    (data/wp6v2/review_20261012/w2/midblock-settled.md "Settled fact" 3-4). v2 printed it as shape and it drove the
#    defects of runs 3, 4 and 5.
# 2. photos 03 and 06 at the back of the faceted block (review 2026-10-12, P2): at the fitted cameras the side + angled
#    readings put the crown's tip (d 75.4-75.6) at t 7.03 (bundle; probe 1's crossing 6.30, three readers' X 67.6-68.4:
#    crown-settled §6), outside both silhouettes (06 u 235.7 against ~250, 03 u 1195.5 against ~1176); turning both
#    angled photos' hinge opening by +2 deg puts it on both silhouettes (t 4.34: 2.7 mm in) but moves the corner at
#    d 36 to t 3.99 against the front views' 5.23-5.58, so no single theta reconciles them (w2/crown-geometry.md §2,
#    crown-geometry/theta_both.log; crown-settled.md §6 "still open"). Read on the charted temple only. Without this
#    row the prompt's goal 5 ("outlines match ... except at a camera conflict the brief names") pointed Astra at
#    closing that gap by moving the tip in: the defect class of runs 5 and 7 (K and E pushed in).
CAMERA_CONFLICTS = ({"photos": ("03",), "what": "outer outline", "d_mm": (10, 40), "against": "the other photos",
                     "up_to_mm": 2.2, "temples": "both temples", "further_out": "03's further out",
                     "evidence": "data/wp6v2/review_20261012/w2/midblock-settled.md"},
                    {"photos": ("03", "06"), "what": "outer outline", "d_mm": 75,
                     "against": "the two-direction readings of the side and angled photos", "up_to_mm": 2.7,
                     "temples": "the charted temple (the mirrored temple was not checked)",
                     "further_out": "the readings further out",
                     "evidence": "data/wp6v2/review_20261012/w2/crown-geometry.md §2 "
                                 "(w2/crown-geometry/theta_both.log); w2/crown-settled.md §6"})
# How well photo_read reads points on this job's photos (job data, measured): crisp corners read in two directions agree
# with the settled values within 0.2-0.5 mm (FINDINGS §0.5: run 7's two-photo readings 0.23-0.37 mm; WP6_V3 §0.3; v3
# probe 1: sd_X 0.18-0.29 at A, H, K, C, L); a tip or a point on an edge along the line of sight between the photos
# only to about 0.7-1.5 mm along that edge (FINDINGS §1 "tip d only to +-0.7-1.5 mm"; crown-geometry §2: B1 band
# 75.58 +- 0.70; v3 probe 1 §1: A as a crossing at 8.8 deg sd_d 0.82, the tip E as a crossing sd_d 1.24; E as a
# single corner pick printed sd_d 0.17, a false precision: v3/v3-readings-fix.md).
READING_PRECISION = {"corner_mm": (0.2, 0.5), "along_mm": (0.7, 1.5),
                     "evidence": "data/wp6v2/review_20261012/FINDINGS.md §0.5 and §1; plan/WP6_V3.md §0.3; "
                                 "data/wp6v2/review_20261012/v3/probe1.md §1"}

PHOTO_IDS = {f"reference-{n:02d}.{ext}": f"{n:02d}" for n in range(1, 7) for ext in ("png", "webp")}

def prepare_kwargs():
    return dict(source_scene=SOURCE_SCENE, scene_sha256=SOURCE_SCENE_SHA256, reference=REFERENCE,
                basis_json=BASIS_JSON, colourway_photos=COLOURWAY_PHOTOS, reference_photos=REFERENCE_PHOTOS,
                camera_conflicts=CAMERA_CONFLICTS, reading_precision=READING_PRECISION, photo_ids=PHOTO_IDS)
