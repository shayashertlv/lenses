"""WP6 relief rules: every threshold the relief check, the view comparison and the $0 proof use (plan/WP6_DESIGN.md
§5, §6, §8, §9), frozen BEFORE any proof run. The proof records this file's sha256 in its output; a changed file is a
new rule set, never a tweak after results. Data only: no logic lives here.

The geometry definitions shared with the Blender check are asserted equal to g3d/temple/measure.py by
tests/test_temple_relief_photo.py (so neither side can drift alone).
"""
RULES_VERSION = "wp6-relief-rules-1 (2026-10-06)"

# ------------------------------------------------------------------ geometry (§5), must equal g3d/temple/measure.py
SHARE_D_RANGE = (2.0, 50.0)          # single-plane share: outer face between these stations
SHARE_TOL_MM = 0.3                   # ... within this of one plane
FACET_NORMAL_DEG = 1.0               # facet table: region growing
FACET_MIN_AREA_MM2 = 4.0
STATION_STEP_MM = 2.5
SLAB_SHARE = 0.8                     # SLAB: share >= this while the photos show >= 2 creases
SLAB_MIN_PHOTO_CREASES = 2
INNER_FLAT_MM = 0.05                 # INNER_NOT_FLAT above this
BULGE_SANITY_MM = (1.9, 4.3)         # BULGE_SANITY: vbulge outside -> warning only (PLAN correction 1)

# ------------------------------------------------------------------ photo creases (§5 "Photos")
CANNY_SIGMA_PX = 1.2
CANNY_LOW, CANNY_HIGH = 6, 14        # on L* x 2.55 (8 bit)
HOUGH_MIN_MM = 4.0                   # probabilistic Hough segments at least this long
HOUGH_THRESHOLD = 18
HOUGH_MAX_GAP_PX = 4
STEP_OFFSETS_PX = (3, 7)             # step = median L* 3..7 px either side
STEP_MIN_L = 3.0                     # a segment whose step is below this is not a crease
MASK_DECAL_PX = 6                    # decals dilated by this
MASK_HIGHLIGHT_L = 97.0              # L* above this masked
MASK_SILHOUETTE_PX = 5               # silhouette band masked
VIEW_MIN_PX_PER_MM = 5.0             # crease views: the outer face at >= 5 px/mm ...
VIEW_MIN_COS = 0.5                   # ... seen at a viewing cosine >= 0.5 ...
VIEW_MAX_REG_MM = 1.0                # ... registered within 1 mm at the temple
DN_SAMPLE_PX = (3, 7)                # model normal change measured 3..7 px either side
CREASE_NEAR_MM = 2.0                 # FLAG: no model crease within this and dn < DN_OK_DEG
DN_OK_DEG = 6.0                      # policy constant (delivered <= 4.6 deg on all side segments)
MODEL_CREASE_DEG = 6.0               # a model crease pixel: normal change >= this between pixels 2 px apart
CONFIRM_MEDIAN_MM = 0.5              # a crease is confirmed when 05 and 01 (mirrored) lift to (d, y) lines this close
INVENTED_CREASE_DEG = 5.0            # INVENTED_CREASE: a model crease >= this ...
INVENTED_CREASE_PX = 2               # ... with no photo segment within this many px
FRONT_BOUND_TOL_MM = 0.2             # FRONT_BOUND: a hinge-zone point beyond the 04 silhouette by more than this

# ------------------------------------------------------------------ views (§6)
REFIT_BOUNDS = {"yaw": 3.0, "pitch": 2.0, "roll": 1.0, "scale_pct": 3.0, "shift_px": 10.0, "persp": 0.1}
REFIT_MAXFEV = {"fit": 220, "final": 140}
FRONT_DEGRADE_MAX_MM = 0.1           # a refit may not worsen the view's front-part contour by more than this
THETA_RANGE_DEG = (-4.0, 12.0)       # per-photo hinge angle
THETA_GRID_DEG = 1.0
THETA_XTOL_DEG = 0.05
REAR_PITCH_DEG = (5.0, 16.0)
REAR_PERSPECTIVE = (0.03, 0.4)
REAR_ALPHAS_DEG = (-2.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0)
REAR_FAMILY_RATIO = 1.2              # the family = every alpha with loss <= 1.2 x the minimum
ROI_DILATE_MODEL_PX = 20             # temple ROI = dilate20(union of every model's temple pixels) ...
ROI_DILATE_MATTE_PX = 10             # ... U dilate10(matte minus dilate6(non-temple render))
ROI_NONTEMPLE_PX = 6
CONTOUR_TRUNC_PX = 60
HINGE_ZONE_D = (0.0, 50.0)           # envelope rows by the render hit's station
ARM_BAND_D = (60.0, 130.0)

# ------------------------------------------------------------------ gates (§8)
G1_TOP_OFFSET_MM = 0.6               # hinge-zone signed offset |median|
G2_REAR_CONTOUR_RATIO = 0.8          # ROI contour <= 0.8 x delivered's at every alpha of the family
G3_OUTLINE_TOP_MM = 0.3
G3_OUTLINE_BOTTOM_MM = 0.5
G4_FRONT_CONTOUR_MARGIN_MM = 0.05
G5_ANGLED_IOU_MARGIN = 0.01
R1_SHARE_MAX = 0.5
R2_FLAG_MIN_MM = 4.0                 # no FLAG segment this long
R2_MEDIAN_DN_DEG = 6.0               # length-weighted median dn
R2_CONFIRMED_NEAR_MM = 1.0           # every confirmed crease: a model crease this close ...
R2_CONFIRMED_DN_DEG = 3.0            # ... with dn >= this
R3_TRANSFER_MAX_MM = 0.7
R3_TRANSFER_MARGIN_MM = 0.5          # and <= delivered - 0.5 and <= fitted slab - 0.5
R4_SLAB_MARGIN_MM = 0.1              # every view's ROI contour <= fitted slab's + 0.1

# ------------------------------------------------------------------ $0 proof (§8 P1/S1/P2, §9)
P1_DELIVERED = {"vbulge_mm": (0.62, 0.03), "pbulge_mm": (1.34, 0.05), "single_plane_share": (0.81, 0.03)}
S1_RECOVERY = {"outline_mm": 0.2, "zone_level_mm": 0.5, "r_B_mm": 0.5, "r_C_mm": 0.5, "f1_rise": 0.05,
               "theta_deg": 0.3}
P2_TRACE = {"in_view_mean_mm": 0.2, "held_out_mm": 0.5, "A_B_mm": 0.5, "C_mm": 1.5}
STOP_R_GUARD_MM = 2.0                # rear: the frame-fit guard above this at every alpha of the family
STOP_S_RATIO = 1.5                   # a relief-seeing view's ROI contour > 1.5 x its front contour + 0.3 mm
STOP_S_OFFSET_MM = 0.3
STOP_C_MIN_MM = 1.0                  # an observable's own optimum outside max(1 mm, 2 sigma) of the joint optimum
STOP_C_SIGMA = 2.0
STOP_C_FRONT_ACTIVE_MM = 0.5         # or the joint optimum needs the front bound active by more than this
FIT_MAX_ROUNDS = 4
FIT_MAXFEV = 300
FIT_STOP_MOVE = 0.1                  # every parameter moves < 0.1 mm / 0.1 deg ...
FIT_STOP_LOSS = 0.01                 # ... and the loss < 1 %
TRANSFER_MM_WEIGHT = 0.02            # objective: 1 mm of 05 -> 02 crease transfer = 0.02
CREASE_REPROJ_MM_WEIGHT = 0.02
