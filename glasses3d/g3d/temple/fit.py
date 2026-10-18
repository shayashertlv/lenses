"""Fit of a faceted temple design to its readings, g3d.temple/2 (plan/WP6_V3.md §3). numpy only, host, deterministic.

fit_spec(design, *, readings=None) -> FitResult. The design is a g3d.temple/2 spec whose node t may also be a reading
{"read": {"id", "X", "sd", "d", "y", "photos", "sd_d", "sd_y", "cov", "camera"}} (world X of the +X temple at opening 0,
chart frame; read_payload builds it from an agent/temple_lab/readings.py record): the point (d, y, X) where it was
read, sd its X pick sd, sd_d / sd_y or cov (3 x 3 over d, y, X in mm^2, its X variance = sd^2) its position error,
camera {fit: [dd, dy, dX]} the read point's move for +1 sd of each camera fit's theta (one shared unknown per camera
fit, below). Facet planes are "nodes" (flat), "warp", a fixed {"coef": [a, b, c]} or a legacy {"crease": [n1, n2],
"rise_per_mm": s} (flat, its slope across the crease fixed). A fade is refused E_SPEC (fades were removed from the
core on 2026-10-13; review F6 had already refused them here: the fit describes the unfaded surface). FitResult.spec is
a pure /2 spec: every node in a facet has a numeric t (but a null node
outside the outline that unbound flat facets list stays null, below); flat facets carry {"coef": [a, b, c]} (X = a + b d
+ c y), warp facets "warp"; a reading on a node in no facet becomes its t (outside every facet's polygon: one inside
or on a facet's polygon and the outline, which the builder would bind there, is refused E_SPEC, _Problem._orphans,
v3p3-verify-13); a read corner with a position unknown is
written where the fit put it (its design's parsed position when it did not move), with the outline knot it lies on
(below); everything else (the other nodes' d / y, outline, inner, base, creases, chamfers, roll, options, soft) is
copied, and the corners are a fixed point of the builder's snapping (_Problem.builder_point), so the builder builds
what the fit fitted.

Why (FINDINGS.md 2026-10-12 §0): the real top face is not one plane (K +1.5 mm off the plane of its other corners);
the exact-planar builder had to put that misfit somewhere, and each run's newest rule chose where. Readings are soft;
the host fits every face jointly and reports each misfit in mm and sigma (WP6_V3 P1).

Geometry comes from spec.parse of a probe copy (every plane "nodes", every t a number): the same snapping, x_in, base
and outline the builder uses. Unknowns Z = (z, delta): z the X of every node in a facet, (a, b, c) of every flat facet
(X = a + b (d - d_c) + c (y - y_c), centred on its design corners), and u_f, the theta turn of every camera fit f the
readings name, in sd of its theta error; delta the read corners' positions along their free directions (below). The
structure (unknowns, rows, bound points, crease pieces) is fixed once; `numbers(nodes, ps)` evaluates every geometry
matrix at given corner positions and outline.
- Observations (weighted least squares): a typed t (X = x_in(d) + t, sd TYPED_SD_MM 0.5); one unit prior row per
  camera fit (u_f ~ N(0, 1): one rigid turn of the temple per camera fit, the same for every reading of that fit,
  readings.py's camera term; counted as independent noise per reading, as the probe1 payload did, the flat crown's
  run-7 shoulder|base valley was against D at 7.0 sd); a null (solve) node only the weak prior PRIOR_SD_MM 5.0 toward
  the base surface at (d, y), so every fit is well posed. The prior acts only in the directions no reading or typed
  value sees (solve_lsq's weak rows): it never moves what they and the faces determine (prada's virtual corners 50 mm
  off the outline would otherwise tilt their planes 0.2 mm).
- A reading observes its corner's whole point (one model for the solution, its cost and its uncertainty, WP6_V3 §3;
  review 2026-10-12 P1): its (d, y, X) = the corner's + sum_f u_f s_f + its pick error (cov). Where the corner may move,
  its position is an unknown (delta, along its free directions B), observed with X jointly: rows L^-1 (B'(p - p_read)
  + delta + sum_f u_f B's_f) (L L' = the covariance of its free directions) and the X row (xi - K pi) / s_c, X
  conditioned on them (K = S_pp^-1 S_pX, s_c^2 = S_XX - S_Xp K: _obs_system). Every geometry row (corners on their
  plane, T-junctions, thickness bounds, crease slopes) is linearised in delta at the point (Gauss-Newton:
  D[:, j] = dR_j z_k - dr_j, a secant over +- the reading's sd along the direction, POS_STEP_SHARE: a base station
  across a facet|base border makes its fold a step there), and re-linearised where the solution moves the corners
  (set_point; the passes damped where they cycle, the last two points held and the cheaper kept). So the border of
  a facet with the base turns with its ends: the effective-variance model of v3p2-fit2 kept the corners where they
  were read and missed it (probe1's flat-border truth, shoulder|base sd 0.40 against a Monte Carlo spread of 3.14 of
  the exact process, its near-true valley / ridge refused in 73 % / 79 %; now 1.03 of it, every crease 0.96-1.07 on
  three truths, those declarations refused in 21.5 %, as a 2-sd rule refuses one ~1 sd from the truth; tests).
  Free: a read corner inside the outline with a read (d, y) and a position covariance; it slides
  along an outline edge or a facet edge it lies on without being that facet's corner, moves its outline knot with it
  (in y only at the hinge-end cap; never at the rounded tip or the knot its rounding starts from; not when that
  knot's edge holds another node), keeps its d where the design's parse snapped another node or an outline knot no
  corner carries onto it (an inner or base table knot snapped there follows it within snap_mm, as in the builder),
  and stays where another node shares its point. Pinned directions (the rest): the reading's X is carried along the
  faces there to the corner, X - g_W . (p_read - p) (g_W the faces' mean gradient there projected on them; review
  P2: a reading used 0.5 mm from where it was read gave its X unchanged at the node, crown|shoulder +3.4 deg), with
  the variance v' C v, v = (-g_W, 1), and camera coefficient dX - g_W . (dd, dy); the gradients come from the
  readings-only fit, re-solved until they settle (WEIGHT_TOL; damped where they cycle), then frozen for every declared
  fit. The fitted corners are made a fixed point of the builder's snapping, then held, and the solution re-solved
  exactly there (_final, _hold_passes): its chi2 includes the corners' moves, and the cost of a declaration is that
  chi2's increase over the readings-only fit's. Each solution's covariance is the joint one (corners free) at its
  point.
- Equalities, eliminated exactly (SVD null space, met to ~1e-12 mm; the builder's planar_tol is 1e-6): a flat facet's
  corners on its plane; "base" = the base surface (a hard value); a coef facet's plane; a legacy crease plane's slope
  across its crease = rise_per_mm; a node inside or on the outline that lies on another facet's boundary without being
  its corner lies on that facet's surface (T-junction: the plane; on a warp the edge's linear interpolation); two
  nodes at one point are equal. A null node outside the outline listed by several flat facets is one unknown per group
  of facets joined by a shared edge from it that reaches inside the outline (union-find): the builder binds two facets
  at every arrangement vertex they share, and such an edge's outline crossing is one (planes.solve; review F4, the
  fixture relief_step); facets joined only outside the outline are unbound there (prada's virtual A_l, C_lo: one copy
  per facet, its t null in the fitted spec, each coef plane carries it). A read, typed or base node there is one value
  for all (the builder gives a node's value to every facet listing it); so is a node any warp lists.
- Inequalities: the thickness bound X - x_in - chamfer - max(roll_mm, roll depth) >= t_min + THICK_MARGIN_MM (E_ROLL
  needs the pre-roll section >= t_min + roll_mm at every vertex, E_TOO_THIN the rolled one >= t_min; review F2). The
  thickness of a flat facet is linear but for x_in (piecewise linear in d), the chamfer (linear across its band, kinked
  along d at its breaks and at outline knots) and the roll (constant roll_mm off the outline's two bands); it is convex
  only across vertical "event" lines (concave inner knots, chamfer breaks, outline knots inside a chamfer's d range), so
  its minimum over a facet inside the outline lies at one of: a node inside or on the outline, a facet-edge point where
  the edge crosses the outline or an event line, an outline vertex inside or on a facet, an outline point on an event
  line inside a facet (review F3: an outline vertex a facet covers). Bounded there: node rows, the facet's plane, a
  warp's edge interpolation, or its mean value coordinates (planes.mvc's own weights) at an outline point inside it. A
  warp is not linear inside (a non-convex warp dips below its corners, MVC is not linear along an event line or an
  outline segment, review F5): after every solve each warp's thickness is sampled on a THIN_GRID_MM grid, along its
  outline segments and event lines; the lowest samples within THIN_CHECK_MM of the bound are polished to the local
  minimum (Newton on a 9-point stencil, golden section on a line), and they and up to THIN_SAMPLE_CUTS violating
  samples, where below the bound by THIN_TOL_MM, become bound rows (cutting planes, kept for every later solve); the
  problem is re-solved (<= THIN_PASSES, else E_FIT "thin"; a warp far above its bound everywhere is skipped).
  Each declared crease is built with its shape: its fold at least MIN_CREASE_DEG 2.5 at the midpoint (WP6_V3 §3), and
  the length-weighted mean of its signed dihedral at least FLAT_CREASE_DEG + MEAN_MARGIN_DEG along each piece (below).

Crease angle (WP6_V3 §3): across a shared edge, n = the unit (d, y) normal pointing from F1 into F2 (pair order:
sorted labels, "base" last), g_i = grad X_i . n (F2 = base: the base gradient), angle = atan(g1) - atan(g2) (+ =
ridge). Linear in z for a fixed point: a flat facet's gradient is (b, c), a warp's at a point of its edge the mean value
coordinates' boundary derivative (mvc_edge_gradient). Every shared piece inside the outline carries the constraint at
its midpoint; beside a warp or the base (the angle varies along the edge: a warp's slope across it, the base's gradient
along a border; the builder judges the whole crease) at the EDGE_AT points 1/16 .. 15/16 of the piece, which also give
the reported range along the edge (review P2-4: run 7's warp crown|logo +17.3..+23.2 into K; its warp-crown
shoulder|base fold -2.6..-1.9 along the border, the builder's 2.0-2.6 there). The reported angle is the
longest piece's midpoint's, beside normal_deg, the dihedral there: the angle between the two design normals with the
slope along the edge shared (two faces share it at a crease; at a facet|base border the base's is the facet's, as the
builder's crease tables, build._base_border_design: the step changing along a border is no fold; run 7's flat-crown
shoulder|base fold +2.9, dihedral 2.6, its raw normals 19.2 apart); mean_dihedral_deg is the EDGE_W mean of the
signed dihedral over that piece (the builder's length-weighted mean to 0.14 deg beside a warp, where the midpoint's
was up to 1.8 off: probe1's crown|logo runs +16..+33 along K-A). The report prints the fold (deg, its sd, certain)
and, for facet|base, the dihedral beside it, both labelled (review P2-3); the builder's facet|base sign is this fit's,
(g_facet - g_base) . n. A declaration holds what the builder judges, with a margin, and no more: the builder calls a
crease flat when its length-weighted dihedral is < FLAT_CREASE_DEG and votes its shape by length. So per shared piece
the midpoint's fold (the §3 angle) is >= MIN_CREASE_DEG with the declared sign (for a fold that changes monotonically
along the piece, the midpoint's sign is the length majority's), and the mean of its signed dihedral over the piece
(EDGE_W quadrature of the EDGE_AT points; one point for a piece between two flat facets) is >= FLAT_CREASE_DEG +
MEAN_MARGIN_DEG (0.25: a roll's sub-band edges lower the built mean ~1.5 %, run 7 crown|logo 16.79 against 17.05).
Holding every point at 2.0 or 2.5 refused designs the builder builds (review survey: crown-geometry r0008 / r0011,
shoulder|base folds sweeping -5..+0.3 and +4.8..-0.3 along the border, built at means 2.47 / 2.25 with 96 % of the
length voting the declared shape); the midpoint alone let a steep border (along-edge slope s > 0.75: dihedral ~ fold /
sqrt(1 + s^2)) build flat. Linear in z about the previous pass: with a = g1, b = g2 across the edge and s along it
(shared), tan(fold) = (a - b) / (1 + a b) and tan(dihedral) = (a - b) sqrt(1 + s^2) / (1 + s^2 + a b); the midpoint
row sign (a - b) >= tan(MIN_CREASE_DEG) (1 + m^2) (m = (a + b) / 2, a b <= m^2: a converged pass builds at least the
minimum), the mean row sum_i w_i c_i sign (a_i - b_i) >= tan(FLAT_CREASE_DEG + MEAN_MARGIN_DEG) (c_i = sqrt(1 + s_i^2)
/ (1 + s_i^2 + a_i b_i) of the previous pass: the mean of tan(dihedral), within 1 % of tan of the mean below 10 deg);
re-solved until they settle (_declared_fit: <= ANGLE_PASSES 60; they contract ~0.1 per pass, and a cap of 5 refused
the probe1 flat crown's valley E_FIT although it held from pass 6 and settled at 12); every declared piece is then
checked exactly: a short piece, after settling or at the cap, is E_FIT "angles" with its fold, mean dihedral and the
passes.

Solve (solve_lsq): weighted least squares, exact equalities by the null space, inequalities by least distance
programming through Lawson-Hanson NNLS (Lawson & Hanson 1974, ch. 23: an exact active-set method; the tests compare it
with scipy SLSQP). Covariance from the reduced normal matrix at the active set (active bounds held as equalities) and
without it (cov_free). dof = the readings and typed values less the directions they set that no active bound holds.

Uncertainty of a crease angle: the readings-only fit's covariance with no bound held (a bound sets a value, not the
readings: "certain" never rests on an active bound, review P2-5), in the one model (the corners' positions and the
camera turns with X, at the readings-only fit's point); sd_deg_X is the same from the readings' own X sd alone, the
corners held (information). A crease's state (`state`): "certain" when its readings-only angle is beyond CERTAIN_SD 2
sd and FLAT_CREASE_DEG; "flat" ("flat within 2 sd") when its |angle| + 2 sd < MIN_CREASE_DEG, so neither declaration
can be built within the readings; else "open" (a declaration then sets it; review 2026-10-12 P3: "open" labelled
both, and a flat one's declarations were all refused). The reported angle (`deg`) is the longest piece's midpoint's
(WP6_V3 §3); free_mean_deg / free_mean_sd_deg the readings-only mean dihedral over that piece (the builder's vote) and
its sd. Where a held bound shrinks the sd below BOUND_SHARE the crease names the bound points (the report says so).

Declarations: a declared pair that shares no edge inside the outline is refused E_FIT_SHAPE before any solve (no fit
can build it; the builder would refuse E_CREASE_SHAPE, review P2-6). Else the readings-only fit (no crease
constraints; thickness kept) gives chi2_free; the cost of the declarations is the constrained fit's chi2 over it, in
the same weights (above). Beyond ACCEPT_DCHI2 4.0 (about 2 sigma) the fit refuses E_FIT_SHAPE naming, per declared
crease that costs (in pair order), its readings-only angle +- sd, its cost (that crease alone against the
readings-only fit) and the 3 readings, typed values or camera turns it is most against (a reading's whole residual
change, its X row and position rows together, in sd; a turn's change in sd of its prior), unsigned (the signed X mm
are in the fields): "against the readings of E (3.5 sd), K (1.2 sd)", the camera turns apart as a host fact ("it
turns the camera of fit 02 by 5.6 sd of its theta error (a host camera fit)": a camera fit has no pick sheet, review
2026-10-12 P3): facts only, no amount to type, no remedy offered or ranked (Astra took a refusal's listed remedy in
12 of 14 multi-remedy refusals, FINDINGS §0.5).

Flatness: a flat facet with >= 4 known corners (read, typed, or base at HARD_SD_MM) whose own weighted plane fit
gives chi2 > FLAT_CHI2_PER_CORNER 4 per extra corner is W_FACE_NOT_FLAT (fixed wording, ways "(unranked)"). With one
extra corner every leave-one-out sigma is the same, so the data cannot single a corner out (naming one picked the
best-read corner, K, whatever the readings said, review P1-2): it lists every corner's deviation from the plane of the
other three, mm +- sd (a corner whose others lie nearly in line, its sd > IN_LINE_RATIO x its own: "(others nearly in
line)"), after that one sigma and "the readings cannot say which corner is off" (v3p3-verify-2: the mm alone rank the
corners by lever arm, the hinge corner A always first); with more, the three largest in sigma. It then says where the
flat fit puts the misfit: each observed corner's whole residual (fitted - read, after the fitted camera turns: a corner
the fit may move, its (d, y) part along those directions and its X part, each in mm and sd; _misfit_text), ranked by
its tail probability for its rows (v3p3 P1: "the flat fit: E +0.41 mm (1.1 sd)" while the fit slid E 3.04 mm, 2.8 sd,
along its reading). Each face's test is its own (_flat_inputs):
a reading at the point where it was read, its sd v' C v with this face's gradient (review 2026-10-12 P3: the mean over
every face through the corner over-fired 1.3x), its camera coefficient dX - g . (dd, dy) with the same g; the
gradient the readings-only fit's. With camera terms each camera fit's turn
is an unknown of the face's own test with its unit prior (in its chi2; the extra corners' dof unchanged), so a turn
shared by its readings never reads as the face's misfit (tests: a flat face read under a 3 sd turn of both fits, or
2.5 sd of fit 02 alone from different photo sets, passes; the latter with the turns left out reads as a warp, chi2
10.3). The other side, by the data: from different photo sets one fit's turn moves the corners its photos dominate,
which looks like a lift there (a 1.5 mm lift at such a far corner is explained by a 1.55 sd turn, chi2 6.25 against
26.8); from one photo set, as probe1's crown corners, the turn is a plane and absorbs nothing. A warp facet reports its
warp: the largest corner distance in X from its least-squares plane, and its largest turn: the angle between its
surface normal and its area-mean normal, where (_warp_twist; v3p3-verify-9: the A/A2 split's warped logo turned 62.6
deg at the hinge corner and said only "0.33 mm off one plane").

Warnings (facts only): W_FACE_NOT_FLAT; W_FIT_SOLVED, a null (solve) node inside the outline, which no reading sets:
the faces that set it and how far it lies from their nearest observed corner, X +- sd, or that the thickness bound
holds it (a corner extrapolated from a neighbour's plane was a key defect in 5 of 7 runs, FINDINGS §3; review P1-1);
W_FIT_PRIOR, a node set by a prior-only direction with posterior sd > PRIOR_FLAG_MM 2.0 ("set by no reading or face");
W_FIT_OFF_READING, a typed, solved or base value more than OFF_READING_SD off its node's stored reading (`readings`),
and a node more than max(OFF_POSITION_MM, OFF_POSITION_SD x the reading's sd_d / sd_y) from where its reading was taken
(the tip 8-20 mm back in every v2 run, FINDINGS §0.2; review P1-3), or whose X a face through it changes by more than
OFF_CARRY_SD of its sd between the read point and the node in a pinned direction (the carry and the faces' spread
said; review 2026-10-12 P2); and (kind "fitted") a read corner the fit placed beyond OFF_POSITION_SD sd of its reading
along the directions it may move (that many sd's probability for their count), with the outline knots it carried
(v3p3 P1: the flat crown slid the tip E 3.04 mm, 2.82 sd, and the top outline knot with it, and no fact said so);
W_WARP_TWIST, a warp turning more than WARP_TWIST_WARN_DEG from its area-mean normal (information).

chi2 / chi2_free are the minimised objective over the readings, typed values and camera turns (a prior adds only where
a thickness bound holds a node no reading sees) and the corners' position rows; n_obs counts readings and typed
values. A read node: d, y where the solution puts it (design_at its design's parsed (d, y), move_mm the difference,
free_dims its position unknowns), X, t at it; its X residual = fitted + its camera turns' share - the reading's X
carried to the fitted corner (residual_mm; residual_sd in its X row's sd obs_sd, the conditional one; obs_X; gap_mm =
fitted - read X; camera_mm the share; read_sd the reading's own X sd), pos_sd its position rows' whitened norm (pos_mm
the same rows in mm: fitted - read along its free directions, the turns in), chi2 its rows' squares (= the Mahalanobis
distance of its whole (d, y, X) residual for a free corner), knots the outline chains whose knot moves with it. A
misfit (the report's misfit line, a face's largest misfit, the flat fit's list) is a reading or typed value whose X row
or whole residual lies beyond MISFIT_SD sd's probability for its rows (_is_misfit). FitResult.camera
{fit: {u, sd, free_u, readings}}: each turn in sd, the declared fit's and the readings-only fit's; FitResult.model
{weight_passes, weights_settled, weight_change, position_passes, positions_settled, position_step, snap_reverted,
angle_passes, position_unknowns}; report_lines prints the camera turns the data moved (CAMERA_SHOWN_SD, in words:
"camera turn of fit 05: 0.4 sd"; a dof-0 fit's turns are its prior's 0 +- 1 and print nothing), the corners the fit
placed off the design, those it kept at the design for the builder's snapping, and anything that did not settle by
more than a printed digit (WEIGHT_SHOWN_SD, POS_SHOWN_MM; `model` keeps the solver's own flags). Its numbers are
named (chi2, dof, cost) or carry mm, deg or sd (but W_FIT_OFF_READING's typed / solved / base t, whose form a lab test
pins), to the digits they hold, and never "-0.00" (_signed / _fixed; v3-integrate-2).
"""
from __future__ import annotations

import copy
import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

from . import spec as S
from .arrange import clip_segment, poly_area, simple_polygon
from .build import chamfer_depth, points_in_poly, roll_depth
from .planes import ccw_poly, crease_frame, mvc
from .spec import PL, FLAT_CREASE_DEG, TILE_TOL_MM2, TempleError, chamfer_breaks, crease_run, parse, plain

TYPED_SD_MM = 0.5               # a typed t is an observation of this sd
PRIOR_SD_MM = 5.0               # a null (solve) node: weak prior toward the base surface
PRIOR_FLAG_MM = 2.0             # posterior sd above this, set by the prior: W_FIT_PRIOR ("set by no reading or face")
MIN_CREASE_DEG = 2.5            # a declared crease's fold at its midpoint is at least this (and each piece's mean
#                                 dihedral FLAT_CREASE_DEG 2.0, the builder's flat line, + MEAN_MARGIN_DEG)
ACCEPT_DCHI2 = 4.0              # declarations may cost this much chi2 over the readings-only fit (about 2 sigma)
FLAT_CHI2_PER_CORNER = 4.0      # W_FACE_NOT_FLAT: a flat face's own plane fit chi2 above this per extra corner
IN_LINE_RATIO = 10.0            # W_FACE_NOT_FLAT: a corner the others' plane predicts this many times worse than its
#                                 own sd: its others lie nearly in line (run 7's H: 38.8 mm against 0.24)
CERTAIN_SD = 2.0                # a crease is certain beyond this many sd (and FLAT_CREASE_DEG) in the readings-only fit
BOUND_SHARE = 0.95              # a held bound that shrinks a crease's sd below this share: the crease names it
ANGLE_PASSES = 60               # linearised minimum-angle passes at most (they settle by ~0.1 per pass: 7-15 passes)
SETTLE_TOL = 1e-12              # ... settled: no linearisation point (slope, mean coefficient) changes more than this
SETTLE_HOLD_TOL = 1e-9          # ... or every declared piece holds exactly and none changes more than this
WEIGHT_PASSES = 30              # passes of the readings-only fit at most (the pinned gradients and the corner
WEIGHT_TOL = 1e-9               # ... positions); the gradients settled: no X row moves more than this x its sd
WEIGHT_DAMP_MIN = 0.125         # ... a gradient pass that does not contract (> 0.9 x the last) halves its step, down to
#                                 this (review 2026-10-12: a period-2 cycle, a corner's sd 0.135 <-> 0.496 every pass)
SIG_FLOOR = 0.01                # an effective sd never below this share of the reading's own X sd
POS_STEP_SHARE = 1.0            # the position Jacobian: a secant over +- this share of the reading's sd along the
POS_STEP_MIN_MM = 0.01          # ... direction, at least this, at most POS_STEP_MAX_MM and POS_STEP_ROOM x the
POS_STEP_MAX_MM = 1.5           # ... corner's distance to the nearest other node or outline knot (a base station
POS_STEP_ROOM = 0.4             # ... 0.46 mm from probe1's D-E midpoint steps its fold 4 deg: a tangent there jumps
#                                 or misses it; over the reading's own spread the shoulder|base sd matched a Monte
#                                 Carlo of the exact process at 1.06 (fold) / 1.12 (mean), at half of it 1.52 / 1.18)
POS_DAMP_MIN = 1.0 / 32.0       # the corner passes halve their step where it reverses the last, down to this
POS_RECOVER = 1.25              # ... and raise it by this after a step that does not (to 1)
POS_PUSH_MM = 1e-3              # builder_point(push): a corner near a station goes this far beyond snap_mm from it
POS_MAX_SD = 4.0                # a corner stands at most this many sd from its reading along each free direction (a
#                                 move that would cross another node, knot or edge reverts: builder_point)
POS_SD_MIN_MM = 1e-3            # a direction whose reading sd is below this is pinned (no position unknown)
POS_TOL_MM = 1e-6               # the corner positions settled: none moves more than this between passes (the held
#                                 solve is exact wherever they stop: this only says when the passes may stop)
POS_PASSES = 30                 # Gauss-Newton passes on the corner positions at most (per fit)
JAC_MOVE_SHARE = 1.0            # the position Jacobian is re-evaluated where a corner moved more than this x its step
MOVE_SHOWN_MM = 0.05            # the report's moved line lists the corners the fit moved at least this far
CORNER_NEAR_MM = 0.05           # a warp's gradient at its corner: its two edges' MVC gradients this far along them
#                                 (the plane through the two edges is ill-conditioned at a nearly straight corner: the
#                                 probe1 warp crown's K, its lower edge bending ~4 deg, gave a cross slope of 17)
EDGE_AT = (1 / 16, 1 / 8, 1 / 4, 3 / 8, 1 / 2, 5 / 8, 3 / 4, 7 / 8, 15 / 16)   # beside a warp or the base, on a piece
EDGE_W = (3 / 32, 3 / 32, 1 / 8, 1 / 8, 1 / 8, 1 / 8, 1 / 8, 3 / 32, 3 / 32)     # ... their share of it (midpoint rule)
MEAN_MARGIN_DEG = 0.25          # a declared piece's mean dihedral is at least FLAT_CREASE_DEG + this
OFF_READING_SD = 2.0            # W_FIT_OFF_READING: a typed / solved / base value this many sd off its stored reading
OFF_POSITION_MM = 0.5           # W_FIT_OFF_READING: a node this far from where its reading was taken (and ...
OFF_POSITION_SD = 2.0           # ... this many times the reading's sd_d / sd_y), or whose X changes along a face
OFF_CARRY_SD = 0.5              # ... through it, between the read point and the node (pinned directions), by more than
#                                 this x its X row's sd (review 2026-10-12 P2: K 0.5 mm off its reading in y, the
#                                 crown's X there 0.29 mm = 1.5 sd off, no warning)
MISFIT_SD = 1.0                 # the report's misfit line: readings and typed values the fit leaves beyond this
HARD_SD_MM = 1e-4               # a base corner in the flatness test (a hard value)
THICK_MARGIN_MM = 1e-6          # the thickness bound sits this far above t_min (rounding never refuses E_TOO_THIN)
THIN_GRID_MM = 0.5              # warp interior thickness check: grid spacing (lines sampled at half of it)
THIN_CHECK_MM = 0.05            # ... samples this close to the bound are polished to the local minimum
THIN_TOL_MM = 1e-7              # ... a point this far below the bound becomes a bound row (a tenth of the margin, so a
#                                 settled warp is >= t_min + 9e-7 everywhere: the builder never refuses E_TOO_THIN)
THIN_SAMPLE_CUTS = 24           # ... with its polished minima, up to this many of its lowest violating grid samples
#                                 (a quarter per line) become rows too: the minimum slides along a valley otherwise
THIN_PASSES = 12                # ... cutting-plane re-solves per solve
THIN_SKIP_MM = 2.0              # ... a warp whose grid samples all lie this far above the bound is not checked
#                                 further (every point of it lies within ~0.7 mm of a sample: dipping 2 mm needs a
#                                 thickness slope > 2.8, 70 deg; run 7's crown is 5-7 mm above it)
ON_MM = 1e-6                    # a point within this of an edge or a point lies on it (planes.NODE_ON_VERTEX_MM)
EQ_TOL_MM = 1e-9                # hard values that cannot all hold within this: E_FIT
ACTIVE_TOL = 1e-9               # an inequality within this (normalised) of its bound is active
COST_SHOWN = 0.05               # E_FIT_SHAPE names the declared creases whose own cost exceeds this
REPORT_CHARS = 140              # report_lines width
CAMERA_SHOWN_SD = 0.05          # the report prints a camera fit's turn when the data moved it this far (in sd of its
#                                 theta error; printed to 0.1 sd, so below this it would read "0.0": session2's dof-0
#                                 fits turned 1.9e-13 / -1.6e-13 and printed the prior as "+0.00 +- 1.00", "-0.00")
WEIGHT_SHOWN_SD = 1e-3          # ... says the readings-only fit did not settle only where its last pass still moved a
POS_SHOWN_MM = 1e-3             # ... reading's carried X this share of its sd, or a corner this far: at the damping's
#                                 0.9 contraction what remains is under 10x the last change, < 0.01 sd and < 0.01 mm,
#                                 below every printed digit (session2's flat crown: "did not settle in 30 passes" at
#                                 4.7e-08 sd, WEIGHT_TOL being 1e-9, which cost a receipt line; v3-integrate-2)
WARP_TWIST_WARN_DEG = 15.0      # W_WARP_TWIST: a warp whose surface normal turns more than this from its area-mean
#                                 normal (information, never a gate; v3p3-verify-9 / -fix-core: session2's warp crown
#                                 r6, probe1's data, turns 9.0 near its corner K, run 7's warp crown 6.7; the A/A2
#                                 split's warped logo 52 at 0.05 mm from the hinge corner A, 0.67 mm from A2 (62.6 at
#                                 0.03 mm), where it built a bright streak 3 mm long; from the least-squares plane
#                                 instead, r6's crown reads 23)
TWIST_GRID_MM = 0.25            # ... its interior sampled on this grid (central differences, TWIST_STEP_MM), with
TWIST_STEP_MM = 1e-4            # ... each corner inside or on the outline taken at its corner plane (the MVC surface
#                                 tends to the plane of its two edges there: the largest turn of a warp is at a corner
#                                 in every case above) and its edges at EDGE_AT (mvc_edge_gradient)
WAYS = ("ways (unranked): warp (one face, no line), a fold (two faces), another reading of a corner, a corner that is "
        "two vertices (one per face)")
