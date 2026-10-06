"""Frame types, rule values and verdicts (WP4_DESIGN.md §4 step 2, §7, §8). Shared by seat_lens and both checks, so
the tool and the check cannot disagree. numpy only.

The numbers are PLAN.md §3 (owner-supplied research, approved 2026-10-05); they are engineering defaults, not
standards, and every one is overridable by the caller:

- acetate full-rim: male V-bevel in a female groove, effective radial capture ~0.8 mm; the lens extends 0.8 mm
  (allowed 0.6-1.0) beyond the visible aperture, its edge between the rim's front and back faces;
- metal eyewire: V channel 0.30-0.60 mm deep, capture 0.3-0.6 mm, default 0.45;
- semi-rimless (nylor): top held in a channel as above; the exposed lower edge is flat with a ~0.6 x 0.6 mm groove;
- browline: the eyewire continues all round, the brow hides the upper edge where the photos show no metal;
- rimless (drilled): the lens edge is the visible edge;
- minimum lens edge ~1.3 mm with a V-bevel, ~1.8-2.2 mm grooved/nylor.

Pass = the §3 lower bound, target = the §3 default (WP4_DESIGN §2 'Pass threshold'); shields are `semi_rimless` with
`lower_edge='plain'` (§2 'Shields').
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .stations import MID, Measurement, layered

PASS_TOLERANCE_MM = 0.005          # §4 step 2: PASS if v >= pass - 0.005
TRANSITION_MM = 3.0                # §4 step 4 / §8: failing points within 3 mm of a visible-by-design point
UNMEASURED_MAX_SHARE = 0.01        # §8: UNMEASURED <= 1% of required stations
TRANSITION_MAX_SHARE = 0.03        # §8: TRANSITION <= 3%
FULLRIM_GATE_SHARE = 0.02          # §4 step 5: full-rim types with > 2% EXPOSED/BACKED refuse (the tool)
BAND_MAX_MM = 0.05                 # §8: browline band <= 0.05 mm in ranges
CRYSTAL_OVERLAP_MAX_MM3 = 0.01     # §8
CORD_OVERLAP_MAX_MM3 = 0.01        # §5 'the check reports cord-lens overlap', §10 P4 'cord-lens overlap <= 0.01 mm3'
RIM_SPLIT_MIN_DEG = 60.0           # §8: rim split p5 >= 60 deg when g3d_seat says the rim was sharpened
VISIBLE_CLASSES = ("BACKED", "EXPOSED", "NEAR")
VERDICTS = ("PASS", "FAIL", "VISIBLE_BY_DESIGN", "TRANSITION", "UNMEASURED", "BLOCKED", "NOT_REQUIRED")


@dataclass(frozen=True)
class FrameType:
    """One row of WP4_DESIGN §7."""
    name: str
    required: str                  # "all" | "captured" (ENCLOSED/LIPPED stations) | "none"
    rule: str                      # "deep" (min over depths) | "thin" (c(0.5)) | "flats" (min of c(0.05), c(0.95))
                                   # | "report"
    pass_mm: float | None
    target_mm: float | None
    range_mm: tuple | None         # §3 allowed range; above the maximum is an over-capture warning
    exposed: str                   # "refused" (full rim) | "visible_by_design"
    profile: str                   # "flat" | "flat_or_vbevel" | "groove"
    min_edge_mm: float | None      # minimum edge thickness for the profile (V-bevel 1.3, groove 1.8)
    lower_edge: str | None = None  # semi_rimless only: "plain" | "nylor"
    in_range_rule: str | None = None       # browline: rule inside the hidden x-ranges
    in_range_pass_mm: float | None = None
    in_range_target_mm: float | None = None
    cite: str = ""

    @property
    def full_rim(self) -> bool:
        return self.exposed == "refused"

    def with_capture(self, capture_mm: float) -> "FrameType":
        """capture_mm sets the target; pass keeps the type's gap (0.20 acetate, 0.15 others) (§4)."""
        if self.target_mm is None:
            return self
        gap = self.target_mm - self.pass_mm
        return replace(self, target_mm=float(capture_mm), pass_mm=float(capture_mm) - gap)


