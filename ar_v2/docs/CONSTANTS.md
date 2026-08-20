# The constants ledger

Every number in this tree that a reviewer could reasonably ask "where did that
come from" has an entry here, with a **class**:

| class | means |
| --- | --- |
| `physics` | follows from geometry or mechanics; could not be otherwise |
| `derived` | computed from another number in this table, or from the template mesh itself |
| `published` | a figure from literature, transferred as a ratio |
| `measured` | this project measured it, and the report that did is named |
| `stated` | somebody chose it. No measurement decides it |

`npm run check:constants` fails if an exported constant has no entry. It does
**not** fail on the count of `stated` ones, and that is deliberate — v1's audit
got this exactly right: *"driving it to zero by writing better sentences is
exactly the dishonesty it exists to prevent."*

Current mix: **24 stated, 16 published, 14 derived, 11 measured, 3 physics**

For comparison, v1's audit of its own tree found 196 constants of which 32 rested
on physics or geometry — six in seven were one person's number or arbitrary. The
difference is not that v2's authors are more careful; it is that v2 has a
synthetic population to measure against, so a number *can* be measured. Where it
still cannot be, it is in `docs/OPEN-QUESTIONS.md`.

---

## core/linalg.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `EPS` | 1e-12 | `stated` | A length below which a vector is treated as zero. Well under any real geometric quantity in millimetres. |

## core/camera.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `FACE_TO_CAMERA_FLIP` | diag(1, −1, −1) | `physics` | Face space is +Y up / +Z out of the face; CV camera space is +Y down / +Z forward. The two differ by a rotation of pi about X, so a **frontal head is this matrix, not the identity**. Grounded by test: it is the pose that puts the wearer's right temple on the image LEFT, which is what an unmirrored camera does. Getting the side of the multiply wrong returns the conjugate — yaw and roll negated — and made the guided scan impossible to complete. |
| `MEDIAPIPE_ASSUMED_VERTICAL_FOV` | 63° | `published` | The pinhole MediaPipe assumes when solving its own facial transform. Used only as a **starting point** for the bundle and as the fallback when no scan has run. v1 used it as truth. |

## core/mesh.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `CM_TO_MM` | 10 | `physics` | The template `.obj` is in centimetres. |
| `LM.*` | landmark indices | `published` | MediaPipe's face-mesh indices, stable across versions. The nasal sidewall pairs (245/465, 114/343) are v1's, chosen off the mesh rather than a diagram: they sit 10.3 mm and 13.0 mm out at bridge height and a centimetre lower, which brackets the strip every pad lands in. |
| region radii (`standardRegions`) | 7–32 mm | `stated` | A region is a statement about which part of the face a solver may touch; no measurement decides it. The temple and cheek radii are larger than the anatomy because this mesh's silhouette ring is coarse — sizes are asserted in `tests/pipeline.test.ts` so a template swap cannot silently empty one. |

## core/shape/anthropometric.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `CV.faceWidth` | 0.05 | `published` | Bizygomatic breadth CV, adult pooled. |
| `CV.faceHeight` | 0.055 | `published` | Morphological face height CV. |
| `CV.noseWidth` | 0.09 | `published` | Alar width — among the most variable facial dimensions across populations. |
| `CV.noseProtrusion` | 0.10 | `published` | Nasal tip protrusion CV. |
| `CV.noseBridgeDepth` | 0.13 | `published` | Nasal root depth. Large between-group variation; the number a borrowed average nose gets worst. |
| `CV.noseSidewallFlare` | 0.14 | `derived` | From the alar-width CV and the bridge-width CV; the flare is their difference. |
| `CV.eyeSpacing` | 0.055 | `published` | Interpupillary distance CV. |
| `CV.eyeDepth`, `browRidge`, `foreheadSlope` | 0.10–0.15 | `stated` | No clean published span maps to these mesh landmarks. |
| `CV.noseDeviationMm` | 1.6 mm | `stated` | Lateral nasal deviation has a zero mean by construction, so a CV is undefined. |
| reference span for depth modes | nasal root depth (14.8 mm) | `derived` | **This one has a history.** An earlier draft scaled the depth modes off the bridge-to-temple span (78 mm) and produced 10 mm standard deviations — a basis that could build faces nobody has. Prominence traits are local stand-off and have 1–3 mm SDs. |

## core/shape/displacement.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `DISPLACEMENT_PRIORS.smoothness` | 2.2 | `derived` | Set so a 1 mm bump across one edge (~2.5 mm on this mesh) costs about as much as 0.35 mm of landmark reprojection error, which is the assumed noise floor (Q1). |
| `DISPLACEMENT_PRIORS.magnitude` | 0.06 | `stated` | Deliberately weak. Its job is to keep the field from wandering where nothing observed it, not to argue with observations. Set too high it silently reproduces v1: an average nose the data was not allowed to move. |