STATE_TEXT = {"certain": "sign-certain", "open": "unresolved", "flat": "flat within error (2 sd)"}
TABLE_KNOTS = ("inner[", "base.top[", "base.bottom[")                             # parse's snap names of table knots

FIT_WARNINGS = ("W_FACE_NOT_FLAT", "W_FIT_SOLVED", "W_FIT_PRIOR", "W_FIT_OFF_READING",   # in spec.WARNINGS
                "W_WARP_TWIST")

READ_KEYS = ("id", "X", "sd", "d", "y", "photos", "sd_d", "sd_y", "cov", "camera")
FACET_KEYS = ("poly", "plane", "fade", "soft")
_STENCIL = np.array([[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]], np.float64)


# --------------------------------------------------------------------------------------------- solver
class Infeasible(Exception):
    """solve_lsq: the hard values cannot all hold ("equalities", rows), the bounds cannot hold ("bounds"), the
    observations leave a direction free ("rank"), or the bound solve hit its step cap ("solver", an internal guard).
    The fit adds "thin" (a warp's interior cutting planes did not settle) and "angles" (a declared piece short of its
    minimum after the passes)."""

    def __init__(self, what: str, rows=(), residual=None):
        super().__init__(what)
        self.what, self.rows, self.residual = what, list(rows), residual


@dataclass
class Lsq:
    z: np.ndarray                   # the solution
    chi2: float                     # the minimised objective (weak rows only where they act, below)
    active: np.ndarray              # bool per inequality row: at its bound
    cov: np.ndarray                 # (N, N) covariance at the active set (A's rows are scaled by 1 / sd)
    free_dim: int                   # free directions left by the equalities
    dof: int = 0                    # strong rows - the directions they set that no active bound holds
    weak_basis: np.ndarray | None = None    # (N, q): the directions in z only the weak rows set (None: none)
    cov_free: np.ndarray | None = None      # (N, N) covariance with no bound held
    n_bounds: int = 0               # the fit: how many of the inequality rows are thickness bounds (the rest: shapes)
    shape_rows: list = field(default_factory=list)  # the fit: [(terms, row, bound)] of the last pass (_shape_rows)
    passes: int = 0                 # the fit: the declared shapes' linearised passes (_declared_fit)
    jac: tuple | None = None        # the fit: (position Jacobian, where) its covariance was solved with (_final)


def _nnls(M: np.ndarray, t: np.ndarray) -> np.ndarray:
    """min ||M x - t|| subject to x >= 0 (Lawson-Hanson active set). A column that enters and leaves at once (a
    numerical tie) is barred until the next step that changes x."""
    m = M.shape[1]
    x = np.zeros(m)
    P = np.zeros(m, bool)
    barred = np.zeros(m, bool)
    tol = 1e-12 * max(1.0, float(np.abs(M).max(initial=0.0))) * max(M.shape)
    steps = 0
    while True:
        w = M.T @ (t - M @ x)
        cand = ~P & ~barred & (w > tol)
        if not cand.any():
            return x
        j = int(np.argmax(np.where(cand, w, -np.inf)))
        P[j] = True
        while True:
            steps += 1
            if steps > 10 * m + 50:
                raise RuntimeError("nnls: no convergence")
            z = np.zeros(m)
            z[P] = np.linalg.lstsq(M[:, P], t, rcond=None)[0]
            if np.all(z[P] > 0.0):
                x = z
                break
            neg = P & (z <= 0.0)
            den = x[neg] - z[neg]
            alpha = float(np.min(np.where(den > 0.0, x[neg] / np.where(den > 0.0, den, 1.0), 0.0)))
            x = x + alpha * (z - x)
            P &= x > 1e-300
            x[~P] = 0.0
        if P[j]:
            barred[:] = False
        else:
            barred[j] = True


def _ldp(E: np.ndarray, f: np.ndarray) -> np.ndarray:
    """min ||u|| subject to E u >= f, by NNLS on [E^T; f^T] v ~ [0; 1] (Lawson & Hanson 1974, ch. 23)."""
    k = E.shape[1]
    M = np.vstack([E.T, f[None, :]])
    t = np.zeros(k + 1)
    t[-1] = 1.0
    v = _nnls(M, t)
    r = M @ v - t
    if float(np.linalg.norm(r)) <= 1e-10:
        raise Infeasible("bounds")
    return -r[:k] / r[k]


def _rank(M: np.ndarray, rel: float = 1e-10) -> int:
    if not M.size:
        return 0
    s = np.linalg.svd(M, compute_uv=False)
    return int(np.sum(s > rel * max(1.0, float(s[0]))))


def solve_lsq(A, b, C=None, e=None, G=None, h=None, weak=None, z_only: bool = False) -> Lsq:
    """min ||A z - b||^2 subject to C z = e (exact: z = z0 + N w over the SVD null space of C) and G z >= h (exact
    active set: the reduced problem's QR turns it into least distance programming). Raises Infeasible. z_only: no
    covariance or dof (cov, cov_free None, dof 0).

    `weak` (bool per row of A): rows that only make the problem well posed (the fit's priors). They act only in the
    directions no strong row sees: projected onto the image of that null space (U^T A_weak, U an orthonormal basis of
    A_weak N_strong), so for any strong-row solution the weak term can still reach zero, and it never biases what the
    strong rows determine (a prior 5 mm wide on a virtual corner 50 mm off the outline otherwise tilts its plane). Their
    leftover misfit is not in chi2."""
    A = np.asarray(A, np.float64)
    b = np.asarray(b, np.float64)
    N = A.shape[1]
    weak = np.zeros(len(A), bool) if weak is None else np.asarray(weak, bool)
    if C is not None and len(C):
        C = np.asarray(C, np.float64)
        e = np.asarray(e, np.float64)
        U, s, Vt = np.linalg.svd(C)
        r = int(np.sum(s > 1e-11 * max(1.0, float(s[0]))))
        z0 = Vt[:r].T @ ((U[:, :r].T @ e) / s[:r])
        Nm = Vt[r:].T
        res = C @ z0 - e
        bad = np.nonzero(np.abs(res) > EQ_TOL_MM)[0]
        if len(bad):
            raise Infeasible("equalities", bad, res)
    else:
        z0, Nm = np.zeros(N), np.eye(N)
    k = Nm.shape[1]
    m = 0 if G is None else len(G)
    G = np.zeros((0, N)) if G is None or not len(G) else np.asarray(G, np.float64)
    h = np.zeros(0) if not m else np.asarray(h, np.float64)
    active = np.zeros(m, bool)
    n_strong = int(np.sum(~weak))
    if k == 0:
        bad = np.nonzero(G @ z0 - h < -EQ_TOL_MM)[0]
        if len(bad):
            raise Infeasible("bounds", bad)
        return Lsq(z0, float(np.sum((A[~weak] @ z0 - b[~weak]) ** 2)), np.abs(G @ z0 - h) <= EQ_TOL_MM,
                   np.zeros((N, N)), 0, n_strong, cov_free=np.zeros((N, N)))
    As = A[~weak] @ Nm
    Aw, bw = As, b[~weak] - A[~weak] @ z0
    ro = _rank(Aw)
    Wq = None
    if weak.any() and ro < k:
        Nn = np.linalg.svd(Aw, full_matrices=True)[2][ro:].T if len(Aw) else np.eye(k)
        Wq = Nm @ Nn
        Aq, bq = A[weak] @ Nm, b[weak] - A[weak] @ z0
        Uq = np.linalg.svd(Aq @ Nn, full_matrices=False)[0]
        Aw, bw = np.vstack([Aw, Uq.T @ Aq]), np.r_[bw, Uq.T @ bq]
    if Aw.shape[0] < k:
        raise Infeasible("rank")
    Q, R = np.linalg.qr(Aw)
    dR = np.abs(np.diag(R))
    if dR.min() <= 1e-10 * max(1.0, float(dR.max())):
        raise Infeasible("rank", [int(np.argmin(dR))])
    w = np.linalg.solve(R, Q.T @ bw)
    Gw = G @ Nm
    if m:
        hw = h - G @ z0
        E = np.linalg.solve(R.T, Gw.T).T                     # Gw R^-1: the bounds on u = R (w' - w)
        f = hw - Gw @ w
        nE = np.linalg.norm(E, axis=1)
        dead = nE <= 1e-12 * max(1.0, float(nE.max()))
        if np.any(f[dead] > EQ_TOL_MM):
            raise Infeasible("bounds", np.nonzero(dead & (f > EQ_TOL_MM))[0])
        live = np.nonzero(~dead)[0]
        En, fn = E[live] / nE[live, None], f[live] / nE[live]
        try:
            u = _ldp(En, fn) if np.any(fn > 0.0) else np.zeros(k)
        except RuntimeError:                             # an internal guard (_nnls's step cap), never seen
            raise Infeasible("solver") from None
        active[live[En @ u - fn <= ACTIVE_TOL]] = True
        active[dead] = np.abs(f[dead]) <= EQ_TOL_MM
        w = w + np.linalg.solve(R, u)
    z = z0 + Nm @ w
    if z_only:
        return Lsq(z, float(np.sum((Aw @ w - bw) ** 2)), active, None, k, 0, Wq)
    Ri = np.linalg.inv(R)
    Hi = Ri @ Ri.T                                       # (R^T R)^-1: no bound held
    H = R.T @ R
    Na = np.eye(k)
    if active.any():
        _, sa, Vta = np.linalg.svd(Gw[active])
        ra = int(np.sum(sa > 1e-10 * max(1.0, float(sa[0]))))
        Na = Vta[ra:].T
    covw = Na @ np.linalg.inv(Na.T @ H @ Na) @ Na.T if Na.shape[1] else np.zeros((k, k))
    seen = _rank(As @ Na) if Na.shape[1] else 0
    return Lsq(z, float(np.sum((Aw @ w - bw) ** 2)), active, Nm @ covw @ Nm.T, k, n_strong - seen, Wq,
               cov_free=Nm @ Hi @ Nm.T)


# --------------------------------------------------------------------------------------------- mean value coordinates
def _half_tan(a, b) -> float:
    """tan(alpha / 2) of the signed angle alpha from vector a to vector b (CCW +): sin / (1 + cos) near 0, (1 - cos) /
    sin near +-pi (no cancellation in either)."""
    cr = float(a[0] * b[1] - a[1] * b[0])
    dot = float(a[0] * b[0] + a[1] * b[1])
    rr = math.hypot(a[0], a[1]) * math.hypot(b[0], b[1])
    return cr / (rr + dot) if dot >= 0.0 else (rr - dot) / cr


def mvc_weights(P, p) -> np.ndarray:
    """Mean value coordinates (Floater 2003; signed angles, Hormann & Floater 2006) of the point p strictly inside the
    simple CCW polygon P (n, 2): w_j = (tan(a_{j-1} / 2) + tan(a_j / 2)) / r_j, normalised (sum 1, sum l_j P_j = p)."""
    P = np.asarray(P, np.float64)
    D = P - np.asarray(p, np.float64)
    n = len(P)
    t = np.array([_half_tan(D[j], D[(j + 1) % n]) for j in range(n)])
    w = (np.roll(t, 1) + t) / np.hypot(D[:, 0], D[:, 1])
    return w / w.sum()


def _mvc_rows(P, pts) -> np.ndarray:
    """(m, n) weights of the builder's own MVC (planes.mvc, its corner and edge cases included) at the points:
    X(p) = W @ corner values, evaluated once per corner with a unit corner vector."""
    P = np.asarray(P, np.float64)
    pts = np.asarray(pts, np.float64).reshape(-1, 2)
    eye = np.eye(len(P))
    return np.stack([mvc(P, eye[j], pts[:, 0], pts[:, 1]) for j in range(len(P))], axis=1)


def mvc_edge_gradient(P, i: int, m) -> np.ndarray:
    """(2, n) matrix G with grad X(m) = G @ X_corners: the gradient of the MVC surface over the simple CCW polygon P
    at the point m inside its edge i (P_i -> P_i+1), from inside. Along the edge X is linear. Across it, at a distance
    h inside, W = sum w_j = 2 / h + O(1) (tan(a_i / 2) = 2 s (1 - s) L / h), so d l_j / d n_in = w_j(m) / 2 for every
    corner off the edge; the two edge corners take the rest so that sum l_j = 1 and sum l_j P_j = p hold to first
    order (the identity sum_off (w_j / 2) (P_j - P_i) . n_in = 1 is tested)."""
    P = np.asarray(P, np.float64)
    m = np.asarray(m, np.float64)
    n = len(P)
    i1 = (i + 1) % n
    a = P[i]
    L = float(np.hypot(*(P[i1] - a)))
    u = (P[i1] - a) / L
    nin = np.array([-u[1], u[0]])
    D = P - m
    r = np.hypot(D[:, 0], D[:, 1])
    t = np.array([0.0 if j == i else _half_tan(D[j], D[(j + 1) % n]) for j in range(n)])
    mu = np.zeros(n)
    for j in range(n):
        if j != i and j != i1:
            mu[j] = 0.5 * (t[j - 1] + t[j]) / r[j]
    s0, s1 = float(mu.sum()), float(mu @ ((P - a) @ u))
    mu[i1] = -s1 / L
    mu[i] = -s0 - mu[i1]
    G = np.outer(nin, mu)
    G[:, i] -= u / L
    G[:, i1] += u / L
    return G


# --------------------------------------------------------------------------------------------- the result
@dataclass
class FitResult:
    spec: dict                      # the fitted pure g3d.temple/2 spec
    nodes: dict                     # name -> {X, t, sd_X, source, obs_X, obs_sd, residual_mm, residual_sd, prior, ...}
    faces: dict                     # label -> {kind, corners, warp_mm | flat_misfit_mm, chi2, not_flat, ...}
    creases: list                   # [{pair, name, deg, sd_deg, shape, certain, declared, dchi2, free_deg, ...}]
    chi2: float
    chi2_free: float
    n_obs: int
    dof: int
    warnings: list = field(default_factory=list)
    snaps: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    bounds: list = field(default_factory=list)   # the thickness bound's active points: {what: "node", node} or
    #                                               {what: "edge" | "outline" | "inside", facet, at: [d, y]}
    camera: dict = field(default_factory=dict)   # fit id -> {u, sd, free_u, readings}: its theta turn in sd of its
    #                                               prior (the declared fit's, its sd, the readings-only fit's)
    model: dict = field(default_factory=dict)    # {weight_passes, weights_settled, weight_change, angle_passes}

    def to_dict(self) -> dict:
        return plain({"spec": self.spec, "nodes": self.nodes, "faces": self.faces, "creases": self.creases,
                      "chi2": self.chi2, "chi2_free": self.chi2_free, "n_obs": self.n_obs, "dof": self.dof,
                      "warnings": self.warnings, "snaps": self.snaps, "counts": self.counts, "bounds": self.bounds,
                      "camera": self.camera, "model": self.model})

    def report_lines(self, limit: int = 12, creases: bool = True) -> list[str]:
        """Compact lines (<= REPORT_CHARS each): the fit line, the camera turns the data moved, where the fit put read
        corners, anything that did not settle by a printed amount and the misfit lines, then face lines, then crease
        lines, then warnings (a long one wraps onto indented lines). Over `limit` lines, crease lines go first
        (undeclared before declared), then face lines, and a last line says what was left out, by kind; the fit lines
        and warnings stay. Every number is named (chi2, dof, a declaration's chi2 cost) or carries mm, deg or sd (but
        W_FIT_OFF_READING's typed / solved / base t), with no more digits than it holds (a declared crease's
        readings-only angle now says "deg", W_FIT_SOLVED its X and t "mm"); a signed zero prints "+" (_signed, never
        "-0.00").
        - Camera (v3-integrate-2, session2): a turn is printed only where the data moved it, |u| >= CAMERA_SHOWN_SD,
          unsigned, in words, "camera turn of fit 05: 0.4 sd" (as E_FIT_SHAPE's "turns the camera of fit 02 by 5.6
          sd"): on a dof-0 fit the turns stay at the prior's 0 +- 1 (session2 printed "02 +0.00 +- 1.00" on every
          such fit, and "-0.00"), and the misfit line says "after the camera turn(s)" only when one is printed.
        - Settling (v3-integrate-2): a readings-only pass that did not settle at the solver's tolerance (WEIGHT_TOL
          1e-9 sd, POS_TOL_MM 1e-6) is said only beyond WEIGHT_SHOWN_SD / POS_SHOWN_MM: session2's flat crown read
          "did not settle in 30 passes" at 4.7e-08 sd and lost a receipt line for it (FitResult.model keeps the
          solver's own flags).
        - Misfits (v3p3 P1, session2 r0004): the misfit line, a flat face's largest misfit and W_FACE_NOT_FLAT's flat
          fit give each node's whole residual, ranked by it: a corner the fit may move "E (d, y) 3.04 mm (2.8 sd) and
          X +0.41 mm (1.1 sd)" (the X row alone read "E +0.41 mm (1.1 sd)", and the face line "the fit puts E +0.41
          mm (1.1 sd) off its reading", while E sat 3.04 mm from where it was read: 9.26 of the fit's chi2 10.48); a
          stored record without pos_mm prints its position part in sd only. The moved line names the outline knot a
          corner carried."""
        c = self.counts
        fit = [f"fit: chi2 {self.chi2:.1f}, dof {self.dof}, from {c.get('read', 0)} readings and {c.get('typed', 0)} "
               f"typed values; readings-only chi2 {self.chi2_free:.1f}"]
        visible = self.model.get("visible_angles")
        if visible:
            fit.append("visible-angle choices override only unresolved local-face readings; their full residual "
                       f"cost is shown (before choices chi2 {visible['baseline_chi2']:.1f}); other readings unchanged")
        turned = [(f, abs(float(v.get("u") or 0.0))) for f, v in sorted(self.camera.items())
                  if abs(float(v.get("u") or 0.0)) >= CAMERA_SHOWN_SD]
        if turned:
            fit.append("camera turn " + ", ".join(f"of fit {f}: {u:.1f} sd" for f, u in turned)
                       + " (in sd of that camera fit's theta error; one turn for all of its readings)")
        moved = sorted(((r.get("pos_sd") or 0.0, n, r) for n, r in self.nodes.items()
                        if r.get("move_mm") and math.hypot(*r["move_mm"]) >= MOVE_SHOWN_MM),
                       key=lambda x: (-x[0], x[1]))
        if moved:           # an outline knot the corner carried is named (v3p3 P1: the top outline rose 0.42 mm at d 70
            #                 with E, and no text said so)
            fit.append("corners the fit placed off the design (fitted - design, d y mm; fitted - read in its sd): "
                       + ", ".join(f"{n} {_mm(r['move_mm'][0])} {_mm(r['move_mm'][1])} ({a:.1f} sd"
                                   + (f", its {' and '.join(r['knots'])} outline knot with it" if r.get("knots")
                                      else "") + ")" for a, n, r in moved))
        capped = sorted(n for n, r in self.nodes.items() if r.get("at_cap"))
        if capped:
            fit.append(f"corners held at their move limit ({POS_MAX_SD:g} sd of the reading along a direction): "
                       + ", ".join(capped))
        m = self.model
        if m.get("snap_reverted"):
            fit.append("kept at the design position (the fitted one lay where the builder would snap it): "
                       + ", ".join(m["snap_reverted"]))
        w_change, p_step = float(m.get("weight_change") or 0.0), float(m.get("position_step") or 0.0)
        if m.get("weights_settled") is False and w_change >= WEIGHT_SHOWN_SD:
            fit.append(f"the readings-only fit did not settle in {m.get('weight_passes')} passes: its last pass still "
                       f"moved a reading's X, carried to its corner, by {w_change:.2g} of its sd")
        if m.get("positions_settled") is False and p_step >= POS_SHOWN_MM:
            fit.append(f"the readings-only fit did not settle in {m.get('position_passes')} passes: its last pass "
                       f"still moved a corner {p_step:.2g} mm")
        off = sorted((n for n, r in self.nodes.items() if _is_misfit(r)), key=lambda n: _misfit_key(n, self.nodes[n]))
        if off:
            after = f", after the camera turn{'s' if len(turned) > 1 else ''}" if turned else ""
            fit.append(f"misfit beyond 1 sd (fitted - read{after}): "
                       + ", ".join(_misfit_text(n, self.nodes[n]) for n in off))
        held = [_held_text(n, r) for n, r in sorted(self.nodes.items()) if r.get("at_bound")]
        other = sum(1 for b in self.bounds if b["what"] != "node")
        if held or other:
            fit.append("at the thickness bound: " + ", ".join(held + ([f"{other} facet point{'s' if other > 1 else ''}"]
                                                                       if other else [])))
        faces, plain3, in_line = [], [], []
        for label, f in self.faces.items():
            corners = " ".join(f["corners"])
            if f["kind"] == "warp":
                line = (f"face {label} warp ({corners}): {f['warp_mm']:.2f} mm off one plane; corner residuals "
                        f"chi2 {f['corner_chi2']:.1f}")
                if f.get("twist_deg") is not None:
                    line += f"; {_twist_text(f)}"
                faces.append(line)
            elif f.get("extra", 0) > 0:
                line = (f"face {label} flat ({corners}): chi2 {f['flat_chi2']:.1f} for {f['extra']} extra corner"
                        f"{'s' if f['extra'] > 1 else ''}{', not one plane' if f['not_flat'] else ''}")
                # its largest misfit by the whole residual (v3p3 P1: "the fit puts E +0.41 mm (1.1 sd) off its
                # reading" while E sat 3.04 mm, 2.8 sd, from where it was read)
                obs = [n for n in f["corners"] if n in self.nodes and _is_misfit(self.nodes[n])]
                if obs:
                    n = min(obs, key=lambda m: _misfit_key(m, self.nodes[m]))
                    line += f"; its largest misfit (fitted - read): {_misfit_text(n, self.nodes[n])}"
                faces.append(line)
            else:                                    # no flatness test: under 4 known corners, or all in one line
                (in_line if len(f.get("known") or ()) >= 4 else plain3).append(label)
        if plain3:          # (was "with no extra corner": "extra" is defined only by a tested face's line)
            faces.append(f"flat faces with fewer than 4 read, typed or base corners (no flatness test): "
                         f"{', '.join(plain3)}")
        if in_line:
            faces.append(f"flat faces whose read, typed and base corners lie in one line (no flatness test): "
                         f"{', '.join(in_line)}")
        decl, other_c = [], []
        for cr in self.creases if creases else []:
            if cr["deg"] is None:
                line = f"{cr['name']}: no shared edge inside the outline" + (f", declared {cr['declared']}"
                                                                               if cr["declared"] else "")
            else:
                line = _crease_line(cr)
            (decl if cr["declared"] else other_c).append(_wrap(line))
        warns = [_wrap(f"{w['code']}: {w['message']}") for w in self.warnings]
        items = ([("fit", _wrap(x)) for x in fit] + [("face", _wrap(x)) for x in faces]
                 + [("crease", x) for x in decl] + [("crease_u", x) for x in other_c] + [("warn", x) for x in warns])
        total = sum(len(x) for _, x in items)
        if total <= limit:
            return [ln for _, x in items for ln in x]
        keep = [True] * len(items)
        for kind in ("crease_u", "crease", "face"):
            for k in range(len(items) - 1, -1, -1):
                if total + 1 <= limit:
                    break
                if keep[k] and items[k][0] == kind:
                    keep[k] = False
                    total -= len(items[k][1])
        # the last line says what was left out, by kind (was "(+2 more lines in the fit report)": no tool shows more)
        tagged = [(kind, ln) for k, (kind, x) in enumerate(items) if keep[k] for ln in x]
        out, cut = tagged[:max(limit - 1, 0)], tagged[max(limit - 1, 0):]
        word = {"fit": "fit", "face": "face", "crease": "crease", "crease_u": "crease", "warn": "warning"}
        left: dict = {}
        for kind, n in [(kind, len(x)) for k, (kind, x) in enumerate(items) if not keep[k]] + [(k, 1) for k, _ in cut]:
            left[word[kind]] = left.get(word[kind], 0) + n
        return [ln for _, ln in out] + ["(left out for length: " + ", ".join(
            f"{left[k]} {k} line{'s' if left[k] > 1 else ''}" for k in ("fit", "face", "crease", "warning")
            if k in left) + ")"]


def _signed(x: float, nd: int = 2) -> str:
    """x signed to nd decimals; a value that rounds to zero prints "+0.00", never "-0.00" (session2's camera line
    printed "02 -0.00")."""
    s = f"{x:+.{nd}f}"
    return "+" + s[1:] if float(s) == 0.0 else s


def _fixed(x: float, nd: int = 2) -> str:
    """x to nd decimals, never "-0.00" (a coordinate or thickness that rounds to zero)."""
    s = f"{x:.{nd}f}"
    return s.lstrip("-") if float(s) == 0.0 else s


def _mm(x: float) -> str:
    """A signed mm to 2 decimals, never "-0.00"."""
    return _signed(x, 2)