FRAME_TYPES = {
    "acetate": FrameType("acetate", "all", "deep", 0.60, 0.80, (0.6, 1.0), "refused", "flat_or_vbevel", 1.3,
                         cite="PLAN §3 acetate full-rim: capture ~0.8 mm (0.6-1.0), edge between the rim faces"),
    "metal": FrameType("metal", "all", "thin", 0.30, 0.45, (0.3, 0.6), "refused", "flat_or_vbevel", 1.3,
                       cite="PLAN §3 metal eyewire: V channel 0.30-0.60 mm, capture 0.3-0.6, default 0.45"),
    "browline": FrameType("browline", "all", "thin", 0.30, 0.45, (0.3, 0.6), "refused", "flat", None,
                          in_range_rule="deep", in_range_pass_mm=0.60, in_range_target_mm=0.80,
                          cite="PLAN §3 browline: eyewire all round (metal numbers); under the brow where the "
                               "photos show no metal the edge is hidden (acetate numbers)"),
    "semi_rimless/plain": FrameType("semi_rimless", "captured", "thin", 0.30, 0.45, (0.3, 0.6),
                                    "visible_by_design", "flat", None, lower_edge="plain",
                                    cite="PLAN §3 semi-rimless: top in a channel (metal numbers); exposed edge "
                                         "visible; WP4_DESIGN §2 'Shields'"),
    "semi_rimless/nylor": FrameType("semi_rimless", "captured", "flats", 0.30, 0.45, (0.3, 0.6),
                                    "visible_by_design", "groove", 1.8, lower_edge="nylor",
                                    cite="PLAN §3 semi-rimless (nylor): channel top, 0.6 x 0.6 mm groove below, "
                                         "edge >= 1.8 mm"),
    "rimless": FrameType("rimless", "none", "report", None, None, None, "visible_by_design", "flat", None,
                         cite="PLAN §3 rimless (drilled): the lens edge is the visible edge"),
}
FRAME_TYPE_NAMES = ("acetate", "metal", "browline", "semi_rimless", "rimless")


def frame_type(name: str, lower_edge: str | None = None, capture_mm: float | None = None) -> FrameType:
    """The §7 row for `name` ('semi_rimless' takes lower_edge 'plain' (default) or 'nylor')."""
    if name == "semi_rimless":
        key = f"semi_rimless/{lower_edge or 'plain'}"
    elif lower_edge not in (None, "plain"):
        raise ValueError(f"lower_edge={lower_edge!r} applies to semi_rimless only")
    else:
        key = name
    if key not in FRAME_TYPES:
        raise ValueError(f"unknown frame_type {name!r} (lower_edge {lower_edge!r}); one of {FRAME_TYPE_NAMES}")
    ft = FRAME_TYPES[key]
    if capture_mm is not None:
        if not 0.0 <= capture_mm <= 1.5:
            raise ValueError(f"capture_mm {capture_mm} outside [0, 1.5]")
        ft2 = ft.with_capture(capture_mm)
        floor = pass_floor(ft)
        if ft2.pass_mm is not None and floor is not None and ft2.pass_mm < floor - 1e-9:
            gap = ft.target_mm - ft.pass_mm
            raise ValueError(f"capture_mm {capture_mm} would put the pass threshold at {ft2.pass_mm:.2f} mm (capture "
                             f"minus the type's {gap:.2f} mm gap), below the PLAN §3 lower bound {floor:.2f} mm for "
                             f"{ft.name}: capture_mm must be >= {floor + gap:.2f} (or leave it unset)")
        ft = ft2
    return ft


def pass_floor(ft: FrameType) -> float | None:
    """The PLAN §3 lower bound of the type's capture range: no pass threshold may sit below it (review fix round 1:
    capture_mm 0 made acetate pass -0.20, so a lens with a visible 0.15 mm gap passed untouched)."""
    if ft.range_mm is not None:
        return float(ft.range_mm[0])
    return ft.pass_mm


def merge_ranges(ranges) -> list:
    """Union of (x0, x1) ranges as sorted, non-overlapping (x0, x1) tuples; overlapping or touching ones merge
    (hide_eyewire declares the union of every call's ranges)."""
    rs = sorted((float(min(r)), float(max(r))) for r in ranges)
    out: list = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def in_ranges(x, ranges) -> np.ndarray:
    """Asset-frame x inside any (x0, x1) hidden range."""
    x = np.asarray(x, np.float64)
    out = np.zeros(len(x), bool)
    for r in ranges or ():
        x0, x1 = float(min(r)), float(max(r))
        out |= (x >= x0) & (x <= x1)
    return out


