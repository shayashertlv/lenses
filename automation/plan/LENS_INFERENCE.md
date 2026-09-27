# Lens inference: identification before confidence

The target remains existing product photographs. The calibrated fitter and native-render experiments are development instruments, not a new requirement for the user to capture calibrated photographs.

## Why minimizing RGB error is insufficient

The effective model predicts linear radiance per channel as `Y = T × background + R × reflected illumination`. In a product photograph, both lighting terms, geometry, camera exposure and white balance can be uncertain. A spatial reflection can resemble a density gradient, and coating color can resemble transmitted tint.

Even with stipulated calibration, local optimizer convergence and a full-rank Jacobian are insufficient to establish unique material parameters. Independent review found two distinct normal-density/reflectance pairs that reproduce two observations to numerical precision: approximately `(0.569919, 0.055529)` and `(0.977714, 0.249802)`. Eight optimization starts can all settle in one basin. Under white transmitted background with no reflection, these alternatives transmit **0.5342 versus 0.2822**. That difference matters in AR even though both fit the original observations.

This counterexample is a regression case. Adding optimization attempts is not a sufficient acceptance rule.

## Current bounded implementation

`reconstruction/lens_fit.py` fits a specified Schlick reflection family and optical-density stops to calibrated linear samples. Intrinsic coordinates, incidence angles, known illumination/background, noise, confidence, calibration provenance, IOR, roughness and knot positions are supplied. It estimates density values and normal RGB reflectance with robust bounded optimization and multiple starts.

The output distinguishes parameter identification, ambiguity, unmet fit/convergence criteria and rejected input. Evidence includes residuals, unregularized data Jacobian information, parameter precision, gradient coverage, plausible alternatives and provenance. Priors must not artificially increase the measured information rank. Unknown calibration and clipped measurements are rejected by this experiment rather than interpreted as reliable RGB.

A sufficient identification condition is independent illumination variation at matching intrinsic position and incidence. For each channel, the linear system with rows `[background, reflection]` must separate `T` and `R`. Then, for nondegenerate values:

1. The stipulated Schlick curve maps measured angular `R` back to normal reflectance.
2. `density(v) = -cos(theta_inside) × log(T / (1 - R))`.
3. The known interpolation basis must constrain all requested density stops.

Noise conditioning, gradient coverage and fit consistency still apply. Data lacking this sufficient condition may be informative, but this prototype cannot certify its uniqueness. It retains an ambiguous result rather than claiming that all such data is mathematically impossible to identify.

## The native inverse experiment

`scripts/lens_inverse_probe.py` consumes **measured Cycles output** from the rendering probes, not the saved ideal RGB reference values. It supplies the known experimental illumination and geometry to the fitter, and consults the original material parameters only to assess recovery afterward.

The initial 1,470-render grid omitted positions very close to the gradient ends. Seven compatible families met the identification criteria; the multistop gradient recovered numerically accurate parameters but failed the explicit coverage policy. The initial eight-family expectation was therefore not met. Its report is retained as `data/lens-conformance/inverse/interior-only-report.json`.

The response to this failure was to acquire **420 additional native measurements** near both ends, for every fixture using the same capture rule. `scripts/blender_lens_boundary_probe.py` does this without changing the original conformance manifest, reference equations, fit thresholds or rendering report. The follow-up inverse run uses both datasets.

With the additional measured coverage, **eight compatible families met the parameter-identification criteria**. Their maximum absolute normal-reflectance error was **0.0000306** and maximum optical-density error was **0.0002155**. The total mirror retained parameter ambiguity, and the angular coating produced an explicit model-mismatch result. All three inverse negative controls produced the expected non-acceptance states.

The angular-coating fixture deliberately lies outside the current inverse fitter's Schlick-only family and must not pass as identified. The total-mirror fixture must retain uncertainty about its hidden absorption. Additional controls reject unknown calibration/clipping and detect missing gradient-height coverage.

Reproduce after the [base conformance capture](LENS_CONFORMANCE.md), from `automation/`:

```powershell
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --background --factory-startup --disable-autoexec --python scripts/blender_lens_boundary_probe.py -- --cases data/lens-conformance/cases.json --output data/lens-conformance/calibration-boundaries
python scripts/lens_inverse_probe.py --extra-native data/lens-conformance/calibration-boundaries/report.json
```

The second command verifies that both native datasets belong to the exact fixture manifest and have complete expected coverage. It writes a parameter report under ignored `data/lens-conformance/inverse/`. Running without the additional dataset intentionally reproduces the unmet eight-family expectation from the original interior-only capture.

## Parameter uncertainty is not automatically an AR failure

The owner needs accurate appearance, not recovery of every microscopic optical parameter. A perfectly reflecting front surface can leave its internal absorption unobservable while its visible response is well constrained. Consequently an ambiguous parameter fit does **not** automatically mean the final automation must reject the model or ask for more photos.

The eventual acceptance gate must propagate admissible material/lighting hypotheses into the intended AR environments and backgrounds. It should ask whether their **visible predictions differ materially**. Unobservable parameters that do not change the supported AR appearance may remain unspecified or receive a declared representative value. Material alternatives that tint a face differently need resolution or an explicit approximation. The current fitter does not implement that final appearance-confidence gate.

## Remaining work for actual product photos

- Automatic lens boundaries, intrinsic coordinates, normals and camera estimates with uncertainties.
- Calibrated handling of image encodings where known, and explicit exposure/white-balance uncertainty where unknown.
- Restricted, shared lighting hypotheses and competing material families; avoid giving each photograph enough lighting freedom to conceal every material error.
- Joint or reversible geometry/material fitting, since a color residual may be caused by wrong surface normals.
- Appearance prediction bounds under face backgrounds, head motion and relevant environments, validated in the production renderer.
- Frozen end-to-end evaluation on held-out glasses, counting targeted input requests and unsupported cases separately from successful unattended reconstruction.