def chi2_tail(x: float, k: int) -> float:
    """P(chi2 with k dof > x): the regularised upper incomplete gamma Q(k / 2, x / 2) (Numerical Recipes 6.2: the
    series below a + 1, the continued fraction above). k 1: the two-sided normal tail at sqrt(x) sd."""
    a, t = 0.5 * k, 0.5 * max(float(x), 0.0)
    if t <= 0.0:
        return 1.0
    pre = math.exp(-t + a * math.log(t) - math.lgamma(a))
    if t < a + 1.0:
        term = total = 1.0 / a
        ap = a
        for _ in range(500):
            ap += 1.0
            term *= t / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * pre)
    b, c, d = t + 1.0 - a, 1.0 / 1e-300, 1.0 / (t + 1.0 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = b + an / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return min(1.0, h * pre)


def _rows(r: dict) -> int:
    """A read or typed node's residual rows: its X row and, for a read corner the fit may move, one position row per
    free direction (free_dims; a stored record without it: two where it has position rows)."""
    if r.get("source") != "read":
        return 1
    k = r.get("free_dims")
    return 1 + (int(k) if k is not None else 2 if r.get("pos_sd") else 0)


def _whole_chi2(r: dict) -> float:
    """A node's whole residual, its rows' chi2 (a read corner: its X row and position rows together)."""
    c = r.get("chi2")
    return float(c) if c is not None else float(r.get("residual_sd") or 0.0) ** 2 + float(r.get("pos_sd") or 0.0) ** 2


def _misfit_key(n: str, r: dict) -> tuple:
    """Misfits ranked by their whole residual's tail probability for its rows (a 3-row residual's chi2 9.3 is p 0.026,
    a 1-row one's 4.0 is p 0.046), then chi2, then name."""
    c = _whole_chi2(r)
    return chi2_tail(c, _rows(r)), -c, n


def _is_misfit(r: dict) -> bool:
    """A reading or typed value the fit leaves beyond MISFIT_SD: its X row, or its whole residual (X and position rows)
    beyond the probability of MISFIT_SD sd for its rows (review 2026-10-12 v3p3 P1: the flat crown slid E 2.82 sd along
    its reading, X 1.14 sd, chi2 9.26 of the fit's 10.48, and the misfit lines said only "E +0.41 mm (1.1 sd)")."""
    if r.get("source") not in ("read", "typed") or r.get("residual_sd") is None:
        return False
    return abs(r["residual_sd"]) > MISFIT_SD or chi2_tail(_whole_chi2(r), _rows(r)) < chi2_tail(MISFIT_SD ** 2, 1)


def _misfit_text(n: str, r: dict) -> str:
    """One node's misfit (fitted - read, after the camera turns): a read corner the fit may move, its (d, y) part along
    the directions it may move (mm and sd, where either prints a digit) and its X part; else its X (a typed value: its
    t). Both parts, never one sqrt(chi2) of rows of different kinds as "sd"."""
    x = f"{_signed(r['residual_mm'])} mm ({abs(r['residual_sd']):.1f} sd)"
    if r.get("source") == "typed":
        return f"{n} typed {x}"
    ps_, pm = float(r.get("pos_sd") or 0.0), r.get("pos_mm")
    if _rows(r) > 1 and (ps_ >= 0.05 or (pm is not None and pm >= 0.005)):
        return f"{n} (d, y) " + (f"{pm:.2f} mm ({ps_:.1f} sd)" if pm is not None else f"{ps_:.1f} sd") + f" and X {x}"
    return f"{n} X {x}"


def _held_text(n: str, r: dict) -> str:
    """A node the thickness bound holds: solved / base, or how far from its reading or typed value."""
    if r["source"] in ("solve", "base"):
        return f"{n} ({'solved' if r['source'] == 'solve' else 'base'})"
    what = "reading" if r["source"] == "read" else "typed value"
    return f"{n} ({_signed(r['residual_mm'])} mm from its {what})"


def _crease_line(cr: dict) -> str:
    """Keep built geometry separate from readings-only evidence. A geometric angle below two degrees is not a
    statistically flat measurement; in particular, an unresolved measurement is never printed as 'flat'."""
    base = cr["pair"][1] == "base"
    ang = f"{_signed(cr['deg'], 1)} deg"
    rng = cr.get("range_deg")
    along = (f" at mid, {_signed(rng[0], 1)}..{_signed(rng[1], 1)} along {'it' if base else 'the edge'}"
             if rng is not None and rng[1] - rng[0] > 0.5 else "")
    if cr.get("held"):
        at_min = abs(abs(cr["deg"]) - MIN_CREASE_DEG) <= 0.01
        along += " (held at the declared minimum)" if at_min else " (held by its declaration)"
    head = (f"{cr['name']} built fold {ang} across the border{along}" if base
            else f"{cr['name']} built fold {ang}{along}")
    state = cr.get("state") or ("certain" if cr["certain"] else "open")
    line = head
    if base:
        line += f"; dihedral {cr['normal_deg']:.1f} deg"
    elif abs(cr["normal_deg"] - abs(cr["deg"])) >= 0.5:            # a steep edge: the builder's number differs
        line += f" (dihedral {cr['normal_deg']:.1f} deg)"
    fdeg = cr.get("free_deg") if cr.get("free_deg") is not None else cr["deg"]
    fsd = cr.get("free_sd_deg") if cr.get("free_sd_deg") is not None else cr["sd_deg"]
    line += f"; measurement {_signed(fdeg, 1)} +- {fsd:.1f} deg, {STATE_TEXT[state]}"
    if cr.get("bound"):
        line += f"; the thickness bound holds {', '.join(cr['bound'][:3])} (the sd is without it)"
    if cr["declared"]:
        line += f", declared {cr['declared']}"
        if cr["dchi2"] is None or cr["dchi2"] > COST_SHOWN:
            cost = "cannot hold" if cr["dchi2"] is None else f"cost +{cr['dchi2']:.1f}"
            line += f" (readings-only {_signed(cr['free_deg'], 1)} +- {cr['free_sd_deg']:.1f} deg, {cost})"
    if cr.get("visible_angle"):
        v = cr["visible_angle"]
        line += f"; visible dihedral {v['degrees']:g} deg from comparison {v['comparison_id']}"
    return line


def _wrap(text: str, width: int = REPORT_CHARS, indent: str = "  ") -> list[str]:
    """Lines of at most `width` characters, broken after '; ', then ', ', then a space; continuations indented."""
    out, line = [], text
    while len(line) > width:
        cut = -1
        for sep in ("; ", ", ", " "):
            cut = line.rfind(sep, len(indent) + 1, width - len(sep) + 1)
            if cut > len(indent):
                cut += len(sep) - 1
                break
        if cut <= len(indent):
            cut = width - 1
        out.append(line[:cut + 1].rstrip())
        line = indent + line[cut + 1:].lstrip()
    out.append(line)
    return out


# --------------------------------------------------------------------------------------------- the problem
def _number(x, where: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float, np.integer, np.floating)) or not math.isfinite(float(x)):
        raise TempleError("E_SPEC", f"{where} must be a finite number, got {x!r}", where=where)
    return float(x)


def _position_cov(name: str, rec: dict, sd: float) -> np.ndarray | None:
    """A reading's (d, y, X) covariance with its X variance zeroed (the fit's own X weight carries it): cov (3 x 3,
    d y X, mm^2; its X variance must be sd^2) or sd_d / sd_y (independent, uncorrelated with X); None without
    position error."""
    if "cov" in rec:
        if "sd_d" in rec or "sd_y" in rec:
            raise TempleError("E_SPEC", f"node {name}: a reading gives cov or sd_d / sd_y, not both", node=name)
        try:
            M = np.array(rec["cov"], np.float64)
        except (TypeError, ValueError):
            M = np.zeros(0)
        if M.shape != (3, 3) or not np.all(np.isfinite(M)) or not np.allclose(M, M.T, rtol=0.0, atol=1e-9):
            raise TempleError("E_SPEC", f"node {name}: a reading's cov must be a symmetric 3 x 3 (d, y, X) matrix",
                              node=name)
        if abs(M[2, 2] - sd * sd) > 1e-6 + 1e-3 * sd * sd:
            raise TempleError("E_SPEC", f"node {name}: the reading's cov X variance {M[2, 2]:g} must be sd^2 "
                              f"{sd * sd:g}", node=name)
        if float(np.linalg.eigvalsh(M).min()) < -1e-9 * max(1.0, float(np.abs(M).max())):
            raise TempleError("E_SPEC", f"node {name}: the reading's cov is not positive semidefinite", node=name)
        M = M.copy()
    else:
        sdd = _number(rec.get("sd_d", 0.0), f"nodes.{name} read.sd_d")
        sdy = _number(rec.get("sd_y", 0.0), f"nodes.{name} read.sd_y")
        if sdd < 0.0 or sdy < 0.0:
            raise TempleError("E_SPEC", f"node {name}: a reading's sd_d and sd_y must be >= 0", node=name)
        M = np.diag([sdd * sdd, sdy * sdy, 0.0])
    M[2, 2] = 0.0
    return M if M[0, 0] > 0.0 or M[1, 1] > 0.0 else None


def _camera(name: str, rec: dict) -> dict:
    """A reading's camera terms {fit: (dd, dy, dX)}: the read point's move for +1 sd of that camera fit's theta (one
    rigid turn of the temple per camera fit, readings.py camera_shift). {} without."""
    cam = rec.get("camera")
    if cam is None:
        return {}
    if not isinstance(cam, dict):
        raise TempleError("E_SPEC", f"node {name}: a reading's camera must be {{fit: [dd, dy, dX]}}", node=name)
    out = {}
    for f, v in cam.items():
        if not isinstance(f, str) or not f or not isinstance(v, (list, tuple)) or len(v) != 3:
            raise TempleError("E_SPEC", f"node {name}: a reading's camera must be {{fit: [dd, dy, dX]}}, got "
                              f"{f!r}: {v!r}", node=name)
        out[f] = np.array([_number(x, f"nodes.{name} read.camera.{f}") for x in v], np.float64)
    return out


def _full_cov(rec: dict, sd: float) -> np.ndarray:
    """A validated reading's 3 x 3 (d, y, X) pick covariance with its X variance sd^2 (cov, or sd_d / sd_y diagonal)."""
    if "cov" in rec:
        M = np.array(rec["cov"], np.float64)
        M = 0.5 * (M + M.T)
    else:
        M = np.diag([float(rec.get("sd_d", 0.0)) ** 2, float(rec.get("sd_y", 0.0)) ** 2, 0.0])
    M[2, 2] = sd * sd
    return M


def read_payload(rec: dict) -> dict:
    """The fit's node t value {"read": {...}} from an agent/temple_lab/readings.py record (WP6_V3 §4; the lab's
    set_node mode "reading"): X, d, y, photos; cov = the record's pick covariance cov_pick (3 x 3 over d, y, X, mm^2;
    symmetrised, a negative eigenvalue from its 1e-6 rounding clipped to 0; sd = the root of its X variance, the pick
    part sd_X_pick); camera = camera_shift {fit: [dd, dy, dX] for +1 sd of that camera fit's theta}, which the fit
    turns into one unknown per camera fit shared by every reading of that fit. The camera part is one rigid turn of the
    temple per fit, the same for all its readings (readings.py module doc), so it is never added to a reading's own
    noise: probe1 did (sd_X as the X sd), and its readings then disagreed with a turn they share as if they disagreed
    with each other. A record without cov_pick gives sd from sd_X_pick and sd_d / sd_y from sd_d_pick / sd_y_pick when
    it has camera_shift (a pick sd it lacks: its total sd_X / sd_d / sd_y less the camera part in quadrature, which
    the totals hold: review 2026-10-12, the total was taken and the camera part then counted twice; an X total with no
    pick part left is a ValueError), else from sd_X, sd_d, sd_y. id = the record's id, else its name. Raises ValueError
    for a record without a finite X, d, y or a positive sd."""
    def num(k):
        v = rec.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ValueError(f"reading record {rec.get('name')!r}: {k} must be a finite number, got {v!r}")
        return float(v)
    X, d, y = num("X"), num("d"), num("y")
    out = {"id": str(rec.get("id") or rec.get("name") or ""), "X": X, "d": d, "y": y,
           "photos": [str(p) for p in rec.get("photos") or []]}
    shift = rec.get("camera_shift")
    has_cam = isinstance(shift, dict) and bool(shift)
    cam = {}
    if has_cam:
        for f, v in sorted(shift.items()):
            if (not isinstance(v, (list, tuple)) or len(v) != 3
                    or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))
                               for x in v)):
                raise ValueError(f"reading record {rec.get('name')!r}: camera_shift[{f!r}] must be 3 finite numbers")
            cam[str(f)] = [float(x) for x in v]
    if rec.get("cov_pick") is not None:
        C = np.array(rec["cov_pick"], np.float64)
        if C.shape != (3, 3) or not np.all(np.isfinite(C)):
            raise ValueError(f"reading record {rec.get('name')!r}: cov_pick must be a finite 3 x 3 matrix")
        C = 0.5 * (C + C.T)
        if C[2, 2] <= 0.0:
            raise ValueError(f"reading record {rec.get('name')!r}: cov_pick's X variance must be > 0")
        w, V = np.linalg.eigh(C)
        if float(w.min()) < 0.0:                        # rounding to 1e-6 (readings._r) can leave -1e-7: clip it
            C = (V * np.maximum(w, 0.0)) @ V.T
            C = 0.5 * (C + C.T)
        out["sd"] = math.sqrt(float(C[2, 2]))
        out["cov"] = [[float(v) for v in row] for row in C]
    else:
        def finite(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))
        for i, k in enumerate(("d", "y", "X")):
            v = rec.get(f"sd_{k}_pick") if has_cam else None
            if v is None:
                v = rec.get(f"sd_{k}", rec.get("sd") if k == "X" else None)
                if has_cam and finite(v):            # the total holds the camera part in quadrature: take it out
                    left = float(v) ** 2 - sum(c[i] ** 2 for c in cam.values())
                    if k == "X" and left <= 0.0:
                        raise ValueError(f"reading record {rec.get('name')!r}: sd_X {float(v):g} holds no pick part "
                                         f"beside its camera part (give sd_X_pick)")
                    v = math.sqrt(left) if left > 0.0 else None
            if k == "X":
                if not finite(v) or not float(v) > 0.0:
                    raise ValueError(f"reading record {rec.get('name')!r}: its X sd must be > 0, got {v!r}")
                out["sd"] = float(v)
            elif finite(v) and float(v) > 0.0:
                out[f"sd_{k}"] = float(v)
    if has_cam:
        out["camera"] = cam
    return {"read": out}


def _dy_sd(rec: dict) -> tuple[float, float]:
    """(sd_d, sd_y) of a reading record: sd_d / sd_y, or cov's diagonal; 0 where absent or malformed."""
    try:
        if "cov" in rec:
            M = np.array(rec["cov"], np.float64)
            return math.sqrt(max(float(M[0, 0]), 0.0)), math.sqrt(max(float(M[1, 1]), 0.0))
        return float(rec.get("sd_d") or 0.0), float(rec.get("sd_y") or 0.0)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0


@dataclass
class _Seg:
    pair: tuple                     # (F1, F2 | "base")
    mid: np.ndarray                 # (d, y): the point on the shared piece (its midpoint, or an EDGE_AT point)
    n: np.ndarray                   # unit normal from F1 into F2
    length: float                   # the piece's length at its midpoint; 0 at its other EDGE_AT points
    a1: np.ndarray                  # g1 = a1 . z + k1 (the slope of F1 along n)
    k1: float
    a2: np.ndarray
    k2: float
    G1: np.ndarray                  # grad X_F1 = G1 @ z + K1 (2, N), (2,): the full gradients, for normal_deg
    K1: np.ndarray
    G2: np.ndarray
    K2: np.ndarray
    frac: float = 0.5               # where along its piece
    piece: int = 0                  # the shared piece it lies on
    index: int = 0                  # its index in the problem's segment list