@dataclass
class Rules:
    """Per point: rule name, value, pass and target thresholds, required flag; `front_required` marks the points
    whose rule also needs the front corner held (§7 metal 'thin: c(0.5) + front covered'), `front_ok` whether it
    is (covered along +n, or ENCLOSED: inside frame material at every depth, the front depth included)."""
    rule: np.ndarray
    value: np.ndarray
    pass_mm: np.ndarray
    target_mm: np.ndarray
    required: np.ndarray
    in_range: np.ndarray
    front_required: np.ndarray | None = None
    front_ok: np.ndarray | None = None


CAPTURED_REQUIRED = ("ENCLOSED", "LIPPED", "UNMEASURED")   # §7 'captured' types grade these; UNMEASURED is never
                                                          # a pass, so it must count toward §8's 1% limit


def rule_values(ft: FrameType, meas: Measurement, x=None, ranges=None) -> Rules:
    """Rule value per point. Browline's in-range rule applies where the asset x lies in a hidden range AND the point
    is layered (an eyewire with the brow over it, §6 step 1): the loop bottom at the same x stays metal.

    On a full rim the thin rule is §7's 'c(0.5) + front covered': the class LIPPED also admits a front held only by
    frame at the front depth (stations.py, for the semi_rimless required set), which on a metal eyewire would pass
    a V ridge with no front lip, so the cover is checked here."""
    N = len(meas.cls)
    if ft.in_range_rule and ranges:
        rng = in_ranges(x if x is not None else np.zeros(N), ranges) & layered(meas)
    else:
        rng = np.zeros(N, bool)
    rule = np.where(rng, ft.in_range_rule or ft.rule, ft.rule).astype(object)
    cap = meas.cap
    with np.errstate(invalid="ignore"):
        deep = np.where(np.isfinite(cap).all(1), np.nanmin(np.where(np.isfinite(cap), cap, np.inf), 1), np.nan)
    thin = cap[:, MID]
    flats = np.where(np.isfinite(cap[:, 0]) & np.isfinite(cap[:, -1]), np.minimum(cap[:, 0], cap[:, -1]), np.nan)
    value = np.where(rule == "deep", deep, np.where(rule == "thin", thin, np.where(rule == "flats", flats, np.nan)))
    p = np.where(rng, ft.in_range_pass_mm if ft.in_range_rule else np.nan,
                 ft.pass_mm if ft.pass_mm is not None else np.nan).astype(float)
    tg = np.where(rng, ft.in_range_target_mm if ft.in_range_rule else np.nan,
                  ft.target_mm if ft.target_mm is not None else np.nan).astype(float)
    if ft.required == "all":
        req = np.ones(N, bool)
    elif ft.required == "captured":
        req = np.isin(meas.cls.astype(str), CAPTURED_REQUIRED)
    else:
        req = np.zeros(N, bool)
    front_req = (rule == "thin") & bool(ft.full_rim)
    front_ok = np.asarray(meas.covered_front, bool) | (meas.cls.astype(str) == "ENCLOSED")
    return Rules(rule, value.astype(float), p, tg, req, rng, front_req.astype(bool), front_ok)