## enroll/bundle.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `BUNDLE_DEFAULTS.rounds` | 3 | `measured` | Three A/B rounds reach the same optimum as more, within 0.02 mm RMS. `report:enroll`. |
| `huberStart` / `huberEnd` | 4.0 → 2.0 σ | `stated` | Loose first so a bad initialisation is not locked out, tight at the end so outliers stop pulling. Standard practice; no measurement decides the exact pair. |
| `shapePrior` | 1.0 | `derived` | The basis is scaled so one coefficient is one population SD, so a unit prior weight *is* an N(0,1) prior. This is the payoff of scaling the modes that way. |
| `silhouetteWeight` | 0.5 | `measured` | Swept; 0.5 gives the protrusion improvement without the contour term dominating the landmarks. |
| `SILHOUETTE_MATCH_PX` | 20 px | `stated` | Beyond this, a model contour point and an image contour point are not the same feature. One of two places in the tree where a hard threshold is still correct. |
| `landmarkRigidity` band | subnasale → eye line | `derived` | Everything below the subnasale moves with speech and has no bearing on where glasses sit. |

## enroll/keyframes.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `KEYFRAME_DEFAULTS.count` | 48 | `measured` | The knee: 24 costs 0.15 mm of nose accuracy against 48; 96 buys 0.02 mm for twice the solve time. |
| `AXIS_WEIGHT` | yaw 1.0, pitch 0.7, roll 0.25 | `derived` | Yaw triangulates, pitch sees the underside of the nose, roll gives almost no new geometry — it only rotates the image. Equal weights would fill the keyframe budget with rolls. |
| `COVERAGE_THRESHOLDS.yawSpanDeg` | 50° | `derived` | Triangulated depth error goes as `σ·Z/(f·sinθ)`. At the ladder's `Z/f ≈ 0.85 mm/px` and 0.7 px of noise, 1 mm of bridge depth needs `sinθ > 0.6`, i.e. ~37° total span. 50 leaves margin. |
| `COVERAGE_THRESHOLDS.profileYawDeg` | 38° | `stated` | In MEASURED degrees, which are compressed against physical ones by an uncalibrated factor (Q13). Lowered from 55, at which essentially no real wearer would ever be credited with a profile view. |
| `PLATEAU` | 20 frames, 1.2° | `stated` | How still a `reach` beat's maximum must be before it counts as "that is as far as I go". Two thirds of a second at 30 fps — long enough not to fire on the pause mid-turn, short enough not to feel like a hang. |
| `COVERAGE_THRESHOLDS.distanceSpanPct` | 25% | `measured` | Below this the focal-length solve is ill-conditioned. Without the lean beat, PD error goes from 1.8 mm to 9.2 mm. |

## enroll/scale.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `IRIS.defaultMm` | 11.7 mm | `published` | Pooled adult horizontal visible iris diameter — the constant MediaPipe's own iris work uses. **Inherited with a known defect**: it is a white-adult mean. |
| `IRIS.sigmaMm` | 0.55 mm | `derived` | Within-group SD ~0.45 mm, plus the between-group spread of means (11.10–11.75) contributing ~0.27 in quadrature when the wearer's group is unknown. v1 used 0.5 and treated the whole thing as noise; a large part of it is bias. |
| `POPULATION_HVID` | 11.10 / 11.26 / 11.75 mm | `published` | Group means from comparative ocular topography (Japanese / Chinese / white adults). |
| `ID1_CARD` | 85.60 × 53.98 ± 0.12 mm | `physics` | ISO 7810 ID-1. |
| `PD_PLAUSIBLE_MM` | 46–80 mm | `published` | Wider than the adult range on both sides on purpose: the job is to refuse a *measurement failure* — a half-closed eye, a highlight — not to police anybody's face. |

## track/pnp.ts, track/tracker.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `PNP_DEFAULTS.loss` | Huber, 2.5σ | `stated` | Tighter than the bundle's, because at track time the model is known: a residual that large is an outlier, not unexplained shape. |
| `TRACKER_DEFAULTS.maxRmsPx` | 14 px | `stated` | Not a quality bar — "these landmarks do not describe this face", which happens when someone walks in front of the wearer. |
| `TRACKER_DEFAULTS.holdFrames` | 4 | `measured` | v1's value, which it shipped while documenting 2. Here there is one number and it is in one place. |
| `TRACKER_DEFAULTS.smooth` | `false` | `measured` | Every One Euro tuning tested is worse than none, on lag and jitter both. Table on `TrackerOptions.smooth`; caveat in Q7. |
| `TRANSLATION_SMOOTHING`, `ROTATION_SMOOTHING` | 1.2 Hz / β 0.047, 1.5 Hz / β 4.5 | `derived` | `β = (wanted_fc − minCutoff) / speed`, sized against speeds a head reaches. Unused while smoothing is off. |

## detect/uncertainty.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `floorPx` | 0.7 px | `stated` | **Q1.** The detector's noise on a still frontal face, not yet measured on a real camera. |
| `occludedInflation` | 7× | `derived` | A hidden landmark is *biased* toward the average face, not merely noisy, so the inflation must be large enough that it cannot outvote a visible one however many frames it appears in. |
| `disagreementGain` | 0.6 | `stated` | How much unexplained frame-to-frame motion inflates a landmark's sigma. |