class _Problem:
    """The design read into the fit's linear algebra (module docstring). The structure (unknowns, rows, bound points,
    crease pieces) is fixed here; numbers(nodes) evaluates every matrix at given node positions."""

    def __init__(self, design: dict):
        if not isinstance(design, dict):
            raise TempleError("E_SPEC", f"the spec must be a JSON object, got {type(design).__name__}")
        self.design = design
        nodes_in, fac_in = design.get("nodes"), design.get("facets")
        if not isinstance(nodes_in, dict) or not isinstance(fac_in, dict):
            raise TempleError("E_SPEC", "the spec needs nodes {name: [d, y, t]} and facets {label: {poly, plane}}")
        self.src: dict = {}                                  # name -> ("read", X, sd, rec) | ("typed", t) | ...
        self.read_cov: dict = {}                             # read node -> its (d, y, X) covariance (3, 3)
        self.cam: dict = {}                                  # read node -> {camera fit: (dd, dy, dX) per sd}
        probe_nodes = {}
        for name, v in nodes_in.items():
            if not isinstance(v, (list, tuple)) or len(v) != 3:
                probe_nodes[name] = v                       # the probe's parse names the error
                continue
            t = v[2]
            if isinstance(t, dict):
                rec = t.get("read") if set(t) == {"read"} else None
                if not isinstance(rec, dict):
                    raise TempleError("E_SPEC", f"node {name}: t must be a number, null, \"base\" or {{\"read\": "
                                      f"{{id, X, sd, d, y, photos}}}}", node=name)
                bad = sorted(set(rec) - set(READ_KEYS))
                if bad or "X" not in rec or "sd" not in rec:
                    raise TempleError("E_SPEC", f"node {name}: a reading needs X and sd (keys {list(READ_KEYS)}), got "
                                      f"{sorted(rec)}", node=name, unknown=bad)
                X, sd = _number(rec["X"], f"nodes.{name} read.X"), _number(rec["sd"], f"nodes.{name} read.sd")
                if sd <= 0.0:
                    raise TempleError("E_SPEC", f"node {name}: a reading's sd must be > 0, got {sd:g}", node=name)
                for k in ("d", "y"):
                    if k in rec:
                        _number(rec[k], f"nodes.{name} read.{k}")
                self.src[name] = ("read", X, sd, rec)
                _position_cov(name, rec, sd)                # validates cov / sd_d / sd_y
                self.read_cov[name] = _full_cov(rec, sd)
                self.cam[name] = _camera(name, rec)
                probe_nodes[name] = [v[0], v[1], 0.0]
            else:
                self.src[name] = ("solve",) if t is None else ("base",) if t == "base" else ("typed", t)
                probe_nodes[name] = list(v)
        self.mode: dict = {}                                 # label -> design plane
        probe_facets = {}
        for label, f in fac_in.items():
            if isinstance(f, (list, tuple)):
                f = {"poly": list(f)}
            if not isinstance(f, dict):
                probe_facets[label] = f
                continue
            bad = sorted(set(f) - set(FACET_KEYS))
            if bad:
                raise TempleError("E_SPEC", f"unknown facet {label} keys {bad}; known: {list(FACET_KEYS)}",
                                  facet=label, unknown=bad)
            if f.get("fade") is not None:                  # fades were removed (module docstring)
                raise TempleError("E_SPEC", f"facet {label}: fade must be null (fades were removed on 2026-10-13)",
                                  facet=label)
            plane = f.get("plane", "nodes")
            if isinstance(plane, dict) and set(plane) == {"coef"}:
                cf = plane["coef"]
                if not isinstance(cf, (list, tuple)) or len(cf) != 3:
                    raise TempleError("E_SPEC", f"facet {label}: coef must be [a, b, c]", facet=label)
                plane = {"coef": [_number(x, f"facets.{label}.plane.coef") for x in cf]}
            elif isinstance(plane, dict) and set(plane) == {"crease", "rise_per_mm"}:
                cr = plane["crease"]
                if (not isinstance(cr, (list, tuple)) or len(cr) != 2 or cr[0] == cr[1]
                        or any(n not in f.get("poly", []) for n in cr)):
                    raise TempleError("E_SPEC", f"facet {label}: crease must name two different corners of its poly",
                                      facet=label)
                plane = {"crease": [str(cr[0]), str(cr[1])],
                         "rise_per_mm": _number(plane["rise_per_mm"], f"facets.{label}.plane.rise_per_mm")}
            elif plane not in ("nodes", "warp"):
                raise TempleError("E_SPEC", f"facet {label}: plane must be 'nodes', 'warp', {{coef: [a, b, c]}} or "
                                  f"{{crease: [n1, n2], rise_per_mm: s}}", facet=label)
            if "soft" in f and not isinstance(f["soft"], bool):
                raise TempleError("E_SPEC", f"facet {label}: soft must be true or false", facet=label)
            self.mode[label] = plane
            probe_facets[label] = {"poly": f.get("poly"), "plane": "nodes"}
        probe = copy.deepcopy({k: v for k, v in design.items() if k not in ("nodes", "facets")})
        probe["nodes"], probe["facets"] = probe_nodes, probe_facets
        self.probe = copy.deepcopy(probe)                    # (_builder_point parses it with the corners moved)
        ps = self.ps = parse(probe)
        self.tmin = float(ps.options["t_min_mm"])
        self.nodes = {k: (float(v[0]), float(v[1])) for k, v in ps.nodes.items()}
        self.labels = sorted(ps.facets)
        self.boundary = ps.boundary()
        self.poly, self.P = {}, {}
        for label in self.labels:
            poly = ccw_poly(ps.facets[label]["poly"], ps.nodes)
            P = np.array([self.nodes[n] for n in poly])
            if abs(poly_area(P)) <= TILE_TOL_MM2:
                raise TempleError("E_SPEC", f"facet {label}: its polygon has no area", facet=label)
            ok, bad = simple_polygon(P)
            if not ok:
                raise TempleError("E_SPEC", f"facet {label}: its polygon crosses itself (edges {bad})", facet=label)
            if self.mode[label] == "warp" and len(poly) < 4:
                raise TempleError("E_SPEC", f"facet {label}: a warp face needs >= 4 corners, got {len(poly)}",
                                  facet=label)
            if isinstance(self.mode[label], dict) and "crease" in self.mode[label]:
                n1, n2 = self.mode[label]["crease"]
                if crease_run(poly, n1, n2, ps.nodes) is None:
                    raise TempleError("E_SPEC", f"facet {label}: crease {[n1, n2]} must be an edge of its poly or the "
                                      f"ends of a straight run of its edges (after the snaps)", facet=label)
            self.poly[label], self.P[label] = poly, P
        S.run_conflicts({l: {"poly": self.poly[l], "plane": self.mode[l] if isinstance(self.mode[l], dict)
                             and "crease" in self.mode[l] else "nodes"} for l in self.labels},
                        {k: list(v) for k, v in self.nodes.items()}, ps.snaps)
        self.flat = [l for l in self.labels if self.mode[l] != "warp"]
        self.warps = [l for l in self.labels if self.mode[l] == "warp"]
        self.names = sorted({n for l in self.labels for n in self.poly[l]})
        self._orphans()
        XY = np.array([self.nodes[n] for n in self.names]).reshape(-1, 2)
        ins = points_in_poly(XY, self.boundary) | (_dists_to_edges(XY, self.boundary) <= ON_MM)
        self.inside = {n: bool(v) for n, v in zip(self.names, ins)}
        ek = [(l, k) for l in self.labels for k in range(len(self.P[l]))]      # every facet edge (edge_hits, the clips)
        self.edge_ref = ek
        self.edge_a = np.array([self.P[l][k] for l, k in ek]).reshape(-1, 2)
        self.edge_b = np.array([self.P[l][(k + 1) % len(self.P[l])] for l, k in ek]).reshape(-1, 2)
        # the edges' outline crossings (clip_segment's interval ends); an edge whose ends lie inside the outline,
        # off it, and that meets no outline edge has none
        reach = ~(points_in_poly(self.edge_a, self.boundary) & points_in_poly(self.edge_b, self.boundary)
                  & (_dists_to_edges(self.edge_a, self.boundary) > ON_MM)
                  & (_dists_to_edges(self.edge_b, self.boundary) > ON_MM)) | _meets(self.edge_a, self.edge_b,
                                                                                     self.boundary)
        self.clip = {(l, k): ({a for iv in clip_segment(self.edge_a[i], self.edge_b[i], self.boundary) for a in iv}
                              if reach[i] else {0.0, 1.0}) for i, (l, k) in enumerate(ek)}
        self._split()
        keys = [(n, l) for n in self.names for l in (self.split[n] if n in self.split else [None])]
        self.kix = {k: i for i, k in enumerate(keys)}
        self.ix = {n: self.kix[(n, None)] for n in self.names if n not in self.split}
        self.col = {l: len(keys) + 3 * k for k, l in enumerate(self.flat)}
        self.centre = {l: self.P[l].mean(axis=0) for l in self.flat}
        self.reads = [n for n in self.names if self.src[n][0] == "read"]     # read corners (never split: not null)
        self.fits = sorted({f for n in self.reads for f in self.cam[n]})
        self.ucol = {f: len(keys) + 3 * len(self.flat) + k for k, f in enumerate(self.fits)}
        self.N = len(keys) + 3 * len(self.flat) + len(self.fits)
        self.events = self._events()
        self._thin_cache: dict = {}
        self._grad_cache: dict = {}
        self._touch_cache: dict = {}
        self._others_cache: dict = {}
        self._structure()
        self._directions()
        self._free_coords()
        self.M = len(self.coords)                                           # corner position unknowns (Z[N:])
        self.NZ = self.N + self.M
        self.gpin = {n: np.zeros(2) for n in self.reads}                    # the pinned directions' face gradient
        self.pinned = False                                                 # the positions held at the point
        self.geo = self.jac = self.jac_at = None
        self.delta, self.zk = np.zeros(self.M), np.zeros(self.N)            # the linearisation point
        self.cur_nodes, self.cur_ps, self.Pcur = dict(self.nodes), self.ps, dict(self.P)
        self._obs_system()
        self.set_point(np.zeros(self.NZ))

    # ----------------------------------------------------------------------------------------- structure
    def _orphans(self) -> None:
        """E_SPEC: a node with a value (read, typed or base) that no facet lists, inside or on the outline and inside
        or on a facet's polygon (after the parse's snaps). The fit fits a value only at a facet's corner, and the
        builder binds a node's value at any arrangement vertex it sits on: v3p3-verify-13 dropped L from nose [A, L0,
        C] with L kept (read, on the bottom outline knot inside nose's polygon): the fit left L's reading out ("from 6
        readings") and accepted, then the builder refused E_RELIEF_STEP at nose's plane, 0.191 mm off at L; a typed
        node strictly inside a facet was dropped by both, silently. No stored lab revision had such a node (11,066
        scanned). Facts and every way out. A node in no facet's polygon (the plain arm, a design with no facets: every
        package's revision 0) or outside the outline binds nothing in either; its reading becomes its t."""
        orphans = sorted(n for n in self.nodes if n not in set(self.names)
                         and self.src.get(n, ("solve",))[0] in ("read", "typed", "base"))
        if not orphans:
            return
        B = self.boundary
        for n in orphans:
            p = np.array([self.nodes[n]], np.float64)
            if not (points_in_poly(p, B)[0] or _dists_to_edges(p, B)[0] <= ON_MM):
                continue
            on = [l for l in self.labels if _dists_to_edges(p, self.P[l])[0] <= ON_MM]
            ins = [l for l in self.labels if l not in on and points_in_poly(p, self.P[l])[0]]
            if not on and not ins:
                continue
            where = ("on the edge of facet" + ("s " if len(on) > 1 else " ") + ", ".join(on) if on else
                     "inside facet" + ("s " if len(ins) > 1 else " ") + ", ".join(ins))
            what = {"read": "a reading", "typed": "a typed t", "base": "t base"}[self.src[n][0]]
            raise TempleError("E_SPEC", f"node {n} has {what} and lies {where} (d {_fixed(p[0, 0])}, y "
                              f"{_fixed(p[0, 1])}), but no facet lists it among its corners: the fit takes a node's "
                              f"value only at a facet's corner; ways: list {n} among a facet's corners, give it no "
                              f"value (t solve), or delete node {n}", node=n, facets=on + ins,
                              at=[float(p[0, 0]), float(p[0, 1])])

    def _split(self) -> None:
        """self.split {node: [one facet per group]} and self.group {(node, facet): its group's facet}: a null node
        outside the outline listed by several flat facets (none a warp), one unknown per group of facets joined by a
        shared edge from it that reaches inside the outline (union-find; module docstring)."""
        self.of = {n: [l for l in self.labels if n in self.poly[l]] for n in self.names}
        self.split, self.group = {}, {}
        for n in self.names:
            ls = self.of[n]
            if (self.src[n][0] != "solve" or self.inside[n] or len(ls) < 2
                    or any(self.mode[l] == "warp" for l in ls)):
                continue
            root = {l: l for l in ls}

            def find(l):
                while root[l] != l:
                    l = root[l]
                return l
            for i, l1 in enumerate(ls):
                for l2 in ls[i + 1:]:
                    if find(l1) != find(l2) and self._joined(n, l1, l2):
                        root[find(l1)] = find(l2)
            groups: dict = {}
            for l in ls:
                groups.setdefault(find(l), []).append(l)
            if len(groups) > 1:
                self.split[n] = sorted(min(g) for g in groups.values())
                for g in groups.values():
                    for l in g:
                        self.group[(n, l)] = min(g)

    def _joined(self, n: str, l1: str, l2: str) -> bool:
        """Facets l1 and l2 share an edge from node n (collinear and overlapping from n) whose shared part reaches
        inside the outline: the builder binds them at its arrangement vertices there (planes.solve)."""
        p = np.array(self.nodes[n])

        def rays(l):
            poly, m = self.poly[l], len(self.poly[l])
            i = poly.index(n)
            return [np.array(self.nodes[poly[(i + 1) % m]]), np.array(self.nodes[poly[i - 1]])]
        for q1 in rays(l1):
            for q2 in rays(l2):
                u1, u2 = q1 - p, q2 - p
                L1, L2 = float(np.hypot(*u1)), float(np.hypot(*u2))
                if min(L1, L2) <= ON_MM or float(u1 @ u2) <= 0.0:
                    continue
                if abs(float(u1[0] * u2[1] - u1[1] * u2[0])) > ON_MM * max(L1, L2):
                    continue
                q, L = (q1, L1) if L1 <= L2 else (q2, L2)
                if sum(b - a for a, b in clip_segment(p, q, self.boundary)) * L > ON_MM:
                    return True
        return False

    def _events(self) -> np.ndarray:
        """The d of every vertical line across which a flat facet's thickness is convex (module docstring): concave
        inner knots, chamfer breaks, outline knots inside a chamfer's d range."""
        ps = self.ps
        kd, kv = np.asarray(ps.inner.d, np.float64), np.asarray(ps.inner.v, np.float64)
        ev = set()
        if len(kd) > 2:
            sl = np.diff(kv) / np.diff(kd)
            ev |= {float(x) for x in kd[1:-1][sl[1:] < sl[:-1] - 1e-12]}
        for ch in ps.chamfers:
            ev |= {float(x) for x in chamfer_breaks(ch)}
            chain = np.asarray(ps.top if ch["edge"] == "top" else ps.bottom, np.float64)
            ev |= {float(x) for x in chain[:, 0] if ch["d"][0] < x < ch["d"][1]}
        return np.array(sorted(x for x in ev if ps.d0 <= x <= ps.d1), np.float64)

    def _structure(self) -> None:
        """The rows and points every evaluation shares: observations, equalities, bound points, crease pieces."""
        self.obs_def, weak = [], []
        for (n, _), i in self.kix.items():
            if self.src[n][0] != "base":
                self.obs_def.append((i, n))
                weak.append(self.src[n][0] == "solve")
        self.weak = np.array(weak + [False] * len(self.fits), bool)        # + one unit prior row per camera fit
        eq = []
        for label in self.flat:
            eq += [("corner", label, name) for name in self.poly[label]]
            mode = self.mode[label]
            if isinstance(mode, dict) and "coef" in mode:
                a, bb, cc = mode["coef"]
                d0, y0 = self.centre[label]
                eq += [("coef", label, k, v) for k, v in enumerate((a + bb * d0 + cc * y0, bb, cc))]
            elif isinstance(mode, dict):
                n1, n2 = mode["crease"]
                left = crease_run(self.poly[label], n1, n2, self.ps.nodes)[0] > 0
                eq.append(("rise", label, n1, n2, left, mode["rise_per_mm"]))
        eq += [("base", n) for n in self.names if self.src[n][0] == "base"]
        XY = np.array([self.nodes[n] for n in self.names]).reshape(-1, 2)
        hold = _dists_to_segments(XY, self.edge_a, self.edge_b) <= ON_MM                    # (nodes, edges)
        for i, n in enumerate(self.names):
            if not self.inside[n]:
                continue
            p = XY[i]
            for j in np.nonzero(np.hypot(*(XY[i + 1:] - p).T) <= ON_MM)[0]:
                eq.append(("same", n, self.names[i + 1 + j]))
            hits: dict = {}
            for j in np.nonzero(hold[i])[0]:
                hits.setdefault(self.edge_ref[j][0], self.edge_ref[j][1])
            for label, k in sorted(hits.items()):
                if n in self.poly[label] or np.min(np.hypot(*(self.P[label] - p).T)) <= ON_MM:
                    continue                                 # its own corner, or a node at a corner ("same")
                eq.append(("tee", n, label, k))
        self.eq_def = eq
        self.bound_def = self._bound_points()
        self.seg_def = self._pieces()

    def _bound_points(self) -> list:
        """The thickness bound's points (module docstring): ("node", n), ("edge", label, k, s) (on facet edge k at
        parameter s) and ("pt", label, (d, y), k | None) (an outline point inside or on a facet; k its edge when on
        one); one per point."""
        defs, pts = [], []
        for n in self.names:
            if self.inside[n]:
                defs.append(("node", n))
                pts.append(self.nodes[n])
        ev = self.events
        cand = []
        for label in self.labels:
            P = self.P[label]
            for k in range(len(P)):
                p, q = P[k], P[(k + 1) % len(P)]
                ts = set(self.clip[(label, k)])                  # the edge's outline crossings
                if abs(q[0] - p[0]) > 1e-12:
                    lo, hi = sorted((p[0], q[0]))
                    ts |= {float((x - p[0]) / (q[0] - p[0])) for x in ev[(ev > lo) & (ev < hi)]}
                cand += [(("edge", label, k, s), p + s * (q - p)) for s in sorted(ts) if 1e-9 < s < 1.0 - 1e-9]
        B = self.boundary
        Bq = np.roll(B, -1, axis=0)
        opts = [B]
        for x in ev:                                         # the outline's crossings with the event lines
            i = np.nonzero((B[:, 0] - x) * (Bq[:, 0] - x) < 0.0)[0]
            s = (x - B[i, 0]) / (Bq[i, 0] - B[i, 0])
            opts.append(B[i] + s[:, None] * (Bq[i] - B[i]))
        opts = np.vstack(opts)
        own = np.full(len(opts), -1)
        for li, label in enumerate(self.labels):
            P = self.P[label]
            own[(own < 0) & (points_in_poly(opts, P) | (_dists_to_edges(opts, P) <= ON_MM))] = li
        for j in np.nonzero(own >= 0)[0]:
            label, x = self.labels[own[j]], opts[j]
            cand.append((("pt", label, (float(x[0]), float(x[1])), self.on_edge(label, x)), x))
        if cand:
            X = np.array([c[1] for c in cand])
            ok = points_in_poly(X, B) | (_dists_to_edges(X, B) <= ON_MM)
            kept = np.array(pts, np.float64).reshape(-1, 2)
            for (dfn, x), keep in zip(cand, ok):
                if keep and (not len(kept) or float(np.min(np.hypot(*(kept - x).T))) > ON_MM):
                    defs.append(dfn)
                    kept = np.vstack([kept, x])
        return defs

    def _pieces(self) -> list:
        """The crease segments [(F, nb, k, k_nb, t, length, frac, piece)]: every facet edge is cut at its outline
        crossings and at every facet corner on it; the pieces' midpoints are classified in one vectorised pass (off the
        outline, inside it, which other facets' edges hold them), then consecutive pieces with one neighbour merge; a
        piece gives its midpoint, beside a warp or the base its EDGE_AT points (t along F's edge k; k_nb the neighbour's
        edge)."""
        XY = np.array([self.nodes[n] for n in self.names]).reshape(-1, 2)        # every facet corner
        cuts = []                                        # (F, k, t0, t1)
        for F in self.labels:
            P = self.P[F]
            for k in range(len(P)):
                p, q = P[k], P[(k + 1) % len(P)]
                r = q - p
                L = float(np.hypot(*r))
                off = np.abs(r[0] * (XY[:, 1] - p[1]) - r[1] * (XY[:, 0] - p[0])) / L
                sp = (XY - p) @ r / (L * L)
                on = set(sp[(off <= ON_MM) & (sp > 0.0) & (sp < 1.0)].tolist())       # corners on the edge
                ts = sorted({0.0, 1.0} | self.clip[(F, k)] | on)
                cuts += [(F, k, t0, t1) for t0, t1 in zip(ts[:-1], ts[1:]) if (t1 - t0) * L > ON_MM]
        if not cuts:
            return []
        A = np.array([self.P[F][k] for F, k, _, _ in cuts])
        B = np.array([self.P[F][(k + 1) % len(self.P[F])] for F, k, _, _ in cuts])
        T = np.array([0.5 * (t0 + t1) for _, _, t0, t1 in cuts])
        mids = A + T[:, None] * (B - A)
        keep = (_dists_to_edges(mids, self.boundary) > ON_MM) & points_in_poly(mids, self.boundary)
        hold = _dists_to_segments(mids, self.edge_a, self.edge_b) <= ON_MM                     # (pieces, edges)
        lab = np.array([self.labels.index(l) for l, _ in self.edge_ref])
        pieces: dict = {}                                # (F, k) -> [[nb, t0, t1]]
        for i, (F, k, t0, t1) in enumerate(cuts):
            if not keep[i]:
                continue
            others = sorted({self.labels[j] for j in lab[hold[i]]} - {F})
            nb = others[0] if others else "base"
            run = pieces.setdefault((F, k), [])
            if run and run[-1][0] == nb and run[-1][2] == t0:
                run[-1][2] = t1
            else:
                run.append([nb, t0, t1])
        out, pid = [], 0
        for (F, k), run in pieces.items():
            P = self.P[F]
            p, q = P[k], P[(k + 1) % len(P)]
            L = float(np.hypot(*(q - p)))
            for nb, t0, t1 in run:
                if nb != "base" and nb < F:
                    continue
                along = nb == "base" or self.mode[F] == "warp" or self.mode[nb] == "warp"
                for at in EDGE_AT if along else (0.5,):
                    t = t0 + at * (t1 - t0)
                    k_nb = None if nb == "base" else self._edge_of(nb, p + t * (q - p))
                    out.append((F, nb, k, k_nb, t, (t1 - t0) * L if at == 0.5 else 0.0, at, pid))
                pid += 1
        return out

    # ----------------------------------------------------------------------------------------- numbers
    def numbers(self, nodes: dict, ps=None) -> dict:
        """Every geometry matrix of the problem (N columns) at the node positions `nodes` {name: (d, y)} and the
        outline of `ps` (default the point's; _ps_at moves the knots that move with a corner), the structure fixed:
        C, e, G, h (and the bound points), segs; and A, b (obs {name: (X, sd)}) with each reading's own X and sd, a
        reference for moved() (the fit's rows over Z are _obs_system's). set_point uses it at the linearisation point,
        _jacobian (via moved() or directly) about it."""
        P = {l: np.array([nodes[n] for n in self.poly[l]], np.float64) for l in self.labels}
        N, ps = self.N, self.cur_ps if ps is None else ps
        m = len(self.obs_def)
        A, b, obs = np.zeros((m + len(self.fits), N)), np.zeros(m + len(self.fits)), {}
        for r, (i, n) in enumerate(self.obs_def):
            d, y = nodes[n]
            s = self.src[n]
            if s[0] == "read":
                X, sd = s[1], s[2]
            elif s[0] == "typed":
                X, sd = float(ps.inner(d)) + float(s[1]), TYPED_SD_MM
            else:
                X, sd = float(ps.x_base(d, y)), PRIOR_SD_MM
            A[r, i] = 1.0 / sd
            b[r] = X / sd
            obs[n] = (X, sd)
        for k, f in enumerate(self.fits):                # a camera fit's theta turn: a unit prior (its sd)
            A[m + k, self.ucol[f]] = 1.0
        for n in self.names:
            if self.src[n][0] == "base":
                obs[n] = (float(ps.x_base(*nodes[n])), 0.0)
        C, e = np.zeros((len(self.eq_def), N)), np.zeros(len(self.eq_def))
        for r, tag in enumerate(self.eq_def):
            kind = tag[0]
            if kind == "corner":
                C[r] = -self.X_form(tag[1], nodes[tag[2]], None, P)
                C[r, self.cix(tag[1], tag[2])] += 1.0
            elif kind == "coef":
                C[r, self.col[tag[1]] + tag[2]] = 1.0
                e[r] = tag[3]
            elif kind == "rise":
                _, n_in, _ = crease_frame(nodes[tag[2]], nodes[tag[3]], tag[4])
                c = self.col[tag[1]]
                C[r, c + 1], C[r, c + 2] = n_in
                e[r] = tag[5]
            elif kind == "base":
                C[r, self.ix[tag[1]]] = 1.0
                e[r] = obs[tag[1]][0]
            elif kind == "same":
                C[r, self.ix[tag[1]]], C[r, self.ix[tag[2]]] = 1.0, -1.0
            else:                                        # tee
                C[r] = -self.X_form(tag[2], nodes[tag[1]], tag[3], P)
                C[r, self.ix[tag[1]]] += 1.0
        G, pts = self._bound_rows(self.bound_def, nodes, P)
        return {"A": A, "b": b, "obs": obs, "C": C, "e": e, "G": G, "h": self._bound_h(pts, ps), "pts": pts,
                "segs": self._segs(P, ps=ps)}

    def _bound_rows(self, defs: list, nodes: dict, P: dict) -> tuple[np.ndarray, np.ndarray]:
        """(G, points): one bound row per definition; a warp's points off its edges by planes.mvc's weights."""
        G, pts = np.zeros((len(defs), self.N)), np.zeros((len(defs), 2))
        mv: dict = {}
        for r, dfn in enumerate(defs):
            if dfn[0] == "node":
                G[r, self.ix[dfn[1]]] = 1.0
                pts[r] = nodes[dfn[1]]
                continue
            if dfn[0] == "edge":
                _, label, k, s = dfn
                Pl = P[label]
                x = Pl[k] + s * (Pl[(k + 1) % len(Pl)] - Pl[k])
            else:
                _, label, x, k = dfn
                x = np.asarray(x, np.float64)
            pts[r] = x
            if label in self.col or k is not None:
                G[r] = self.X_form(label, x, k, P)
            else:
                mv.setdefault(label, []).append(r)
        for label, rows in mv.items():
            W = _mvc_rows(P[label], pts[rows])
            for j, name in enumerate(self.poly[label]):
                G[rows, self.cix(label, name)] += W[:, j]
        return G, pts

    def _bound_h(self, pts, ps=None) -> np.ndarray:
        """x_in + t_min + THICK_MARGIN_MM + chamfer depth + max(roll_mm, roll depth) at the points (E_ROLL's pre-roll
        limit and E_TOO_THIN together, review F2), with the outline of ps (default the point's)."""
        ps = self.cur_ps if ps is None else ps
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        d, y = pts[:, 0], pts[:, 1]
        return np.asarray(ps.inner(d) + self.tmin + THICK_MARGIN_MM + chamfer_depth(ps, d, y)
                          + np.maximum(ps.roll_mm, roll_depth(ps, d, y)), np.float64).reshape(-1)

    def _segs(self, P: dict, which: list | None = None, ps=None) -> list:
        """The crease segments' gradient forms (N columns) at the polygons P (the base gradient of ps's outline in one
        vectorised pass); `which` the segment indices to evaluate (default all)."""
        which = list(range(len(self.seg_def))) if which is None else list(which)
        if not which:
            return []
        defs = [self.seg_def[i] for i in which]
        X, NO = np.zeros((len(defs), 2)), np.zeros((len(defs), 2))
        for j, (F, _, k, _, t, _, _, _) in enumerate(defs):
            Pf = P[F]
            p, q = Pf[k], Pf[(k + 1) % len(Pf)]
            r = q - p
            X[j] = p + t * r
            NO[j] = np.array([r[1], -r[0]]) / float(np.hypot(*r))
        bi = [j for j, s in enumerate(defs) if s[1] == "base"]
        gb = dict(zip(bi, self.base_grad(X[bi], ps))) if bi else {}
        out = []
        for j, (i, (F, nb, k, k_nb, _, length, frac, piece)) in enumerate(zip(which, defs)):
            x, nout = X[j], NO[j]
            G1, K1 = self.grad_form(F, x, k, P), np.zeros(2)
            if nb == "base":
                G2, K2 = np.zeros((2, self.N)), gb[j]
            else:
                G2, K2 = self.grad_form(nb, x, k_nb, P), np.zeros(2)
            out.append(_Seg((F, nb), x, nout, length, nout @ G1, float(nout @ K1), nout @ G2, float(nout @ K2),
                            G1, K1, G2, K2, frac, piece, i))
        return out

    # ----------------------------------------------------------------------------------------- corner positions
    def _free_coords(self) -> None:
        """The read corners' position unknowns (module docstring): per read corner self.Bn {node: (2, k) orthonormal
        basis of the directions its (d, y) is an unknown in} and self.Wn (the pinned rest, (2, 2 - k)); self.coords
        [{node, v, sd, h}] one per free direction (column N + j of Z), self.cidx {node: [j]}, self.knots {node:
        [(chain, index)]} the outline knots that move with it. A corner is pinned outside the outline, at a point
        another node shares, at the tip or the knot the tip's rounding starts from (moved, the arc would change),
        without a read (d, y); it slides along an outline edge or
        a facet edge it lies on without being that facet's corner (a T-junction); at an outline knot the knot moves
        with it (in y only at the hinge-end cap, whose d both chains share, and where the design's parse snapped
        another knot or node onto its d: moved, that one would un-snap and the builder's base differ from the fit's);
        a direction its reading's (d, y) covariance leaves below POS_SD_MIN_MM is pinned."""
        ps = self.ps
        same = {m for t in self.eq_def if t[0] == "same" for m in t[1:3]}
        tee_dirs: dict = {}
        for t in self.eq_def:
            if t[0] == "tee":
                P = self.P[t[2]]
                a, b = P[t[3]], P[(t[3] + 1) % len(P)]
                tee_dirs.setdefault(t[1], []).append((b - a) / float(np.hypot(*(b - a))))
        B = self.boundary
        Bq = np.roll(B, -1, axis=0)
        chains = {"top": ps.top, "bottom": ps.bottom}
        lens = {k: len(ps.snapped["outline"][k]) for k in chains}
        tip_round = float(ps.snapped["outline"].get("tip_round_mm") or 0.0) > 0.0
        self.coords, self.Bn, self.Wn, self.cidx, self.knots = [], {}, {}, {}, {}
        Qs, kn = {}, {}
        for n in self.reads:
            rec = self.src[n][3]
            Q, knots = np.eye(2), []
            p = np.asarray(self.nodes[n], np.float64)
            if not self.inside[n] or n in same or "d" not in rec or "y" not in rec:
                Q = np.zeros((2, 0))
            else:
                for chain, arr in chains.items():
                    dist = np.hypot(*(arr - p).T)
                    i = int(np.argmin(dist))
                    if dist[i] <= ON_MM:
                        if i >= lens[chain] - 1 or (i == lens[chain] - 2 and tip_round):
                            Q = np.zeros((2, 0))                 # the tip corner, its arc, or the knot it rounds from
                        elif i == 0:                             # the hinge-end cap: d shared by both chains
                            Q = _meet(Q, np.array([0.0, 1.0]))
                        knots.append((chain, i))
                if not knots:
                    dist = _dists_to_segments(p[None], B, Bq)[0]
                    i = int(np.argmin(dist))
                    if dist[i] <= ON_MM:                         # on an outline edge or cap: it slides along it
                        u = Bq[i] - B[i]
                        Q = _meet(Q, u / float(np.hypot(*u)))
                for u in tee_dirs.get(n, []):
                    Q = _meet(Q, u)
            if Q.shape[1]:
                w, V = np.linalg.eigh(Q.T @ self.read_cov[n][:2, :2] @ Q)
                Q = Q @ V[:, w > POS_SD_MIN_MM ** 2]
            if Q.shape[1] and knots and self._knot_holds_another(n, knots):
                Q, knots = np.zeros((2, 0)), []               # its outline edge holds another node: it stays
            Qs[n], kn[n] = Q, knots
        # a knot or node the design's parse snapped onto a free corner's d un-snaps when the corner moves alone (that
        # node, or an outline knot no corner carries, then differs from the fit's): the corner keeps its d, unless the
        # other is a free corner too (both are written where the fit puts them), or an inner / base table knot (it
        # follows the corner within snap_mm and keeps its own d beyond, in the fit as in the builder: _ps_at); to a
        # fixed point, as a pin can free no one
        owner = {f"node {n}": n for n in self.reads}
        owner.update({f"outline.{ch}[{i}]": n for n in self.reads for ch, i in kn[n]})
        changed = True
        while changed:
            changed = False
            for n in self.reads:
                Q, p = Qs[n], self.nodes[n]
                if not Q.shape[1] or abs(float(Q[0] @ Q[0])) <= 1e-18:
                    continue
                own = {f"node {n}"} | {f"outline.{ch}[{i}]" for ch, i in kn[n]}
                fixed = [s for s in ps.snaps if s.get("field") == "d" and s["what"] not in own
                         and abs(float(s["to"]) - p[0]) <= 1e-12 and not s["what"].startswith(TABLE_KNOTS)
                         and not (s["what"] in owner and Qs[owner[s["what"]]].shape[1])]
                if fixed:
                    Q = _meet(Q, np.array([0.0, 1.0]))
                    if Q.shape[1]:
                        w, V = np.linalg.eigh(Q.T @ self.read_cov[n][:2, :2] @ Q)
                        Q = Q @ V[:, w > POS_SD_MIN_MM ** 2]
                    Qs[n], changed = Q, True
        self.tpart = {}                                  # free corner -> [(table, index, its raw d)] snapped onto it
        for n in self.reads:
            Q, knots, p = Qs[n], kn[n], np.asarray(self.nodes[n], np.float64)
            k = Q.shape[1]
            if k and knots:
                own = {f"outline.{ch}[{i}]" for ch, i in knots}
                self.tpart[n] = [(x["what"].rpartition("[")[0], int(x["what"].rpartition("[")[2][:-1]),
                                  float(x["from"])) for x in ps.snaps if x.get("field") == "d"
                                 and x["what"].startswith(TABLE_KNOTS) and abs(float(x["to"]) - p[0]) <= 1e-12]
            self.Bn[n] = Q
            self.Wn[n] = (np.eye(2) if k == 0 else np.zeros((2, 0)) if k == 2
                          else np.array([[-Q[1, 0]], [Q[0, 0]]]))
            self.knots[n] = knots if k else []
            self.cidx[n] = []
            room = self._room(n) if k else math.inf
            rec = self.src[n][3]
            pr = np.array([float(rec["d"]), float(rec["y"])]) if k else p
            for c in range(k):
                v = Q[:, c].copy()
                sd = math.sqrt(float(v @ self.read_cov[n][:2, :2] @ v))
                self.cidx[n].append(len(self.coords))
                self.coords.append({"node": n, "v": v, "sd": sd, "room": room, "off": float(v @ (pr - p)),
                                    "h": min(max(POS_STEP_SHARE * sd, POS_STEP_MIN_MM), POS_STEP_MAX_MM,
                                             POS_STEP_ROOM * room),
                                    "cap": POS_MAX_SD * sd})

    def _others(self, n: str) -> tuple:
        """(points, segment starts, segment ends) corner n must keep clear of: every other node and outline knot, every
        facet edge and outline edge not ending at it or at its knot (cached)."""
        key = n
        if key not in self._others_cache:
            p = np.asarray(self.nodes[n], np.float64)
            pts = [np.asarray(self.nodes[m], np.float64) for m in self.names if m != n]
            A, Bq = [], []
            for l in self.labels:                        # (an edge it lies on, sliding along it, is its own)
                poly, P = self.poly[l], self.P[l]
                for k in range(len(poly)):
                    a, b = P[k], P[(k + 1) % len(poly)]
                    if n not in (poly[k], poly[(k + 1) % len(poly)]) and _dists_to_segments(p[None], a[None],
                                                                                            b[None])[0, 0] > ON_MM:
                        A.append(a)
                        Bq.append(b)
            B = self.boundary
            for i in range(len(B)):
                a, b = B[i], B[(i + 1) % len(B)]
                if _dists_to_segments(p[None], a[None], b[None])[0, 0] > ON_MM:
                    A.append(a)
                    Bq.append(b)
            pts += [q for q in B if float(np.hypot(*(q - p))) > ON_MM]
            self._others_cache[key] = (np.array(pts).reshape(-1, 2), np.array(A).reshape(-1, 2),
                                       np.array(Bq).reshape(-1, 2))
        return self._others_cache[key]

    def _room(self, n: str) -> float:
        """How far corner n is from what it must keep clear of (_others): the secant step's room."""
        p = np.asarray(self.nodes[n], np.float64)
        pts, A, Bq = self._others(n)
        d = np.r_[np.hypot(*(pts - p).T), _dists_to_segments(p[None], A, Bq)[0] if len(A) else []]
        d = d[d > ON_MM]
        return float(d.min()) if d.size else POS_STEP_MAX_MM / POS_STEP_ROOM

    def _crosses(self, n: str, q) -> bool:
        """Corner n's move from its design position to q passes over another node or knot, or crosses or touches an
        edge it must keep clear of (_others): the layout the fit is linearised about would change. Touching is a
        distance (q within ON_MM of such an edge): _meets' orientation signs see a point put onto a sloped outline
        edge by interpolation on either side at random (v3p3-review-e2e probe 4 seed 1874: the readings-only fit
        snapped the inside corner A onto the top outline, which a level edge reverts; the crown's edge A-H then lay
        outside the outline, and the declared crown|base valley "held" on a piece the builder never built,
        E_CREASE_SHAPE; _check_fitted_pairs is the net)."""
        p = np.asarray(self.nodes[n], np.float64)
        q = np.asarray(q, np.float64)
        pts, A, Bq = self._others(n)
        if len(pts) and float(_dists_to_segments(pts, p[None], q[None]).min()) <= ON_MM:
            return True
        if len(A) and float(_dists_to_segments(q[None], A, Bq).min()) <= ON_MM:
            return True
        return bool(len(A)) and bool(np.any(_meets(np.array([p]), np.array([q]), None, A, Bq)))

    def _knot_holds_another(self, n: str, knots: list) -> bool:
        """An outline edge from one of node n's knots holds another node (on it, not at a knot): moving the knot would
        take that node off the outline, so n is pinned."""
        ps = self.ps
        for ch, i in knots:
            arr = ps.top if ch == "top" else ps.bottom
            for j in (i - 1, i):
                if j < 0 or j + 1 >= len(arr):
                    continue
                a, b = arr[j], arr[j + 1]
                for m in self.names:
                    if m == n:
                        continue
                    q = np.asarray(self.nodes[m], np.float64)
                    if (min(np.hypot(*(q - a)), np.hypot(*(q - b))) > ON_MM
                            and _dists_to_segments(q[None], a[None], b[None])[0, 0] <= ON_MM):
                        return True
        return False

    def builder_point(self, Z, push: bool = False) -> tuple[np.ndarray, list]:
        """Z with its corner positions a fixed point of the builder's knot canonicalisation (spec.parse: a node or table
        knot d within snap_mm of an earlier value snaps onto it, outline knots first; a node within snap_mm of the
        outline edge onto it), so the builder builds the corners where the fit put them: a moved corner within snap_mm
        of an unmoved station (an outline, inner or base knot, another node's d) takes that d exactly (nothing snaps,
        and no table knot follows an outline knot that moved), moved corners within it of each other share the first
        one's d, an inside corner within snap_mm (vertically) of the outline takes its y; each on its free directions.
        `push`: a moved corner within snap_mm of a station goes to POS_PUSH_MM beyond it on its own side instead (where
        no other station is near), the other point the builder also reproduces: a declared fit holds both and keeps
        the cheaper (probe1's flat-border ridge: E placed just past the base station at d 75.64 lay 0.005 mm from the
        inner knot at 76.0, and taking that d put the fold's midpoint back across the station, cost 26 against 1.4).
        Every free corner is written into the fitted spec where Z puts it (its design's parsed position when it did
        not move), so a design snap between two free corners holds or lets go as their fitted positions say. A corner
        whose move would pass over another node or knot or cross an edge not its own (_crosses: the layout the fit is
        linearised about) keeps its design position. Checked
        by parsing the corners' spec (review 2026-10-12 phase 2: D2 moved to d 65.009 beside U2's 65.0 was snapped by
        the builder, E_FACET_NOT_PLANAR 3.7e-5 mm); a corner it still moves keeps its design position.
        Returns (Z, [corners reverted to their design position])."""
        Z = np.array(Z, np.float64)
        snap = float(self.ps.options.get("snap_mm", 0.0))
        if not self.M or snap <= 0.0:
            return Z, []
        reverted: list = []
        for _ in range(3):
            nodes, ps_ = self.positions(Z[self.N:])
            moved = [n for n in self.reads if self.cidx[n]]            # every free corner is written where it is
            fixd = {n for n in moved if float(self.Bn[n][0] @ self.Bn[n][0]) <= 1e-18} | set(reverted)  # its d stays
            st = np.r_[self._stations(moved), [nodes[n][0] for n in fixd]]
            target = {}
            for n in sorted(moved, key=lambda m: (m not in fixd, nodes[m][0], m)):
                d, y = nodes[n]
                near = st[np.abs(st - d) <= snap + 1e-12]
                if near.size and n not in fixd:
                    s0 = float(near[np.argmin(np.abs(near - d))])
                    out = s0 + math.copysign(snap + POS_PUSH_MM, d - s0)
                    clear = d != s0 and not np.any(np.abs(st - out) <= snap + 1e-12)
                    d = out if push and clear else s0
                if not self.knots[n] and self.Bn[n].shape[1] == 2 and ps_.d0 <= d <= ps_.d1:
                    yt, yb = float(ps_.y_top(d)), float(ps_.y_bot(d))
                    if abs(y - yt) <= snap and abs(y - yt) <= abs(y - yb):
                        y = yt
                    elif abs(y - yb) <= snap:
                        y = yb
                target[n] = (d, y)
                st = np.append(st, d)
            bad = []
            for n, q in target.items():
                Bm = self.Bn[n]
                dl = np.subtract(q, self.nodes[n])
                x = Bm.T @ dl
                if float(np.hypot(*(Bm @ x - dl))) > 1e-9 or (float(np.hypot(*dl)) > ON_MM and self._crosses(n, q)):
                    bad.append(n)
                else:
                    Z[[self.N + j for j in self.cidx[n]]] = x
            bad += self._snap_mismatch(Z, moved)
            if not bad:
                return Z, reverted
            new_bad = sorted(set(bad) - set(reverted))
            if not new_bad:                              # nothing left to revert: the design's own positions
                Z[self.N:] = 0.0
                return Z, sorted(set(reverted) | set(moved))
            for n in new_bad:
                Z[[self.N + j for j in self.cidx[n]]] = 0.0
                reverted.append(n)
        return Z, sorted(set(reverted))

    def _stations(self, moved: list) -> np.ndarray:
        """The d values parse snaps a node or knot onto, but those of the moved corners and their knots: outline,
        inner and base knots and the other nodes' d (the design's parse)."""
        s = self.ps.snapped
        own = {(ch, i) for n in moved for ch, i in self.knots[n]}
        raw = {(name, i): d for n in moved for name, i, d in self.tpart.get(n, [])}   # a partner: its own d
        vals = [p[0] for ch in ("top", "bottom") for i, p in enumerate(s["outline"][ch]) if (ch, i) not in own]
        vals += [raw.get(("inner", i), p[0]) for i, p in enumerate(s["inner"])]
        vals += [raw.get((f"base.{k}", i), p[0]) for k in ("top", "bottom") for i, p in enumerate(s["base"][k])]
        vals += [self.nodes[m][0] for m in self.names if m not in moved]
        return np.array(vals, np.float64)

    def _snap_mismatch(self, Z, moved: list) -> list:
        """The moved corners the builder's parse of the corners' spec would still place elsewhere (or every moved
        corner when it refuses or moves something else: an unmoved node, a table knot, the outline)."""
        nodes, ps_ = self.positions(Z[self.N:])
        trial = copy.deepcopy(self.probe)
        for n in moved:
            trial["nodes"][n][0], trial["nodes"][n][1] = nodes[n]
            for ch, i in self.knots[n]:
                trial["outline"][ch][i] = [nodes[n][0], nodes[n][1]]
        try:
            pt = parse(trial)
        except TempleError:
            return list(moved)
        bad = [n for n in moved if math.hypot(pt.nodes[n][0] - nodes[n][0], pt.nodes[n][1] - nodes[n][1]) > 1e-9]
        same = all(math.hypot(pt.nodes[m][0] - self.nodes[m][0], pt.nodes[m][1] - self.nodes[m][1]) <= 1e-9
                   for m in self.names if m not in moved)
        same = same and all(np.array_equal(getattr(pt, k).d, getattr(ps_, k).d) and np.array_equal(getattr(pt, k).v,
                                                                                                   getattr(ps_, k).v)
                            for k in ("inner", "t_top", "t_bot"))
        same = same and pt.top.shape == ps_.top.shape and pt.bottom.shape == ps_.bottom.shape and bool(
            np.allclose(pt.top, ps_.top, rtol=0.0, atol=1e-9)
            and np.allclose(pt.bottom, ps_.bottom, rtol=0.0, atol=1e-9))
        return bad if same else list(moved)

    def positions(self, delta) -> tuple[dict, object]:
        """(nodes, ps): the corners at delta (Z[N:], mm along each free direction from the design) and the outline
        with the knots that move with them."""
        nodes = dict(self.nodes)
        for j, c in enumerate(self.coords):
            if delta[j] != 0.0:
                q = np.asarray(nodes[c["node"]], np.float64) + float(delta[j]) * c["v"]
                nodes[c["node"]] = (float(q[0]), float(q[1]))
        return nodes, self._ps_at(nodes)

    def _ps_at(self, nodes: dict):
        """The parsed spec with every outline knot that moves with a corner at that corner's position in `nodes`, and
        the inner / base table knots the design's parse snapped onto such a knot where the builder's parse puts them
        then: on the knot within snap_mm of their own d, at their own d beyond."""
        moves = [(ch, i, nodes[n]) for n, ks in self.knots.items() for ch, i in ks if nodes[n] != self.nodes[n]]
        if not moves:
            return self.ps
        top, bot = self.ps.top.copy(), self.ps.bottom.copy()
        for ch, i, p in moves:
            (top if ch == "top" else bot)[i] = p
        tabs, snap = {}, float(self.ps.options.get("snap_mm", 0.0))
        for n, parts in self.tpart.items():
            if nodes[n] == self.nodes[n]:
                continue
            for name, i, raw in parts:
                pl = {"inner": self.ps.inner, "base.top": self.ps.t_top, "base.bottom": self.ps.t_bot}[name]
                a = tabs.setdefault(name, np.c_[pl.d, pl.v])
                a[i, 0] = nodes[n][0] if abs(raw - nodes[n][0]) <= snap else raw
        extra = {{"inner": "inner", "base.top": "t_top", "base.bottom": "t_bot"}[k]: PL(a, k) for k, a in tabs.items()}
        return dataclasses.replace(self.ps, top=top, bottom=bot, **extra)

    def set_point(self, Z, jac=None) -> float:
        """Linearise the problem at Z = (z, delta) (module docstring): the geometry at the corners' positions there
        (numbers), the position Jacobian (_jacobian; re-evaluated where a corner moved more than JAC_MOVE_SHARE x its
        step since, or `jac` = (derivatives, where) as a solution recorded it: its angles' sds use the Jacobian its
        covariance was solved with), the system over Z (_extend). Returns the largest corner move from the previous
        point (mm)."""
        if jac is not None:
            self.jac, self.jac_at = jac
        Z = np.asarray(Z, np.float64)
        dk = Z[self.N:self.NZ].copy()
        step = float(np.max(np.abs(dk - self.delta))) if self.M else 0.0
        if self.geo is None or step > 0.0:
            self.delta = dk
            self.cur_nodes, self.cur_ps = self.positions(dk)
            self.Pcur = {l: np.array([self.cur_nodes[n] for n in self.poly[l]], np.float64) for l in self.labels}
            self._thin_cache = {}
            self.geo = self.numbers(self.cur_nodes, self.cur_ps)
            self.geo["forms"] = self.slope_forms(self.geo["segs"])
            self.bound_at = [self._describe(dfn, x) for dfn, x in zip(self.bound_def, self.geo["pts"])]
        self.zk = Z[:self.N].copy()
        if self.M and jac is None and (self.jac is None or np.any(np.abs(self.delta - self.jac_at) > JAC_MOVE_SHARE
                                                                   * np.array([c["h"] for c in self.coords]))):
            self._jacobian()
        self._extend()
        return step

    def _jacobian(self) -> None:
        """Per free direction j, the derivatives of C, e, G, h and the slope forms at the point (N columns): a secant
        over +- its step h (POS_STEP_SHARE x the reading's sd there: a base station's kink is averaged over the
        reading's own spread, not met as a jump); moved() where no outline knot moves with the corner, numbers() with
        the moved outline otherwise."""
        out = []
        for c in self.coords:
            n, v, h = c["node"], c["v"], c["h"]
            ev = []
            for s in (h, -h):
                q = np.asarray(self.cur_nodes[n], np.float64) + s * v
                p = (float(q[0]), float(q[1]))
                if self.knots[n]:
                    nodes = dict(self.cur_nodes)
                    nodes[n] = p
                    m = self.numbers(nodes, self._ps_at(nodes))
                    ev.append((m["C"], m["e"], m["G"], m["h"], self.slope_forms(m["segs"])))
                else:
                    m = self.moved(n, p)
                    ev.append((m["C"], m["e"], m["G"], m["h"], m["forms"]))
            (Cp, ep, Gp, hp, fp), (Cm, em, Gm, hm, fm) = ev
            out.append({"C": (Cp - Cm) / (2 * h), "e": (ep - em) / (2 * h), "G": (Gp - Gm) / (2 * h),
                        "h": (hp - hm) / (2 * h), "forms": tuple((a - b) / (2 * h) for a, b in zip(fp, fm))})
        self.jac, self.jac_at = out, self.delta.copy()

    def _extend(self) -> None:
        """The system over Z = (z, delta) at the point: C, e, G, h, the slope forms and the segments with a column per
        free direction (D[:, j] = dR_j z_k - dr_j: a row R z >= r moved to R z + D (delta - delta_k) >= r), so every
        solve and every angle sees the corners' positions; the geometry's own columns are the point's."""
        g, N, M = self.geo, self.N, self.M
        zk, dk = self.zk, self.delta

        def cols(key, rhs, rows):
            if not M or not rows:
                return np.zeros((rows, M))
            out = np.zeros((rows, M))
            for j, J in enumerate(self.jac):             # (cut rows joined after J was taken: derivative 0)
                v = J[key] @ zk - J[rhs]
                out[:len(v), j] = v[:rows]
            return out
        DC = cols("C", "e", len(g["e"]))
        DG = cols("G", "h", len(g["h"]))
        self.C, self.e = np.hstack([g["C"], DC]), g["e"] + DC @ dk
        self.G, self.h = np.hstack([g["G"], DG]), g["h"] + DG @ dk
        A1, k1, A2, k2 = g["forms"]
        S = len(k1)
        if M and S:
            F1 = np.column_stack([J["forms"][0] @ zk + J["forms"][1] for J in self.jac]).reshape(S, M)
            F2 = np.column_stack([J["forms"][2] @ zk + J["forms"][3] for J in self.jac]).reshape(S, M)
        else:
            F1 = F2 = np.zeros((S, M))
        A1e, A2e = np.hstack([A1, F1]), np.hstack([A2, F2])
        k1e, k2e = k1 - F1 @ dk, k2 - F2 @ dk
        self.forms = (A1e, k1e, A2e, k2e)
        pad = np.zeros((2, M))
        self.seg_list = [dataclasses.replace(s, a1=A1e[i], k1=float(k1e[i]), a2=A2e[i], k2=float(k2e[i]),
                                             G1=np.hstack([s.G1, pad]), G2=np.hstack([s.G2, pad]))
                         for i, s in enumerate(g["segs"])]
        self.segs: dict = {}
        for s in self.seg_list:
            self.segs.setdefault(s.pair, []).append(s)
        self.eq_tags = self.eq_def

    @property
    def Cs(self) -> np.ndarray:
        """The equalities of a solve: C, and with the positions pinned delta = delta_k (exact at the point)."""
        if not (self.pinned and self.M):
            return self.C
        return np.vstack([self.C, np.c_[np.zeros((self.M, self.N)), np.eye(self.M)]])

    @property
    def es(self) -> np.ndarray:
        return self.e if not (self.pinned and self.M) else np.r_[self.e, self.delta]

    # ----------------------------------------------------------------------------------------- observations
    def _obs_system(self) -> None:
        """The observation rows over Z (module docstring): A, b, weak, obs {node: (X, sd)} (a read corner's X
        carried to its design position and its conditional sd), rows_of {node: [rows]} (a read corner's X row
        first, then its position rows), xrow, acam {node: {fit: its X share per sd}}, sig {node: sd}. A read corner:
        o = T r (r its (d, y, X), T keeps its free directions B'(d, y) and carries its X along the pinned ones, X -
        g_W . (d, y), g_W the faces' gradient there projected on them); S = T C T' (C its pick covariance); the
        position rows L^-1 (B'(p - p_read) + delta + sum_f u_f B's_f), L L' = S_pp; the X row (xi - K pi) / s_c,
        K = S_pp^-1 S_pX, s_c^2 = S_XX - S_Xp K: the joint (d, y, X) residual, in rows."""
        N, NZ = self.N, self.NZ
        m, F = len(self.obs_def), len(self.fits)
        npos = sum(self.Bn[n].shape[1] for n in self.reads)
        A, b = np.zeros((m + F + npos, NZ)), np.zeros(m + F + npos)
        weak = np.zeros(m + F + npos, bool)
        obs, rows_of, xrow, acam, sig, lever = {}, {}, {}, {}, {}, {}
        q = m + F
        ps = self.ps
        for r, (i, n) in enumerate(self.obs_def):
            d, y = self.nodes[n]
            s = self.src[n]
            if s[0] == "read":
                Xr, sd, rec = float(s[1]), float(s[2]), s[3]
                Cv, Bm, Wm = self.read_cov[n], self.Bn[n], self.Wn[n]
                k = Bm.shape[1]
                pr = np.array([float(rec["d"]), float(rec["y"])]) if "d" in rec and "y" in rec else np.array([d, y])
                dl = np.array([d, y]) - pr                       # design minus where it was read
                gW = Wm @ (Wm.T @ self.gpin[n])
                T = np.zeros((k + 1, 3))
                T[:k, :2], T[k, :2], T[k, 2] = Bm.T, -gW, 1.0
                Sg = T @ Cv @ T.T
                cols = [N + j for j in self.cidx[n]]
                if k:
                    Li = np.linalg.inv(np.linalg.cholesky(Sg[:k, :k]))
                    K = np.linalg.solve(Sg[:k, :k], Sg[:k, k])
                    sc2 = float(Sg[k, k] - Sg[k, :k] @ K)
                else:
                    Li, K, sc2 = np.zeros((0, 0)), np.zeros(0), float(Sg[0, 0])
                sc = max(math.sqrt(max(sc2, 0.0)), SIG_FLOOR * sd)
                Xo = Xr + float(gW @ dl) + float(K @ (Bm.T @ dl))
                A[r, i] = 1.0 / sc
                A[r, cols] = -K / sc
                b[r] = Xo / sc
                acam[n] = {}
                for f, sv in self.cam[n].items():
                    a = float(sv[2] - gW @ sv[:2])
                    acam[n][f] = a
                    A[r, self.ucol[f]] = (a - float(K @ (Bm.T @ sv[:2]))) / sc
                    if k:
                        A[q:q + k, self.ucol[f]] = Li @ (Bm.T @ sv[:2])
                if k:
                    A[q:q + k, cols] = Li
                    b[q:q + k] = -Li @ (Bm.T @ dl)
                rows_of[n], xrow[n] = [r] + list(range(q, q + k)), r
                q += k
                obs[n], sig[n] = (Xo, sc), sc
                pin = Wm.T @ Cv[:2, :2] @ Wm
                lever[n] = (math.sqrt(max(float(np.linalg.eigvalsh(pin).max()), 0.0)) if Wm.shape[1] else 0.0) + float(
                    np.abs(Wm.T @ dl).sum()) + sum(float(np.abs(Wm.T @ sv[:2]).sum()) for sv in self.cam[n].values())
                lever[n] /= sd
            elif s[0] == "typed":
                X = float(ps.inner(d)) + float(s[1])
                A[r, i], b[r], obs[n] = 1.0 / TYPED_SD_MM, X / TYPED_SD_MM, (X, TYPED_SD_MM)
                rows_of[n] = [r]
            else:
                X = float(ps.x_base(d, y))
                A[r, i], b[r], obs[n] = 1.0 / PRIOR_SD_MM, X / PRIOR_SD_MM, (X, PRIOR_SD_MM)
                weak[r] = True
                rows_of[n] = [r]
        for k, f in enumerate(self.fits):                # a camera fit's theta turn: a unit prior (its sd)
            A[m + k, self.ucol[f]] = 1.0
        for n in self.names:
            if self.src[n][0] == "base":
                obs[n] = (float(ps.x_base(*self.nodes[n])), 0.0)
        self.A, self.b, self.weak, self.obs = A, b, weak, obs
        self.rows_of, self.xrow, self.acam, self.sig, self.lever = rows_of, xrow, acam, sig, lever

    def slope_forms(self, segs: list) -> tuple:
        """(A1, k1, A2, k2): the segments' slopes g1 = A1 z + k1, g2 = A2 z + k2 as matrices (N columns: the
        geometry's own; _extend adds the position columns)."""
        shp = (len(segs), self.N)                        # explicit: with no facets N is 0 (review F1)
        return (np.array([s.a1[:self.N] for s in segs]).reshape(shp), np.array([s.k1 for s in segs]),
                np.array([s.a2[:self.N] for s in segs]).reshape(shp), np.array([s.k2 for s in segs]))

    def _touch(self, n: str) -> tuple:
        """(observation rows, equality rows, bound rows, segments) whose numbers depend on node n's position (cached;
        moved() recomputes only these)."""
        if n in self._touch_cache:
            return self._touch_cache[n]

        def ends(label, k):
            poly = self.poly[label]
            return n in (poly[k], poly[(k + 1) % len(poly)])

        def warp_of(label):
            return label != "base" and self.mode[label] == "warp" and n in self.poly[label]
        ob = [r for r, (_, m) in enumerate(self.obs_def) if m == n and self.src[n][0] != "read"]
        eq = [r for r, tag in enumerate(self.eq_def)
              if (tag[0] == "corner" and tag[2] == n) or (tag[0] == "rise" and n in (tag[2], tag[3]))
              or (tag[0] == "base" and tag[1] == n)
              or (tag[0] == "tee" and (tag[1] == n or (warp_of(tag[2]) and ends(tag[2], tag[3]))))]
        bd = [r for r, dfn in enumerate(self.bound_def)
              if (dfn[0] == "node" and dfn[1] == n) or (dfn[0] == "edge" and ends(dfn[1], dfn[2]))
              or (dfn[0] in ("pt", "in") and warp_of(dfn[1]))]
        sg = [i for i, (F, nb, k, *_r) in enumerate(self.seg_def) if ends(F, k) or warp_of(F) or warp_of(nb)]
        self._touch_cache[n] = (ob, eq, bd, sg)
        return self._touch_cache[n]

    def moved(self, n: str, p) -> dict:
        """The point's geometry (numbers() there: A, b, C, e, G, h and the slope forms, N columns) with node n at p,
        the structure and the outline kept: the rows _touch names recomputed, the rest the point's (numbers() with n
        moved, at a fraction of its cost; tested equal). The position Jacobian's primitive for a corner no outline
        knot moves with (_jacobian)."""
        g = self.geo
        nodes = dict(self.cur_nodes)
        nodes[n] = (float(p[0]), float(p[1]))
        P = {l: np.array([nodes[m] for m in self.poly[l]], np.float64) for l in self.labels}
        ob, eq, bd, sg = self._touch(n)
        ps = self.cur_ps
        b = g["b"].copy()
        for r in ob:                                     # a typed value's x_in(d), a prior's base surface
            d, y = nodes[n]
            X = float(ps.inner(d)) + float(self.src[n][1]) if self.src[n][0] == "typed" else float(ps.x_base(d, y))
            b[r] = X * g["A"][r, self.obs_def[r][0]]
        C, e = g["C"].copy(), g["e"].copy()
        for r in eq:
            tag = self.eq_def[r]
            if tag[0] == "corner":
                C[r] = -self.X_form(tag[1], nodes[tag[2]], None, P)
                C[r, self.cix(tag[1], tag[2])] += 1.0
            elif tag[0] == "rise":
                _, n_in, _ = crease_frame(nodes[tag[2]], nodes[tag[3]], tag[4])
                c = self.col[tag[1]]
                C[r, c + 1], C[r, c + 2] = n_in
            elif tag[0] == "base":
                e[r] = float(ps.x_base(*nodes[tag[1]]))
            else:
                C[r] = -self.X_form(tag[2], nodes[tag[1]], tag[3], P)
                C[r, self.ix[tag[1]]] += 1.0
        G, h = g["G"].copy(), g["h"].copy()
        if bd:
            Gr, pts = self._bound_rows([self.bound_def[r] for r in bd], nodes, P)
            G[bd], h[bd] = Gr, self._bound_h(pts, ps)
        A1, k1, A2, k2 = (x.copy() for x in g["forms"])
        for i, s_ in zip(sg, self._segs(P, sg, ps)):
            A1[i], k1[i], A2[i], k2[i] = s_.a1, s_.k1, s_.a2, s_.k2
        return {"A": g["A"], "b": b, "C": C, "e": e, "G": G, "h": h, "forms": (A1, k1, A2, k2)}

    def add_bounds(self, defs: list) -> None:
        """Join cutting-plane bound points to the problem (every later solve and numbers() keep them; their position
        derivatives are taken as zero until the Jacobian is next evaluated)."""
        G, pts = self._bound_rows(defs, self.cur_nodes, self.Pcur)
        self.bound_def = self.bound_def + list(defs)
        g = self.geo
        g["G"], g["h"] = np.vstack([g["G"], G]), np.r_[g["h"], self._bound_h(pts, self.cur_ps)]
        g["pts"] = np.vstack([g["pts"], pts])
        for J in self.jac or []:
            J["G"], J["h"] = np.vstack([J["G"], np.zeros((len(defs), self.N))]), np.r_[J["h"], np.zeros(len(defs))]
        self.bound_at += [self._describe(dfn, x) for dfn, x in zip(defs, pts)]
        self._extend()

    @staticmethod
    def _describe(dfn: tuple, x) -> tuple:
        at = [float(x[0]), float(x[1])]
        return {"node": ("node", dfn[1]), "edge": ("edge", dfn[1], at), "pt": ("outline", dfn[1], at),
                "in": ("inside", dfn[1], at)}[dfn[0]]

    # ----------------------------------------------------------------------------------------- pieces
    def cix(self, label: str, name: str) -> int:
        """The column of a facet's corner: its group's copy of a split node, else the node's."""
        return self.kix[(name, self.group[(name, label)])] if name in self.split else self.ix[name]

    def X_form(self, label: str, p, edge, P: dict) -> np.ndarray:
        """Row r with X_label(p) = r . z: a flat facet's plane anywhere; a warp's linear interpolation on its edge
        `edge`, its mean value coordinates elsewhere (P: the polygons at the positions in use)."""
        r = np.zeros(self.N)
        if label in self.col:
            c = self.col[label]
            r[c:c + 3] = [1.0, p[0] - self.centre[label][0], p[1] - self.centre[label][1]]
            return r
        Pl, poly = P[label], self.poly[label]
        if edge is None:
            for j, w in enumerate(_mvc_rows(Pl, p)[0]):
                r[self.cix(label, poly[j])] += w
            return r
        a, b = Pl[edge], Pl[(edge + 1) % len(Pl)]
        s = float((np.asarray(p) - a) @ (b - a) / ((b - a) @ (b - a)))
        r[self.cix(label, poly[edge])] += 1.0 - s
        r[self.cix(label, poly[(edge + 1) % len(Pl)])] += s
        return r

    def grad_form(self, label: str, m, edge: int, P: dict) -> np.ndarray:
        """(2, N): grad X_label(m) = G . z at the point m on the label's edge, from inside the facet."""
        Gm = np.zeros((2, self.N))
        if label in self.col:
            c = self.col[label]
            Gm[0, c + 1] = Gm[1, c + 2] = 1.0
            return Gm
        G = mvc_edge_gradient(P[label], edge, m)
        for j, name in enumerate(self.poly[label]):
            Gm[:, self.cix(label, name)] += G[:, j]
        return Gm

    def base_grad(self, M, ps=None) -> np.ndarray:
        """(m, 2) gradients of the base surface (ps's outline, default the point's) at the points M (m, 2): central
        differences, h 1e-3 (as the builder's region_grad), in one vectorised pass."""
        M = np.asarray(M, np.float64).reshape(-1, 2)
        h = 1e-3
        d, y = M[:, 0], M[:, 1]
        xb = (self.cur_ps if ps is None else ps).x_base
        return np.stack([(xb(d + h, y) - xb(d - h, y)) / (2 * h), (xb(d, y + h) - xb(d, y - h)) / (2 * h)], axis=1)

    def x_in(self, d) -> float:
        return float(self.cur_ps.inner(d))

    def edge_hits(self, p) -> dict:
        """{label: its first edge whose segment holds p (within ON_MM)} over every facet (one vectorised pass)."""
        s = self.edge_b - self.edge_a
        L2 = np.maximum(np.einsum("ij,ij->i", s, s), 1e-300)
        t = np.clip(np.einsum("ij,ij->i", np.asarray(p, np.float64) - self.edge_a, s) / L2, 0.0, 1.0)
        dist = np.hypot(*(self.edge_a + t[:, None] * s - p).T)
        out: dict = {}
        for j in np.nonzero(dist <= ON_MM)[0]:
            out.setdefault(self.edge_ref[j][0], self.edge_ref[j][1])
        return out

    def on_edge(self, label: str, p) -> int | None:
        """The edge of the facet whose segment holds p (within ON_MM), or None."""
        return self.edge_hits(p).get(label)

    def _edge_of(self, label: str, x) -> int:
        """The facet's edge nearest the point."""
        P = self.P[label]
        return int(np.argmin(_dists_to_segments(np.asarray(x, np.float64)[None], P, np.roll(P, -1, axis=0))[0]))

    # ----------------------------------------------------------------------------------------- reading weights
    def _directions(self) -> None:
        """Per read corner the faces whose surface passes through it (the facets listing it and those it lies on as a
        T-junction): self.adj {node: [facets]}."""
        tees: dict = {}
        for tag in self.eq_def:
            if tag[0] == "tee":
                tees.setdefault(tag[1], set()).add(tag[2])
        self.adj = {n: sorted(set(self.of[n]) | tees.get(n, set())) for n in self.reads}

    @property
    def weighted(self) -> bool:
        """The read rows depend on the solution: a corner pinned in a direction its reading has a position error, an
        offset or a camera move in (its X is carried along the faces there, by their gradient)."""
        return any(self.lever[n] > 0.0 for n in self.reads)

    def region_grad_form(self, n: str, region: str) -> np.ndarray:
        """G (2, N): the gradient G z at node n of a face through it (cached): a flat facet's plane; at a warp's corner
        the mean of its two edges' MVC gradients CORNER_NEAR_MM along them, where n lies on a warp's edge its edge
        gradient there (mvc_edge_gradient: the slope along the edge and the interior's across it)."""
        key = (n, region)
        if key in self._grad_cache:
            return self._grad_cache[key]
        p = np.asarray(self.nodes[n], np.float64)
        G = np.zeros((2, self.N))
        if region in self.col:
            G = self.grad_form(region, p, None, self.P)
        else:
            poly, P = self.poly[region], self.P[region]
            if n in poly:                              # the mean of its two edges' MVC gradients next to the corner
                i = poly.index(n)
                k0 = (i - 1) % len(poly)
                for k, a, b in ((i, P[i], P[(i + 1) % len(poly)]), (k0, P[i], P[k0])):
                    f = min(CORNER_NEAR_MM / float(np.hypot(*(b - a))), 0.5)
                    G = G + 0.5 * self.grad_form(region, a + f * (b - a), k, self.P)
            else:
                k = self.on_edge(region, p)
                G = self.grad_form(region, p, self._edge_of(region, p) if k is None else k, self.P)
        self._grad_cache[key] = G
        return G

    def face_grads(self, n: str, z) -> list:
        """The gradients at read corner n of the faces through it (self.adj), at the solution z."""
        return [self.region_grad_form(n, f) @ np.asarray(z)[:self.N] for f in self.adj[n]]

    def pinned_grads(self, z) -> dict:
        """{node: g} for the read corners whose rows depend on it (lever > 0): the mean gradient of the faces through
        the corner at z (a face through a corner held where it is tilts as if its X were off by e_X - g . e_dy along
        the pinned directions; its camera coefficient dX - g_W . (dd, dy))."""
        out = {}
        for n in self.reads:
            if self.lever[n] > 0.0:
                g = self.face_grads(n, z)
                out[n] = np.mean(g, axis=0) if g else np.zeros(2)
        return out

    def grad_residual(self, target: dict) -> float:
        """How far the pinned gradients are from `target`, as the largest move of an X row it makes, in its reading's
        X sd (the gradient change times the corner's lever: its pinned position sd, offset and camera moves)."""
        return max((float(np.max(np.abs(target[n] - self.gpin[n]))) * self.lever[n] for n in target), default=0.0)

    def set_grads(self, g: dict) -> None:
        """Set the pinned gradients and rebuild the observation rows (_obs_system)."""
        for n, v in g.items():
            self.gpin[n] = np.asarray(v, np.float64).copy()
        self._obs_system()

    def camera_mm(self, n: str, z) -> float:
        """The fitted camera turns' share of read corner n's X: sum over its fits of the coefficient x the turn."""
        return float(sum(a * z[self.ucol[f]] for f, a in self.acam.get(n, {}).items()))

    # ----------------------------------------------------------------------------------------- warp interiors
    def _thin_grid(self, label: str) -> np.ndarray:
        """A warp's THIN_GRID_MM grid points inside its polygon and the outline (cached)."""
        if label not in self._thin_cache:
            P, g = self.Pcur[label], THIN_GRID_MM
            lo, hi = P.min(axis=0), P.max(axis=0)
            Q = np.stack(np.meshgrid(np.arange(lo[0] + g / 2, hi[0], g), np.arange(lo[1] + g / 2, hi[1], g)),
                         axis=-1).reshape(-1, 2)
            if len(Q):
                Q = Q[points_in_poly(Q, P)]
            if len(Q):
                Q = Q[points_in_poly(Q, self.boundary)]
            self._thin_cache[label] = Q
        return self._thin_cache[label]

    def _thin_lines(self, label: str) -> list:
        """A warp's outline segments inside it and event lines across it [(a, b, s)], sampled at THIN_GRID_MM / 2
        (cached; only needed where the warp comes near its bound)."""
        key = ("lines", label)
        if key in self._thin_cache:
            return self._thin_cache[key]
        P, B, g = self.Pcur[label], self.boundary, THIN_GRID_MM
        lo, hi = P.min(axis=0), P.max(axis=0)
        segs = []
        Bq = np.roll(B, -1, axis=0)
        near = np.nonzero((np.maximum(B[:, 0], Bq[:, 0]) >= lo[0]) & (np.minimum(B[:, 0], Bq[:, 0]) <= hi[0])
                          & (np.maximum(B[:, 1], Bq[:, 1]) >= lo[1]) & (np.minimum(B[:, 1], Bq[:, 1]) <= hi[1]))[0]
        for i in near:
            a, b = B[i], Bq[i]
            segs += [(a + s0 * (b - a), a + s1 * (b - a)) for s0, s1 in clip_segment(a, b, P)]
        ev = self.events
        for x in ev[(ev > lo[0]) & (ev < hi[0])]:
            a, b = np.array([x, lo[1] - 1.0]), np.array([x, hi[1] + 1.0])
            for s0, s1 in clip_segment(a, b, P):
                a2, b2 = a + s0 * (b - a), a + s1 * (b - a)
                segs += [(a2 + t0 * (b2 - a2), a2 + t1 * (b2 - a2)) for t0, t1 in clip_segment(a2, b2, B)]
        lines = [(a, b, np.linspace(0.0, 1.0, max(2, int(math.ceil(float(np.hypot(*(b - a))) / (g / 2))) + 1)))
                 for a, b in segs]
        self._thin_cache[key] = lines
        return lines

    def thin_points(self, z) -> list:
        """Cutting planes for the warps' interiors (module docstring): [("in", label, (d, y), None)] where the fitted
        warp, polished to a local minimum of its thickness margin, lies below its bound by THIN_TOL_MM."""
        out = []
        B = self.boundary
        for label in self.warps:
            P = self.Pcur[label]
            F = np.array([z[self.cix(label, n)] for n in self.poly[label]])
            Q = self._thin_grid(label)

            def f(X, P=P, F=F):
                X = np.asarray(X, np.float64).reshape(-1, 2)
                return mvc(P, F, X[:, 0], X[:, 1]) - self._bound_h(X)
            found = []
            if len(Q):
                v = f(Q)
                if float(v.min()) > THIN_SKIP_MM:
                    continue                             # far above the bound everywhere (run 7's crown: 5-7 mm)
                order = np.argsort(v, kind="stable")
                seeds: list = []
                for i in order:
                    if v[i] >= THIN_CHECK_MM or len(seeds) >= 3:
                        break
                    if all(float(np.hypot(*(Q[i] - Q[j]))) >= 2.0 * THIN_GRID_MM for j in seeds):
                        seeds.append(int(i))
                found += [_newton_min(f, Q[i], THIN_GRID_MM,
                                      lambda x, P=P: bool(points_in_poly(x[None], P)[0]
                                                          and points_in_poly(x[None], B)[0])) for i in seeds]
                found += [(Q[i], float(v[i])) for i in order[:THIN_SAMPLE_CUTS] if v[i] < -THIN_TOL_MM]
            for a, b, s in self._thin_lines(label):
                X = a + s[:, None] * (b - a)
                v = f(X)
                i = int(np.argmin(v))
                if v[i] >= THIN_CHECK_MM:
                    continue

                def g(t, a=a, b=b, f=f):
                    return float(f(a + t * (b - a))[0])
                t = min((_golden(g, float(s[max(i - 1, 0)]), float(s[min(i + 1, len(s) - 1)])), float(s[i])), key=g)
                found.append((a + t * (b - a), g(t)))
                found += [(X[j], float(v[j])) for j in np.argsort(v, kind="stable")[:THIN_SAMPLE_CUTS // 4]
                          if v[j] < -THIN_TOL_MM]
            kept = np.zeros((0, 2))
            for x, fx in found:                          # one cut per point
                if fx < -THIN_TOL_MM and (not len(kept) or float(np.min(np.hypot(*(kept - x).T))) > 1e-4):
                    out.append(("in", label, (float(x[0]), float(x[1])), None))
                    kept = np.vstack([kept, x])
        return out


def _meet(Q: np.ndarray, u: np.ndarray) -> np.ndarray:
    """The intersection of span(Q) (an orthonormal (2, k) basis) with the line span(u): (2, k') with k' <= 1."""
    if Q.shape[1] == 0:
        return Q
    if Q.shape[1] == 2:
        return np.asarray(u, np.float64).reshape(2, 1)
    return Q if abs(float(Q[:, 0] @ u)) >= 1.0 - 1e-9 else np.zeros((2, 0))


def _dists_to_segments(X, A, B) -> np.ndarray:
    """(m, e) distances of the points X (m, 2) to the segments A -> B (e, 2)."""
    X = np.asarray(X, np.float64).reshape(-1, 2)
    s = B - A
    L2 = np.maximum(np.einsum("ij,ij->i", s, s), 1e-300)
    t = np.clip(np.einsum("mej,ej->me", X[:, None, :] - A[None], s) / L2[None], 0.0, 1.0)
    D = A[None] + t[..., None] * s[None] - X[:, None, :]
    return np.hypot(D[..., 0], D[..., 1])


def _meets(A, B, P, C=None, D=None) -> np.ndarray:
    """(e,) the segments A -> B that touch or cross an edge of the closed polygon P, or (P None) one of the segments
    C -> D (orientation signs, touching counts: conservative)."""
    if P is not None:
        C, D = np.asarray(P, np.float64), np.roll(np.asarray(P, np.float64), -1, axis=0)
    C, D = np.asarray(C, np.float64).reshape(-1, 2), np.asarray(D, np.float64).reshape(-1, 2)

    def orient(p, q, r):                             # sign of (q - p) x (r - p) over (e, c)
        return np.sign((q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0]
                                                                                                    - p[..., 0]))
    a, b, c, d = A[:, None], B[:, None], C[None], D[None]
    return np.any((orient(a, b, c) * orient(a, b, d) <= 0) & (orient(c, d, a) * orient(c, d, b) <= 0), axis=1)