def verdicts(ft: FrameType, meas: Measurement, rules: Rules, s=None, loop_mm: float = 0.0,
             transition: bool = True) -> np.ndarray:
    """Per point verdict (§4 step 2, §8). The check has no BLOCKED (a planner state); TRANSITION = a failing point
    within 3 mm (arc) of a visible-by-design point with v >= 0 and the front covered."""
    cls = meas.cls.astype(str)
    ok = np.isfinite(rules.value) & (rules.value >= rules.pass_mm - PASS_TOLERANCE_MM)
    if rules.front_required is not None:
        ok &= ~rules.front_required | rules.front_ok
    vis = (ft.exposed == "visible_by_design") & np.isin(cls, VISIBLE_CLASSES)
    out = np.where(cls == "UNMEASURED", "UNMEASURED", np.where(vis, "VISIBLE_BY_DESIGN",
                   np.where(ok, "PASS", "FAIL"))).astype(object)
    if ft.required == "none":
        out[:] = "NOT_REQUIRED"
        out[cls == "UNMEASURED"] = "UNMEASURED"
        return out
    if transition and s is not None and loop_mm > 0:
        vis_idx = np.flatnonzero(out == "VISIBLE_BY_DESIGN")
        fail_idx = np.flatnonzero((out == "FAIL") & meas.covered_front & (np.nan_to_num(rules.value, nan=-1) >= 0))
        if len(vis_idx) and len(fail_idx):
            s = np.asarray(s, np.float64)
            d = np.abs(s[fail_idx, None] - s[None, vis_idx])
            d = np.minimum(d, loop_mm - d).min(1)
            out[fail_idx[d <= TRANSITION_MM]] = "TRANSITION"
    return out


def check_verdict(ft: FrameType, meas: Measurement, rules: Rules, verdict: np.ndarray, *, band_ok: bool | None = None,
                  crystal_overlap_mm3: float | None = None, rim_split_p5_deg: float | None = None,
                  sharpened: bool = False, cord_overlap_mm3: float | None = None) -> dict:
    """§8 PASS over the required points. Returns {"pass": bool, "reasons": [...], counts}. `cord_overlap_mm3`: the
    nylor lens's overlap with the frame parts sitting in its groove (§5; graded like §8's crystal overlap, the same
    0.01 mm3 that §10 P4 sets: an existing cord is never moved, so a cord poking through the grooved lens fails the
    lens instead of passing silently)."""
    req = rules.required
    nreq = int(req.sum())
    v = verdict.astype(str)
    cls = meas.cls.astype(str)
    counts = {k: int(((v == k) & req).sum()) for k in VERDICTS}
    reasons = []
    if ft.required != "none" and nreq == 0:
        reasons.append("no required station: nothing holds this lens (no ENCLOSED or LIPPED station); the lens is "
                       "not mounted, or the frame parts or the frame type are wrong")
    if counts["FAIL"]:
        uncovered = 0
        if rules.front_required is not None:
            uncovered = int(((v == "FAIL") & req & rules.front_required & ~rules.front_ok).sum())
        reasons.append(f"{counts['FAIL']} required stations FAIL"
                       + (f" ({uncovered} with the front corner not covered: the thin rule on a full rim needs a "
                          f"front lip, §7)" if uncovered else ""))
    if counts["BLOCKED"]:
        reasons.append(f"{counts['BLOCKED']} required stations BLOCKED")
    if nreq and counts["UNMEASURED"] > UNMEASURED_MAX_SHARE * nreq:
        reasons.append(f"UNMEASURED {counts['UNMEASURED']}/{nreq} > 1%")
    if nreq and counts["TRANSITION"] > TRANSITION_MAX_SHARE * nreq:
        reasons.append(f"TRANSITION {counts['TRANSITION']}/{nreq} > 3%")
    if ft.full_rim:
        eb = int(np.isin(cls, ("EXPOSED", "BACKED")).sum())
        if eb:
            reasons.append(f"full-rim frame type with {eb} EXPOSED/BACKED stations")
    if band_ok is False:
        reasons.append("buried eyewire visible (band > 0.05 mm or area outside the brow) inside the hidden ranges")
    if crystal_overlap_mm3 is not None and crystal_overlap_mm3 > CRYSTAL_OVERLAP_MAX_MM3:
        reasons.append(f"crystal overlap {crystal_overlap_mm3:.3f} mm3 > 0.01")
    if cord_overlap_mm3 is not None and cord_overlap_mm3 > CORD_OVERLAP_MAX_MM3:
        reasons.append(f"cord-lens overlap {cord_overlap_mm3:.4f} mm3 > {CORD_OVERLAP_MAX_MM3:g} (a frame part in the "
                       f"groove pokes into the lens)")
    if sharpened and (rim_split_p5_deg is None or rim_split_p5_deg < RIM_SPLIT_MIN_DEG):
        reasons.append(f"g3d_seat says the rim was sharpened but the GLB rim split p5 is {rim_split_p5_deg}")
    return {"pass": not reasons, "reasons": reasons, "required": nreq, "verdicts": counts}