## detect/mediapipe.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `DEFAULT_NUM_FACES` | 2 | `measured` | v1's finding, carried over: MediaPipe applies untunable internal smoothing when and only when this is 1. Two switches it off, worth a large visible reduction in lag. |
| `DETECT_LONG_SIDE` | 640 px | `measured` | v1 measured 0.42 mm of landmark disagreement for 61% fewer pixels. The detector runs at 192×192 and its mesh on a 256×256 crop regardless. |

## fit/contact.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `SKIN.stiffnessNPerMm` | 1.0 | `derived` | Sidewall normal is 24% vertical (measured on the template); a 24 g frame weighs 0.235 N; ~1 N of normal force at ~1 mm of visible skin compression. **Q4.** |
| `SKIN.earStiffnessNPerMm` | 0.35 | `stated` | Softer than the nose. An earlier 0.9 made the ear term 22× the frame's weight at the nominal configuration and pushed the frame off the nose entirely. |
| `SKIN.hookStiffnessNPerMm` | 0.8 | `stated` | **The term whose absence was the largest modelling error in this file.** The sidewall normal has a 0.60 forward component, so the nose pushes the frame off the face as hard as it pushes it up; only the temple hook opposes that. Without it the frame slid 60 mm and rotated 88°. |
| `SKIN.clearanceStiffnessNPerMm` | 6 | `stated` | Not tissue compliance — "geometry must not interpenetrate". |
| `TARGET_CONTACT_MM` | −0.5 mm | `derived` | Where a pad sample sits at rest, from the stiffness derivation above. |
| `BEHIND_CHEEK_MM` | 17 mm | `published` | Anthropometric offset from the cheek landmark to the ear attachment. v1's number. |
| `ABOVE_EYE_LINE_MM` | 6 mm | `stated` | Where a temple actually rests, in the auriculo-cephalic sulcus. **Q5.** v1 used landmark 162 minus 5 mm, which is ~10 mm too high because that landmark is near the hairline — this mesh has no ears. |
| `SEAT_DEFAULTS.priorWeight` | 0.004 | `stated` | Keeps the genuinely unconstrained directions (sliding along a locally flat sidewall) from wandering. Too weak to move a converged answer. |
| `SEAT_DEFAULTS.rotationPriorWeight` | 0.02 | `stated` | Same job for rotation: with only one-sided contacts, a rotation that lifts every contact off the surface costs nothing. |

## fit/advice.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `FRAME_TO_FACE_WIDTH` | 0.90 | `derived` | A frame sits *inside* the widest point of the face, not level with it. The template spans 155 mm across landmarks 127/356 and normal adult fronts run 135–142 mm. Comparing the two spans directly reported every frame as 29 mm too narrow. |
| `ADVICE_CONFIDENCE` | 0.45 | `stated` | How well the nose must be known before the system will tell somebody to bend something. Higher than `SOFT_VERDICT`: a hedged verdict is information, a hedged instruction is a wrong instruction with a disclaimer. |
| `CORNEAL_APEX_MM` | 12 mm | `published` | Corneal apex forward of the canthal plane, 11–13 mm in adults. Vertex distance is measured to the cornea, not the eye-corner plane. |
| `WEDGE_SLOPE_MM_PER_MM` | 0.74 | `measured` | Descent per mm of pad separation, from the sweep in `report:seat`. The purely geometric prediction from the template's sidewall angle is ~1.8; the difference is the temple arms taking load as the frame descends. v1 quoted the geometric figure and had no way to check it. |
| verdict thresholds | 4/10 mm, 10/25°, 1.0/2.5° | `stated` | Where "good" becomes "fair" becomes "poor". These are judgement calls about what a wearer notices, and they should be calibrated against real opticians' opinions. |
| `WEIGHTS` (score) | height 3.0, pads 3.0, width 2.0, … | `stated` | Weighted by what a wearer notices within a minute, which is not the same as what is easy to measure. |

## testkit/synthetic.ts

| constant | value | class | why |
| --- | --- | --- | --- |
| `noseDetailMm` | 2.2 mm | `stated` | Amplitude of the non-basis nose detail. Chosen so the basis provably cannot explain it — the falsifiability guard, asserted in `tests/pipeline.test.ts`. |
| `shapeLimit` | 2.5σ | `stated` | Truncation of the shape draw. |
| `PLAUSIBLE.*` | see file | `published` | Adult extremes plus margin, transferred as ratios. Rejection sampling, because an orthogonalised basis makes coefficients independent and *measurements* are not. |
| `CAMERA_LADDER` | 3 geometries | `measured` | `laptop-lid` at 13.5° below the eyes is the geometry v1's seat scored **0 of 600 admitted frames** on. `phone-lap` at 30° is the harder version. |
| `noisePx` | 0.7 px | `stated` | Mirrors Q1. |
| `biasMm` | 0.6 mm | `stated` | Per-landmark systematic offset. Mirrors Q2 — this is the harness's model of the thing that is not calibrated. |
| `gazeAmplitudeMm` | 1.5 mm | `published` | v1 measured MediaPipe's landmarks following the eyes: 2.3 mm mean and 4 mm peak bridge swing under pure gaze. A documented detector defect. |