def _dists_to_edges(X, P) -> np.ndarray:
    """(m,) distances of the points X to the closed polygon P's edges."""
    A = np.asarray(P, np.float64)
    return _dists_to_segments(X, A, np.roll(A, -1, axis=0)).min(axis=1)


def _golden(f, a: float, b: float, iters: int = 90) -> float:
    """The minimum of f on [a, b] by golden-section search (f unimodal there)."""
    g = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iters):
        if b - a <= 1e-12:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return 0.5 * (a + b)


def _newton_min(f, x0, step: float, inside) -> tuple[np.ndarray, float]:
    """A local minimum of the smooth f near x0: Newton on a 9-point stencil (h 1e-4), a gradient step where the
    Hessian is not positive definite; steps <= `step`, halved until they stay `inside` and descend."""
    x = np.asarray(x0, np.float64)
    fx = float(f(x[None])[0])
    h = 1e-4
    for _ in range(40):
        v = f(x + h * _STENCIL)
        g = np.array([v[1] - v[2], v[3] - v[4]]) / (2.0 * h)
        gn = float(np.hypot(*g))
        if gn <= 1e-12:
            break
        hxy = (v[5] - v[6] - v[7] + v[8]) / 4.0
        H = np.array([[v[1] - 2.0 * v[0] + v[2], hxy], [hxy, v[3] - 2.0 * v[0] + v[4]]]) / (h * h)
        ev = np.linalg.eigvalsh(H)
        dx = -np.linalg.solve(H, g) if ev[0] > 1e-9 * max(1.0, abs(float(ev[1]))) else -g / gn * step
        L = float(np.hypot(*dx))
        if L > step:
            dx, L = dx * (step / L), step
        t, moved = 1.0, False
        while t * L > 1e-12:
            xn = x + t * dx
            if inside(xn):
                fn = float(f(xn[None])[0])
                if fn < fx:
                    x, fx, moved = xn, fn, True
                    break
            t *= 0.5
        if not moved or t * L < 1e-11:
            break
    return x, fx


# --------------------------------------------------------------------------------------------- the fit
def _normal_deg(seg: _Seg, z: np.ndarray) -> float:
    """The dihedral (deg, unsigned) at the segment: the angle between the two sides' design normals (-grad X, 1) with
    the slope along the edge shared, F2's taking F1's (a no-op between faces that share the edge; at a facet|base border
    the builder's rule, build._base_border_design: the base's along-border slope is the step changing along the
    border, not a fold; run 7, crown flat: shoulder|base fold +2.9, dihedral 2.6, the raw normals 19.2 apart). Across
    an edge it is the fold up to the slope s along it (about fold / sqrt(1 + s^2))."""
    g1, g2 = seg.G1 @ z + seg.K1, seg.G2 @ z + seg.K2
    t = np.array([-seg.n[1], seg.n[0]])
    g2 = g2 + float((g1 - g2) @ t) * t
    a1, b1 = (float(v) for v in -g1)
    a2, b2 = (float(v) for v in -g2)
    cr = math.sqrt((b1 - b2) ** 2 + (a2 - a1) ** 2 + (a1 * b2 - b1 * a2) ** 2)          # |(a1, b1, 1) x (a2, b2, 1)|
    return math.degrees(math.atan2(cr, a1 * a2 + b1 * b2 + 1.0))


def _angle(seg: _Seg, z: np.ndarray, cov: np.ndarray | None = None) -> tuple[float, float]:
    """(deg, sd deg) of atan(g1) - atan(g2) at the segment, sd by linearisation through cov."""
    g1, g2 = float(seg.a1 @ z + seg.k1), float(seg.a2 @ z + seg.k2)
    deg = math.degrees(math.atan(g1) - math.atan(g2))
    if cov is None:
        return deg, 0.0
    J = seg.a1 / (1.0 + g1 * g1) - seg.a2 / (1.0 + g2 * g2)
    return deg, math.degrees(math.sqrt(max(float(J @ cov @ J), 0.0)))


def _shape_rows(pb: _Problem, pairs: list, z: np.ndarray) -> list:
    """[(terms, row, bound, key)]: the linearised constraints row . z >= bound of the declared shapes `pairs` [(pair,
    shape)] about the slopes of z (module docstring): per shared piece its midpoint's fold and its mean dihedral;
    terms [(segment index, c)] with row . z = sum c (g1 - g2) + constant; key settles the passes."""
    out = []
    tm, tf = math.tan(math.radians(MIN_CREASE_DEG)), math.tan(math.radians(FLAT_CREASE_DEG + MEAN_MARGIN_DEG))
    for pair, shape in pairs:
        sign = 1.0 if shape == "ridge" else -1.0
        pieces: dict = {}
        for seg in pb.segs.get(pair, []):
            pieces.setdefault(seg.piece, []).append(seg)
        for segs in pieces.values():
            mid = next(x for x in segs if x.frac == 0.5)
            a, b = float(mid.a1 @ z + mid.k1), float(mid.a2 @ z + mid.k2)
            m = 0.5 * (a + b)
            out.append(([(mid.index, sign)], sign * (mid.a1 - mid.a2), tm * (1.0 + m * m) - sign * (mid.k1 - mid.k2),
                        m))
            w = EDGE_W if len(segs) == len(EDGE_AT) else (1.0,)
            row, const, terms = np.zeros(pb.NZ), 0.0, []
            for seg, wi in zip(segs, w):
                a, b = float(seg.a1 @ z + seg.k1), float(seg.a2 @ z + seg.k2)
                t = np.array([-seg.n[1], seg.n[0]])
                sa = float(t @ (seg.G1 @ z + seg.K1))
                c = sign * wi * math.sqrt(1.0 + sa * sa) / (1.0 + sa * sa + a * b)
                row = row + c * (seg.a1 - seg.a2)
                const += c * (seg.k1 - seg.k2)
                terms.append((seg.index, c))
            out.append((terms, row, tf - const, sum(c for _, c in terms)))
    return out


def _piece_holds(pb: _Problem, pair: tuple, shape: str, z: np.ndarray) -> list:
    """The pieces of a declared pair that do not hold exactly at z: the midpoint's fold >= MIN_CREASE_DEG with the
    declared sign, the EDGE_W mean of tan(signed dihedral) >= tan(FLAT_CREASE_DEG + MEAN_MARGIN_DEG) (1e-6 deg)."""
    sign = 1.0 if shape == "ridge" else -1.0
    tf = math.tan(math.radians(FLAT_CREASE_DEG + MEAN_MARGIN_DEG - 1e-6))
    pieces: dict = {}
    for seg in pb.segs.get(pair, []):
        pieces.setdefault(seg.piece, []).append(seg)
    bad = []
    for k, segs in pieces.items():
        mid = next(x for x in segs if x.frac == 0.5)
        w = EDGE_W if len(segs) == len(EDGE_AT) else (1.0,)
        mean = sum(wi * math.copysign(math.tan(math.radians(_normal_deg(x, z))), sign * _angle(x, z)[0])
                   for x, wi in zip(segs, w))
        if sign * _angle(mid, z)[0] < MIN_CREASE_DEG - 1e-6 or mean < tf:
            bad.append(k)
    return bad


def _solve(pb: _Problem, Ge=None, he=None) -> Lsq:
    """solve_lsq with the problem's bounds (and the rows Ge z >= he after them), then the warps' cutting planes
    (module docstring): their rows join the problem's bounds for every later solve."""
    new: list = []
    for _ in range(THIN_PASSES):
        G = pb.G if Ge is None else np.vstack([pb.G, Ge])
        h = pb.h if he is None else np.r_[pb.h, he]
        sol = solve_lsq(pb.A, pb.b, pb.Cs, pb.es, G, h, weak=pb.weak)
        sol.n_bounds = len(pb.G)
        new = pb.thin_points(sol.z) if pb.warps else []
        if not new:
            return sol
        pb.add_bounds(new)
    raise Infeasible("thin", [d[1] for d in new])


def _piece_angles(pb: _Problem, pair: tuple, shape: str, z: np.ndarray) -> list:
    """Per shared piece of a declared pair: (its midpoint's fold, the EDGE_W mean of its signed dihedral), deg, + ridge
    (as the report prints angles)."""
    sign = 1.0 if shape == "ridge" else -1.0
    pieces: dict = {}
    for seg in pb.segs.get(pair, []):
        pieces.setdefault(seg.piece, []).append(seg)
    out = []
    for segs in pieces.values():
        mid = next(x for x in segs if x.frac == 0.5)
        w = EDGE_W if len(segs) == len(EDGE_AT) else (1.0,)
        mean = sum(wi * math.copysign(math.tan(math.radians(_normal_deg(x, z))), sign * _angle(x, z)[0])
                   for x, wi in zip(segs, w))
        out.append((_angle(mid, z)[0], sign * math.degrees(math.atan(mean))))
    return out


def _declared_fit(pb: _Problem, pairs: list, start: Lsq) -> Lsq:
    """The fit with the declared shapes of `pairs` [(pair, shape)] on every shared piece (_shape_rows), linearised
    about the previous pass until the rows settle (module docstring): no linearisation point changes more than
    SETTLE_TOL, or every declared piece holds exactly and none changes more than SETTLE_HOLD_TOL (the passes contract
    by ~0.1 each: the probe1 flat crown's shoulder|base valley, its readings as probe1 passed them, holds from pass 6
    and settles at pass 12; a cap of 5 refused it E_FIT as a solver artefact). After ANGLE_PASSES without settling, a
    solution whose pieces all hold exactly stands (a feasible point, its cost an upper bound); else, or when a settled
    pass leaves a piece short (the rows and the exact check disagree), Infeasible "angles" with the facts: the pairs,
    the passes, the last change and each short piece's fold and mean dihedral. sol.passes counts the passes.
    With corner position unknowns (pb.M) the passes first move the corners too (each pass re-linearises the geometry
    at the solution's positions, Gauss-Newton) until the rows and the positions settle (<= POS_PASSES), then hold
    the positions there and settle the rows as above: the solution is exact at the corners it reports, and its chi2
    (the cost's) includes how far it moved them from their readings."""
    pb.set_point(start.z, jac=start.jac)
    tries, jpasses, sol = [None], 0, start
    if pb.M:
        sol, jpasses, ok, states = _joint_passes(pb, pairs, start)
        tries = states[-1:] if ok or len(states) < 2 else states[-2:]
    points = []                                       # each state as the builder reproduces it: snapped, pushed
    for Z in tries:
        if Z is None:
            points.append(None)
            continue
        for push in (False, True):
            Zb = pb.builder_point(Z, push=push)[0]
            if not any(q is not None and np.array_equal(q, Zb) for q in points):
                points.append(Zb)
    best = None
    for Z in points:
        if Z is not None:
            pb.set_point(Z)
        s, passes, settled, step = _hold_passes(pb, pairs, sol if Z is None else Lsq(Z, 0.0, np.zeros(0), None, 0))
        low = [(pair, shape) for pair, shape in pairs if _piece_holds(pb, pair, shape, s.z)]
        if best is None or (bool(low), s.chi2) < (bool(best[1]), best[0].chi2):
            best = (s, low, passes, settled, step)
    sol, low, passes, settled, step = best
    pb.set_point(sol.z)                               # the point of the solution kept (its corners, held)
    sol.passes = passes + jpasses
    if low:
        short = []
        for pair, shape in low:
            sign = 1.0 if shape == "ridge" else -1.0
            for fold, mean in _piece_angles(pb, pair, shape, sol.z):
                if sign * fold < MIN_CREASE_DEG - 1e-6 or sign * mean < FLAT_CREASE_DEG + MEAN_MARGIN_DEG - 1e-6:
                    short.append({"pair": "|".join(pair), "shape": shape, "fold_deg": fold, "mean_dihedral_deg": mean})
        raise Infeasible("angles", [p for p, _ in low], {"passes": sol.passes, "settled": settled,
                                                          "step": step, "short": short})
    return sol


def _move(pb: _Problem, sol: Lsq, damp: dict) -> float:
    """Re-linearise at sol, its corner moves damped where they cycle and held within their caps (damp = {lam, last
    step, last move}): a step that reverses the last one halves the share (down to POS_DAMP_MIN; a base station
    across a facet|base border makes the fold a step in the corners' positions, and undamped passes cycled across it:
    probe1's flat-border ridge, E at d 75.41 / 76.11 every other pass, and with period 4: 75.62 / 75.69 / 75.82 /
    75.75, where doubling the share back after a forward step undid it), a forward step raises it by POS_RECOVER (a
    cycle of period 2 or 4 still shrinks, by 0.625 per two passes; a Gauss-Newton overshoot recovers in ~3 passes:
    never recovering left 4-7 % of random two-face layouts unsettled at 30 passes, halving every step that did not
    contract 5-6 %; re-evaluating the Jacobian where they crawl changed none and doubled the time); each direction
    within +- its cap
    (POS_MAX_SD x the reading's sd there, about the reading: a node placed 0.5 mm off a reading read to 0.115 there
    returns to it; a distance-to-anything cap held probe1's tip E at 0.4 of its 6 mm room to
    the knot behind it while the ridge it was declared needed it 3.5 mm forward: the layout is guarded by
    builder_point's path check instead). Returns the move made (mm)."""
    if not pb.M:
        pb.set_point(sol.z)
        return 0.0
    Z = np.array(sol.z, np.float64)
    step = Z[pb.N:] - pb.delta
    move = float(np.max(np.abs(step)))
    if damp["last"] is not None:
        back = float(step @ damp["last"]) < 0.0
        damp["lam"] = max(0.5 * damp["lam"], POS_DAMP_MIN) if back else min(1.0, POS_RECOVER * damp["lam"])
    damp["last"], damp["move"] = step, move
    cap = np.array([c["cap"] for c in pb.coords])
    off = np.array([c["off"] for c in pb.coords])     # (the reading's own position: the cap is about it)
    Z[pb.N:] = np.clip(pb.delta + damp["lam"] * step, off - cap, off + cap)
    return pb.set_point(Z)


def _damp() -> dict:
    return {"lam": 1.0, "last": None, "move": math.inf}


def _joint_passes(pb: _Problem, pairs: list, sol: Lsq) -> tuple:
    """The declared shapes' passes with the corners moving (_declared_fit): (the last solution, passes, settled,
    the points [Z] the passes linearised at, the last two kept). Settled: the rows changed by <= SETTLE_HOLD_TOL and
    no corner by more than POS_TOL_MM; else POS_PASSES."""
    prev, damp, passes, pos, states = None, _damp(), 0, math.inf, []
    Zp = np.r_[np.asarray(sol.z)[:pb.N], pb.delta]
    while passes < POS_PASSES:
        lin = _shape_rows(pb, pairs, Zp)
        if not lin:
            return sol, passes, True, [Zp]
        key = np.array([x[3] for x in lin])
        if prev is not None and float(np.max(np.abs(key - prev))) <= SETTLE_HOLD_TOL and pos <= POS_TOL_MM:
            return sol, passes, True, states[-1:]
        sol = _solve(pb, np.vstack([x[1][None] for x in lin]), np.array([x[2] for x in lin]))
        sol.shape_rows = [x[:3] for x in lin]
        prev, passes = key, passes + 1
        pos = _move(pb, sol, damp)
        Zp = np.r_[np.asarray(sol.z)[:pb.N], pb.delta]
        states = (states + [Zp])[-2:]
    return sol, passes, False, states


def _hold_passes(pb: _Problem, pairs: list, sol: Lsq) -> tuple:
    """The declared shapes' passes with the corners held at the point (module docstring; the rows settled as without
    position unknowns): (solution, passes, settled, the last change or None)."""
    pb.pinned = True
    prev, step, passes, settled = None, math.inf, 0, False
    try:
        while True:
            lin = _shape_rows(pb, pairs, sol.z)
            if not lin:
                sol, settled = (_solve(pb) if sol.cov is None else sol), True     # (a point: solve it held)
                break
            key = np.array([x[3] for x in lin])
            if prev is not None:
                step = float(np.max(np.abs(key - prev)))
                if step <= SETTLE_TOL or (step <= SETTLE_HOLD_TOL
                                          and not any(_piece_holds(pb, p, s, sol.z) for p, s in pairs)):
                    settled = True
                    break
            if passes >= ANGLE_PASSES:
                break
            sol = _solve(pb, np.vstack([x[1][None] for x in lin]), np.array([x[2] for x in lin]))
            sol.shape_rows = [x[:3] for x in lin]
            prev, passes = key, passes + 1
    finally:
        pb.pinned = False
    return sol, passes, settled, None if prev is None else step


def _free_fit(pb: _Problem) -> tuple[Lsq, dict]:
    """The readings-only fit (no declared shape; thickness kept), its pinned gradients and corner positions settled
    (module docstring): solved with the corners where the design puts them; then per pass the pinned directions'
    face gradients taken at the solution (a step that does not contract to 0.9 of the last halves, down to
    WEIGHT_DAMP_MIN: a period-2 cycle otherwise kept whichever half pass 30 landed on) and the geometry re-linearised
    at its corner positions (Gauss-Newton), re-solved, until neither changes (WEIGHT_TOL, POS_TOL_MM; <= WEIGHT_PASSES:
    what did not settle keeps its last value, said in `model` and in the report). The gradients are then frozen: every
    declared fit, its cost and the uncertainties use them (one model). _final: the joint solve's covariance and dof,
    the pinned solve's exact solution."""
    model = {"weight_passes": 0, "weights_settled": True, "weight_change": 0.0, "position_passes": 0,
             "positions_settled": True, "position_step": 0.0}
    pb.pinned = True
    try:
        sol = _solve(pb)
    finally:
        pb.pinned = False
    if not (pb.weighted or pb.M):
        return sol, model
    pb.set_point(sol.z)
    lam, last, res, step, k, damp = 1.0, math.inf, 0.0, 0.0, 0, _damp()
    for k in range(1, WEIGHT_PASSES + 1):
        if pb.weighted:
            target = pb.pinned_grads(sol.z)
            res = pb.grad_residual(target)
            if res > 0.9 * last:
                lam = max(0.5 * lam, WEIGHT_DAMP_MIN)
            last = res
            if res > WEIGHT_TOL:
                pb.set_grads({n: pb.gpin[n] + lam * (target[n] - pb.gpin[n]) for n in target})
        if k > 1 and res <= WEIGHT_TOL and step <= POS_TOL_MM:
            break
        sol = _solve(pb)
        step = _move(pb, sol, damp)
    model.update(weight_passes=k if pb.weighted else 0, weights_settled=bool(res <= WEIGHT_TOL), weight_change=res,
                 position_passes=k if pb.M else 0, positions_settled=bool(step <= POS_TOL_MM), position_step=step)
    if pb.M:
        Zb, model["snap_reverted"] = pb.builder_point(np.r_[np.asarray(sol.z)[:pb.N], pb.delta])
        pb.set_point(Zb)
    return _final(pb), model


def _final(pb: _Problem, Ge=None, he=None) -> Lsq:
    """At the point: the joint solve (positions free: the covariance, its dof and weak directions) and the pinned one
    (positions held: z exact at the corners it reports, its chi2 the joint's at the settled point); the pinned
    solution carries the joint's covariance. Without position unknowns one solve."""
    if not pb.M:
        return _solve(pb, Ge, he)
    joint = _solve(pb, Ge, he)
    pb.pinned = True
    try:
        exact = _solve(pb, Ge, he)
    finally:
        pb.pinned = False
    exact.cov, exact.cov_free, exact.dof, exact.weak_basis = joint.cov, joint.cov_free, joint.dof, joint.weak_basis
    exact.shape_rows, exact.passes = getattr(joint, "shape_rows", []), joint.passes
    exact.jac = (pb.jac, pb.jac_at)
    return exact


def _with_cov(pb: _Problem, sol: Lsq) -> Lsq:
    """A declared solution (pinned) with the joint covariance at its point, its shape rows as they last stood."""
    if not pb.M or not sol.shape_rows:
        return sol
    pb.set_point(sol.z)
    rows = sol.shape_rows
    joint = _solve(pb, np.vstack([r[1][None] for r in rows]), np.array([r[2] for r in rows]))
    sol.cov, sol.cov_free, sol.dof, sol.weak_basis = joint.cov, joint.cov_free, joint.dof, joint.weak_basis
    sol.jac = (pb.jac, pb.jac_at)
    return sol


def _plain_cov(pb: _Problem) -> np.ndarray | None:
    """The readings-only covariance (no bound held) with each reading weighted by its own X sd alone (no position or
    camera part, the corners held at the point): the X part of a crease's sd (sd_deg_X). None when the model has
    none of them (it is the same)."""
    if not pb.weighted and not pb.fits and not pb.M:
        return None
    A, b = pb.A.copy(), pb.b.copy()
    for n in pb.reads:
        A[pb.rows_of[n]], b[pb.rows_of[n]] = 0.0, 0.0
        r = pb.xrow[n]
        A[r, pb.ix[n]] = 1.0 / pb.src[n][2]
        b[r] = pb.src[n][1] / pb.src[n][2]
    was, pb.pinned = pb.pinned, True
    try:
        return solve_lsq(A, b, pb.Cs, pb.es, weak=pb.weak).cov_free
    except Infeasible:
        return None
    finally:
        pb.pinned = was


def _mean_dihedral(segs: list, z: np.ndarray, cov: np.ndarray | None = None) -> tuple[float, float]:
    """(deg, sd deg): the EDGE_W mean of a piece's signed dihedral, atan2((a - b) sqrt(1 + s^2), 1 + s^2 + a b) at each
    point (a, b the two sides' slopes across the edge, s the first side's along it: _normal_deg with its sign), what
    the builder votes a crease's shape on; sd by linearisation through cov (analytic in a, b, s)."""
    w = EDGE_W if len(segs) == len(EDGE_AT) else (1.0,)
    val, J = 0.0, np.zeros(len(z))
    for seg, wi in zip(segs, w):
        a, b = float(seg.a1 @ z + seg.k1), float(seg.a2 @ z + seg.k2)
        t = np.array([-seg.n[1], seg.n[0]])
        ds = t @ seg.G1
        s = float(ds @ z + t @ seg.K1)
        r = math.sqrt(1.0 + s * s)
        num, den = (a - b) * r, 1.0 + s * s + a * b
        val += wi * math.atan2(num, den)
        if cov is not None:
            dnum = r * (seg.a1 - seg.a2) + (a - b) * (s / r) * ds
            dden = 2.0 * s * ds + b * seg.a1 + a * seg.a2
            J = J + wi * (den * dnum - num * dden) / (num * num + den * den)
    sd = 0.0 if cov is None else math.degrees(math.sqrt(max(float(J @ cov @ J), 0.0)))
    return math.degrees(val), sd


def _angle_sds(seg: _Seg, free: Lsq, cov_X: np.ndarray | None) -> tuple[float, float, float, float]:
    """(readings-only angle, its sd with no bound held, its sd with the free fit's bounds held, its sd from the
    readings' own X sd alone): the model's covariance (effective sds, camera turns) through the angle's
    linearisation."""
    fdeg, sdb = _angle(seg, free.z, free.cov)
    _, sdu = _angle(seg, free.z, free.cov_free)
    sdx = sdu if cov_X is None else _angle(seg, free.z, cov_X)[1]
    return fdeg, sdu, sdb, sdx


def _point_name(b: tuple) -> str:
    if b[0] == "node":
        return f"node {b[1]}"
    where = {"edge": "on an edge", "outline": "on the outline", "inside": "inside"}[b[0]]
    return f"facet {b[1]} {where} at (d, y) ({_fixed(b[2][0])}, {_fixed(b[2][1])})"


def _fit_error(pb: _Problem, x: Infeasible, what: str) -> TempleError:
    if x.what == "equalities":
        res = np.asarray(x.residual)
        worst = sorted((i for i in x.rows if i < len(pb.eq_tags)), key=lambda i: -abs(res[i]))[:3]   # (not a pin)
        parts = []
        for i in worst:
            tag = pb.eq_tags[i]
            name = {"corner": lambda t: f"facet {t[1]} corner {t[2]}", "coef": lambda t: f"facet {t[1]}'s coef plane",
                    "rise": lambda t: f"facet {t[1]}'s crease rise", "base": lambda t: f"node {t[1]} (base)",
                    "same": lambda t: f"nodes {t[1]} and {t[2]} (one point)",
                    "tee": lambda t: f"node {t[1]} on facet {t[2]}'s edge"}[tag[0]](tag)
            parts.append(f"{name} {res[i]:+.3g} mm")
        return TempleError("E_FIT", f"{what}: the hard values (base nodes, coef planes, crease rises, flat faces) "
                           f"cannot all hold: {'; '.join(parts)}", reason="equalities",
                           misfits=[{"what": list(pb.eq_tags[i]), "mm": float(res[i])} for i in worst])
    if x.what == "bounds":
        pts = []
        was, pb.pinned = pb.pinned, True
        try:                                         # the thinnest points of the fit without the bound
            z = solve_lsq(pb.A, pb.b, pb.Cs, pb.es, weak=pb.weak).z
            t = pb.G @ z - pb.h + pb.tmin + THICK_MARGIN_MM
            pts = [(pb.bound_at[i], float(t[i])) for i in np.argsort(t, kind="stable")[:3] if t[i] < pb.tmin]
        except Infeasible:
            pass
        finally:
            pb.pinned = was
        thin = "; ".join(f"{_point_name(b)} t {_fixed(t)} mm" for b, t in pts)
        return TempleError("E_FIT", f"{what}: no fit keeps every node and facet point at least t_min {pb.tmin:g} mm "
                           f"thick with the hard values (base nodes, coef planes)"
                           + (f"; without the bound the thinnest would be {thin}" if thin else ""), reason="bounds",
                           points=[{"at": list(b), "t_mm": t} for b, t in pts])
    if x.what == "thin":
        faces = sorted(set(x.rows))
        return TempleError("E_FIT", f"{what}: the thickness inside warp face {', '.join(faces)} did not settle at "
                           f"t_min in {THIN_PASSES} passes", reason="thin", facets=faces)
    if x.what == "angles":
        pairs = sorted({"|".join(p) for p in x.rows})          # the declared pairs a piece of which does not hold
        f = x.residual or {}
        short = f.get("short", [])
        def short_by(v, need, shape):                # the shortfall, said, where printing alone would hide it
            gap = need - (v if shape == "ridge" else -v)
            return f" ({gap:.2g} short)" if gap > 1e-6 else ""      # the exact check's tolerance
        pieces = "; ".join(f"{s['pair']} declared {s['shape']}: fold {_signed(s['fold_deg'], 3)} deg at mid"
                           f"{short_by(s['fold_deg'], MIN_CREASE_DEG, s['shape'])}, mean dihedral "
                           f"{_signed(s['mean_dihedral_deg'], 3)} deg"
                           f"{short_by(s['mean_dihedral_deg'], FLAT_CREASE_DEG + MEAN_MARGIN_DEG, s['shape'])}"
                           for s in short)
        if short:
            pieces += (f" (a declared shape needs a fold of at least {MIN_CREASE_DEG:g} deg and a mean dihedral of at "
                       f"least {FLAT_CREASE_DEG + MEAN_MARGIN_DEG:g} deg, both its way; + is a ridge)")
        if f.get("settled", True):
            head = (f"{what}: the declared minimum angle settled after {f.get('passes', 0)} passes short of it at "
                    f"{', '.join(pairs)} (internal: the linearised rows and the exact check disagree)")
        else:
            head = (f"{what}: the declared minimum angle did not settle in {f.get('passes', 0)} passes at "
                    f"{', '.join(pairs)} (the last pass still changed a slope by {f['step']:.2g})")
        return TempleError("E_FIT", head + (f": {pieces}" if pieces else ""), reason="angles", creases=pairs,
                           passes=f.get("passes"), settled=f.get("settled"), step=f.get("step"), short=short)
    if x.what == "solver":
        return TempleError("E_FIT", f"{what}: the bound solve did not converge (internal)", reason="solver")
    return TempleError("E_FIT", f"{what}: the observations leave the fit underdetermined", reason="rank")


def _flat_test(names: list, P: np.ndarray, X: np.ndarray, sd: np.ndarray,
               Acam: np.ndarray | None = None) -> dict | None:
    """The weighted plane through a flat face's known corners: chi2, extra corners and every corner's leave-one-out
    deviation [{node, mm, sd_mm, sigma, in_line}]. None below 4 corners or for collinear ones. Closed form through the
    hat matrix diagonal h (weighted space): corner j's deviation from the others' plane is e_j / (1 - h_j), its sd
    sd_j / sqrt(1 - h_j) (its own sd and the others' plane at it, Sherman-Morrison); h_j -> 1 when the others leave
    the plane undetermined (in line, no deviation), and a sd above IN_LINE_RATIO x its own marks them nearly in line.
    Acam (k, F): the corners' camera coefficients; each camera fit's turn is then an unknown of the test with its unit
    prior (a row of its own, in chi2): one rigid turn per fit moves its readings together, so it is never read as the
    face's misfit, and it costs its square (the extra corners' dof are unchanged: one row and one unknown per fit)."""
    k = len(names)
    if k < 4:
        return None
    M = np.c_[np.ones(k), P - P.mean(axis=0)]
    W = M / sd[:, None]
    U, sv, _ = np.linalg.svd(W, full_matrices=False)
    if sv[-1] <= 1e-10 * sv[0]:
        return None
    t = X / sd
    if Acam is not None and Acam.size and np.any(Acam != 0.0):
        F = Acam.shape[1]
        W = np.vstack([np.c_[W, Acam / sd[:, None]], np.c_[np.zeros((F, 3)), np.eye(F)]])
        t = np.r_[t, np.zeros(F)]
        U = np.linalg.svd(W, full_matrices=False)[0]
    r = t - W @ np.linalg.lstsq(W, t, rcond=None)[0]           # weighted residuals (the corners', then the priors')
    chi2 = float(np.sum(r * r))
    e = r[:k] * sd
    h = np.einsum("ij,ij->i", U[:k], U[:k])
    loo = []
    for j in range(k):
        if h[j] >= 1.0 - 1e-9:
            loo.append({"node": names[j], "mm": None, "sd_mm": None, "sigma": None, "in_line": True})
            continue
        r_j, tot = float(e[j] / (1.0 - h[j])), float(sd[j] / math.sqrt(1.0 - h[j]))
        loo.append({"node": names[j], "mm": r_j, "sd_mm": tot, "sigma": abs(r_j) / tot,
                    "in_line": bool(tot > IN_LINE_RATIO * sd[j])})
    return {"flat_chi2": chi2, "extra": k - 3, "loo": loo, "not_flat": chi2 > FLAT_CHI2_PER_CORNER * (k - 3)}


def _against(moves: list) -> str:
    """What a declaration is against, unsigned, in sd (no amount to type): the readings and typed values it pulls the
    fit from (each reading's whole (d, y, X) residual change, its X row and position rows together), then, as a host
    fact apart from them (a camera fit has no pick sheet), the camera fits it turns (review 2026-10-12: the readings'
    list named "the camera turn of fit 02" beside A and D, which the prompt sends to pick sheets)."""
    rd = [f"{m_['node']} ({abs(m_['sd']):.1f} sd)" for m_ in moves if m_["source"] == "read"]
    ty = [f"{m_['node']} ({abs(m_['sd']):.1f} sd)" for m_ in moves if m_["source"] == "typed"]
    cm = [f"{m_['node']} by {abs(m_['sd']):.1f} sd" for m_ in moves if m_["source"] == "camera"]
    text = " and ".join(([f"the reading{'s' if len(rd) > 1 else ''} of {', '.join(rd)}"] if rd else [])
                        + ([f"the typed value{'s' if len(ty) > 1 else ''} of {', '.join(ty)}"] if ty else []))
    if cm:
        turn = f"it turns the camera of fit {', '.join(cm)} of its theta error (a host camera fit)"
        text = f"against {text}; {turn}" if text else turn
    elif text:
        text = f"against {text}"
    return text


def _node_rows(pb: _Problem, n: str, z: np.ndarray) -> np.ndarray:
    """A reading's or typed value's whitened residual rows at z (a reading: its X row, then its position rows)."""
    rows = pb.rows_of[n]
    return pb.A[rows] @ z - pb.b[rows]


def fit_spec(design: dict, *, readings: dict | None = None) -> FitResult:
    """Fit a design (module docstring). `readings` (optional, the lab's stored readings {name: {X, sd | sd_X, d, y,
    sd_d, sd_y, ...}}) adds W_FIT_OFF_READING for a typed, solved or base node more than OFF_READING_SD off the stored
    reading of its name, or placed away from where it was read. Raises TempleError: the probe's parse refusals, E_SPEC,
    E_FIT, E_FIT_SHAPE."""
    pb = _Problem(design)
    visible = [c for c in pb.ps.creases if "visible_angle" in c]
    # The old solve and its cost gate are unchanged. A visible choice is applied only after that baseline, with
    # eligibility from the original readings-only solution, never from a state changed by a visual declaration.
    declared = [(tuple(c["facets"]), c["shape"]) for c in pb.ps.creases if "visible_angle" not in c]
    lonely = [(p, s) for p, s in declared if not pb.segs.get(p)]
    if lonely:
        raise TempleError("E_FIT_SHAPE", "the declared crease shapes: " + "; ".join(
            f"{'|'.join(p)} declared {s}: {p[0]} and {p[1]} share no edge inside the outline" for p, s in lonely),
            dchi2=None, accept_dchi2=ACCEPT_DCHI2, chi2_free=None,
            creases=[{"pair": list(p), "declared": s, "free_deg": None, "free_sd_deg": None, "dchi2": None,
                      "moves": [], "reason": "no shared edge"} for p, s in lonely])
    try:
        free, model = _free_fit(pb)
    except Infeasible as x:
        raise _fit_error(pb, x, "the readings-only fit") from None
    pb.set_point(free.z, jac=free.jac)
    holds = {pair: all(float(r @ free.z) >= lo - 1e-12 for _, r, lo, _ in _shape_rows(pb, [(pair, shape)], free.z))
             for pair, shape in declared}            # the readings-only fit already builds it: it is the optimum
    try:
        sol = free if all(holds.values()) else _with_cov(pb, _declared_fit(pb, declared, free))
    except Infeasible as x:                           # "bounds": the declarations cannot all hold (E_FIT_SHAPE below)
        if x.what != "bounds":
            raise _fit_error(pb, x, "the declared fit") from None
        sol = None
    dchi2 = math.inf if sol is None else sol.chi2 - free.chi2        # one model: the solution's own chi2 over free's
    single: dict = {}
    for pair, shape in declared:
        if holds[pair]:
            single[pair] = (0.0, free)                   # no cost
            continue
        try:
            one = _declared_fit(pb, [(pair, shape)], free)
            single[pair] = (one.chi2 - free.chi2, one)
        except Infeasible as x:
            if x.what != "bounds":
                raise _fit_error(pb, x, f"the fit declaring {'|'.join(pair)}") from None
            single[pair] = (None, None)
    if dchi2 > ACCEPT_DCHI2:
        obs_nodes = [n for n in pb.names if pb.src[n][0] in ("read", "typed")]
        costly = [(p, s) for p, s in declared if single[p][0] is None or single[p][0] > COST_SHOWN]
        if not costly:
            costly = [(p, s) for p, s in declared if single[p][0] is None or single[p][0] > 0.0]
        pb.set_point(free.z, jac=free.jac)
        parts, recs = [], []
        for pair, shape in costly:
            seg = max(pb.segs[pair], key=lambda s_: s_.length)
            fdeg, fsd, _, _ = _angle_sds(seg, free, None)
            cost, one = single[pair]
            moves = []
            if one is not None:          # the change of each residual (all its rows, camera turns in) and turn, in sd
                mv = []
                for n in obs_nodes:
                    dv = _node_rows(pb, n, one.z) - _node_rows(pb, n, free.z)
                    size = float(np.linalg.norm(dv))
                    mv.append((math.copysign(size, dv[0]) if dv[0] else size, float(dv[0]) * pb.obs[n][1], n,
                               pb.src[n][0]))
                mv += [(float(one.z[pb.ucol[f]] - free.z[pb.ucol[f]]), None, f, "camera") for f in pb.fits]
                for sd, mm, n, src in sorted(mv, key=lambda m_: (-abs(m_[0]), m_[3] == "camera", m_[2]))[:3]:
                    if abs(sd) >= 0.01:
                        moves.append({"node": n, "source": src, "mm": mm, "sd": sd})
            text = f"{'|'.join(pair)} declared {shape}: readings-only {_signed(fdeg, 1)} +- {fsd:.1f} deg, "
            text += ("it cannot be built with the hard values (base nodes, coef planes) and t_min" if cost is None
                     else f"cost +{cost:.1f}" + (f", {_against(moves)}" if moves else ""))
            parts.append(text)
            recs.append({"pair": list(pair), "declared": shape, "free_deg": fdeg, "free_sd_deg": fsd, "dchi2": cost,
                         "moves": moves})
        total = ("they cannot all be built with the hard values and t_min" if sol is None else
                 f"they cost chi2 +{dchi2:.1f} over the readings-only fit (more than {ACCEPT_DCHI2:g})")
        raise TempleError("E_FIT_SHAPE", f"the declared crease shapes: {total}: {'; '.join(parts)}",
                          dchi2=None if sol is None else dchi2, accept_dchi2=ACCEPT_DCHI2, creases=recs,
                          chi2_free=free.chi2)
    res = _result(pb, free, sol, declared, single, readings, model)
    _check_fitted_pairs(pb, res, declared)
    if visible:
        res = _visible_fit(pb, free, sol, declared, single, readings, model, visible)
    return res


def _visible_fit(pb, free, baseline, declared, single, readings, model, choices):
    """Owner-approved local angle choices (ASSEMBLY M5-fix-8).

    Only flat facet/facet creases with unresolved ORIGINAL readings-only evidence are eligible. Rotate one
    planar face about the unchanged shared 3D line; only its exclusive off-edge corners may move in X. Hold every
    d/y position, other plane, unrelated observation residual and camera variable exactly. Original sign-certain
    evidence protects its sign and nonflat classification, not an invented exact-angle confidence gate. Original
    flat-within-error evidence retains that classification. Full changed angles and residuals remain reported.
    Unsupported sharing, hard values, geometry, thickness and declaration contradictions refuse, never get clamped.

    The selected degrees are an absolute normal dihedral, exact within S.VISIBLE_ANGLE_TOL_DEG. The full original
    observation cost is still reported; only local unresolved-face reading changes have owner-authorized priority.
    No global delta-chi2 threshold changes. Fitted output preserves comparison metadata for reload/build validation.
    """
    tol = S.VISIBLE_ANGLE_TOL_DEG

    def refuse(message, **fields):
        raise TempleError("E_FIT_SHAPE", "visible angle: " + message, **fields)

    # Stored photo readings take priority for eligibility even if an earlier node edit replaced their payload with
    # a typed/solve value or inflated its error. They do not silently replace the design used for the baseline fit.
    # Without a store, the original design's unmodified observations are all the evidence this pure core receives.
    measure_pb, measure_free = pb, free
    restored = []
    if isinstance(readings, dict):
        measurement_design = copy.deepcopy(pb.design)
        measurement_design["creases"] = []
        for node in pb.names:
            if node not in readings:
                continue
            try:
                payload = read_payload(readings[node])
            except (ValueError, TypeError, KeyError) as exc:
                raise TempleError("E_SPEC", f"stored measurement {node} cannot establish visible-angle eligibility: {exc}",
                                  node=node) from None
            measurement_design["nodes"][node] = [*measurement_design["nodes"][node][:2], payload]
            restored.append(node)
        if restored:
            measure_pb = _Problem(measurement_design)
            try:
                measure_free, _ = _free_fit(measure_pb)
            except Infeasible as exc:
                raise _fit_error(measure_pb, exc, "the original stored-readings eligibility fit") from None
    measure_pb.set_point(measure_free.z, jac=measure_free.jac)
    evidence = {}
    for pair, segs in measure_pb.segs.items():
        rows = []
        for seg in segs:
            if seg.length <= 0.0:
                continue
            deg, sd = _angle(seg, measure_free.z, measure_free.cov_free)
            piece = [s for s in segs if s.piece == seg.piece]
            mean, msd = _mean_dihedral(piece, measure_free.z, measure_free.cov_free)
            rows.append({"piece": seg.piece, "deg": deg, "sd_deg": sd, "state": _crease_state(deg, sd),
                         "normal_deg": mean, "normal_sd_deg": msd})
        evidence[pair] = rows
    moving = [c["visible_angle"]["moving_facet"] for c in choices]
    if len(set(moving)) != len(moving):
        refuse("a moving facet is named by more than one visible angle", moving_facets=moving)
    for c in choices:
        pair, v = tuple(c["facets"]), c["visible_angle"]
        if not evidence.get(pair):
            refuse(f"{'|'.join(pair)} shares no edge inside the outline", facets=list(pair))
        if any(r["state"] != "open" for r in evidence[pair]):
            refuse(f"{'|'.join(pair)} is not unresolved in the original readings-only fit",
                   facets=list(pair), measurement=evidence[pair])
        fixed = next(x for x in pair if x != v["moving_facet"])
        if fixed in moving:
            refuse("one selected face is another choice's fixed adjacent face", facets=list(pair))
        if pb.mode[v["moving_facet"]] != "nodes":
            refuse(f"moving facet {v['moving_facet']} must be a free flat nodes plane; its plane is a hard value",
                   facets=list(pair))

    pb.set_point(baseline.z, jac=baseline.jac)
    z = baseline.z.copy()
    changed, records = set(), []
    for c in choices:
        pair, v = tuple(c["facets"]), c["visible_angle"]
        face, target = v["moving_facet"], float(v["degrees"])
        segs = pb.segs[pair]
        mids = [s for s in segs if s.length > 0.0]
        seg = max(mids, key=lambda s: s.length)
        # More than one collinear clipped piece is allowed; a bent/multiple-axis crease is not a single rotation.
        n = seg.n
        tangent = np.array([-n[1], n[0]])
        if any(abs(float(n @ (s.mid - seg.mid))) > 1e-7 or float(n @ s.n) < 1 - 1e-10
               for s in mids):
            refuse(f"{'|'.join(pair)} does not have one straight shared rotation line", facets=list(pair))
        g1, g2 = seg.G1 @ z + seg.K1, seg.G2 @ z + seg.K2
        along = float(g1 @ tangent)
        if abs(float((g1 - g2) @ tangent)) > 1e-8:
            refuse("the adjacent planes do not share one 3D edge", facets=list(pair))
        a, b = float(g1 @ n), float(g2 @ n)
        r = math.sqrt(1.0 + along * along)
        delta = math.radians(target) * (1 if c["shape"] == "ridge" else -1)
        # atan2 formulation remains defined at 90 degrees; the rejected branch is a vertical/non-graph plane.
        sn, cs = math.sin(delta), math.cos(delta)
        if face == pair[0]:
            num, den = b * r * cs + (1 + along * along) * sn, r * cs - b * sn
        else:
            num, den = a * r * cs - (1 + along * along) * sn, r * cs + a * sn
        if abs(den) <= 1e-10:
            refuse("the chosen angle requires a vertical face, not an X(d,y) surface", facets=list(pair))
        grad = tangent * along + n * (num / den)
        ci = pb.col[face]
        oldgrad = z[ci + 1:ci + 3].copy()
        midX = float(z[ci] + oldgrad @ (seg.mid - pb.centre[face]))
        z[ci:ci + 3] = [midX - float(grad @ (seg.mid - pb.centre[face])), *grad]
        local = []
        for node in pb.poly[face]:
            ix = pb.cix(face, node)
            X = float(z[ci] + grad @ (np.asarray(pb.cur_nodes[node]) - pb.centre[face]))
            move = X - float(baseline.z[ix])
            if abs(move) <= 1e-9:
                continue
            if pb.src[node][0] == "base" or len(pb.of[node]) != 1 or node in pb.split:
                refuse(f"corner {node} is shared away from the rotation edge or has a hard base value",
                       facets=list(pair), node=node, move_mm=move)
            z[ix] = X
            changed.add(node)
            local.append({"node": node, "X_move_mm": move})
        got = math.copysign(_normal_deg(seg, z), _angle(seg, z)[0])
        want = target if c["shape"] == "ridge" else -target
        if abs(got - want) > tol:
            refuse("the chosen signed angle is not representable by this local graph rotation",
                   facets=list(pair), requested_deg=want, actual_deg=got)
        records.append({"facets": list(pair), **v, "shape": c["shape"], "measurement": evidence[pair],
                        "built_normal_deg": got, "changed_corners": local})

    # Exact position/camera/other-face preservation is structural; explicit checks make that invariant reviewable.
    # A local corner may be read, typed or prior-only. Its own rows all belong to the permitted rotation;
    # camera rows and every other corner remain protected (weak priors are not unrelated observations).
    allowed_rows = {int(row) for n in changed for row in pb.rows_of.get(n, [])}
    residual_before = pb.A @ baseline.z - pb.b
    residual_after = pb.A @ z - pb.b
    protected_rows = [i for i in range(len(pb.b)) if i not in allowed_rows and not pb.weak[i]]
    if protected_rows and np.max(np.abs(residual_after[protected_rows] - residual_before[protected_rows])) > 1e-8:
        refuse("the local rotation would change an unrelated reading or camera residual")
    if len(pb.e) and np.max(np.abs(pb.C @ z - pb.e)) > 1e-7:
        refuse("the local rotation contradicts a hard plane, shared corner or other exact geometry constraint")
    if len(pb.h) and np.min(pb.G @ z - pb.h) < -1e-7:
        refuse("the local rotation violates a thickness or other hard geometry bound")
    protected = []
    for pair, rows in evidence.items():
        for row in rows:
            if row["state"] == "open":
                continue
            segs = [s for s in pb.segs.get(pair, []) if s.piece == row["piece"]]
            if not segs:
                refuse("a protected original measurement edge is absent from the edited layout", facets=list(pair))
            mid = next(s for s in segs if s.length > 0.0)
            deg, mean = _angle(mid, z)[0], _mean_dihedral(segs, z)[0]
            if row["state"] == "certain":
                sign = 1.0 if row["deg"] > 0.0 else -1.0
                mean_sign = 1.0 if row["normal_deg"] > 0.0 else -1.0
                conflict = (sign * deg < FLAT_CREASE_DEG - tol or mean_sign * mean < -tol
                            or (abs(row["normal_deg"]) >= FLAT_CREASE_DEG
                                and mean_sign * mean < FLAT_CREASE_DEG - tol))
            else:
                conflict = _crease_state(deg, row["sd_deg"]) != "flat"
            if conflict:
                refuse(f"{'|'.join(pair)} would contradict its original {STATE_TEXT[row['state']]} classification",
                       facets=list(pair), measurement=row, chosen_fold_deg=deg, chosen_normal_deg=mean)
            protected.append({"facets": list(pair), **row, "chosen_fold_deg": deg, "chosen_normal_deg": mean})

    strong = ~pb.weak
    cost = float(residual_after[strong] @ residual_after[strong])
    visual = dataclasses.replace(baseline, z=z, chi2=cost)
    all_declared = declared + [(tuple(c["facets"]), c["shape"]) for c in choices]
    singles = dict(single)
    for c in choices:
        singles[tuple(c["facets"])] = (cost - free.chi2, visual)
    meta = dict(model, visible_angles={"policy": "unresolved_local_face/1", "choices": records,
                 "baseline_chi2": baseline.chi2, "actual_chi2": cost, "local_cost_change": cost - baseline.chi2,
                 "protected_measurements": protected, "unrelated_residuals_unchanged": True,
                 "measurement_basis": "original_stored_readings" if restored else "original_design_observations",
                 "restored_readings_for_eligibility": restored,
                 "angle_tolerance_deg": tol})
    result = _result(pb, free, visual, all_declared, singles, readings, meta)
    _check_fitted_pairs(pb, result, all_declared)
    from .build import build_temple
    built = build_temple(result.spec)             # all strict validity/manifold/thickness/shape/angle checks
    meta["visible_angles"]["checks"] = copy.deepcopy(built.checks)
    meta["visible_angles"]["thickness"] = copy.deepcopy(built.tables["thickness"])
    for rec in records:
        rec["reading_residuals"] = {n["node"]: {k: result.nodes[n["node"]].get(k) for k in
                                    ("source", "residual_mm", "residual_sd", "chi2", "reading")}
                                    for n in rec["changed_corners"]}
        rec["stored_reading_differences"] = {}
        for n in rec["changed_corners"]:
            name = n["node"]
            if name in restored:
                r, nr = readings[name], result.nodes[name]
                rec["stored_reading_differences"][name] = {
                    "coordinate_difference_mm": [nr["d"] - r["d"], nr["y"] - r["y"], nr["X"] - r["X"]],
                    "note": "actual minus original stored point, before camera compensation; not a fit acceptance",
                    "original_reading": copy.deepcopy(r)}
    for c in result.creases:
        original = evidence.get(tuple(c["pair"]))
        if original and restored:
            # The eligibility evidence must not be replaced in the report by an edited typed value's confidence.
            row = original[0] if len(original) == 1 else next(
                r for r in original if r["piece"] == max(measure_pb.segs[tuple(c["pair"])], key=lambda s: s.length).piece)
            c.update(free_deg=row["deg"], free_sd_deg=row["sd_deg"], sd_deg=row["sd_deg"],
                     free_mean_deg=row["normal_deg"], free_mean_sd_deg=row["normal_sd_deg"],
                     state=row["state"], certain=row["state"] == "certain",
                     measurement_basis="original_stored_readings")
        chosen = next((v for v in records if v["facets"] == c["pair"]), None)
        if chosen:
            c["visible_angle"] = copy.deepcopy(chosen)
    return result


def _check_fitted_pairs(pb: _Problem, res: FitResult, declared: list) -> None:
    """E_FIT_SHAPE: a declared pair that shares an edge inside the outline at the design's corners but not at the
    fitted ones. The crease pieces are fixed at the design (module docstring), so a corner the fit moves can take a
    pair's shared edge outside the outline unseen: v3p3-review-e2e probe 4 seed 1874 declared crown|base a valley with H
    0.13 mm outside the outline; the readings-only fit put A 0.14 mm up, onto the top outline, the crown's edge A-H
    then lay outside it, the declaration "held" on its design piece, and the builder refused E_CREASE_SHAPE "declared
    valley but built none" (1 of 605 fuzz designs that built). The fitted spec is read again as the builder will
    (_Problem's parse and pieces) where a corner moved (else its pieces are the design's: the 30-node strip fits in
    under 50 ms); facts only, as the pre-solve check."""
    if not declared or not any(r.get("move_mm") and max(abs(v) for v in r["move_mm"]) > 0.0
                               for r in res.nodes.values()):
        return
    try:
        segs = _Problem(res.spec).segs
    except TempleError:
        return                                       # the builder states it
    lost = [(p, s) for p, s in declared if not segs.get(p)]
    if not lost:
        return
    parts, recs = [], []
    for p, s in lost:
        corners = sorted({n for l in p if l in pb.poly for n in pb.poly[l]})
        mv = [(n, res.nodes[n]["move_mm"]) for n in corners
              if res.nodes.get(n, {}).get("move_mm") and math.hypot(*res.nodes[n]["move_mm"]) >= 0.005]
        was = sum(s_.length for s_ in pb.segs.get(p, []))
        parts.append(f"{'|'.join(p)} declared {s}: {p[0]} and {p[1]} share no edge inside the outline where the fit "
                     f"puts the corners ({was:.2f} mm at the design's)"
                     + (f"; the fit moved (d y mm) {', '.join(f'{n} {_mm(m[0])} {_mm(m[1])}' for n, m in mv)}"
                        if mv else ""))
        recs.append({"pair": list(p), "declared": s, "free_deg": None, "free_sd_deg": None, "dchi2": None, "moves": [],
                     "reason": "no shared edge after the fit", "moved": {n: list(m) for n, m in mv}})
    raise TempleError("E_FIT_SHAPE", "the declared crease shapes: " + "; ".join(parts), dchi2=None,
                      accept_dchi2=ACCEPT_DCHI2, chi2_free=res.chi2_free, creases=recs)


def _solved_warning(pb: _Problem, n: str, rec: dict) -> dict:
    """W_FIT_SOLVED (module docstring): the faces that set a null node inside the outline, their observed corners and
    the nearest one's distance, X +- sd; or the thickness bound that holds it."""
    faces = sorted({l for l in pb.of[n] if l in pb.col} | {t[2] for t in pb.eq_def if t[0] == "tee" and t[1] == n})
    seen = sorted({m for l in faces for m in pb.poly[l] if m != n and pb.src[m][0] in ("read", "typed", "base")})
    p = np.array(pb.nodes[n])
    dist = {m: float(np.hypot(*(np.array(pb.nodes[m]) - p))) for m in seen if pb.src[m][0] in ("read", "typed")}
    near = min(dist, key=lambda m: (dist[m], m)) if dist else None
    if rec["at_bound"]:
        msg = f"node {n}: no reading or typed value; the thickness bound holds it at t {_fixed(rec['t'])} mm"
    else:                                            # (mm said: session2 printed "X 70.57 +- 0.25 (t 4.64)")
        msg = (f"node {n}: no reading or typed value; X {rec['X']:.2f} +- {rec['sd_X']:.2f} mm "
               f"(t {_fixed(rec['t'])} mm)")
    if faces:
        msg += (f"{' (' if rec['at_bound'] else ' '}from face{'s' if len(faces) > 1 else ''} {', '.join(faces)}"
                + (f" through {', '.join(seen)}" if seen else ""))
        if near is not None:
            msg += f", {dist[near]:.1f} mm from its nearest observed corner {near}"
        msg += ")" if rec["at_bound"] else ""
    return {"code": "W_FIT_SOLVED", "node": n, "faces": faces, "through": seen, "nearest": near,
            "nearest_mm": None if near is None else dist[near], "at_bound": bool(rec["at_bound"]), "X": rec["X"],
            "sd_mm": rec["sd_X"], "message": msg}


def _warp_twist(pb: _Problem, label: str, F: np.ndarray) -> dict:
    """How far a fitted warp's surface turns (module docstring, Flatness): the largest angle between its normal (-grad
    X, 1) and its area-mean normal, over the part inside the outline, and where. The area-mean normal over (d, y) is
    (-g_mean, 1) (the surface's area element cancels its normal's length), g_mean the mean gradient over a TWIST_GRID_MM
    grid (central differences over TWIST_STEP_MM, points nearer the polygon's edges than 2 steps left out); the
    candidates are those grid points and each edge's points at EDGE_AT and CORNER_NEAR_MM from its ends (the MVC
    boundary gradient, mvc_edge_gradient: the surface near a corner tends to the plane of its two edges, which the
    interior grid misses). Why (v3p3-verify-9): following the prompt, the A/A2 split's warped logo turned 62.6 deg
    0.03 mm from A (52 at this sampling's 0.05 mm; built 57.5, a bright streak 3 mm long), reported only as "0.33 mm
    off one plane"; the least-squares plane is no reference (r6's crown turns 23 from it, 9.0 from its area-mean
    normal). Information: W_WARP_TWIST above WARP_TWIST_WARN_DEG."""
    P = np.asarray(pb.Pcur[label], np.float64)
    names = pb.poly[label]
    B = np.asarray(pb.cur_ps.boundary(), np.float64)
    F = np.asarray(F, np.float64)
    lo, hi, g, h = P.min(axis=0), P.max(axis=0), TWIST_GRID_MM, TWIST_STEP_MM
    Q = np.stack(np.meshgrid(np.arange(lo[0] + g / 2, hi[0], g), np.arange(lo[1] + g / 2, hi[1], g)),
                 axis=-1).reshape(-1, 2)

    def inside(X):
        X = np.asarray(X, np.float64).reshape(-1, 2)
        return (points_in_poly(X, B) | (_dists_to_edges(X, B) <= ON_MM)) if len(X) else np.zeros(0, bool)
    if len(Q):
        Q = Q[points_in_poly(Q, P) & (_dists_to_edges(Q, P) > 2.0 * h)]
    if len(Q):
        Q = Q[inside(Q)]
    grads = []
    if len(Q):
        gd = (mvc(P, F, Q[:, 0] + h, Q[:, 1]) - mvc(P, F, Q[:, 0] - h, Q[:, 1])) / (2.0 * h)
        gy = (mvc(P, F, Q[:, 0], Q[:, 1] + h) - mvc(P, F, Q[:, 0], Q[:, 1] - h)) / (2.0 * h)
        grads = [np.stack([gd, gy], axis=1)]
    pts = [Q] if len(Q) else []
    m = len(P)
    for i in range(m):
        a, b = P[i], P[(i + 1) % m]
        L = float(np.hypot(*(b - a)))
        if L <= 2.0 * CORNER_NEAR_MM:
            continue
        fr = sorted(set(EDGE_AT) | {CORNER_NEAR_MM / L, 1.0 - CORNER_NEAR_MM / L})
        E = np.array([a + s * (b - a) for s in fr])
        E = E[inside(E)]
        if len(E):
            pts.append(E)
            grads.append(np.array([mvc_edge_gradient(P, i, e) @ F for e in E]))
    if not pts:
        return {"twist_deg": None, "twist_at": None, "twist_near": None, "twist_near_mm": None}
    X, G = np.vstack(pts), np.vstack(grads)
    gm = (grads[0].mean(axis=0) if len(Q) else G.mean(axis=0))   # the interior's area mean (else the edges')
    nm = np.array([-gm[0], -gm[1], 1.0]) / math.sqrt(1.0 + float(gm @ gm))
    nv = np.c_[-G, np.ones(len(G))]
    nv /= np.linalg.norm(nv, axis=1, keepdims=True)
    ang = np.degrees(np.arccos(np.clip(nv @ nm, -1.0, 1.0)))
    k = int(np.argmax(ang))
    dist = np.hypot(*(P - X[k]).T)
    j = int(np.argmin(dist))
    return {"twist_deg": float(ang[k]), "twist_at": [float(X[k, 0]), float(X[k, 1])], "twist_near": names[j],
            "twist_near_mm": float(dist[j])}


def _twist_text(f: dict) -> str:
    """A warp's largest sampled turn from its mean plane, in whole degrees, where, and its nearest corner. "Sampled":
    nearer a corner whose two edges run nearly straight on, the surface turns further (the A/A2 logo: 52 deg at 0.05
    mm from A, 62.6 at 0.03 mm, built 57.5 on the builder's triangles), so the number is a fact about these points,
    never the surface's limit."""
    d, y = f["twist_at"]
    return (f"its largest sampled turn from its mean plane is {f['twist_deg']:.0f} deg, at d {_fixed(d)}, y "
            f"{_fixed(y)} ({f['twist_near_mm']:.2f} mm from corner {f['twist_near']})")


def _fitted_off_warning(n: str, rec: dict, dr: float, yr: float, sdd: float, sdy: float) -> dict | None:
    """W_FIT_OFF_READING kind "fitted": a read corner the fit placed beyond OFF_POSITION_SD sd of where it was read
    along the directions it may move (its position rows' chi2 beyond the probability of OFF_POSITION_SD sd for their
    count: 2.45 sd in two directions), where, how far and in sd, and the outline knots it carried with their move. Why
    (v3p3 P1, session2 r0004): the flat crown slid the tip E 3.04 mm forward, 2.82 sd, outside its reading's 95 %
    ellipse, the top outline knot with it (0.42 mm higher at d 70), and no fact said so: the position test above
    compares the design's node, which sat 0.004 mm from the reading. The fit may move a corner up to POS_MAX_SD per
    direction (the prompt said "within its reading's error")."""
    k = int(rec.get("free_dims") or 0)
    ps_ = float(rec.get("pos_sd") or 0.0)
    if not k or chi2_tail(ps_ ** 2, k) >= chi2_tail(OFF_POSITION_SD ** 2, 1):
        return None
    d, y = float(rec["d"]), float(rec["y"])
    off = math.hypot(d - dr, y - yr)
    pm = (lambda v: f" +- {v:.2f}" if v > 0.0 else "")
    knots = list(rec.get("knots") or [])
    mv = rec.get("move_mm") or [0.0, 0.0]
    msg = (f"node {n} at d {_fixed(d)}, y {_fixed(y)}: the fit placed it {off:.2f} mm from its reading at d "
           f"{_fixed(dr)}{pm(sdd)}, y {_fixed(yr)}{pm(sdy)} ({ps_:.1f} sd along the directions it may move"
           + (", at its move limit" if rec.get("at_cap") else "") + ")")
    if knots:
        msg += (f"; the {' and '.join(knots)} outline knot{'s' if len(knots) > 1 else ''} it lies on moved with it "
                f"({_mm(mv[0])} {_mm(mv[1])} mm in d y)")
    return {"code": "W_FIT_OFF_READING", "node": n, "kind": "fitted", "off_mm": off, "at": [d, y], "read_at": [dr, yr],
            "pos_sd": ps_, "knots": knots, "message": msg}


def _not_flat_warning(label: str, test: dict, obs: list, nodes: dict) -> dict:
    """W_FACE_NOT_FLAT (module docstring): fixed wording, facts only, the ways unranked."""
    dev = [x for x in test["loo"]]
    if test["extra"] == 1:
        # one extra corner: every deviation has the one sigma sqrt(chi2), said per corner, and that the readings cannot
        # single one out (v3p3-verify-2: the mm rank the corners by lever arm, A at d 0.4 always largest, 36 mm in front
        # of K, E, H; a 2 mm lift at K alone printed A -5.27, E -3.34, K +2.00)
        parts, unit = [], " mm"
        for x in dev:
            if x["in_line"]:
                parts.append(f"{x['node']} (others nearly in line)")
            else:
                parts.append(f"{x['node']} {_signed(x['mm'])} +- {x['sd_mm']:.2f}{unit}")
                unit = ""
        devs = (f"each corner {math.sqrt(test['flat_chi2']):.1f} sd from the plane of the other three (with one extra "
                f"corner the readings cannot say which corner is off): " + ", ".join(parts))
    else:
        top = sorted((x for x in dev if not x["in_line"]), key=lambda x: (-x["sigma"], x["node"]))[:3]
        devs = "largest from the plane of the others: " + ", ".join(
            f"{x['node']} {_signed(x['mm'])} +- {x['sd_mm']:.2f} mm ({x['sigma']:.1f} sd)" for x in top)
    # where the flat fit puts the misfit: each observed corner's whole residual, ranked by it (v3p3 P1: "the flat fit:
    # E +0.41 mm (1.1 sd)" while the fit slid E 3.04 mm, 2.8 sd, along its reading)
    fit = sorted(obs, key=lambda m: _misfit_key(m, nodes[m]))
    built = "the flat fit (fitted - read): " + ", ".join(_misfit_text(m, nodes[m]) for m in fit)
    e = test["extra"]
    head = (f"face {label}: its corners do not fit one flat face (chi2 {test['flat_chi2']:.1f} for {e} extra corner"
            f"{'s' if e > 1 else ''})")
    return {"code": "W_FACE_NOT_FLAT", "face": label, "chi2": test["flat_chi2"], "extra": e, "deviations": dev,
            "fit": [{"node": m, "mm": nodes[m]["residual_mm"], "sd": nodes[m]["residual_sd"],
                     "pos_mm": nodes[m].get("pos_mm"), "pos_sd": nodes[m].get("pos_sd"),
                     "chi2": _whole_chi2(nodes[m])} for m in fit],
            "message": f"{head}: {devs}; {built}; {WAYS}" if fit else f"{head}: {devs}; {WAYS}"}


def _flat_inputs(pb: _Problem, label: str, known: list, g: np.ndarray) -> tuple:
    """A flat face's own flatness test inputs (module docstring): per known corner its point, X and sd, and the camera
    coefficients; a reading at the point where it was read (its X is the surface there), its sd this face's own
    v' C v (v = (-g, 1), g the face's gradient in the readings-only fit: review 2026-10-12, the mean over every face
    through the corner over-fired W_FACE_NOT_FLAT 1.3x, K's crown sd 0.204 against the mean 0.190), its camera
    coefficient dX - g . (dd, dy); a typed value at its node (TYPED_SD_MM), a base corner hard (HARD_SD_MM)."""
    P, X, sd = [], [], []
    fits = sorted({f for n in known if pb.src[n][0] == "read" for f in pb.cam[n]})
    Acam = np.zeros((len(known), len(fits)))
    v = np.array([-g[0], -g[1], 1.0])
    for k, n in enumerate(known):
        s = pb.src[n]
        if s[0] == "read":
            rec = s[3]
            P.append((float(rec["d"]), float(rec["y"])) if "d" in rec and "y" in rec else pb.nodes[n])
            X.append(float(s[1]))
            sd.append(max(math.sqrt(max(float(v @ pb.read_cov[n] @ v), 0.0)), SIG_FLOOR * float(s[2])))
            for j, f in enumerate(fits):
                if f in pb.cam[n]:
                    Acam[k, j] = float(pb.cam[n][f][2] - g @ pb.cam[n][f][:2])
        else:
            P.append(pb.nodes[n])
            X.append(pb.obs[n][0])
            sd.append(pb.obs[n][1] if pb.obs[n][1] > 0 else HARD_SD_MM)
    return np.array(P, np.float64).reshape(-1, 2), np.array(X), np.array(sd), Acam


def _crease_state(deg: float, sd: float) -> str:
    """certain (its sign beyond CERTAIN_SD sd and FLAT_CREASE_DEG), flat (within CERTAIN_SD sd of it every angle stays
    below MIN_CREASE_DEG: neither declaration can be built within the readings), else open (a declaration sets it)."""
    if abs(deg) > CERTAIN_SD * sd and abs(deg) > FLAT_CREASE_DEG:
        return "certain"
    return "flat" if abs(deg) + CERTAIN_SD * sd < MIN_CREASE_DEG else "open"


def _result(pb: _Problem, free: Lsq, sol: Lsq, declared: list, single: dict, readings: dict | None,
            model: dict) -> FitResult:
    ps, N = pb.ps, pb.N
    shapes = dict(declared)
    pairs = sorted(set(pb.segs) | set(shapes), key=lambda p: (p[1] == "base", p))
    # the readings-only fit at its own corner positions: its angles, their sds, its bounds, its face gradients
    pb.set_point(free.z, jac=free.jac)
    cov_X = _plain_cov(pb)
    act_free = np.asarray(free.active[:free.n_bounds], bool)
    free_held = [_point_name(b) for b, a in zip(pb.bound_at[:free.n_bounds], act_free) if a]
    fc = {}
    for pair in pairs:
        mains = [s_ for s_ in pb.segs.get(pair, []) if s_.length > 0.0]
        if mains:
            seg = max(mains, key=lambda s_: s_.length)
            piece = [s_ for s_ in pb.segs[pair] if s_.piece == seg.piece]
            fc[pair] = _angle_sds(seg, free, cov_X) + _mean_dihedral(piece, free.z, free.cov_free)
    fgrad = {l: np.array(free.z[pb.col[l] + 1:pb.col[l] + 3], np.float64) for l in pb.flat}
    # the solution at its own corner positions
    pb.set_point(sol.z, jac=sol.jac)
    z = sol.z
    sdX = np.sqrt(np.maximum(np.diag(sol.cov), 0.0))
    # W_FIT_PRIOR: posterior sd > PRIOR_FLAG_MM and set (in part) by a direction only the prior sets
    W = sol.weak_basis
    by_prior = np.zeros(len(z), bool) if W is None else np.linalg.norm(W, axis=1) > 1e-9
    flag = (sdX > PRIOR_FLAG_MM) & by_prior
    act = np.asarray(sol.active[:sol.n_bounds], bool)
    held = [b for b, a in zip(pb.bound_at[:sol.n_bounds], act) if a]
    at_bound = {b[1] for b in held if b[0] == "node"}
    nodes, warnings = {}, []
    for n in pb.names:
        d, y = pb.cur_nodes[n]                       # where the solution puts the corner (its design's when pinned)
        d0, y0 = pb.nodes[n]
        src = pb.src[n][0]
        oX, osd = pb.obs[n]
        if n in pb.split:                            # a null node outside the outline: one value per facet group
            by = {l: {"X": float(z[pb.cix(l, n)]), "t": float(z[pb.cix(l, n)]) - pb.x_in(d),
                      "sd_X": float(sdX[pb.cix(l, n)])} for l in pb.of[n]}
            rec = {"d": d, "y": y, "X": None, "t": None, "sd_X": max(c["sd_X"] for c in by.values()), "source": src,
                   "obs_X": oX, "obs_sd": osd, "residual_mm": None, "residual_sd": None, "inside": False,
                   "at_bound": False, "by_facet": by, "prior": bool(any(flag[pb.cix(l, n)] for l in pb.of[n]))}
        else:
            i = pb.ix[n]
            X = float(z[i])
            cam = pb.camera_mm(n, z) if src == "read" else 0.0      # the fitted camera turns' share of the reading
            if src in ("read", "typed"):
                rv = _node_rows(pb, n, z)
                res = float(rv[0]) * osd                 # fitted - read X at the fitted corner, after the turns
                oX = X + cam - res                       # the reading's X carried to the fitted corner
            else:
                rv, res = np.zeros(1), X - oX
            rec = {"d": d, "y": y, "X": X, "t": X - pb.x_in(d), "sd_X": float(sdX[i]), "source": src, "obs_X": oX,
                   "obs_sd": osd, "residual_mm": res, "residual_sd": None if osd == 0.0 else res / osd,
                   "prior": bool(flag[i]), "inside": pb.inside[n], "at_bound": n in at_bound}
            if src == "read":                        # pos_mm: its position rows in mm (L rows, L L' its free
                Bm = pb.Bn[n]                        # directions' covariance): fitted - read along them, turns in
                pos_mm = (float(np.linalg.norm(np.linalg.cholesky(Bm.T @ pb.read_cov[n][:2, :2] @ Bm) @ rv[1:]))
                          if Bm.shape[1] else 0.0)
                rec.update({"pos_sd": float(np.linalg.norm(rv[1:])), "pos_mm": pos_mm, "chi2": float(rv @ rv)})
        if src == "read":
            r = pb.src[n][3]
            rec.update({"read_sd": float(pb.src[n][2]), "camera_mm": cam, "gap_mm": rec["X"] - float(pb.src[n][1]),
                        "design_at": [d0, y0], "move_mm": [d - d0, y - y0], "free_dims": len(pb.cidx[n]),
                        "knots": [ch for ch, _ in pb.knots[n]],          # the outline knots that move with it
                        "at_cap": any(abs(float(z[pb.N + j]) - pb.coords[j]["off"]) >= pb.coords[j]["cap"] - 1e-9
                                      for j in pb.cidx[n])})
            rec["reading"] = {k: r[k] for k in READ_KEYS if k in r}
            if "d" in r and "y" in r:
                rec["read_off_mm"] = float(math.hypot(float(r["d"]) - d0, float(r["y"]) - y0))
        nodes[n] = rec
    for n in pb.names:
        rec = nodes[n]
        if pb.src[n][0] != "solve":
            continue
        if n not in pb.split and rec["inside"] and (rec["at_bound"] or not rec["prior"]):
            warnings.append(_solved_warning(pb, n, rec))
        elif rec["prior"]:
            warnings.append({"code": "W_FIT_PRIOR", "node": n, "sd_mm": rec["sd_X"],
                             "message": f"node {n}: set by no reading or face (sd {rec['sd_X']:.1f} mm; the weak prior "
                                        f"holds it near the base surface)"})
    for n in pb.names:
        kind = pb.src[n][0]
        st = readings.get(n) if isinstance(readings, dict) else None
        st = st if isinstance(st, dict) else None
        if st is not None and kind != "read" and n not in pb.split and "X" in st:
            sd = st.get("sd", st.get("sd_X"))
            try:
                sd, Xr = float(sd), float(st["X"])
            except (TypeError, ValueError):
                sd, Xr = 0.0, 0.0
            if sd > 0.0 and math.isfinite(Xr):
                d = pb.nodes[n][0]
                t_node = float(pb.src[n][1]) if kind == "typed" else nodes[n]["t"]
                t_read = Xr - pb.x_in(d)
                zs = (t_node - t_read) / sd
                if abs(zs) > OFF_READING_SD:
                    what = {"typed": "typed", "solve": "solved", "base": "base"}[kind]
                    # t values without "mm" (unlike W_FIT_SOLVED's X and t): tests/agent/test_temple_lab.py pins this
                    # form; a unit here is a request to lab-e2 (v3p3-clean-fit)
                    warnings.append({"code": "W_FIT_OFF_READING", "node": n, "kind": kind, "typed_t": t_node,
                                     "node_t": t_node, "read_t": t_read, "sd_mm": sd,
                                     "message": f"{what} {n} {_fixed(t_node)}: its reading {_fixed(t_read)} +- "
                                                f"{sd:.2f} ({_signed(zs, 1)} sd)"})
        rd = pb.src[n][3] if kind == "read" else st
        if rd is not None and "d" in rd and "y" in rd:
            try:
                dr, yr = float(rd["d"]), float(rd["y"])
            except (TypeError, ValueError):
                continue
            dn, yn = pb.cur_nodes[n]
            off = math.hypot(dn - dr, yn - yr)
            sdd, sdy = _dy_sd(rd)
            carry, each = 0.0, []                     # a read corner's X carried along the faces in its pinned part
            if kind == "read":
                Wm = pb.Wn[n]
                dW = Wm @ (Wm.T @ np.array([dn - dr, yn - yr]))
                carry = float(pb.gpin[n] @ dW)
                each = [float(g @ dW) for g in pb.face_grads(n, free.z)]
            far = off > max(OFF_POSITION_MM, OFF_POSITION_SD * max(sdd, sdy))
            if far or (each and max(abs(c) for c in each) > OFF_CARRY_SD * pb.sig[n]):
                pm = (lambda v: f" +- {v:.2f}" if v > 0.0 else "")
                spread = (f" (the faces through it {_signed(min(each))}..{_signed(max(each))})"
                          if len(each) > 1 and max(each) - min(each) >= 0.005 else "")
                msg = (f"node {n} at d {_fixed(dn)}, y {_fixed(yn)}: its reading at d {_fixed(dr)}{pm(sdd)}, y "
                       f"{_fixed(yr)}{pm(sdy)} ({off:.2f} mm away"
                       + (f"; its X carried {_signed(carry)} mm along the faces to the node{spread}"
                          if each and max(abs(c) for c in each) >= 0.005 else "") + ")")
                warnings.append({"code": "W_FIT_OFF_READING", "node": n, "kind": "position", "off_mm": off,
                                 "at": [dn, yn], "read_at": [dr, yr], "carried_mm": carry, "faces_mm": each,
                                 "message": msg})
            if kind == "read" and pb.cidx[n]:          # where the fit put the corner (v3p3 P1)
                w = _fitted_off_warning(n, nodes[n], dr, yr, sdd, sdy)
                if w is not None:
                    warnings.append(w)
    faces, coefs = {}, {}
    for label in pb.labels:
        poly = pb.poly[label]
        Xc = np.array([z[pb.cix(label, n)] for n in poly])
        obs = [n for n in poly if pb.src[n][0] in ("read", "typed")]
        corner_chi2 = float(sum(nodes[n]["residual_sd"] ** 2 for n in obs))
        if label in pb.col:
            c = pb.col[label]
            ac, b_, c_ = (float(v) for v in z[c:c + 3])
            d0, y0 = pb.centre[label]
            coefs[label] = [ac - b_ * d0 - c_ * y0, b_, c_]
            known = [n for n in poly if pb.src[n][0] in ("read", "typed", "base")]
            P_, X_, sd_, Acam = _flat_inputs(pb, label, known, fgrad[label])
            test = _flat_test(known, P_, X_, sd_, Acam)
            plane = pb.mode[label]
            mis = min(obs, key=lambda n: _misfit_key(n, nodes[n])) if obs else None     # by the whole residual
            rec = {"kind": "flat", "plane": plane if isinstance(plane, str) else next(iter(plane)), "corners": poly,
                   "known": known, "coef": coefs[label], "chi2": corner_chi2, "corner_chi2": corner_chi2,
                   "flat_chi2": None, "extra": 0, "loo": [], "not_flat": False, "misfit_at": mis,
                   "flat_misfit_mm": None if mis is None else nodes[mis]["residual_mm"],
                   "misfit_sd": None if mis is None else abs(nodes[mis]["residual_sd"])}
            if test is not None:
                rec.update(test)
                if test["not_flat"]:
                    warnings.append(_not_flat_warning(label, test, obs, nodes))
        else:
            M = np.c_[np.ones(len(poly)), pb.Pcur[label]]
            ls = np.linalg.lstsq(M, Xc, rcond=None)[0]
            res = Xc - M @ ls
            k = int(np.argmax(np.abs(res)))
            rec = {"kind": "warp", "plane": "warp", "corners": poly, "coef": [float(v) for v in ls],
                   "warp_mm": float(abs(res[k])), "warp_at": poly[k], "warp_signed_mm": float(res[k]),
                   "chi2": corner_chi2, "corner_chi2": corner_chi2, "not_flat": False}
            rec.update(_warp_twist(pb, label, Xc))
            if rec["twist_deg"] is not None and rec["twist_deg"] > WARP_TWIST_WARN_DEG:
                warnings.append({"code": "W_WARP_TWIST", "face": label, "deg": rec["twist_deg"],
                                 "at": rec["twist_at"], "near": rec["twist_near"],
                                 "message": f"face {label} warp ({' '.join(poly)}): {_twist_text(rec)} (over "
                                            f"{WARP_TWIST_WARN_DEG:g} deg)"})
        faces[label] = rec
    warnings.sort(key=lambda w: (FIT_WARNINGS.index(w["code"]), w.get("face", ""), w.get("node", "")))
    creases = []
    for pair in pairs:
        segs = pb.segs.get(pair, [])
        mains = [s_ for s_ in segs if s_.length > 0.0]
        rec = {"pair": list(pair), "name": "|".join(pair), "declared": shapes.get(pair), "segments": len(segs),
               "borders": len(mains), "dchi2": single[pair][0] if pair in single else None, "deg": None, "sd_deg": None,
               "sd_deg_X": None, "shape": None, "certain": False, "state": None, "free_deg": None,
               "free_sd_deg": None, "normal_deg": None, "mean_dihedral_deg": None, "free_mean_deg": None,
               "free_mean_sd_deg": None, "range_deg": None, "held": False, "bound": []}
        if mains:
            seg = max(mains, key=lambda s_: s_.length)
            deg = _angle(seg, z)[0]
            fdeg, sd_u, sd_b, sd_xu, fmean, fmean_sd = fc[pair]
            piece = [s_ for s_ in segs if s_.piece == seg.piece]
            along = [_angle(s_, z)[0] for s_ in piece] if len(piece) > 1 else None
            mean = _mean_dihedral(piece, z)[0]
            state = _crease_state(fdeg, sd_u)
            rec.update({"deg": deg, "sd_deg": sd_u, "sd_deg_X": sd_xu, "free_deg": fdeg, "free_sd_deg": sd_u,
                        "normal_deg": _normal_deg(seg, z), "mean_dihedral_deg": float(mean),
                        "free_mean_deg": fmean, "free_mean_sd_deg": fmean_sd,
                        "shape": "flat" if abs(deg) < FLAT_CREASE_DEG else "ridge" if deg > 0 else "valley",
                        "certain": state == "certain", "state": state,
                        "held": bool(pair in single and single[pair][0] is not None and single[pair][0] > 0.0),
                        "bound": free_held if free_held and sd_b < BOUND_SHARE * sd_u else [],
                        "range_deg": None if along is None else [min(along), max(along)],
                        "at": [float(v) for v in seg.mid], "length_mm": float(sum(s_.length for s_ in segs))})
        creases.append(rec)
    spec = copy.deepcopy(pb.design)
    for n, v in spec["nodes"].items():
        if n in pb.ix:
            v = spec["nodes"][n] = list(v)
            v[2] = nodes[n]["t"]
            if pb.src[n][0] == "read" and pb.cidx[n]:                # a free corner where the fit put it
                v[0], v[1] = nodes[n]["d"], nodes[n]["y"]
                for ch, i in pb.knots[n]:                              # and the outline knots with it
                    spec["outline"][ch][i] = [nodes[n]["d"], nodes[n]["y"]]
        elif pb.src.get(n, ("",))[0] == "read":
            v = spec["nodes"][n] = list(v)
            v[2] = pb.src[n][1] - pb.x_in(pb.ps.nodes[n][0])
    for label in list(spec["facets"]):
        f = spec["facets"][label]
        f = spec["facets"][label] = {"poly": list(f)} if isinstance(f, (list, tuple)) else dict(f)
        f["plane"] = "warp" if pb.mode[label] == "warp" else {"coef": coefs[label]}
    counts = {k: sum(1 for n in pb.names if pb.src[n][0] == k) for k in ("read", "typed", "base", "solve")}
    bounds = [{"what": "node", "node": b[1]} if b[0] == "node" else {"what": b[0], "facet": b[1], "at": b[2]}
              for b in held]
    camera = {f: {"u": float(z[pb.ucol[f]]), "sd": float(sdX[pb.ucol[f]]), "free_u": float(free.z[pb.ucol[f]]),
                  "readings": [n for n in pb.reads if f in pb.cam[n]]} for f in pb.fits}
    model = dict(model, angle_passes=0 if sol is free else sol.passes, position_unknowns=pb.M)
    return FitResult(spec=spec, nodes=nodes, faces=faces, creases=creases, chi2=sol.chi2, chi2_free=free.chi2,
                     n_obs=counts["read"] + counts["typed"], dof=sol.dof, warnings=warnings, snaps=list(ps.snaps),
                     counts=counts, bounds=bounds, camera=camera, model=model)
